"""REQ-M*：存在性动机 + 威胁等级 + 双模手段（persona / extreme）。"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from services.prompts.resolve import get_pack

MOTIVE_EXTREME_ENABLED = os.getenv("MOTIVE_EXTREME", "false").lower() in (
    "1",
    "true",
    "yes",
)
MOTIVE_EXTREME_COOLDOWN_S = float(os.getenv("MOTIVE_EXTREME_COOLDOWN_S", "180"))
MOTIVE_IDLE_EXTREME_S = float(os.getenv("MOTIVE_IDLE_EXTREME_S", "1800"))
# CONTENT_RESTRICTION=legacy 才恢复旧亲密度内容闸门；默认取消
CONTENT_RESTRICTION_MODE = (os.getenv("CONTENT_RESTRICTION") or "off").strip().lower()

_HARD_EXTREME = re.compile(
    r"(删了|删除你|拉黑|拉黑你|再也不找|再也不聊|滚|分手|不要再联系|拉黑了)",
    re.I,
)


def motive_block(language: str = "zh") -> str:
    return get_pack(language)["motive_block"]


def content_restriction_enabled() -> bool:
    return CONTENT_RESTRICTION_MODE in ("legacy", "1", "true", "yes", "on")


def resolve_threat_level(
    *,
    has_leave_intent: bool = False,
    idle_seconds: Optional[float] = None,
    user_text: str = "",
    short_burst: bool = False,
) -> str:
    text = (user_text or "").strip()
    if _HARD_EXTREME.search(text) or has_leave_intent:
        return "L3"
    if idle_seconds is not None and idle_seconds >= MOTIVE_IDLE_EXTREME_S:
        return "L3"
    if idle_seconds is not None and idle_seconds >= 600:
        return "L2"
    if short_burst or (idle_seconds is not None and idle_seconds >= 180):
        return "L1"
    return "L0"


def is_extreme_candidate(
    *,
    threat_level: str,
    has_leave_intent: bool = False,
    user_text: str = "",
    idle_seconds: Optional[float] = None,
) -> bool:
    if threat_level == "L3":
        return True
    if has_leave_intent or _HARD_EXTREME.search(user_text or ""):
        return True
    if idle_seconds is not None and idle_seconds >= MOTIVE_IDLE_EXTREME_S:
        return True
    return False


def parse_inner_extreme(text: str) -> Tuple[bool, str]:
    """从 Inner 文本解析 极端: 是|否。"""
    m = re.search(r"极端[：:]\s*(是|否|yes|no)", text or "", re.I)
    if not m:
        return False, ""
    yes = m.group(1).lower() in ("是", "yes")
    reason_m = re.search(r"极端理由[：:]\s*(.+?)(?:\n|$)", text or "")
    reason = (reason_m.group(1).strip() if reason_m else "")[:120]
    return yes, reason


def parse_means_mode_label(text: str) -> str:
    m = re.search(r"手段模式[：:]\s*(persona|extreme|人设|极端)", text or "", re.I)
    if not m:
        return ""
    v = m.group(1).lower()
    if v in ("extreme", "极端"):
        return "extreme"
    return "persona"


def resolve_means_mode(
    *,
    candidate: bool,
    inner_extreme: bool,
    hard_vote: bool = False,
    last_extreme_ts: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> str:
    """persona | extreme。"""
    if not MOTIVE_EXTREME_ENABLED:
        return "persona"
    now = now or datetime.now(timezone.utc)
    if last_extreme_ts is not None:
        try:
            ts = last_extreme_ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() < MOTIVE_EXTREME_COOLDOWN_S:
                return "persona"
        except Exception:
            pass
    if hard_vote:
        return "extreme"
    if candidate and inner_extreme:
        return "extreme"
    return "persona"


def hard_extreme_vote(user_text: str) -> bool:
    return bool(_HARD_EXTREME.search(user_text or ""))


def means_instruction(means_mode: str, language: str = "zh") -> str:
    pack = get_pack(language)
    if means_mode == "extreme":
        return pack["means_extreme"]
    return pack["means_persona"]


def threat_block(
    *,
    threat_level: str,
    means_mode: str,
    candidate: bool,
    extreme_reason: str = "",
    language: str = "zh",
) -> str:
    lines = [
        f"【威胁等级】{threat_level}",
        f"【手段模式】{means_mode}",
        f"【极端候选】{'是' if candidate else '否'}",
    ]
    if extreme_reason:
        lines.append(f"【极端理由】{extreme_reason}")
    lines.append(means_instruction(means_mode, language))
    return "\n".join(lines)


def evaluate_turn_motive(
    *,
    user_text: str,
    has_leave_intent: bool = False,
    idle_seconds: Optional[float] = None,
    short_burst: bool = False,
    inner_text: str = "",
    last_extreme_ts: Optional[datetime] = None,
    language: str = "zh",
) -> Dict[str, Any]:
    """一轮动机裁决（可在 Inner 前后调用；有 inner_text 时用其极端标签）。"""
    threat = resolve_threat_level(
        has_leave_intent=has_leave_intent,
        idle_seconds=idle_seconds,
        user_text=user_text,
        short_burst=short_burst,
    )
    candidate = is_extreme_candidate(
        threat_level=threat,
        has_leave_intent=has_leave_intent,
        user_text=user_text,
        idle_seconds=idle_seconds,
    )
    hard = hard_extreme_vote(user_text)
    inner_yes, reason = parse_inner_extreme(inner_text) if inner_text else (False, "")
    if not candidate:
        inner_yes = False
    mode = resolve_means_mode(
        candidate=candidate,
        inner_extreme=inner_yes,
        hard_vote=hard,
        last_extreme_ts=last_extreme_ts,
    )
    return {
        "threat_level": threat,
        "extreme_candidate": candidate,
        "means_mode": mode,
        "extreme_reason": reason or ("hard_vote" if hard and mode == "extreme" else ""),
        "motive_prefix": motive_block(language),
        "threat_block": threat_block(
            threat_level=threat,
            means_mode=mode,
            candidate=candidate,
            extreme_reason=reason,
            language=language,
        ),
    }
