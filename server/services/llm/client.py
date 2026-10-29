"""Unified synchronous LLM invoke: concurrency gate, metrics, optional prompt caching."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

from langchain_core.messages import BaseMessage, SystemMessage

from core.concurrency import llm_gate_stats, llm_gate_timed
from services.agent_utils import llm_content_to_str
from services.llm import turn_timing
from services.llm.token_meter import record

logger = logging.getLogger(__name__)

PROMPT_CACHE_ENABLED = os.getenv("PROMPT_CACHE", "0").lower() in ("1", "true", "yes")


def _prefix_version_prompt(messages: List[BaseMessage]) -> List[BaseMessage]:
    """版本合规提示词插在 system 最前，权重最高；未启用则原样。"""
    try:
        from services.version_prompt import current_version_prefix

        prefix = current_version_prefix()
    except Exception:
        return messages
    if not prefix:
        return messages
    out = list(messages or [])
    for i, msg in enumerate(out):
        role = (getattr(msg, "type", None) or "").lower()
        if role != "system" and not isinstance(msg, SystemMessage):
            continue
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            break
        if content.lstrip().startswith(prefix):
            break
        out[i] = SystemMessage(content=f"{prefix}\n\n{content}")
        break
    return out


def resolve_max_tokens(affection: float = 0, override: Optional[int] = None) -> int:
    """S-14: dynamic max_tokens by intimacy tier.

    Floors raised so early-stage replies can finish a complete thought
    after answering the user's intent (not telegraph-short).
    """
    if override is not None:
        return override
    free_cap = int(os.getenv("MAX_TOKENS_FREE", "1200"))
    paid_cap = int(os.getenv("MAX_TOKENS_PAID", "1400"))
    cap = paid_cap
    aff = float(affection or 0)
    #aff亲密度
    if aff < 30:
        return min(int(os.getenv("MAX_TOKENS_AFF_LOW", "960")), cap)
    if aff < 60:
        return min(int(os.getenv("MAX_TOKENS_AFF_MID", "1100")), cap)
    return max(cap, free_cap)


def _provider_supports_prompt_cache(llm: Any) -> bool:
    name = type(llm).__name__.lower()
    if "anthropic" in name:
        return True
    # OpenAI prompt caching uses different headers; keep off unless explicitly extended.
    return False


def _apply_prompt_cache(messages: List[BaseMessage], llm: Any) -> List[BaseMessage]:
    if not PROMPT_CACHE_ENABLED or not _provider_supports_prompt_cache(llm):
        return messages
    out: List[BaseMessage] = []
    for i, msg in enumerate(messages):
        if i == 0 and isinstance(msg, SystemMessage) and isinstance(msg.content, str):
            out.append(
                SystemMessage(
                    content=[
                        {
                            "type": "text",
                            "text": msg.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                )
            )
        else:
            out.append(msg)
    return out


def _llm_identity(llm: Any) -> tuple[str, str]:
    provider = type(llm).__name__ if llm is not None else ""
    model = ""
    for attr in ("model", "model_name", "model_id"):
        v = getattr(llm, attr, None)
        if isinstance(v, str) and v.strip():
            model = v.strip()
            break
    return provider, model


def llm_invoke(
    llm: Any,
    messages: List[BaseMessage],
    *,
    node: str = "unknown",
    affection: float = 0,
    max_tokens: Optional[int] = None,
) -> Any:
    """Thread-safe LLM invoke used by v1/v2/worker and auxiliary flows."""
    if max_tokens is not None and hasattr(llm, "max_tokens"):
        try:
            llm.max_tokens = max_tokens
        except Exception:
            pass
    elif hasattr(llm, "max_tokens"):
        try:
            llm.max_tokens = resolve_max_tokens(affection)
        except Exception:
            pass

    messages = _prefix_version_prompt(messages)
    prepared = _apply_prompt_cache(messages, llm)
    provider, model = _llm_identity(llm)
    start = time.perf_counter()
    with llm_gate_timed() as wait_ms:
        call_t0 = time.perf_counter()
        resp = llm.invoke(prepared)
        call_ms = (time.perf_counter() - call_t0) * 1000
    total_ms = (time.perf_counter() - start) * 1000
    text = llm_content_to_str(getattr(resp, "content", resp))
    try:
        record(node, prepared, text, total_ms)
    except Exception as e:
        logger.debug("token_meter record failed: %s", e)
    try:
        in_text = ""
        for msg in prepared:
            c = getattr(msg, "content", "")
            if isinstance(c, str):
                in_text += c
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        in_text += block["text"]
        inflight = int(llm_gate_stats().get("in_flight") or 0)
        turn_timing.record_llm(
            node=node or "unknown",
            wait_ms=float(wait_ms or 0),
            call_ms=call_ms,
            total_ms=total_ms,
            in_chars=len(in_text),
            out_chars=len(text or ""),
            provider=provider,
            model=model,
            inflight=inflight,
        )
    except Exception as e:
        logger.debug("turn_timing llm record failed: %s", e)
    return resp
