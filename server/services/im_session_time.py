"""IM 列表时间范围：UTC 近 2 天 / 自定义 / 全部。"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple

TIME_MODE_LAST_2_DAYS = "last_2_days"
TIME_MODE_ALL = "all"
TIME_MODE_CUSTOM = "custom"
VALID_TIME_MODES = (TIME_MODE_LAST_2_DAYS, TIME_MODE_ALL, TIME_MODE_CUSTOM)


def naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def last_2_days_utc_range(now: Optional[datetime] = None) -> Tuple[datetime, datetime, datetime]:
    """含今天在内最近 2 个 UTC 自然日。

    返回 (start, end_inclusive, end_exclusive)，均为 naive UTC。
    start = 昨天 00:00:00，end_inclusive = 今天 23:59:59，查询用 < 明天 00:00:00。
    """
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    today = now_utc.date()
    start = datetime.combine(today - timedelta(days=1), time.min)
    end_inclusive = datetime.combine(today, time(23, 59, 59))
    end_exclusive = datetime.combine(today + timedelta(days=1), time.min)
    return start, end_inclusive, end_exclusive


def parse_utc_datetime(raw: Optional[str], *, field: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{field} 不能为空")
    text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法 UTC 时间") from exc
    return naive_utc(dt)


def resolve_list_time_range(
    time_mode: Optional[str],
    start_at: Optional[str],
    end_at: Optional[str],
) -> Tuple[str, Optional[datetime], Optional[datetime], Optional[datetime]]:
    """返回 (mode, start, end_inclusive, end_exclusive)。all 时后三项为 None。"""
    mode = (time_mode or "").strip().lower()
    has_custom = bool((start_at or "").strip() or (end_at or "").strip())
    if not mode:
        mode = TIME_MODE_CUSTOM if has_custom else TIME_MODE_LAST_2_DAYS
    if mode not in VALID_TIME_MODES:
        raise ValueError("time_mode 必须是 last_2_days / all / custom")
    if mode == TIME_MODE_ALL:
        return mode, None, None, None
    if mode == TIME_MODE_LAST_2_DAYS:
        start, end_inc, end_excl = last_2_days_utc_range()
        return mode, start, end_inc, end_excl
    start = parse_utc_datetime(start_at, field="start_at")
    end = parse_utc_datetime(end_at, field="end_at")
    if start > end:
        raise ValueError("开始时间不能晚于结束时间")
    end_exclusive = end + timedelta(seconds=1)
    return mode, start, end, end_exclusive


def filename_utc_stamp(dt: datetime) -> str:
    return naive_utc(dt).strftime("%Y%m%dT%H%M%SZ")
