"""Unified synchronous LLM invoke: concurrency gate, metrics, optional prompt caching."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

from langchain_core.messages import BaseMessage, SystemMessage

from core.concurrency import llm_gate
from services.agent_utils import llm_content_to_str
from services.llm.token_meter import record

logger = logging.getLogger(__name__)

PROMPT_CACHE_ENABLED = os.getenv("PROMPT_CACHE", "0").lower() in ("1", "true", "yes")


def resolve_max_tokens(affection: float = 0, override: Optional[int] = None) -> int:
    """S-14: dynamic max_tokens by intimacy tier."""
    if override is not None:
        return override
    free_cap = int(os.getenv("MAX_TOKENS_FREE", "256"))
    paid_cap = int(os.getenv("MAX_TOKENS_PAID", "384"))
    cap = paid_cap
    aff = float(affection or 0)
    if aff < 30:
        return min(128, cap)
    if aff < 60:
        return min(256, cap)
    return cap if cap >= free_cap else free_cap


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

    prepared = _apply_prompt_cache(messages, llm)
    start = time.perf_counter()
    with llm_gate():
        resp = llm.invoke(prepared)
    elapsed_ms = (time.perf_counter() - start) * 1000
    text = llm_content_to_str(getattr(resp, "content", resp))
    try:
        record(node, prepared, text, elapsed_ms)
    except Exception as e:
        logger.debug("token_meter record failed: %s", e)
    return resp
