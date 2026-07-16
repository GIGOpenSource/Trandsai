"""
DAU / 留存：基于 page_views 与 button_clicks 中的 user_id（优先）或 device_id。
当日任意一次页面浏览或按钮点击即计为该用户当日活跃。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import ButtonClickORM, PageViewORM


def _day_sql(col):
    return func.strftime("%Y-%m-%d", col)


def _load_activity_sets(db: Session, language: Optional[str]) -> Dict[str, Set[str]]:
    """user_id/device_id -> 出现过行为的日期集合 (YYYY-MM-DD，SQLite strftime UTC 与存储一致)。
    优先使用 user_id，降级使用 device_id。
    """
    q_pv = db.query(
        PageViewORM.user_id,
        PageViewORM.device_id,
        _day_sql(PageViewORM.created_at).label("day")
    )
    q_bc = db.query(
        ButtonClickORM.user_id,
        ButtonClickORM.device_id,
        _day_sql(ButtonClickORM.created_at).label("day")
    )
    if language:
        q_pv = q_pv.filter(PageViewORM.language == language)
        q_bc = q_bc.filter(ButtonClickORM.language == language)

    active: Dict[str, Set[str]] = defaultdict(set)

    # 处理 page_views
    for user_id, device_id, day in q_pv.all():
        if user_id:
            key = f"user_{user_id}"
        elif device_id:
            key = f"device_{device_id}"
        else:
            continue
        if day:
            active[key].add(day)

    # 处理 button_clicks
    for user_id, device_id, day in q_bc.all():
        if user_id:
            key = f"user_{user_id}"
        elif device_id:
            key = f"device_{device_id}"
        else:
            continue
        if day:
            active[key].add(day)

    return active


def _default_range_days() -> tuple[datetime, datetime]:
    """最近 30 天（UTC 日历日）。"""
    end = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=999999)
    start = (end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29))
    return start, end


def compute_dau_series(
    db: Session,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    language: Optional[str],
) -> List[Dict[str, Any]]:
    """按日返回活跃设备数 dau。"""
    active = _load_activity_sets(db, language)

    if not start_dt or not end_dt:
        start_dt, end_dt = _default_range_days()

    start_d = start_dt.date()
    end_d = end_dt.date()
    out: List[Dict[str, Any]] = []
    cur = start_d
    while cur <= end_d:
        day = cur.strftime("%Y-%m-%d")
        dau = sum(1 for ds in active.values() if day in ds)
        out.append({"date": day, "dau": dau})
        cur += timedelta(days=1)
    return out


DEFAULT_RETENTION_DAYS: Sequence[int] = (1, 3, 7, 30)


def compute_retention_cohorts(
    db: Session,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    language: Optional[str],
    retention_offsets: Sequence[int] = DEFAULT_RETENTION_DAYS,
) -> Dict[str, Any]:
    """
    按新增日（设备全局首次活跃日）分组的留存。
    cohort 天内「new_users」= 当日首次出现的设备数；
    dN 留存 = 这批设备在第 N 天仍有活跃的比例（N=1/3/7/30）。
    """
    active = _load_activity_sets(db, language)
    first_seen: Dict[str, str] = {}
    for dev, days in active.items():
        if days:
            first_seen[dev] = min(days)

    if not start_dt or not end_dt:
        start_dt, end_dt = _default_range_days()

    start_d = start_dt.date()
    end_d = end_dt.date()

    cohorts: List[Dict[str, Any]] = []
    cur = start_d
    while cur <= end_d:
        cohort_day = cur.strftime("%Y-%m-%d")
        cohort_devices = {d for d, fs in first_seen.items() if fs == cohort_day}
        n = len(cohort_devices)
        entry: Dict[str, Any] = {
            "cohort_date": cohort_day,
            "new_users": n,
            "retention_pct": {},
            "retention_counts": {},
        }
        if n > 0:
            entry["retention_pct"]["d0"] = 100.0
            entry["retention_counts"]["d0"] = n
            for off in retention_offsets:
                target = cur + timedelta(days=off)
                ts = target.strftime("%Y-%m-%d")
                cnt = sum(1 for dev in cohort_devices if ts in active.get(dev, set()))
                key = f"d{off}"
                entry["retention_counts"][key] = cnt
                entry["retention_pct"][key] = round(100.0 * cnt / n, 2)
        cohorts.append(entry)
        cur += timedelta(days=1)

    return {
        "cohorts": cohorts,
        "windows": list(retention_offsets),
        "note": "新增日以设备首次浏览/点击日为准（UTC 日历日）；dN 为 cohort 日后第 N 日是否活跃。",
    }
