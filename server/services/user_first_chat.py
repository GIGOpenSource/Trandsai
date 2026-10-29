"""用户端聊天 WS：是否第一次进入该智能体会话。以首次连上为准，与有没有发消息无关。"""
from __future__ import annotations

from datetime import datetime, timezone

from core.database import get_db
from services.credit_service import get_or_create_user_companion_state


def claim_first_chat(user_id: int, companion_id: str) -> bool:
    """本次连接是否「第一次进入该智能体聊天」。

    第一次连上 /ws/chat/{id} 返回 True，并立刻落库 first_im_at。
    之后再进一律 False。

    Say Hi / 助手回复会先写消息，不能据此当成已经进过 IM。
    """
    cid = (companion_id or "").strip()[:8]
    if not user_id or not cid:
        return False
    now = datetime.now(timezone.utc)
    with get_db() as db:
        row = get_or_create_user_companion_state(db, int(user_id), cid)
        if getattr(row, "first_im_at", None):
            return False
        row.first_im_at = now
        row.updated_at = now
        return True


def peek_first_chat(user_id: int, companion_id: str) -> bool:
    """是否还没进过该智能体 IM（不落库）。first_im_at 为空即为 true。"""
    cid = (companion_id or "").strip()[:8]
    if not user_id or not cid:
        return False
    with get_db() as db:
        row = get_or_create_user_companion_state(db, int(user_id), cid)
        return not bool(getattr(row, "first_im_at", None))


def first_chat_hello_payload(user_id: int, companion_id: str, first_chat: bool) -> dict:
    return {
        "type": "hello",
        "first_chat": bool(first_chat),
        "user_id": int(user_id),
        "companion_id": (companion_id or "").strip()[:8],
    }
