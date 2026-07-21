"""Application health and queue depth metrics (S-19)."""
from __future__ import annotations

import os

from core.agent_queue import agent_queue_depth, queue_enabled
from core.chat_cache import flush_processing_depth, flush_queue_depth
from core.concurrency import MAX_CONCURRENT_AGENTS, llm_gate_stats
from services.llm.token_meter import snapshot as llm_metrics_snapshot


def build_health_payload() -> dict:
    return {
        "status": "ok",
        "agent_queue_enabled": queue_enabled(),
        "agent_queue_depth": agent_queue_depth(),
        "chat_flush_queue_depth": flush_queue_depth(),
        "chat_flush_processing_depth": flush_processing_depth(),
        "max_concurrent_agents": MAX_CONCURRENT_AGENTS,
        "llm": llm_gate_stats(),
        "llm_metrics": llm_metrics_snapshot(),
        "env": os.getenv("APP_ENV", os.getenv("ENV", "development")),
    }
