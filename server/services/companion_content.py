"""内容管理：策略类型常量、埋点事件、列表聚合辅助。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, case, exists, func, or_
from sqlalchemy.orm import Session

from core.database import (
    CompanionORM,
    CompanionStrategyORM,
    MomentCommentORM,
    MomentLikeORM,
    MomentORM,
    ShortTermMessageORM,
    UserAttributionEventORM,
    UserCompanionStateORM,
    serialize_datetime,
)
from services.moment_schedule import moment_effective_at
from services.moment_urls import moment_preview_pos

_MOMENT_MSG_CLICK_EVENTS = (
    "send_message_click_moment",
    "test_send_message_click_moment",
)

# 管理端下拉 / 新建可选语种（产品已定这 12 个）
ADMIN_LANGUAGES = (
    "zh", "en", "ja", "ko", "pt", "es", "id",
    "ar", "fa", "ru", "bg", "tr",
)

# 库内 companions.language 历史值仍可匹配；不进管理端下拉
SUPPORTED_LANGUAGES = (
    *ADMIN_LANGUAGES,
    "hi", "vi", "th", "de", "fr", "my", "km", "it", "lo", "nl",
    "am", "pl", "cs", "da", "el", "sv", "hu", "fi", "ro", "sw", "nb",
)

# 管理端下拉展示名；写入库 / 匹配智能体只用 value 短码
LANGUAGE_LABELS = {
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "pt": "Português",
    "es": "Español",
    "id": "Bahasa Indonesia",
    "ar": "العربية（ar）",
    "fa": "فارسی（fa）",
    "ru": "Русский（ru）",
    "bg": "Български（bg）",
    "tr": "Türkçe（tr）",
}


def supported_language_options() -> List[Dict[str, str]]:
    return [
        {"value": code, "label": LANGUAGE_LABELS.get(code, code)}
        for code in ADMIN_LANGUAGES
    ]

# 管理端语言筛选项可能是码、locale 或界面文案（如 日本語）
_LANGUAGE_ALIASES = {
    "zh": {
        "zh", "zh-cn", "zh-hans", "zh-tw", "zh-hant", "cn",
        "中文", "汉语", "漢語", "简体中文", "繁體中文", "繁体中文", "chinese",
    },
    "en": {"en", "en-us", "en-gb", "english", "英文", "英語", "英语"},
    "ja": {"ja", "jp", "ja-jp", "日本語", "日语", "日語", "日文", "japanese"},
    "ko": {"ko", "kr", "ko-kr", "한국어", "韩语", "韓語", "韩文", "korean"},
    "pt": {
        "pt", "pt-br", "pt-pt", "português", "portugues",
        "葡萄牙语", "葡萄牙語", "portuguese",
    },
    "es": {
        "es", "es-es", "es-mx", "español", "espanol",
        "西班牙语", "西班牙語", "spanish",
    },
    "id": {
        "id", "id-id", "bahasa indonesia", "bahasaindonesia",
        "印尼语", "印尼語", "indonesian",
    },
    "ar": {"ar", "ar-sa", "ar-eg", "arabic", "阿拉伯语", "阿拉伯語"},
    "bg": {"bg", "bg-bg", "bulgarian", "保加利亚语", "保加利亞語"},
    "fa": {"fa", "fa-ir", "persian", "farsi", "波斯语", "波斯語"},
    "ru": {"ru", "ru-ru", "russian", "俄语", "俄語"},
    "tr": {"tr", "tr-tr", "turkish", "土耳其语", "土耳其語"},
    "hi": {"hi", "hi-in", "hindi", "印地语", "印地語"},
    "vi": {"vi", "vi-vn", "vietnamese", "越南语", "越南語"},
    "th": {"th", "th-th", "thai", "泰语", "泰語"},
    "de": {"de", "de-de", "german", "德语", "德語"},
    "fr": {"fr", "fr-fr", "french", "法语", "法語"},
    "my": {"my", "my-mm", "burmese", "缅甸语", "緬甸語"},
    "km": {"km", "km-kh", "khmer", "高棉语", "柬埔寨语"},
    "it": {"it", "it-it", "italian", "意大利语", "意大利語"},
    "lo": {"lo", "lo-la", "lao", "老挝语", "老撾語"},
    "nl": {"nl", "nl-nl", "dutch", "荷兰语", "荷蘭語"},
    "am": {"am", "am-et", "amharic", "阿姆哈拉语"},
    "pl": {"pl", "pl-pl", "polish", "波兰语", "波蘭語"},
    "cs": {"cs", "cs-cz", "czech", "捷克语", "捷克語"},
    "da": {"da", "da-dk", "danish", "丹麦语", "丹麥語"},
    "el": {"el", "el-gr", "greek", "希腊语", "希臘語"},
    "sv": {"sv", "sv-se", "swedish", "瑞典语", "瑞典語"},
    "hu": {"hu", "hu-hu", "hungarian", "匈牙利语", "匈牙利語"},
    "fi": {"fi", "fi-fi", "finnish", "芬兰语", "芬蘭語"},
    "ro": {"ro", "ro-ro", "romanian", "罗马尼亚语", "羅馬尼亞語"},
    "sw": {"sw", "sw-ke", "swahili", "斯瓦希里语"},
    "nb": {"nb", "nb-no", "no", "norwegian", "挪威语", "挪威語"},
}
_LANGUAGE_EXPAND = {
    "zh": ("zh", "zh-cn", "zh-hans", "zh-tw"),
    "en": ("en", "en-us", "en-gb"),
    "ja": ("ja", "jp", "ja-jp"),
    "ko": ("ko", "kr", "ko-kr"),
    "pt": ("pt", "pt-br", "pt-pt"),
    "es": ("es", "es-es", "es-mx"),
    "id": ("id", "id-id"),
    "ar": ("ar", "ar-sa", "ar-eg"),
    "bg": ("bg", "bg-bg"),
    "fa": ("fa", "fa-ir"),
    "ru": ("ru", "ru-ru"),
    "tr": ("tr", "tr-tr"),
    "hi": ("hi", "hi-in"),
    "vi": ("vi", "vi-vn"),
    "th": ("th", "th-th"),
    "de": ("de", "de-de"),
    "fr": ("fr", "fr-fr"),
    "my": ("my", "my-mm"),
    "km": ("km", "km-kh"),
    "it": ("it", "it-it"),
    "lo": ("lo", "lo-la"),
    "nl": ("nl", "nl-nl"),
    "am": ("am", "am-et"),
    "pl": ("pl", "pl-pl"),
    "cs": ("cs", "cs-cz"),
    "da": ("da", "da-dk"),
    "el": ("el", "el-gr"),
    "sv": ("sv", "sv-se"),
    "hu": ("hu", "hu-hu"),
    "fi": ("fi", "fi-fi"),
    "ro": ("ro", "ro-ro"),
    "sw": ("sw", "sw-ke"),
    "nb": ("nb", "nb-no", "no"),
}


def canonical_content_language(raw: Optional[str]) -> str:
    """归一成策略/用户语言短码（ar/zh/en…）；无法识别或未支持则返回空串。"""
    codes = content_language_codes(raw)
    if not codes:
        return ""
    code = (codes[0] or "").strip().lower()
    return code if code in SUPPORTED_LANGUAGES else ""


def content_language_codes(raw: Optional[str]) -> List[str]:
    """把 日本語 / ja-JP / jp 等归一成可匹配的 caption_lang / companions.language 值。"""
    s = str(raw or "").strip()
    if not s:
        return []
    lowered = s.lower().replace("_", "-")
    compact = lowered.replace(" ", "")
    alias_lower = {
        code: {n.lower().replace("_", "-").replace(" ", "") for n in names}
        for code, names in _LANGUAGE_ALIASES.items()
    }
    for code, names in _LANGUAGE_ALIASES.items():
        if s in names or lowered in {n.lower() for n in names} or compact in alias_lower[code]:
            return list(_LANGUAGE_EXPAND.get(code, (code,)))
    base = lowered.split("-")[0]
    if base == "jp":
        return list(_LANGUAGE_EXPAND["ja"])
    if base == "kr":
        return list(_LANGUAGE_EXPAND["ko"])
    if base == "no":
        return list(_LANGUAGE_EXPAND["nb"])
    if base in SUPPORTED_LANGUAGES:
        return list(_LANGUAGE_EXPAND.get(base, (base,)))
    # 通配：未知但合法的 ISO 639-1 短码，按库内 language 原样匹配
    if len(base) == 2 and base.isalpha():
        return [base]
    return [s]


def _csv_tokens(*parts: Any) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for raw in parts:
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, (list, tuple, set)):
            seq = raw
        else:
            seq = str(raw).replace("，", ",").split(",")
        for item in seq:
            tok = str(item).strip()
            if tok and tok.lower() not in ("none", "null", "undefined") and tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    return tokens

# 兼容旧导入：策略类型 = 埋点事件（完整字段见 services.strategy_types）
from services.strategy_types import DEFAULT_STRATEGY_TYPES as _DEFAULT_ST

PRODUCT_EVENTS: List[Dict[str, str]] = [
    {"event_key": x["event_key"], "name_zh": x["name_zh"]} for x in _DEFAULT_ST
]
PRODUCT_EVENT_KEYS = {x["event_key"] for x in PRODUCT_EVENTS}
_PRODUCT_EVENT_MAP = {x["event_key"]: x for x in PRODUCT_EVENTS}


def product_event_meta(event_key: str) -> Dict[str, str]:
    from services.strategy_types import get_event_meta
    meta = get_event_meta(event_key)
    if meta:
        return meta
    return dict(_PRODUCT_EVENT_MAP.get((event_key or "").strip(), {}))


def validate_trigger_event(event_key: str, db: Optional[Session] = None) -> str:
    """校验并返回规范化 event_key，非法则抛 ValueError。"""
    from services.strategy_types import validate_event_key
    return validate_event_key(event_key, db)


I18N_PROFILE_FIELDS = (
    "name", "personality", "background", "description", "speech_style",
    "appearance", "hobbies", "values", "fears", "love_view",
    "daily_routine", "favorite_things", "life_story", "cultural_values", "gender_perspective",
)


def _month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _chat_cost() -> int:
    try:
        from services.credit_service import get_cost_config
        return int(get_cost_config().get("chat_cost") or 1)
    except Exception:
        return 1


def companion_credit_stats(db: Session, companion_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """累计/本月积分收益估算：对话轮次 × 单次对话扣费。"""
    if not companion_ids:
        return {}
    cost = _chat_cost()
    month_start = _month_start_utc()

    total_map = {
        r.companion_id: int(r.total or 0)
        for r in db.query(
            UserCompanionStateORM.companion_id,
            func.sum(UserCompanionStateORM.turns).label("total"),
        )
        .filter(UserCompanionStateORM.companion_id.in_(companion_ids))
        .group_by(UserCompanionStateORM.companion_id)
        .all()
    }

    month_map = {
        r.companion_id: int(r.cnt or 0)
        for r in db.query(
            ShortTermMessageORM.companion_id,
            func.count(ShortTermMessageORM.id).label("cnt"),
        )
        .filter(
            ShortTermMessageORM.companion_id.in_(companion_ids),
            ShortTermMessageORM.role == "user",
            ShortTermMessageORM.created_at >= month_start,
        )
        .group_by(ShortTermMessageORM.companion_id)
        .all()
    }

    out: Dict[str, Dict[str, int]] = {}
    for cid in companion_ids:
        total_turns = total_map.get(cid, 0)
        month_msgs = month_map.get(cid, 0)
        out[cid] = {
            "total_credit_revenue": total_turns * cost,
            "month_credit_revenue": month_msgs * cost,
        }
    return out


def strategy_to_dict(
    row: CompanionStrategyORM,
    *,
    companion_name: str = "",
) -> dict:
    """单体消息策略（companion_strategies）序列化。

    与「埋点消息策略库 msg_strategy_library」「快速回复 quick_reply_*」无关。
    """
    event_key = (row.trigger_event or row.strategy_type or "").strip()
    meta = product_event_meta(event_key)
    return {
        "id": row.id,
        "companion_id": row.companion_id,
        "companion_name": companion_name or "",
        "trigger_event": event_key,
        "trigger_event_name": meta.get("name_zh", event_key),
        "strategy_type": row.strategy_type or event_key,
        "content": row.content or "",
        "image_url": (getattr(row, "image_url", None) or "").strip(),
        "language": row.language or "zh",
        "source": row.source or "manual",
        "sort_order": int(row.sort_order or 0),
        "is_active": bool(row.is_active),
        "created_by": row.created_by or "",
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at),
    }

def _moment_companion_avatar(companion_id: str, avatar_url: Optional[str]) -> str:
    u = (avatar_url or "").strip()
    if u and u != "__GENERATING__":
        return u
    cid = (companion_id or "").strip()
    if not cid:
        return ""
    return f"https://api.dicebear.com/7.x/avataaars/svg?seed={cid}"

def _moment_msg_click_stats(db: Session, moment_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """Moment 发消息按钮：product_event=send_message_click_moment，按 firebase_event_params.target_id 归到动态。"""
    out: Dict[int, Dict[str, int]] = {}
    if not moment_ids:
        return out
    id_strs = [str(i) for i in moment_ids]
    target_expr = UserAttributionEventORM.firebase_event_params["target_id"].astext
    source_expr = UserAttributionEventORM.firebase_event_params["source"].astext
    actor_expr = case(
        (
            UserAttributionEventORM.user_id.isnot(None),
            func.concat("u:", UserAttributionEventORM.user_id),
        ),
        else_=func.concat(
            "d:", func.coalesce(UserAttributionEventORM.device_id, "")
        ),
    )
    rows = (
        db.query(
            target_expr.label("mid"),
            func.count(UserAttributionEventORM.id).label("cnt"),
            func.count(func.distinct(actor_expr)).label("uv"),
        )
        .filter(
            UserAttributionEventORM.product_event.in_(_MOMENT_MSG_CLICK_EVENTS),
            target_expr.in_(id_strs),
            or_(source_expr == "moments", source_expr.is_(None), source_expr == ""),
        )
        .group_by(target_expr)
        .all()
    )
    for mid_s, cnt, uv in rows:
        try:
            mid = int(str(mid_s or "").strip())
        except (TypeError, ValueError):
            continue
        out[mid] = {"count": int(cnt or 0), "users": int(uv or 0)}
    return out


def apply_moment_engagement_stats(db: Session, items: List[dict]) -> List[dict]:
    """点赞/评论按实表覆盖；发消息按钮点击按归因埋点聚合。

    展示、详情点击读 moments 上已同步的计数字段，不在此扫流水。
    新增字段（不改原字段语义）：likes_users / comments_users / msg_click_count / msg_click_users。
    """
    ids = []
    for it in items:
        try:
            ids.append(int(it.get("id")))
        except (TypeError, ValueError):
            continue
    if not ids:
        return items
    id_set = set(ids)
    like_rows = (
        db.query(
            MomentLikeORM.moment_id,
            func.count(MomentLikeORM.id),
            func.count(func.distinct(MomentLikeORM.user_id)),
        )
        .filter(MomentLikeORM.moment_id.in_(id_set))
        .group_by(MomentLikeORM.moment_id)
        .all()
    )
    comment_rows = (
        db.query(
            MomentCommentORM.moment_id,
            func.count(MomentCommentORM.id),
            func.count(func.distinct(MomentCommentORM.user_id)),
        )
        .filter(MomentCommentORM.moment_id.in_(id_set))
        .group_by(MomentCommentORM.moment_id)
        .all()
    )
    likes = {int(mid): int(n or 0) for mid, n, _uv in like_rows}
    like_users = {int(mid): int(uv or 0) for mid, _n, uv in like_rows}
    comments = {int(mid): int(n or 0) for mid, n, _uv in comment_rows}
    comment_users = {int(mid): int(uv or 0) for mid, _n, uv in comment_rows}
    msg_clicks = _moment_msg_click_stats(db, ids)
    for it in items:
        try:
            mid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        it["likes_count"] = likes.get(mid, 0)
        it["likes_users"] = like_users.get(mid, 0)
        it["comments_count"] = comments.get(mid, 0)
        it["comments_users"] = comment_users.get(mid, 0)
        mc = msg_clicks.get(mid) or {}
        it["msg_click_count"] = int(mc.get("count") or 0)
        it["msg_click_users"] = int(mc.get("users") or 0)
    return items


def moment_to_dict(row: MomentORM, companion: Optional[CompanionORM] = None) -> dict:
    cid = (row.companion_id or "").strip()
    if companion:
        avatar = _moment_companion_avatar(cid, companion.avatar_url)
        author_id = (companion.created_by or "").strip()
        companion_name = (companion.name or "").strip()
    else:
        avatar = _moment_companion_avatar(cid, "")
        author_id = ""
        companion_name = "已注销"
    payload = {
        "id": row.id,
        "companion_id": row.companion_id,
        "companion_name": companion_name,
        "companion_avatar": avatar,
        "author_id": author_id,
        "image_url": row.image_url or "",
        "preview_url": getattr(row, "preview_url", None) or "",
        "preview_pos": moment_preview_pos(row),
        "has_image": bool((row.image_url or "").strip()),
        "has_preview": bool((getattr(row, "preview_url", None) or "").strip()),
        "caption": row.caption or "",
        "caption_lang": row.caption_lang or "",
        "language": (row.caption_lang or (companion.language if companion else "") or ""),
        "view_count": int(row.view_count or 0),
        "view_users": int(row.view_users or 0),
        "detail_click_count": int(row.detail_click_count or 0),
        "detail_click_users": int(row.detail_click_users or 0),
        "likes_count": int(row.likes_count or 0),
        "likes_users": 0,  # apply_moment_engagement_stats 覆盖
        "comments_count": int(row.comments_count or 0),
        "comments_users": 0,
        "msg_click_count": 0,  # send_message_click_moment → target_id
        "msg_click_users": 0,
        "created_at": serialize_datetime(row.created_at),
        "published_at": serialize_datetime(
            getattr(row, "publish_at", None) or row.created_at
        ),
        # 责任运营占位；列表/详情 handler 按 companion 回填
        "ops_admin_id": None,
        "ops_admin": None,
    }
    try:
        from services.moment_schedule import schedule_meta_for_row

        payload.update(schedule_meta_for_row(row))
    except Exception:
        payload.setdefault("schedule_lang", getattr(row, "schedule_lang", None) or "")
        payload.setdefault("schedule_at", getattr(row, "schedule_at_local", None) or "")
        payload.setdefault("publish_at", serialize_datetime(getattr(row, "publish_at", None)))
        payload.setdefault("is_published", True)
        payload.setdefault("is_scheduled", False)
    return payload


def companion_list_item(row: CompanionORM, credit: Dict[str, int]) -> dict:
    return {
        "id": row.id,
        "avatar_url": row.avatar_url or "",
        "avatar_style": getattr(row, "avatar_style", None) or "",
        "name": row.name,
        "gender": row.gender or "",
        "sexual_orientation": (getattr(row, "sexual_orientation", None) or "") or "",
        "age": row.age,
        "city": row.city or "",
        "language": row.language or "zh",
        "tier": row.tier or "featured",
        "is_active": bool(getattr(row, "is_active", True)),
        "is_deleted": bool(row.is_deleted),
        "total_credit_revenue": credit.get("total_credit_revenue", 0),
        "month_credit_revenue": credit.get("month_credit_revenue", 0),
        "created_at": serialize_datetime(row.created_at),
        "created_by": row.created_by or "",
        "tags": list(getattr(row, "tags", None) or []),
        # 责任运营：列表构建时先占位，handler 再按 admin_companion_ids 回填
        "ops_admin_id": None,
        "ops_admin": None,
    }


def _profile_backgrounds_of(row: CompanionORM) -> List[str]:
    from services.companion_manager import normalize_profile_backgrounds

    return normalize_profile_backgrounds(getattr(row, "profile_backgrounds", None))


def profile_payload(row: CompanionORM) -> dict:
    base = {
        "id": row.id,
        "name": row.name,
        "age": row.age,
        "gender": row.gender or "",
        "city": row.city or "",
        "language": row.language or "zh",
        "tier": row.tier or "featured",
        "personality": row.personality or "",
        "background": row.background or "",
        "description": row.description or "",
        "speech_style": row.speech_style or "",
        "appearance": row.appearance or "",
        "hobbies": row.hobbies or "",
        "values": row.values or "",
        "fears": row.fears or "",
        "love_view": row.love_view or "",
        "daily_routine": row.daily_routine or "",
        "favorite_things": row.favorite_things or "",
        "mbti": row.mbti or "",
        "sexual_orientation": row.sexual_orientation or "",
        "life_story": row.life_story or "",
        "cultural_values": row.cultural_values or "",
        "gender_perspective": row.gender_perspective or "",
        "avatar_url": row.avatar_url or "",
        "avatar_style": getattr(row, "avatar_style", None) or "",
        "profile_backgrounds": _profile_backgrounds_of(row),
        "country": row.country or "",
        "region_key": row.region_key or "",
        "region_label": row.region_label or "",
        "persona_axes": row.persona_axes or "",
        "tags": list(getattr(row, "tags", None) or []),
        "is_active": bool(getattr(row, "is_active", True)),
        "is_deleted": bool(row.is_deleted),
        "moderation_status": (getattr(row, "moderation_status", None) or "published").strip() or "published",
        "created_by": row.created_by or "",
        "i18n_profile": dict(getattr(row, "i18n_profile", None) or {}),
    }
    return base


def parse_tag_filter(raw: Any) -> List[str]:
    """运营标签多选：tags=romance&tags=中东24 或 tags=romance,中东24。"""
    if raw is None:
        return []
    chunks: List[Any] = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
    out: List[str] = []
    seen: set[str] = set()
    for item in chunks:
        text = str(item).strip()
        if not text or text.lower() in ("none", "null", "undefined"):
            continue
        for part in text.replace("，", ",").replace(";", ",").split(","):
            tag = part.strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            out.append(tag)
    return out


def apply_companion_tag_filter(q, tags: Optional[List[str]] = None):
    """companions.tags 含任一所选值即命中（JSONB ?|）。"""
    keys = parse_tag_filter(tags)
    if not keys:
        return q
    from sqlalchemy.dialects.postgresql import array as pg_array

    return q.filter(CompanionORM.tags.op("?|")(pg_array(keys)))


def _admin_id_token(raw: Any) -> Optional[str]:
    """筛选项 ID：兼容 5 / 5.0 / 空白。"""
    if raw is None or isinstance(raw, bool):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "undefined"):
        return None
    s = s.replace("，", ",").split(",")[0].strip()
    if len(s) > 2 and s.endswith(".0") and s[:-2].lstrip("-").isdigit():
        s = s[:-2]
    return s or None


def apply_companion_filters(
    q,
    *,
    companion_id: Optional[str] = None,
    keyword: Optional[str] = None,
    language: Optional[str] = None,
    tier: Optional[str] = None,
    gender: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_deleted: Optional[bool] = None,
    created_by: Optional[str] = None,
    created_from=None,
    created_to=None,
):
    tok = _admin_id_token(companion_id)
    if tok:
        clauses = [CompanionORM.id.ilike(f"%{tok}%")]
        if tok.isdigit():
            clauses.append(CompanionORM.created_by == tok)
        q = q.filter(or_(*clauses))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.filter(or_(CompanionORM.id.ilike(kw), CompanionORM.name.ilike(kw)))
    if language and language.strip():
        raw = language.strip()
        if raw.lower() not in ("none", "null", "undefined"):
            codes = content_language_codes(raw)
            lowered = []
            seen = set()
            for code in codes + [raw]:
                key = str(code).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    lowered.append(key)
            q = q.filter(func.lower(func.coalesce(CompanionORM.language, "")).in_(lowered))
    if tier and tier.strip():
        q = q.filter(CompanionORM.tier == tier.strip())
    if gender and gender.strip():
        from services.companion_manager import _normalize_gender
        q = q.filter(CompanionORM.gender == _normalize_gender(gender))
    if is_active is not None:
        q = q.filter(CompanionORM.is_active == is_active)
    if is_deleted is not None:
        q = q.filter(CompanionORM.is_deleted == is_deleted)
    if created_by and created_by.strip():
        q = q.filter(CompanionORM.created_by == created_by.strip())
    if created_from is not None:
        q = q.filter(CompanionORM.created_at >= created_from)
    if created_to is not None:
        end = created_to.replace(hour=23, minute=59, second=59, microsecond=999999)
        q = q.filter(CompanionORM.created_at <= end)
    return q


def apply_moment_filters(
    q,
    *,
    companion_id: Optional[str] = None,
    companion_ids: Optional[List[str]] = None,
    keyword: Optional[str] = None,
    language: Optional[str] = None,
    has_image: Optional[bool] = None,
    published_from: Optional[datetime] = None,
    published_to: Optional[datetime] = None,
):
    """列表筛选。published_from/published_to 按有效发布时间 = coalesce(publish_at, created_at)。"""
    tokens = _csv_tokens(companion_ids, companion_id, keyword)
    if tokens:
        clauses = []
        for tok in tokens:
            pat = f"%{tok}%"
            clauses.append(
                or_(
                    MomentORM.companion_id == tok,
                    MomentORM.companion_id.ilike(pat),
                    exists().where(
                        and_(
                            CompanionORM.id == MomentORM.companion_id,
                            or_(
                                CompanionORM.id.ilike(pat),
                                CompanionORM.name.ilike(pat),
                            ),
                        )
                    ),
                )
            )
        q = q.filter(or_(*clauses))
    if language and str(language).strip():
        raw = str(language).strip()
        if raw.lower() not in ("none", "null", "undefined"):
            codes = content_language_codes(raw)
            lowered = []
            seen_codes = set()
            for code in codes + [raw]:
                key = str(code).strip().lower()
                if key and key not in seen_codes:
                    seen_codes.add(key)
                    lowered.append(key)
            lang_match = [
                func.lower(func.coalesce(MomentORM.caption_lang, "")).in_(lowered),
                exists().where(
                    and_(
                        CompanionORM.id == MomentORM.companion_id,
                        func.lower(func.coalesce(CompanionORM.language, "")).in_(lowered),
                    )
                ),
            ]
            for code in lowered:
                if 1 <= len(code) <= 3:
                    lang_match.append(
                        func.lower(func.coalesce(MomentORM.caption_lang, "")).like(f"{code}%")
                    )
            q = q.filter(or_(*lang_match))
    if has_image is True:
        q = q.filter(MomentORM.image_url.isnot(None), MomentORM.image_url != "")
    elif has_image is False:
        q = q.filter(or_(MomentORM.image_url.is_(None), MomentORM.image_url == ""))
    # 发布时间范围（定时 publish_at，否则 created_at）
    pub = moment_effective_at()
    if published_from is not None:
        q = q.filter(pub >= published_from)
    if published_to is not None:
        end = published_to.replace(hour=23, minute=59, second=59, microsecond=999999)
        q = q.filter(pub <= end)
    return q
