"""D2/D4：水平扩展相关开关与健康快照（状态外置依赖 Redis/DB）。"""
from __future__ import annotations

import os
from typing import Any, Dict


def session_externalized() -> bool:
    """会话/近聊是否走 Redis（无状态 API 进程的前提之一）。"""
    return os.getenv("REDIS_URL", "").strip() != "" or os.getenv("CHAT_REDIS", "0") in (
        "1",
        "true",
        "yes",
    )


def sticky_ws_hint() -> str:
    """多实例 WS 建议：LB sticky 或独立网关。"""
    return os.getenv(
        "WS_STICKY_HINT",
        "Use sticky sessions or a dedicated WS gateway when scaling API replicas.",
    )


def horizontal_snapshot() -> Dict[str, Any]:
    from services.archive_queue import archive_queue_depth, archive_queue_enabled
    from services.local_inner import local_inner_available

    return {
        "session_externalized": session_externalized(),
        "archive_queue_enabled": archive_queue_enabled(),
        "archive_queue_depth": archive_queue_depth(),
        "local_inner_available": local_inner_available(),
        "chroma_remote": bool((os.getenv("CHROMA_HOST") or "").strip()),
        "ws_sticky_hint": sticky_ws_hint(),
    }
