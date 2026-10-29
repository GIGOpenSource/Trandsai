"""Lightweight in-process LLM token/latency metrics."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List

_lock = threading.Lock()
_totals: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})
_latencies: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=500))


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Rough heuristic: ~1.5 chars/token for CJK-heavy text, ~4 chars/token for Latin.
    n = len(text)
    if any("\u4e00" <= c <= "\u9fff" for c in text[:200]):
        return max(1, int(n / 1.5))
    return max(1, n // 4)


def _messages_input_text(messages: List[Any]) -> str:
    parts: List[str] = []
    for msg in messages:
        content = getattr(msg, "content", msg)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
    return "\n".join(parts)


def record(node: str, messages: List[Any], output_text: str, latency_ms: float) -> None:
    node = node or "unknown"
    in_tok = _estimate_tokens(_messages_input_text(messages))
    out_tok = _estimate_tokens(output_text or "")
    with _lock:
        bucket = _totals[node]
        bucket["calls"] += 1
        bucket["input_tokens"] += in_tok
        bucket["output_tokens"] += out_tok
        _latencies[node].append(latency_ms)


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct))
    return ordered[idx]


def snapshot() -> dict:
    with _lock:
        nodes = {}
        for node, bucket in _totals.items():
            lats = list(_latencies.get(node, []))
            nodes[node] = {
                **bucket,
                "latency_p50_ms": round(_percentile(lats, 0.5), 1),
                "latency_p95_ms": round(_percentile(lats, 0.95), 1),
            }
        return {"nodes": nodes, "updated_at": time.time()}
