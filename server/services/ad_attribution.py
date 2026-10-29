"""广告归因 → 推荐 user_tags（install+ads → 广义；另打 gclid / campaign）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from services.recommend_tags import normalize_tag_list
from services.recommend_ua import apply_ua_tags_to_user

logger = logging.getLogger(__name__)

GENERIC_AD_USER_TAG = "ua:source:ads"

_INSTALL_EVENTS = frozenset({"install", "first_open"})
# 精准广告标签（与广义 ua:source:ads 在策略 user_tags 中为并集 / OR）
_SPECIFIC_AD_PREFIXES = (
    "ua:campaign:",
    "ua:adgroup:",
    "ua:gclid:",
    "audience:",
)

_AD_INSTALL_SOURCES = frozenset({"ads", "paid", "ad", "advertising"})
_NOISE_UA_VALUES = frozenset({"(not set)", "not set", "none", "null", "undefined", "(none)"})


def is_specific_ad_tag(tag: str) -> bool:
    t = (tag or "").strip()
    return any(t.startswith(p) for p in _SPECIFIC_AD_PREFIXES)


def is_ad_user_tag(tag: str) -> bool:
    t = (tag or "").strip()
    return t == GENERIC_AD_USER_TAG or is_specific_ad_tag(t)


def enrich_paid_ad_tags(tags: Iterable[str]) -> List[str]:
    """有精准广告标签时，自动补上广义广告标签 ua:source:ads。"""
    out = normalize_tag_list(tags)
    if any(is_specific_ad_tag(t) for t in out) and GENERIC_AD_USER_TAG not in out:
        out.append(GENERIC_AD_USER_TAG)
    return out


def is_qualified_ad_install(
    *,
    event_type: str = "",
    install_source: str = "",
    gclid: str = "",
    gad_campaignid: str = "",
    utm_source: str = "",
    utm_medium: str = "",
) -> bool:
    """合格广告安装：install/first_open + install_source=ads。

    gclid / gad_campaignid 用于附加精准标签，不再作为合格门槛。
    """
    _ = (gclid, gad_campaignid, utm_source, utm_medium)
    et = (event_type or "").strip().lower()
    if et and et not in _INSTALL_EVENTS:
        return False
    return (install_source or "").strip().lower() in _AD_INSTALL_SOURCES


def tags_from_attribution_fields(
    *,
    event_type: str = "install",
    install_source: str = "",
    gclid: str = "",
    gad_campaignid: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    utm_content: str = "",
    utm_term: str = "",
    raw_payload: Optional[dict] = None,
) -> List[str]:
    """install + ads → ua:source:ads；有值再打 ua:campaign: / ua:gclid:。"""
    _ = (utm_source, utm_medium, utm_content, utm_term)
    g = (gclid or "").strip()
    camp = (gad_campaignid or "").strip()
    if (not g or not camp) and raw_payload is not None:
        from services.growth_attribution import extract_gclid_and_campaign

        eg, ec = extract_gclid_and_campaign(gclid=g, gad_campaignid=camp, raw_payload=raw_payload)
        g = g or eg
        camp = camp or ec
    if not camp:
        camp = (utm_campaign or "").strip()

    if not is_qualified_ad_install(
        event_type=event_type or "install",
        install_source=install_source,
        gclid=g,
        gad_campaignid=camp,
    ):
        return []

    tags: List[str] = [GENERIC_AD_USER_TAG]
    if camp:
        tags.append(f"ua:campaign:{camp}")
    if g:
        tags.append(f"ua:gclid:{g}")
    return normalize_tag_list(tags)


def tags_from_attribution_row(row) -> List[str]:
    return tags_from_attribution_fields(
        event_type=getattr(row, "event_type", "") or "",
        install_source=getattr(row, "install_source", "") or "",
        gclid=getattr(row, "gclid", "") or "",
        gad_campaignid=getattr(row, "gad_campaignid", "") or "",
        utm_campaign=getattr(row, "utm_campaign", "") or "",
        raw_payload=getattr(row, "raw_payload", None) or {},
    )


def merge_attribution_tags_for_user(db, user_id: int, device_id: Optional[str] = None) -> List[str]:
    """从合格广告 install 重算并写入 users.ua_tags。

    - 回填历史行的 gclid / gad_campaignid（从 play_referrer 解析）
    - 清掉噪声标签（(not set)、ua:medium、错误 ads、旧 gclid）
    - install+ads → ua:source:ads；另写 ua:campaign / ua:gclid
    """
    from core.database import UserAttributionEventORM, UserORM
    from services.growth_attribution import extract_gclid_and_campaign
    from sqlalchemy.orm.attributes import flag_modified

    user = db.query(UserORM).filter(UserORM.id == user_id).first()
    if not user:
        return []

    q = db.query(UserAttributionEventORM).filter(UserAttributionEventORM.user_id == user_id)
    if device_id:
        q = q.filter(UserAttributionEventORM.device_id == device_id)
    rows = q.order_by(UserAttributionEventORM.created_at.asc()).all()
    if not rows and device_id:
        rows = (
            db.query(UserAttributionEventORM)
            .filter(UserAttributionEventORM.device_id == device_id)
            .order_by(UserAttributionEventORM.created_at.asc())
            .all()
        )

    incoming: List[str] = []
    # 同设备所有事件上的 gclid/campaign 汇总，回填到 install 后再打标
    harvest_gclid = ""
    harvest_camp = ""
    for row in rows:
        g, c = extract_gclid_and_campaign(
            gclid=getattr(row, "gclid", "") or "",
            gad_campaignid=getattr(row, "gad_campaignid", "") or "",
            raw_payload=getattr(row, "raw_payload", None) or {},
        )
        if g and not harvest_gclid:
            harvest_gclid = g
        if c and not harvest_camp:
            harvest_camp = c

    for row in rows:
        if (row.event_type or "") not in _INSTALL_EVENTS:
            continue
        gclid, camp = extract_gclid_and_campaign(
            gclid=getattr(row, "gclid", "") or "",
            gad_campaignid=getattr(row, "gad_campaignid", "") or "",
            raw_payload=getattr(row, "raw_payload", None) or {},
        )
        if not gclid:
            gclid = harvest_gclid
        if not camp:
            camp = harvest_camp
        dirty = False
        if gclid and gclid != (row.gclid or ""):
            row.gclid = gclid
            dirty = True
        if camp and camp != (getattr(row, "gad_campaignid", None) or ""):
            row.gad_campaignid = camp
            dirty = True
        if dirty:
            db.flush()
        incoming.extend(tags_from_attribution_row(row))

    incoming = enrich_paid_ad_tags(incoming)
    cleaned = _strip_stale_attribution_tags(getattr(user, "ua_tags", None))

    if not incoming:
        cleaned = enrich_paid_ad_tags(cleaned)
        if cleaned != normalize_tag_list(getattr(user, "ua_tags", None)):
            user.ua_tags = cleaned
            flag_modified(user, "ua_tags")
        return cleaned

    if cleaned != normalize_tag_list(getattr(user, "ua_tags", None)):
        user.ua_tags = cleaned
        flag_modified(user, "ua_tags")
    return apply_ua_tags_to_user(db, user, incoming)


def _strip_stale_attribution_tags(existing: Optional[Iterable[str]]) -> List[str]:
    """去掉噪声与会被归因重算覆盖的标签。"""
    out: List[str] = []
    for t in normalize_tag_list(existing):
        low = t.lower()
        if t == GENERIC_AD_USER_TAG:
            continue
        if t.startswith("ua:gclid:"):
            continue
        if t.startswith("ua:medium:"):
            continue
        if t.startswith("ua:channel:"):
            # 渠道噪声（含 google-play / (not set)）；ZeasAds 真渠道若需保留可再收紧
            val = t.split(":", 2)[-1].strip().lower()
            if val in _NOISE_UA_VALUES or val == "google-play":
                continue
        if "(not set)" in low or low.endswith(":not set"):
            continue
        out.append(t)
    return out


def _option(
    *,
    value: str,
    kind: str,
    user_tag: str,
    source: str,
    count: int,
    label: Optional[str] = None,
) -> dict:
    val = (value or "").strip()
    tag = (user_tag or "").strip()
    if not val or not tag:
        return {}
    return {
        "id": val,
        "label": (label or val)[:200],
        "kind": kind,
        "user_tag": tag,
        "source": source,
        "count": int(count or 0),
    }


def _merge_options(options: Dict[str, dict], item: dict) -> None:
    if not item:
        return
    key = item["user_tag"]
    if key in options:
        options[key]["count"] = int(options[key].get("count") or 0) + int(item.get("count") or 0)
        return
    options[key] = item


def count_generic_ad_users(db) -> int:
    """有广义或具体广告标签的用户数。"""
    from core.database import UserORM

    n = 0
    for (ua_tags,) in db.query(UserORM.ua_tags).filter(UserORM.ua_tags.isnot(None)).all():
        tags = normalize_tag_list(ua_tags)
        if GENERIC_AD_USER_TAG in tags or any(is_specific_ad_tag(t) for t in tags):
            n += 1
    return n


def backfill_attribution_ad_columns(db, *, limit: int = 5000) -> int:
    """把历史 raw_payload/play_referrer 解析进 gclid、gad_campaignid 列（只写列，不当选项源）。"""
    from core.database import UserAttributionEventORM
    from services.growth_attribution import extract_gclid_and_campaign

    rows = (
        db.query(UserAttributionEventORM)
        .order_by(UserAttributionEventORM.id.desc())
        .limit(limit)
        .all()
    )
    updated = 0
    for row in rows:
        gclid, camp = extract_gclid_and_campaign(
            gclid=getattr(row, "gclid", "") or "",
            gad_campaignid=getattr(row, "gad_campaignid", "") or "",
            raw_payload=getattr(row, "raw_payload", None) or {},
        )
        dirty = False
        if gclid and gclid != (row.gclid or ""):
            row.gclid = gclid[:256]
            dirty = True
        if camp and camp != (getattr(row, "gad_campaignid", None) or ""):
            row.gad_campaignid = camp[:128]
            dirty = True
        if dirty:
            updated += 1
    if updated:
        db.flush()
    return updated


def list_ad_group_options(db) -> dict:
    """管理端精准广告选项：只从 user_attribution_events.gad_campaignid / gclid 两列分组。

    打开接口时会先回填空列（从 raw 写入这两列），选项本身不扫 JSONB / ua_tags。
    """
    from sqlalchemy import func
    from sqlalchemy.exc import ProgrammingError, OperationalError

    from core.database import UserAttributionEventORM, _ensure_column

    try:
        _ensure_column("user_attribution_events", "gad_campaignid", "VARCHAR(128) DEFAULT ''")
        _ensure_column("user_attribution_events", "gclid", "VARCHAR(256) DEFAULT ''")
    except Exception:
        logger.warning("ensure attribution ad columns failed", exc_info=True)

    # 历史数据只躺在 raw 里时，先写入两列，再按列聚合
    try:
        n = backfill_attribution_ad_columns(db)
        if n:
            logger.info("list_ad_group_options backfilled %s rows into gclid/gad_campaignid", n)
    except Exception:
        logger.warning("list_ad_group_options backfill failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    options: Dict[str, dict] = {}

    def add_rows(column, kind: str, prefix: str):
        if column is None:
            return
        try:
            rows = (
                db.query(column, func.count(UserAttributionEventORM.id))
                .filter(column.isnot(None), column != "")
                .group_by(column)
                .order_by(func.count(UserAttributionEventORM.id).desc())
                .limit(200)
                .all()
            )
        except (ProgrammingError, OperationalError) as e:
            logger.warning("list_ad_group_options query %s failed: %s", kind, e)
            try:
                db.rollback()
            except Exception:
                pass
            return
        for val, cnt in rows:
            _merge_options(
                options,
                _option(
                    value=str(val),
                    kind=kind,
                    user_tag=f"{prefix}:{val}",
                    source="attribution",
                    count=cnt,
                ),
            )

    add_rows(UserAttributionEventORM.gad_campaignid, "campaign", "ua:campaign")
    add_rows(UserAttributionEventORM.gclid, "gclid", "ua:gclid")

    specific = list(options.values())
    specific.sort(key=lambda x: (-int(x.get("count") or 0), x.get("kind") or "", x.get("label") or ""))

    # 广义广告人数：两列有值的事件数 ∪ install+ads 事件数（不扫 users.ua_tags）
    qualified_ads = 0
    gclid_or_camp = 0
    try:
        qualified_ads = (
            db.query(func.count(UserAttributionEventORM.id))
            .filter(
                UserAttributionEventORM.event_type.in_(tuple(_INSTALL_EVENTS)),
                func.lower(UserAttributionEventORM.install_source).in_(tuple(_AD_INSTALL_SOURCES)),
            )
            .scalar()
        ) or 0
        gclid_or_camp = (
            db.query(func.count(UserAttributionEventORM.id))
            .filter(
                (UserAttributionEventORM.gclid != "")
                | (UserAttributionEventORM.gad_campaignid != "")
            )
            .scalar()
        ) or 0
    except Exception:
        logger.warning("list_ad_group_options counts failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass

    generic = {
        "id": "ads",
        "label": "任意广告来源用户",
        "kind": "generic",
        "user_tag": GENERIC_AD_USER_TAG,
        "source": "attribution",
        "count": max(int(qualified_ads or 0), int(gclid_or_camp or 0)),
        "hint": "匹配 ua:source:ads；精准选项仅来自 gad_campaignid / gclid 两列",
    }
    return {"specific": specific, "generic": generic}


def user_has_ad_audience_tags(user_tags: Iterable[str]) -> bool:
    """用户是否带有任意广告标识（广义 ua:source:ads 或精准 campaign/gclid/…）。"""
    return any(is_ad_user_tag(t) for t in normalize_tag_list(user_tags))


ACQUISITION_NONE = "none"
ACQUISITION_ADS = "ads"
ACQUISITION_ATTRIBUTED = "attributed"
ACQUISITION_LABELS = {
    ACQUISITION_NONE: "无",
    ACQUISITION_ADS: "广告户",
    ACQUISITION_ATTRIBUTED: "带广告",
}


def acquisition_from_ua_tags(ua_tags: Any) -> dict:
    """管理端获客标识：无 / 广告户（仅 ua:source:ads）/ 带广告（有 campaign/gclid）。"""
    tags = normalize_tag_list(ua_tags)
    if any(is_specific_ad_tag(t) for t in tags):
        kind = ACQUISITION_ATTRIBUTED
    elif GENERIC_AD_USER_TAG in tags or any(is_ad_user_tag(t) for t in tags):
        kind = ACQUISITION_ADS
    else:
        kind = ACQUISITION_NONE
    return {
        "acquisition": kind,
        "acquisition_label": ACQUISITION_LABELS[kind],
        "is_ads": kind != ACQUISITION_NONE,
    }


def normalize_admin_ua_tags(raw: Any) -> List[str]:
    """管理端写入 ua_tags：只收 ua:* / 广告标签；有精准标签时自动补 ua:source:ads。"""
    tags = normalize_tag_list(raw)
    out: List[str] = []
    for t in tags:
        if t.startswith("ua:") or is_ad_user_tag(t):
            out.append(t)
        else:
            raise ValueError(f"invalid_ua_tag:{t}")
    return enrich_paid_ad_tags(out)


def split_strategy_ad_tags(tags: Iterable[str]) -> tuple[List[str], bool]:
    """拆成：具体广告 ID 标签 + 是否启用广义广告策略。"""
    specific: List[str] = []
    generic = False
    for tag in normalize_tag_list(tags):
        if tag == GENERIC_AD_USER_TAG:
            generic = True
        elif is_specific_ad_tag(tag):
            specific.append(tag)
    return specific, generic


def strategy_has_ad_audience(tags: Iterable[str]) -> bool:
    """策略是否勾选了广告受众（广义和/或精准）。

    未勾选 → False：运行时只匹配「无广告标识」的自然量用户。
    """
    specific, generic = split_strategy_ad_tags(tags)
    return bool(specific) or generic


def split_ad_and_other_user_tags(tags: Iterable[str]) -> tuple[List[str], List[str]]:
    ad: List[str] = []
    other: List[str] = []
    for tag in normalize_tag_list(tags):
        if is_ad_user_tag(tag):
            ad.append(tag)
        else:
            other.append(tag)
    return ad, other
