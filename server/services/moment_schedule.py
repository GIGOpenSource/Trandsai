"""朋友圈定时发布：按语种对应 IANA 时区，将本地「未转换」时间转为 UTC 再控制可见性。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Query
from zoneinfo import ZoneInfo

from core.database import MomentORM
from core.i18n import normalize_ui_language

_SCHEDULE_FMTS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def lang_to_timezone(lang: Optional[str]) -> str:
    """语种短码 → IANA 时区（与 agent_utils 智能体默认时区一致）。"""
    from services.agent_utils import _fallback_tz_iana

    code = normalize_ui_language(lang or "") or "en"
    return _fallback_tz_iana(code, None)


def parse_naive_schedule_at(raw: Any) -> datetime:
    """解析前端传入的本地年月日时分秒（不带时区）。"""
    s = str(raw or "").strip().replace("T", " ")
    if not s:
        raise ValueError("empty_schedule_at")
    if s.endswith("Z") or "+" in s[10:] or s.count("-") > 2:
        raise ValueError("schedule_at_must_be_naive_local")
    for fmt, maxlen in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        try:
            dt = datetime.strptime(s[:maxlen], fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=0, minute=0, second=0)
            return dt
        except ValueError:
            continue
    raise ValueError("invalid_schedule_at")


def format_schedule_local(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def schedule_local_to_utc(naive_local: datetime, lang: Optional[str]) -> datetime:
    """把「该语种所在时区」的本地时间转为 UTC aware。"""
    tz = ZoneInfo(lang_to_timezone(lang))
    local_aware = naive_local.replace(tzinfo=tz)
    return local_aware.astimezone(timezone.utc)


def utc_to_schedule_local(utc_dt: Optional[datetime], lang: Optional[str]) -> str:
    if utc_dt is None:
        return ""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(lang_to_timezone(lang))
    return utc_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def moment_effective_at():
    """列表排序 / 可见性：定时 publish_at，否则 created_at。"""
    return func.coalesce(MomentORM.publish_at, MomentORM.created_at)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_moment_published(row: MomentORM, now: Optional[datetime] = None) -> bool:
    now = now or utc_now()
    eff = row.publish_at if row.publish_at is not None else row.created_at
    if eff is None:
        return True
    if eff.tzinfo is None:
        eff = eff.replace(tzinfo=timezone.utc)
    return eff <= now


def apply_published_moment_filter(query: Query, now: Optional[datetime] = None) -> Query:
    """用户端：仅返回已到发布时间的朋友圈。"""
    now = now or utc_now()
    return query.filter(moment_effective_at() <= now)


def extract_schedule_fields(data: dict) -> Tuple[Optional[str], Optional[str]]:
    """从 body 取 schedule_at / timestamp 与 schedule_lang / language。"""
    at_raw = data.get("schedule_at")
    if at_raw is None or str(at_raw).strip() == "":
        at_raw = data.get("timestamp")
    lang_raw = data.get("schedule_lang")
    if lang_raw is None or str(lang_raw).strip() == "":
        lang_raw = data.get("language")
    at_s = str(at_raw).strip() if at_raw is not None and str(at_raw).strip() else None
    lang_s = normalize_ui_language(str(lang_raw).strip()) if lang_raw is not None and str(lang_raw).strip() else None
    return at_s, lang_s


def resolve_schedule_for_create(
    data: dict,
    *,
    fallback_lang: str = "",
) -> Tuple[Optional[str], Optional[str], Optional[datetime]]:
    """返回 (schedule_lang, schedule_at_local, publish_at_utc)；无定时则后两者为 None。"""
    at_raw, lang = extract_schedule_fields(data)
    if not at_raw:
        return None, None, None
    if not lang:
        lang = normalize_ui_language(fallback_lang or "") or None
    if not lang:
        raise ValueError("schedule_lang_required")
    naive = parse_naive_schedule_at(at_raw)
    publish_at = schedule_local_to_utc(naive, lang)
    return lang, format_schedule_local(naive), publish_at


def schedule_meta_for_row(row: MomentORM, now: Optional[datetime] = None) -> Dict[str, Any]:
    """管理端展示用。"""
    now = now or utc_now()
    lang = (getattr(row, "schedule_lang", None) or "").strip()
    local = (getattr(row, "schedule_at_local", None) or "").strip()
    publish_at = getattr(row, "publish_at", None)
    if not local and publish_at and lang:
        local = utc_to_schedule_local(publish_at, lang)
    published = is_moment_published(row, now)
    return {
        "schedule_lang": lang,
        "schedule_at": local,
        "publish_at": (
            publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if publish_at and publish_at.tzinfo
            else (publish_at.isoformat() + "Z" if publish_at else None)
        ),
        "timezone": lang_to_timezone(lang) if lang else "",
        "is_published": published,
        "is_scheduled": bool(publish_at and not published),
    }
