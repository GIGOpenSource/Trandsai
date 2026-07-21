"""Global concurrency limits for Agent and LLM calls."""
import asyncio
import os
import threading
from contextlib import contextmanager
from typing import Iterator

MAX_CONCURRENT_AGENTS = int(os.getenv("MAX_CONCURRENT_AGENTS", "20"))
agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)

MAX_LLM_CONCURRENT = int(os.getenv("MAX_LLM_CONCURRENT", "50"))

# asyncio.Semaphore is not safe across threads; LLM invoke runs in thread pools.
_llm_thread_gate = threading.Semaphore(MAX_LLM_CONCURRENT)
_inflight_lock = threading.Lock()
_llm_inflight = 0


@contextmanager
def llm_gate() -> Iterator[None]:
    """Thread-safe gate for all synchronous LLM invoke paths."""
    global _llm_inflight
    _llm_thread_gate.acquire()
    with _inflight_lock:
        _llm_inflight += 1
    try:
        yield
    finally:
        with _inflight_lock:
            _llm_inflight -= 1
        _llm_thread_gate.release()


def llm_gate_stats() -> dict:
    """In-flight LLM calls vs configured cap (S-19)."""
    with _inflight_lock:
        inflight = _llm_inflight
    return {"max_concurrent": MAX_LLM_CONCURRENT, "in_flight": inflight}
