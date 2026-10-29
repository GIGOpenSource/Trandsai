"""语言路由：请求 → 一个 lang key → 按需加载一份 pack。"""
from __future__ import annotations

import importlib
from typing import Any, Optional

from core.i18n import normalize_ui_language

SUPPORTED_LANGS = ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr")
_PACK_CACHE: dict[str, dict[str, Any]] = {}


def pack_lang(lang: Optional[str] = None) -> str:
    """归一到已有 pack 的短码；未知语种回退 en（不要再掉到 zh）。"""
    key = normalize_ui_language(lang)
    return key if key in SUPPORTED_LANGS else "en"


def resolve_lang(
    *,
    body_lang: Optional[str] = None,
    query_lang: Optional[str] = None,
    accept_language: Optional[str] = None,
    session_lang: Optional[str] = None,
    default: str = "en",
) -> str:
    """只选一个语言 key，不拼接。

    优先级：消息体 lang > ?lang= > Accept-Language > 会话 lang > default。
    """
    for raw in (body_lang, query_lang, accept_language, session_lang):
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        first = text.split(",")[0].strip().split(";")[0].strip()
        if not first:
            continue
        return normalize_ui_language(first)
    return normalize_ui_language(default)


def resolve_persona_generate_lang(
    data: dict | None = None,
    *,
    query_lang: Optional[str] = None,
    accept_language: Optional[str] = None,
    default: str = "en",
) -> str:
    """/companions/generate：仅 body/query/Accept-Language 决定输出语种，不按城市推断。"""
    body = str((data or {}).get("lang") or (data or {}).get("language") or "").strip()
    return resolve_lang(
        body_lang=body or None,
        query_lang=str(query_lang or "").strip() or None,
        accept_language=str(accept_language or "").strip() or None,
        default=default,
    )


def resolve_persona_output_lang(
    data: dict,
    *,
    query_lang: Optional[str] = None,
    accept_language: Optional[str] = None,
    city: str = "",
) -> str:
    """一键填充输出语种：UI lang（body/query/Accept-Language）优先，否则按城市推断。"""
    from services.culture_data import infer_language_from_city

    body = str((data or {}).get("lang") or (data or {}).get("language") or "").strip()
    query = str(query_lang or "").strip()
    accept = str(accept_language or "").strip()
    if body or query or accept:
        return resolve_lang(
            body_lang=body or None,
            query_lang=query or None,
            accept_language=accept or None,
            default="en",
        )
    return normalize_ui_language(infer_language_from_city(city) or "en")


def get_pack(lang: str) -> dict[str, Any]:
    """只 import 对应语种 pack，进程内缓存。缺键时用同语 extras 补齐，不串语言。"""
    key = pack_lang(lang)
    cached = _PACK_CACHE.get(key)
    if cached is not None:
        return cached
    mod = importlib.import_module(f"services.prompts.packs.{key}")
    pack = dict(getattr(mod, "PACK"))
    from services.prompts.pack_extras import extras_for

    # soft / extras 可覆盖的成人力度键（否则 pack 内旧 bands 盖不住外语对齐）
    _soft_override = frozenset(
        {
            "adult_intimacy_bands",
            "adult_ads_unrestricted",
            "adult_legal_floor",
            "adult_legal_floor_ads",
            "chat_vibe_adult",
            "tease_ads",
            "enrich_tease_ads",
            "rewrite_adult_ads",
            "adult_cater_hard",
            "proactive_flirt",
            "uptake_soft",
            "follow_user_hard",
        }
    )

    extra = extras_for(key)
    for field, value in extra.items():
        if field in _soft_override:
            pack[field] = value
        elif field not in pack:
            pack[field] = value
        elif field == "respond_ui" and isinstance(value, dict) and isinstance(pack[field], dict):
            # extras 垫底，语种 pack 覆盖（保留各语已有译文）
            merged = dict(value)
            merged.update(pack[field])
            pack[field] = merged
        elif field == "quality_ui" and isinstance(value, dict) and isinstance(pack[field], dict):
            # extras 覆盖：全语种软氛围统一（en 垫底 → 各语 extras）
            merged = dict(pack[field])
            merged.update(value)
            pack[field] = merged
    _PACK_CACHE[key] = pack
    return pack
