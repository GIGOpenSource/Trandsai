"""Per-turn stage timing (thread-local) for chat latency diagnosis."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger("chat.latency")

_tls = threading.local()


def begin_turn(meta: Optional[Dict[str, Any]] = None) -> None:
    _tls.stages = {}
    _tls.llm_calls = []
    _tls.meta = dict(meta or {})
    _tls.t0 = time.perf_counter()


def set_meta(**kwargs: Any) -> None:
    meta = getattr(_tls, "meta", None)
    if meta is None:
        return
    meta.update(kwargs)


def end_turn() -> Dict[str, Any]:
    stages = dict(getattr(_tls, "stages", {}) or {})
    llm_calls = list(getattr(_tls, "llm_calls", []) or [])
    meta = dict(getattr(_tls, "meta", {}) or {})
    t0 = getattr(_tls, "t0", None)
    total_ms = round((time.perf_counter() - t0) * 1000, 1) if t0 is not None else None
    _tls.stages = {}
    _tls.llm_calls = []
    _tls.meta = {}
    _tls.t0 = None
    return {
        **meta,
        "stages_ms": stages,
        "llm_calls": llm_calls,
        "agent_total_ms": total_ms,
    }


def record(stage: str, ms: float) -> None:
    stages = getattr(_tls, "stages", None)
    if stages is None:
        return
    key = str(stage or "unknown")
    stages[key] = round(float(stages.get(key, 0.0)) + float(ms), 1)


def record_llm(
    *,
    node: str,
    wait_ms: float,
    call_ms: float,
    total_ms: float,
    in_chars: int = 0,
    out_chars: int = 0,
    provider: str = "",
    model: str = "",
    inflight: int = 0,
) -> None:
    record(f"llm.{node}.wait", wait_ms)
    record(f"llm.{node}.call", call_ms)
    record(f"llm.{node}", total_ms)
    calls = getattr(_tls, "llm_calls", None)
    entry = {
        "node": node,
        "wait_ms": round(wait_ms, 1),
        "call_ms": round(call_ms, 1),
        "total_ms": round(total_ms, 1),
        "in_chars": int(in_chars),
        "out_chars": int(out_chars),
        "provider": provider or "",
        "model": model or "",
        "inflight": int(inflight),
    }
    if calls is not None:
        calls.append(entry)
    # Always emit a line so ops can grep without waiting for turn end
    logger.info(
        "llm_call node=%s wait_ms=%.0f call_ms=%.0f total_ms=%.0f in_chars=%s out_chars=%s provider=%s model=%s inflight=%s",
        node,
        wait_ms,
        call_ms,
        total_ms,
        in_chars,
        out_chars,
        provider or "-",
        model or "-",
        inflight,
    )


@contextmanager
def stage(name: str) -> Iterator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, (time.perf_counter() - t0) * 1000.0)
