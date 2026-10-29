"""关系卡 v1（REQ-B1）：落库 user_companion_states.relation_card_json。"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from services.affection_math import coalesce_affection_write
from core.database import UserCompanionStateORM, get_db

logger = logging.getLogger(__name__)

RELATION_CARD_MAX_CHARS = int(os.getenv("RELATION_CARD_MAX_CHARS", "1200"))
RELATION_CARD_ENABLED = os.getenv("RELATION_CARD_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
RELATION_CARD_FALLBACK_FULL_MEMORY = os.getenv(
    "RELATION_CARD_FALLBACK_FULL_MEMORY", "true"
).lower() in ("1", "true", "yes")


def _empty_card() -> Dict[str, Any]:
    return {
        "version": 1,
        "summary": "",
        "facts": [],
        "mood": "",
        "affection": 0.0,
        "open_threads": [],
        "deny_hooks": [],
        "stage": "stranger",
    }


def load_card(user_id: int, companion_id: str) -> Dict[str, Any]:
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == companion_id,
            )
            .first()
        )
        if not row or not row.relation_card_json:
            return _empty_card()
        try:
            data = json.loads(row.relation_card_json)
            if not isinstance(data, dict):
                return _empty_card()
            card = _empty_card()
            card.update({k: data.get(k, card[k]) for k in card})
            card["version"] = int(data.get("version") or 1)
            return card
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("relation_card load failed: %s", e)
            return _empty_card()


def save_card(user_id: int, companion_id: str, card: Dict[str, Any]) -> None:
    payload = dict(_empty_card())
    payload.update(card or {})
    payload["version"] = 1
    raw = json.dumps(payload, ensure_ascii=False)
    with get_db() as db:
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == companion_id,
            )
            .first()
        )
        if row is None:
            row = UserCompanionStateORM(
                user_id=user_id,
                companion_id=companion_id,
                affection=coalesce_affection_write(0, payload.get("affection")),
                turns=0,
                relation_card_json=raw,
                relation_card_version=int(payload.get("version") or 1),
            )
            db.add(row)
        else:
            row.relation_card_json = raw
            row.relation_card_version = int(payload.get("version") or 1)
            if payload.get("affection") is not None:
                row.affection = coalesce_affection_write(row.affection, payload.get("affection"))


def merge_facts(
    card: Dict[str, Any],
    new_facts: List[str],
    max_items: int = 10,
    *,
    lang: str = "",
) -> Dict[str, Any]:
    """合并事实。按 UI 语言门禁脚本污染（对齐中文关系卡卫生，同时允许韩文卡保留 Hangul）。"""
    from core.i18n import normalize_ui_language

    out = dict(card or _empty_card())
    facts = list(out.get("facts") or [])
    seen = {str(f).strip() for f in facts}
    lk = normalize_ui_language(lang) if lang else ""
    for f in new_facts or []:
        s = str(f).strip()
        if not s or s in seen:
            continue
        has_ko = bool(re.search(r"[\uac00-\ud7af]", s))
        has_zh = bool(re.search(r"[\u4e00-\u9fff]", s))
        # 中文卡：丢弃纯韩文污染行；韩文卡：保留 Hangul
        if has_ko and not has_zh and lk in ("", "zh"):
            continue
        facts.append(s)
        seen.add(s)
    out["facts"] = facts[-max_items:]
    return out


def touch_state(
    card: Dict[str, Any],
    *,
    mood: Optional[str] = None,
    affection: Optional[float] = None,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    out = dict(card or _empty_card())
    if mood is not None:
        out["mood"] = mood
    if affection is not None:
        out["affection"] = float(affection)
    if summary is not None and str(summary).strip():
        out["summary"] = str(summary).strip()
    return out


def render_card(
    card: Dict[str, Any],
    max_chars: Optional[int] = None,
    lang: str = "zh",
) -> str:
    max_chars = max_chars if max_chars is not None else RELATION_CARD_MAX_CHARS
    c = card or _empty_card()
    labels = {
        "zh": {
            "bond": "【关系】",
            "empty": "（萌芽中）",
            "mood": "【情绪】",
            "aff": "亲密度",
            "stage": "【阶段】",
            "facts": "【事实】",
            "open": "【未完成】",
            "deny": "【忌用】",
        },
        "en": {
            "bond": "[Bond]",
            "empty": "(just starting)",
            "mood": "[Mood]",
            "aff": "affection",
            "stage": "[Stage]",
            "facts": "[Facts]",
            "open": "[Open]",
            "deny": "[Avoid]",
        },
    }
    key = (lang or "zh").split("-")[0].lower()
    L = labels.get(key) or labels["en"]
    if key == "zh":
        L = labels["zh"]
    lines = [
        f"{L['bond']}{(c.get('summary') or '').strip() or L['empty']}",
        f"{L['mood']}{(c.get('mood') or '').strip() or '—'} | {L['aff']} {float(c.get('affection') or 0):.2f}",
        f"{L['stage']}{(c.get('stage') or 'stranger')}",
    ]
    facts = [str(f).strip() for f in (c.get("facts") or []) if str(f).strip()]
    if facts:
        lines.append(L["facts"])
        for f in facts[:10]:
            lines.append(f"- {f}")
    threads = [str(t).strip() for t in (c.get("open_threads") or []) if str(t).strip()]
    if threads:
        lines.append(L["open"])
        for t in threads[:5]:
            lines.append(f"- {t}")
    deny = [str(d).strip() for d in (c.get("deny_hooks") or []) if str(d).strip()]
    if deny:
        sep = "、" if key == "zh" else ", "
        lines.append(L["deny"] + sep.join(deny[:10]))
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def is_card_empty(card: Dict[str, Any]) -> bool:
    c = card or {}
    return not (
        (c.get("summary") or "").strip()
        or (c.get("facts") or [])
        or (c.get("open_threads") or [])
    )
