"""消息策略配图：文案可关联一张图；会员才作为独立图片消息下发。"""
from __future__ import annotations

from typing import Any, Optional

STRATEGY_IMAGE_PREFIX = "__IMAGE_MSG__:"
_URL_MAX = 500


def normalize_strategy_image_url(raw) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text == "__GENERATING__":
        return ""
    from services.moment_urls import image_url_valid

    return (image_url_valid(text) or "")[:_URL_MAX]


def encode_strategy_image_content(url: str) -> str:
    return STRATEGY_IMAGE_PREFIX + (url or "").strip()


def is_strategy_image_content(content: Optional[str]) -> bool:
    return str(content or "").startswith(STRATEGY_IMAGE_PREFIX)


def decode_strategy_image_url(content: Optional[str]) -> str:
    raw = str(content or "")
    if not raw.startswith(STRATEGY_IMAGE_PREFIX):
        return ""
    return raw[len(STRATEGY_IMAGE_PREFIX) :].strip()


def annotate_chat_message(msg: dict) -> dict:
    """历史/热缓存消息：图片独立气泡用 content_type=image。"""
    if not isinstance(msg, dict):
        return msg
    content = str(msg.get("content") or "")
    if is_strategy_image_content(content):
        url = decode_strategy_image_url(content)
        msg["content"] = ""
        msg["text"] = ""
        msg["image_url"] = url
        msg["content_type"] = "image"
    else:
        msg.setdefault("image_url", "")
        msg.setdefault("content_type", "text")
    msg.setdefault("send_status", str(msg.get("send_status") or "").strip())
    return msg


def user_can_receive_strategy_image(user_id: Optional[int]) -> bool:
    """复用现有会员校验：有效 VIP 才下发策略配图。"""
    if not user_id:
        return False
    try:
        from core.database import get_db
        from services.credit_service import get_or_create_user_credits, is_vip_active

        with get_db() as db:
            uc = get_or_create_user_credits(db, int(user_id))
            return bool(is_vip_active(uc))
    except Exception:
        return False


def assistant_message_payload(text: str = "", image_url: str = "") -> dict[str, Any]:
    url = (image_url or "").strip()
    payload = {
        "type": "message",
        "role": "assistant",
        "text": (text or "").strip(),
    }
    if url:
        payload["image_url"] = url
        payload["content_type"] = "image"
        if not payload["text"]:
            payload["text"] = ""
    else:
        payload["content_type"] = "text"
    return payload
