"""WebSocket 下发辅助：纯逻辑，不依赖 FastAPI。"""
from __future__ import annotations

import asyncio
from typing import Optional


def ws_session_key(user_id: int, companion_id: str) -> str:
    return f"{int(user_id)}:{companion_id}"


def queue_has_user_interrupt(message_queue: Optional[asyncio.Queue]) -> bool:
    """队列里是否有真正的用户新消息。断开连接不得当成打断，否则会丢弃已生成回复。"""
    if message_queue is None or message_queue.empty():
        return False
    parked: list = []
    has_user = False
    while True:
        try:
            item = message_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        parked.append(item)
        if (
            isinstance(item, dict)
            and not item.get("__disconnect")
            and (item.get("text") or "").strip()
        ):
            has_user = True
    for item in parked:
        message_queue.put_nowait(item)
    return has_user
