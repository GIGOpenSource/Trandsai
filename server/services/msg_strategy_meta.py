"""消息策略：触发类型映射、全局控制 / 规则 / 文案库常量。

triggers / library_types 数据源 = strategy_types 表（埋点事件）。
约定：trigger_key == event_key == library type_key；event_keys == [event_key]。
消息策略仅使用归因上报的 product_event（裸 event_key），与 firebase_event_name 无关。
meta 短缓存，避免每次读库；无库时回退 DEFAULT_STRATEGY_TYPES。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from services.companion_content import ADMIN_LANGUAGES, supported_language_options
from services.strategy_types import (
    COMPANION_BOUND_EVENT_KEYS,
    DEFAULT_STRATEGY_TYPES,
    event_sender_mode,
    list_strategy_types,
)

# ---------- 旧版别名（兼容已入库的 rules.triggers / library.type_key）----------
LEGACY_TRIGGER_TO_EVENTS: Dict[str, List[str]] = {
    "filter_guide": ["newbie_guide_show"],
    "register": ["Registration_finish", "registration_skip"],
    "avatar_upload": ["avatar_upload"],
    "profile_browse_15s": ["profile_displayed_15s"],
    "moment_browse_15s": ["Moment_15s"],
    "first_message": [
        "send_message_click_im",
        "send_message_icon_click",
        "send_message_click_moment",
    ],
    "im_no_unread": ["no_data_of_im"],
}

LEGACY_TYPE_TO_EVENTS: Dict[str, List[str]] = {
    "register_welcome": ["Registration_finish", "registration_skip"],
    "filter_guide_complete": ["newbie_guide_show"],
    "first_active_message": [
        "send_message_click_im",
        "send_message_icon_click",
        "send_message_click_moment",
    ],
    "read_no_reply_recall": ["no_data_of_im"],
    "avatar_match": ["avatar_upload"],
    "moment_like_request": ["Moment_15s"],
    "discover_browse_recall": ["profile_displayed_15s"],
}

LEGACY_EVENT_TO_TRIGGERS: Dict[str, List[str]] = {}
for _tk, _eks in LEGACY_TRIGGER_TO_EVENTS.items():
    for _ek in _eks:
        LEGACY_EVENT_TO_TRIGGERS.setdefault(_ek, []).append(_tk)

LEGACY_EVENT_TO_TYPES: Dict[str, List[str]] = {}
for _type, _eks in LEGACY_TYPE_TO_EVENTS.items():
    for _ek in _eks:
        LEGACY_EVENT_TO_TYPES.setdefault(_ek, []).append(_type)

LEGACY_TRIGGER_TO_TYPES: Dict[str, List[str]] = {}
for _type, _eks in LEGACY_TYPE_TO_EVENTS.items():
    for _ek in _eks:
        for _tk in LEGACY_EVENT_TO_TRIGGERS.get(_ek, []):
            LEGACY_TRIGGER_TO_TYPES.setdefault(_tk, [])
            if _type not in LEGACY_TRIGGER_TO_TYPES[_tk]:
                LEGACY_TRIGGER_TO_TYPES[_tk].append(_type)

USER_SCOPE_TYPES = [
    {"value": "all", "label": "全部用户"},
    {"value": "ads", "label": "Ads标记"},
    {"value": "attr", "label": "用户属性标记"},
]

TRIGGER_DEFS: List[Dict[str, Any]] = [
    {
        "trigger_key": x["event_key"],
        "name_zh": x.get("name_zh") or "",
        "event_keys": [x["event_key"]],
        "default_count": 1,
        "sender_mode": event_sender_mode(x["event_key"]),
    }
    for x in DEFAULT_STRATEGY_TYPES
]

LIBRARY_TYPES: List[Dict[str, str]] = [
    {
        "type_key": x["event_key"],
        "name_zh": x.get("name_zh") or "",
        "trigger_key": x["event_key"],
        "sender_mode": event_sender_mode(x["event_key"]),
    }
    for x in DEFAULT_STRATEGY_TYPES
]

TRIGGER_KEY_SET = {t["trigger_key"] for t in TRIGGER_DEFS} | set(LEGACY_TRIGGER_TO_EVENTS)
LIBRARY_TYPE_SET = {t["type_key"] for t in LIBRARY_TYPES} | set(LEGACY_TYPE_TO_EVENTS)

_META_TTL_SEC = 60.0
_meta_lock = threading.Lock()
_meta_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}


def canonicalize_product_event(event_key: str) -> str:
    """策略匹配用规范 event_key（trim；测试环境可剥 test_ 前缀）。"""
    ek = (event_key or "").strip()
    prefix = (os.getenv("PRODUCT_EVENT_TEST_PREFIX") or "test_").strip()
    if prefix and ek.startswith(prefix):
        ek = ek[len(prefix):]
    return ek


def expand_library_type_keys(keys: List[str]) -> List[str]:
    """策略库 type_key 匹配：含 canonical、原值、test_ 前缀（库内可能存 test_ 事件名）。"""
    out: List[str] = []
    prefix = (os.getenv("PRODUCT_EVENT_TEST_PREFIX") or "test_").strip()
    for k in keys:
        raw = (k or "").strip()
        if not raw:
            continue
        canon = canonicalize_product_event(raw)
        for variant in (raw, canon):
            if variant and variant not in out:
                out.append(variant)
        if prefix and canon and not canon.startswith(prefix):
            test_key = f"{prefix}{canon}"
            if test_key not in out:
                out.append(test_key)
    return out


def invalidate_meta_cache() -> None:
    with _meta_lock:
        _meta_cache["ts"] = 0.0
        _meta_cache["payload"] = None


def _rows_to_triggers(rows: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "trigger_key": (r.event_key or "").strip(),
            "name_zh": (r.name_zh or "").strip(),
            "event_keys": [(r.event_key or "").strip()],
            "default_count": 1,
            "seq": int(getattr(r, "seq", 0) or 0),
            "sender_mode": event_sender_mode((r.event_key or "").strip()),
        }
        for r in rows
        if (getattr(r, "event_key", None) or "").strip()
    ]


def _rows_to_library_types(rows: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "type_key": (r.event_key or "").strip(),
            "name_zh": (r.name_zh or "").strip(),
            "trigger_key": (r.event_key or "").strip(),
            "seq": int(getattr(r, "seq", 0) or 0),
            "sender_mode": event_sender_mode((r.event_key or "").strip()),
        }
        for r in rows
        if (getattr(r, "event_key", None) or "").strip()
    ]


def build_meta_from_db(db: Optional[Session]) -> dict:
    """从 strategy_types 组装 meta；无库/无数据时回退常量种子。"""
    rows = []
    source = "fallback_defaults"
    if db is not None:
        try:
            rows = list_strategy_types(db, active_only=True)
            if rows:
                source = "strategy_types"
        except Exception:
            rows = []
    triggers = _rows_to_triggers(rows) if rows else list(TRIGGER_DEFS)
    library_types = _rows_to_library_types(rows) if rows else list(LIBRARY_TYPES)
    return {
        "triggers": triggers,
        "library_types": library_types,
        "user_scope_types": USER_SCOPE_TYPES,
        "languages": list(ADMIN_LANGUAGES),
        "language_options": supported_language_options(),
        "global_defaults": {
            "fatigue_window_hours": 2,
            "fatigue_max_unreplied": 10,
            "daily_limit": 20,
            "interval_min_sec": 15,
            "interval_max_sec": 45,
        },
        "global_field_constraints": {
            "daily_limit": {"min": 0, "max": None, "note": "后端不设上限，20 仅为默认值"},
            "fatigue_max_unreplied": {"min": 0, "max": None},
            "fatigue_window_hours": {"min": 0, "max": None},
        },
        "companion_bound_events": sorted(COMPANION_BOUND_EVENT_KEYS),
        "source": source,
    }


def meta_payload(db: Optional[Session] = None, *, bypass_cache: bool = False) -> dict:
    """短缓存：读 strategy_types 成本低，减少管理端连点卡顿。"""
    now = time.time()
    if not bypass_cache:
        with _meta_lock:
            cached = _meta_cache.get("payload")
            if cached is not None and (now - float(_meta_cache.get("ts") or 0)) < _META_TTL_SEC:
                return dict(cached)
    payload = build_meta_from_db(db)
    with _meta_lock:
        _meta_cache["ts"] = now
        _meta_cache["payload"] = payload
    return payload


def known_trigger_keys(db: Optional[Session] = None) -> Set[str]:
    meta = build_meta_from_db(db)
    keys = {t["trigger_key"] for t in meta["triggers"]}
    keys |= set(LEGACY_TRIGGER_TO_EVENTS)
    return keys


def known_library_type_keys(db: Optional[Session] = None) -> Set[str]:
    meta = build_meta_from_db(db)
    keys = {t["type_key"] for t in meta["library_types"]}
    keys |= set(LEGACY_TYPE_TO_EVENTS)
    return keys


def event_to_trigger_keys(event_key: str) -> List[str]:
    """埋点 event → 规则 trigger 列表。"""
    ek = canonicalize_product_event(event_key)
    if not ek:
        return []
    out: List[str] = [ek]
    for tk in LEGACY_EVENT_TO_TRIGGERS.get(ek, []):
        if tk not in out:
            out.append(tk)
    return out


def type_key_to_trigger(type_key: str) -> str:
    tk = canonicalize_product_event(type_key)
    if not tk:
        return ""
    if tk in LEGACY_TYPE_TO_EVENTS:
        return LEGACY_TYPE_TO_EVENTS[tk][0]
    return tk


def trigger_to_type_keys(trigger_key: str) -> List[str]:
    """规则 trigger → 策略库可匹配的 type_key 列表。"""
    tk = canonicalize_product_event(trigger_key)
    if not tk:
        return []
    out: List[str] = [tk]
    for legacy_type in LEGACY_TRIGGER_TO_TYPES.get(tk, []):
        if legacy_type not in out:
            out.append(legacy_type)
    for ek in LEGACY_TRIGGER_TO_EVENTS.get(tk, []):
        if ek not in out:
            out.append(ek)
    for legacy_type in LEGACY_EVENT_TO_TYPES.get(tk, []):
        if legacy_type not in out:
            out.append(legacy_type)
    return out


def event_keys_for_trigger(trigger_key: str) -> List[str]:
    """取文案匹配用的埋点 event 列表。"""
    tk = canonicalize_product_event(trigger_key)
    if not tk:
        return []
    if tk in LEGACY_TRIGGER_TO_EVENTS:
        return list(LEGACY_TRIGGER_TO_EVENTS[tk])
    return [tk]


def normalize_triggers(raw: Any, db: Optional[Session] = None) -> Dict[str, int]:
    """规范化规则 triggers：仅保留已知 key（strategy_types.event_key 或旧别名）。"""
    if not isinstance(raw, dict):
        return {}
    allowed = known_trigger_keys(db)
    out: Dict[str, int] = {}
    for k, v in raw.items():
        key = canonicalize_product_event(str(k).strip())
        if key not in allowed:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[key] = n
    return out
