"""推荐标签：用户运行时组装、智能体保存时 sync、字典种子与回填。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.personality_catalog import (
    all_personality_keys,
    keys_from_personality_text,
    personality_defs,
)
from services.prompts.resolve import SUPPORTED_LANGS

logger = logging.getLogger(__name__)

SIDES = ("user", "agent")
TAG_SOURCES = ("seed", "ops", "ua_auto")
SURFACES = ("companions_all", "companions_recommend", "moments")
APPLY_TO = (*SURFACES, "both", "companions")  # companions = 旧值，覆盖推荐+全部
ALL_SENTINEL = "__all__"
ORIENTATION_KEYS = (
    "heterosexual",
    "homosexual",
    "bisexual",
    "pansexual",
    "asexual",
    "secret",
)
_ORIENTATION_ALIASES = {
    "heterosexual": "heterosexual",
    "异性恋": "heterosexual",
    "homosexual": "homosexual",
    "同性恋": "homosexual",
    "bisexual": "bisexual",
    "双性恋": "bisexual",
    "pansexual": "pansexual",
    "泛性恋": "pansexual",
    "asexual": "asexual",
    "无性恋": "asexual",
    "secret": "secret",
    "保密": "secret",
}
_USER_GENDER_ALIASES = {
    "男": "male",
    "male": "male",
    "m": "male",
    "man": "male",
    "女": "female",
    "female": "female",
    "f": "female",
    "woman": "female",
    "non-binary": "non-binary",
    "nonbinary": "non-binary",
    "非二元": "non-binary",
    "confidential": "confidential",
    "secret": "confidential",
    "保密": "confidential",
}

_PERSONALITY_KEYS = None


def personality_key_set() -> set:
    global _PERSONALITY_KEYS
    if _PERSONALITY_KEYS is None:
        _PERSONALITY_KEYS = set(all_personality_keys())
    return _PERSONALITY_KEYS


def short_lang(raw: Optional[str]) -> str:
    return (raw or "").strip().split("-")[0].lower()


def gender_tag_from_companion(gender: Optional[str]) -> Optional[str]:
    """智能体资料列 男/女 → gender:male|female。"""
    g = str(gender or "").strip().lower()
    if g in ("男", "male", "m", "man"):
        return "gender:male"
    if g in ("女", "female", "f", "woman"):
        return "gender:female"
    return None


def gender_tag_from_user(gender: Optional[str]) -> Optional[str]:
    """用户 gender：male/female/non-binary/confidential。"""
    raw = str(gender or "").strip()
    if not raw:
        return None
    key = _USER_GENDER_ALIASES.get(raw) or _USER_GENDER_ALIASES.get(raw.lower())
    return f"gender:{key}" if key else None


_ORIENTATION_MSGIDS: Dict[str, str] = {
    "heterosexual": "异性恋",
    "homosexual": "同性恋",
    "bisexual": "双性恋",
    "pansexual": "泛性恋",
    "asexual": "无性恋",
    "secret": "保密",
}

_ORIENTATION_LABEL_TO_KEY: Optional[Dict[str, str]] = None


def _orientation_label_to_key_map() -> Dict[str, str]:
    """英文 key + 各语种展示文案 → canonical key（创建入库用）。"""
    global _ORIENTATION_LABEL_TO_KEY
    if _ORIENTATION_LABEL_TO_KEY is not None:
        return _ORIENTATION_LABEL_TO_KEY
    from core.i18n import _load_locale_catalogs

    out = dict(_ORIENTATION_ALIASES)
    catalogs = _load_locale_catalogs()
    for key, msgid in _ORIENTATION_MSGIDS.items():
        out[msgid] = key
        for table in catalogs.values():
            label = table.get(msgid)
            if label:
                out[label] = key
                if str(label).isascii():
                    out[str(label).lower()] = key
    _ORIENTATION_LABEL_TO_KEY = out
    return out


def display_sexual_orientation(raw: Optional[str], lang: Optional[str] = None) -> str:
    """API 展示用性取向文案；落库仍用 canonical_orientation。"""
    from core.i18n import _, normalize_ui_language

    key = canonical_orientation(raw) or str(raw or "").strip()
    if not key:
        return ""
    msgid = _ORIENTATION_MSGIDS.get(key)
    if msgid:
        return _(msgid, lang=normalize_ui_language(lang))
    return key


def canonical_orientation(raw: Optional[str]) -> str:
    v = str(raw or "").strip()
    if not v:
        return ""
    mapping = _orientation_label_to_key_map()
    key = mapping.get(v) or mapping.get(v.lower())
    return key if key in ORIENTATION_KEYS else ""


def orientation_tag(raw: Optional[str]) -> Optional[str]:
    key = canonical_orientation(raw)
    return f"orientation:{key}" if key else None


def normalize_surface(surface: Optional[str]) -> str:
    s = (surface or "").strip()
    if s in ("companions", "browse"):
        return "companions_all"
    if s == "recommend":
        return "companions_recommend"
    return s


def apply_to_covers(apply_to: Optional[str], surface: Optional[str]) -> bool:
    """策略 apply_to 是否覆盖当前入口。"""
    surf = normalize_surface(surface)
    v = (apply_to or "").strip() or "both"
    if v == "both":
        return surf in SURFACES
    if v == "companions":
        return surf in ("companions_all", "companions_recommend")
    return v == surf


def waterfall_surface(*, special: Optional[str] = None) -> str:
    if (special or "").strip() == "recommend":
        return "companions_recommend"
    return "companions_all"


def is_auto_agent_tag(tag: str) -> bool:
    t = str(tag or "").strip()
    if not t:
        return False
    if t.startswith("gender:") or t.startswith("orientation:"):
        return True
    return t in personality_key_set()


def normalize_tag_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = list(raw)
    else:
        return []
    out: List[str] = []
    for item in items:
        t = str(item or "").strip()
        if not t or t in out:
            continue
        out.append(t)
    return out


def split_auto_and_ops(tags: Optional[Iterable[str]]) -> Tuple[List[str], List[str]]:
    auto: List[str] = []
    ops: List[str] = []
    for t in normalize_tag_list(tags):
        if is_auto_agent_tag(t):
            auto.append(t)
        else:
            ops.append(t)
    return auto, ops


def sync_companion_tags(
    personality: Optional[str],
    gender: Optional[str],
    existing: Optional[Iterable[str]] = None,
    extra_ops: Optional[Iterable[str]] = None,
    sexual_orientation: Optional[str] = None,
) -> List[str]:
    """每次保存：性格 / gender:* / orientation:* 替换自动项；保留 ops。

    extra_ops 为 None 时沿用 existing 中的非自动 key；传入则以其 ops 为准。
    existing/None 视为 []。
    """
    if extra_ops is None:
        _, ops = split_auto_and_ops(existing)
    else:
        _, ops = split_auto_and_ops(extra_ops)

    out: List[str] = []
    for key in keys_from_personality_text(personality or ""):
        if key not in out:
            out.append(key)
    gtag = gender_tag_from_companion(gender)
    if gtag and gtag not in out:
        out.append(gtag)
    otag = orientation_tag(sexual_orientation)
    if otag and otag not in out:
        out.append(otag)
    for op in ops:
        if op and op not in out:
            out.append(op)
    return out


def assemble_user_tags(
    *,
    chat_purpose: Optional[str] = None,
    preferred_gender: Optional[str] = None,
    gender: Optional[str] = None,
    sexual_orientation: Optional[str] = None,
    ui_lang: Optional[str] = None,
    ops_tags: Optional[Iterable[str]] = None,
    ua_tags: Optional[Iterable[str]] = None,
    mapped_tags: Optional[Iterable[str]] = None,
) -> List[str]:
    """每次请求组装；空字段不产生标签。不落库。"""
    out: List[str] = []

    def add(tag: Optional[str]) -> None:
        t = (tag or "").strip()
        if t and t not in out:
            out.append(t)

    purpose = (chat_purpose or "").strip()
    if purpose:
        add(f"purpose:{purpose}" if not purpose.startswith("purpose:") else purpose)

    pref = (preferred_gender or "").strip()
    if pref.startswith("pref_gender:"):
        add(pref)
    else:
        from core.gender import canonical_preferred_gender

        canon = canonical_preferred_gender(pref)
        if canon:
            add(f"pref_gender:{canon}")

    add(gender_tag_from_user(gender))
    add(orientation_tag(sexual_orientation))

    lang = short_lang(ui_lang)
    if lang:
        add(f"lang:{lang}")

    for t in normalize_tag_list(ops_tags):
        add(t)
    for t in normalize_tag_list(ua_tags):
        add(t)
    for t in normalize_tag_list(mapped_tags):
        add(t)
    try:
        from services.ad_attribution import enrich_paid_ad_tags

        enriched = enrich_paid_ad_tags(out)
        out.clear()
        for t in enriched:
            add(t)
    except Exception:
        pass
    return out


def assemble_user_tags_from_orm(user, ui_lang: str, mapped_tags: Optional[Iterable[str]] = None) -> List[str]:
    """lang:* 只来自 users.language；空语言不加 lang 标签（推荐策略要求语种时不命中）。
    ui_lang 仅作未登录兼容参数，已登录用户忽略请求头语言。
    """
    if user is None:
        return []
    profile_lang = short_lang(getattr(user, "language", None) or "")
    return assemble_user_tags(
        chat_purpose=getattr(user, "chat_purpose", None),
        preferred_gender=getattr(user, "preferred_gender", None),
        gender=getattr(user, "gender", None),
        sexual_orientation=getattr(user, "sexual_orientation", None),
        ui_lang=profile_lang,
        ops_tags=getattr(user, "tags", None),
        ua_tags=getattr(user, "ua_tags", None),
        mapped_tags=mapped_tags,
    )


def group_keys(
    keys: Sequence[str],
    side: str,
    tag_def_map: Dict[Tuple[str, str], str],
) -> Tuple[Dict[str, List[str]], List[str]]:
    """按字典 group 分组；无 group 的进 ungrouped。"""
    grouped: Dict[str, List[str]] = {}
    ungrouped: List[str] = []
    for key in keys:
        group = tag_def_map.get((key, side))
        if group:
            grouped.setdefault(group, []).append(key)
        else:
            ungrouped.append(key)
    return grouped, ungrouped


# 同一维度多选（男/女、性格）组内 OR；运营自定义（ops 且 temprecompanis）组内 AND。
OR_TAG_GROUPS = frozenset({
    "gender",
    "orientation",
    "personality",
    "purpose",
    "pref_gender",
    "lang",
    "ua",
})


def group_keys_match_any(group: Optional[str]) -> bool:
    return (group or "").strip() in OR_TAG_GROUPS


def companion_covers_agent_tags(
    companion_tags: Iterable[str],
    agent_tags: Sequence[str],
) -> bool:
    """智能体 tags 包含策略 agent_tags 中每一个 key，顺序无关；可有多余标签。"""
    need = normalize_tag_list(agent_tags)
    if not need:
        return True
    have = set(normalize_tag_list(companion_tags))
    return set(need).issubset(have)


def object_matches_tags(
    object_tags: Iterable[str],
    strategy_tags: Sequence[str],
    side: str,
    tag_def_map: Dict[Tuple[str, str], str],
    match_mode: str = "any",
) -> bool:
    """用户侧：组间 AND、维度组内 OR。智能体侧：tags 包含全部 agent_tags。
    strategy_tags 空 = 该侧全部。
    """
    if not strategy_tags:
        return True
    if side == "agent":
        return companion_covers_agent_tags(object_tags, strategy_tags)
    obj = set(normalize_tag_list(object_tags))
    grouped, ungrouped = group_keys(strategy_tags, side, tag_def_map)
    for _group, keys in grouped.items():
        if group_keys_match_any(_group):
            if not (obj & set(keys)):
                return False
        elif not set(keys).issubset(obj):
            return False
    if ungrouped:
        keys = list(ungrouped)
        if keys:
            mode = (match_mode or "any").lower()
            if mode == "all":
                if not set(keys).issubset(obj):
                    return False
            elif not (obj & set(keys)):
                return False
    return True


def mapped_tags_for_ua(db, ua_tags: Sequence[str]) -> List[str]:
    """ua_tag_maps 产出附加 key，不删原始 ua:*。"""
    from core.database import UaTagMapORM

    ua = normalize_tag_list(ua_tags)
    if not ua:
        return []
    rows = db.query(UaTagMapORM).all()
    extra: List[str] = []
    for row in rows:
        kind = (row.source_kind or "").strip()
        value = (row.source_value or "").strip()
        tag_key = (row.tag_key or "").strip()
        if not kind or not value or not tag_key:
            continue
        raw = f"ua:{kind}:{value}"
        alt = f"{kind}:{value}"
        audience = f"audience:{value}" if kind == "audience" else ""
        if raw in ua or alt in ua or (audience and audience in ua):
            if tag_key not in extra:
                extra.append(tag_key)
    return extra


_SEED_USER = [
    ("purpose:chatting", "purpose", "闲聊", "Chatting"),
    ("purpose:companionship", "purpose", "陪伴", "Companionship"),
    ("purpose:romance", "purpose", "恋爱", "Romance"),
    ("purpose:communication", "purpose", "沟通练习", "Communication"),
    ("pref_gender:male", "pref_gender", "偏好男性", "Prefer male"),
    ("pref_gender:female", "pref_gender", "偏好女性", "Prefer female"),
    ("pref_gender:non-binary", "pref_gender", "偏好非二元", "Prefer non-binary"),
    ("pref_gender:confidential", "pref_gender", "偏好保密", "Prefer confidential"),
    ("gender:male", "gender", "男性", "Male"),
    ("gender:female", "gender", "女性", "Female"),
    ("gender:non-binary", "gender", "非二元", "Non-binary"),
    ("gender:confidential", "gender", "保密", "Confidential"),
    ("orientation:heterosexual", "orientation", "异性恋", "Heterosexual"),
    ("orientation:homosexual", "orientation", "同性恋", "Homosexual"),
    ("orientation:bisexual", "orientation", "双性恋", "Bisexual"),
    ("orientation:pansexual", "orientation", "泛性恋", "Pansexual"),
    ("orientation:asexual", "orientation", "无性恋", "Asexual"),
    ("orientation:secret", "orientation", "保密", "Confidential"),
]

_SEED_LANG_LABELS = {
    "zh": ("中文", "Chinese"),
    "en": ("英语", "English"),
    "ja": ("日语", "Japanese"),
    "ko": ("韩语", "Korean"),
    "pt": ("葡萄牙语", "Portuguese"),
    "es": ("西班牙语", "Spanish"),
    "id": ("印尼语", "Indonesian"),
    "ar": ("阿拉伯语", "Arabic"),
    "bg": ("保加利亚语", "Bulgarian"),
    "fa": ("波斯语", "Persian"),
    "ru": ("俄语", "Russian"),
    "tr": ("土耳其语", "Turkish"),
    "hi": ("印地语", "Hindi"),
    "vi": ("越南语", "Vietnamese"),
    "th": ("泰语", "Thai"),
    "de": ("德语", "German"),
    "fr": ("法语", "French"),
    "my": ("缅甸语", "Burmese"),
    "km": ("高棉语", "Khmer"),
    "it": ("意大利语", "Italian"),
    "lo": ("老挝语", "Lao"),
    "nl": ("荷兰语", "Dutch"),
    "am": ("阿姆哈拉语", "Amharic"),
    "pl": ("波兰语", "Polish"),
    "cs": ("捷克语", "Czech"),
    "da": ("丹麦语", "Danish"),
    "el": ("希腊语", "Greek"),
    "sv": ("瑞典语", "Swedish"),
    "hu": ("匈牙利语", "Hungarian"),
    "fi": ("芬兰语", "Finnish"),
    "ro": ("罗马尼亚语", "Romanian"),
    "sw": ("斯瓦希里语", "Swahili"),
    "nb": ("挪威语", "Norwegian"),
}


def iter_seed_tag_defs() -> List[dict]:
    rows: List[dict] = []
    for key, group, zh, en in _SEED_USER:
        rows.append({
            "key": key, "side": "user", "group": group,
            "label_zh": zh, "label_en": en, "source": "seed",
        })
    # 内容语种通配：prompt pack 白名单 + 前端/库内常见语种
    from services.companion_content import SUPPORTED_LANGUAGES
    seed_langs = list(dict.fromkeys([*SUPPORTED_LANGS, *SUPPORTED_LANGUAGES]))
    for code in seed_langs:
        zh, en = _SEED_LANG_LABELS.get(code, (code, code))
        rows.append({
            "key": f"lang:{code}", "side": "user", "group": "lang",
            "label_zh": zh, "label_en": en, "source": "seed",
        })
    for d in personality_defs():
        labels = d.get("labels") or {}
        rows.append({
            "key": d["key"], "side": "agent", "group": "personality",
            "label_zh": labels.get("zh") or d["key"],
            "label_en": labels.get("en") or d["key"],
            "source": "seed",
        })
    rows.append({
        "key": "gender:male", "side": "agent", "group": "gender",
        "label_zh": "男性", "label_en": "Male", "source": "seed",
    })
    rows.append({
        "key": "gender:female", "side": "agent", "group": "gender",
        "label_zh": "女性", "label_en": "Female", "source": "seed",
    })
    for key, _group, zh, en in _SEED_USER:
        if not key.startswith("orientation:"):
            continue
        rows.append({
            "key": key, "side": "agent", "group": "orientation",
            "label_zh": zh, "label_en": en, "source": "seed",
        })
    rows.append({
        "key": "romance", "side": "agent", "group": "ops",
        "label_zh": "恋爱向", "label_en": "Romance", "source": "seed",
    })
    rows.append({
        "key": "ops", "side": "agent", "group": "ops",
        "label_zh": "有责任运营", "label_en": "Has ops admin", "source": "seed",
    })
    return rows


def seed_recommend_tag_defs(db) -> int:
    from core.database import RecommendTagDefORM

    inserted = 0
    for row in iter_seed_tag_defs():
        exists = (
            db.query(RecommendTagDefORM)
            .filter(RecommendTagDefORM.key == row["key"], RecommendTagDefORM.side == row["side"])
            .first()
        )
        if exists:
            continue
        db.add(RecommendTagDefORM(
            key=row["key"],
            side=row["side"],
            group=row["group"],
            label_zh=row["label_zh"],
            label_en=row["label_en"],
            enabled=True,
            source=row["source"],
        ))
        inserted += 1
    if inserted:
        db.flush()
    return inserted


def strategies_using_tag(rows, side: str, key: str) -> List[dict]:
    """策略 JSON 数组里精确包含该 key 则视为占用。"""
    out: List[dict] = []
    for r in rows or []:
        tags = r.user_tags if side == "user" else r.agent_tags
        if key in (tags or []):
            out.append({"id": r.id, "name": r.name or ""})
    return out


def tag_def_to_dict(row) -> dict:
    return {
        "key": row.key,
        "side": row.side,
        "group": row.group,
        "label_zh": row.label_zh or "",
        "label_en": row.label_en or "",
        "enabled": bool(row.enabled),
        "source": row.source or "ops",
    }


def apply_tag_def_filters(
    q,
    *,
    side: Optional[str] = None,
    group: Optional[str] = None,
    key: Optional[str] = None,
    label_zh: Optional[str] = None,
    label_en: Optional[str] = None,
    enabled: Optional[bool] = None,
    source: Optional[str] = None,
):
    """按表头字段 AND 筛选。side/group/source 精确，key 与中英文名模糊。"""
    from core.database import RecommendTagDefORM

    side_v = (side or "").strip()
    if side_v:
        q = q.filter(RecommendTagDefORM.side == side_v)
    group_v = (group or "").strip()
    if group_v:
        q = q.filter(RecommendTagDefORM.group == group_v)
    key_v = (key or "").strip()
    if key_v:
        q = q.filter(RecommendTagDefORM.key.ilike(f"%{key_v}%"))
    zh = (label_zh or "").strip()
    if zh:
        q = q.filter(RecommendTagDefORM.label_zh.ilike(f"%{zh}%"))
    en = (label_en or "").strip()
    if en:
        q = q.filter(RecommendTagDefORM.label_en.ilike(f"%{en}%"))
    if enabled is not None:
        q = q.filter(RecommendTagDefORM.enabled.is_(bool(enabled)))
    source_v = (source or "").strip()
    if source_v:
        q = q.filter(RecommendTagDefORM.source == source_v)
    return q


def list_tag_defs(
    db,
    *,
    page: int = 1,
    page_size: int = 10,
    side: Optional[str] = None,
    group: Optional[str] = None,
    key: Optional[str] = None,
    label_zh: Optional[str] = None,
    label_en: Optional[str] = None,
    enabled: Optional[bool] = None,
    source: Optional[str] = None,
) -> Tuple[List[dict], int]:
    from core.database import RecommendTagDefORM

    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 10)))
    q = apply_tag_def_filters(
        db.query(RecommendTagDefORM),
        side=side,
        group=group,
        key=key,
        label_zh=label_zh,
        label_en=label_en,
        enabled=enabled,
        source=source,
    )
    total = int(q.count() or 0)
    rows = (
        q.order_by(
            RecommendTagDefORM.side.asc(),
            RecommendTagDefORM.group.asc(),
            RecommendTagDefORM.key.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [tag_def_to_dict(r) for r in rows], total


def list_tag_def_groups(db) -> List[str]:
    from core.database import RecommendTagDefORM

    rows = (
        db.query(RecommendTagDefORM.group)
        .distinct()
        .order_by(RecommendTagDefORM.group.asc())
        .all()
    )
    return [str(g).strip() for (g,) in rows if str(g or "").strip()]


def upsert_ua_tag_def(db, key: str) -> None:
    """新 ua:* 写入字典 (key, user)。超长 key（如完整 gclid）跳过，避免撑爆 VARCHAR(64)。"""
    from core.database import RecommendTagDefORM

    key = (key or "").strip()
    if not key.startswith("ua:") and not key.startswith("audience:"):
        return
    if len(key) > 64:
        return
    exists = (
        db.query(RecommendTagDefORM)
        .filter(RecommendTagDefORM.key == key, RecommendTagDefORM.side == "user")
        .first()
    )
    if exists:
        return
    group = "ua"
    db.add(RecommendTagDefORM(
        key=key,
        side="user",
        group=group,
        label_zh=key,
        label_en=key,
        enabled=True,
        source="ua_auto",
    ))


def backfill_companion_tags(db) -> int:
    """存量：能解性格则写 + 性别；解不出只写性别。已有 gender:* 则跳过。"""
    from sqlalchemy.orm.attributes import flag_modified

    from core.database import CompanionORM

    rows = db.query(CompanionORM).all()
    updated = 0
    for row in rows:
        tags = normalize_tag_list(getattr(row, "tags", None))
        has_gender = "gender:male" in tags or "gender:female" in tags
        has_ori = any(t.startswith("orientation:") for t in tags)
        ori_val = (getattr(row, "sexual_orientation", None) or "").strip()
        if has_gender and (has_ori or not ori_val):
            continue
        new_tags = sync_companion_tags(
            row.personality,
            row.gender,
            existing=tags,
            sexual_orientation=ori_val,
        )
        row.tags = new_tags
        flag_modified(row, "tags")
        updated += 1
    if updated:
        db.flush()
    return updated


def persist_companion_tags(
    db,
    companion_id: str,
    personality: str,
    gender: str,
    extra_ops=None,
    sexual_orientation: Optional[str] = None,
) -> List[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from core.database import CompanionORM

    row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
    if not row:
        return []
    existing = normalize_tag_list(getattr(row, "tags", None))
    new_tags = sync_companion_tags(
        personality,
        gender,
        existing=existing,
        extra_ops=extra_ops,
        sexual_orientation=sexual_orientation if sexual_orientation is not None else getattr(row, "sexual_orientation", None),
    )
    row.tags = new_tags
    flag_modified(row, "tags")
    return new_tags
