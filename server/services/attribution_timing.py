"""归因时间中数：按自然日筛选 cohort，计算安装→注册/登录/付费等耗时中位数。"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_RANGE_DAYS = 60
DEFAULT_RANGE_DAYS = 30

# (key, label_zh, label_en, from_event, to_event)
TIMING_METRICS: Sequence[Tuple[str, str, str, str, str]] = (
    ("install_to_register", "安装 → 注册", "Install → Register", "install", "register"),
    ("install_to_login", "安装 → 登录", "Install → Login", "install", "login"),
    ("install_to_purchase", "安装 → 付费", "Install → Purchase", "install", "purchase"),
    ("register_to_purchase", "注册 → 付费", "Register → Purchase", "register", "purchase"),
)

_TRACKED_TYPES = ("install", "first_open", "register", "login", "purchase")


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    sec = max(0, int(round(float(seconds))))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and days == 0:
        parts.append(f"{minutes}分钟")
    if not parts:
        parts.append(f"{secs}秒" if secs else "0秒")
    return "".join(parts)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.median(values))


def compute_attribution_timing(
    db,
    start_d: date,
    end_d: date,
) -> Dict[str, Any]:
    """以筛选期内首次安装/打开为 cohort，计算转化耗时中位数。"""
    from sqlalchemy import func

    from core.database import UserAttributionEventORM
    from services.admin_data_stats import _day_end, _day_start

    start_at = _day_start(start_d)
    end_at = _day_end(end_d)

    rows = (
        db.query(
            UserAttributionEventORM.device_id,
            UserAttributionEventORM.event_type,
            func.min(UserAttributionEventORM.created_at).label("first_at"),
        )
        .filter(
            UserAttributionEventORM.device_id != "",
            UserAttributionEventORM.device_id.isnot(None),
            UserAttributionEventORM.event_type.in_(_TRACKED_TYPES),
        )
        .group_by(UserAttributionEventORM.device_id, UserAttributionEventORM.event_type)
        .all()
    )

    by_device: Dict[str, Dict[str, datetime]] = {}
    for device_id, event_type, first_at in rows:
        did = (device_id or "").strip()
        et = (event_type or "").strip()
        ts = _as_utc(first_at)
        if not did or not et or ts is None:
            continue
        slot = by_device.setdefault(did, {})
        prev = slot.get(et)
        if prev is None or ts < prev:
            slot[et] = ts

    deltas: Dict[str, List[float]] = {m[0]: [] for m in TIMING_METRICS}
    cohort_size = 0

    for events in by_device.values():
        t_install = events.get("install")
        t_first_open = events.get("first_open")
        if t_install and t_first_open:
            t0 = min(t_install, t_first_open)
        else:
            t0 = t_install or t_first_open
        if t0 is None:
            continue
        if t0 < start_at or t0 > end_at:
            continue
        cohort_size += 1

        t_register = events.get("register")
        t_login = events.get("login")
        t_purchase = events.get("purchase")

        pairs = {
            "install_to_register": (t0, t_register),
            "install_to_login": (t0, t_login),
            "install_to_purchase": (t0, t_purchase),
            "register_to_purchase": (t_register, t_purchase),
        }
        for key, (a, b) in pairs.items():
            if a is None or b is None:
                continue
            sec = (b - a).total_seconds()
            if sec < 0:
                continue
            deltas[key].append(sec)

    metrics: List[Dict[str, Any]] = []
    for key, label_zh, label_en, _frm, _to in TIMING_METRICS:
        vals = deltas.get(key) or []
        med = _median(vals)
        metrics.append(
            {
                "key": key,
                "label_zh": label_zh,
                "label_en": label_en,
                "median_seconds": None if med is None else round(med, 1),
                "median_display": _format_duration(med),
                "sample_size": len(vals),
            }
        )

    return {
        "start_date": start_d.strftime("%Y-%m-%d"),
        "end_date": end_d.strftime("%Y-%m-%d"),
        "max_days": MAX_RANGE_DAYS,
        "default_days": DEFAULT_RANGE_DAYS,
        "cohort_size": cohort_size,
        "cohort_rule": "device first install/first_open within [start_date, end_date] (UTC natural day)",
        "metrics": metrics,
    }


def apply_created_at_range(q, start_d: Optional[date], end_d: Optional[date]):
    """给事件查询加 created_at 自然日范围（UTC 日历日，闭区间）。

    使用 **naive UTC** 边界，匹配库内 `timestamp without time zone` 存 UTC 的约定，
    避免 aware/naive 比较在部分驱动下筛选失效。
    """
    from datetime import datetime, timedelta

    from core.database import UserAttributionEventORM

    if start_d is not None:
        start_dt = datetime(start_d.year, start_d.month, start_d.day)  # naive UTC 日初
        q = q.filter(UserAttributionEventORM.created_at >= start_dt)
    if end_d is not None:
        # 次日 00:00 半开区间，避免微秒/驱动对 23:59:59.999999 的取舍差异
        end_exclusive = datetime(end_d.year, end_d.month, end_d.day) + timedelta(days=1)
        q = q.filter(UserAttributionEventORM.created_at < end_exclusive)
    return q
