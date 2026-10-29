"""IM / 运营接管：跨 uvicorn worker 的 WebSocket 推送桥（Redis Pub/Sub）。

本机仍维护连接注册表；发消息时本机投递 + PUBLISH，其它 worker 订阅后投递本机连接。
Redis 不可用时退化为仅本机推送（与改桥之前行为一致）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

from core.redis_client import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    get_redis_client,
)

logger = logging.getLogger(__name__)

IM_WS_BRIDGE = os.getenv("IM_WS_BRIDGE", "1").lower() in ("1", "true", "yes")
IM_WS_CHANNEL = (os.getenv("IM_WS_CHANNEL", "im:ws:bridge") or "im:ws:bridge").strip()
# 用户会话在线标记 TTL；有入站/心跳时续期，断线删除
_ONLINE_TTL_S = int(os.getenv("IM_WS_ONLINE_TTL_S", "120") or 120)

_ORIGIN = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
_sub_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

KIND_ADMIN_IM = "admin_im"
KIND_ADMIN_IM_INBOX = "admin_im_inbox"
KIND_USER_ASSISTANT = "user_assistant"
KIND_USER_TYPING = "user_typing"
KIND_USER_INBOX = "user_inbox"


def bridge_enabled() -> bool:
    return bool(IM_WS_BRIDGE and REDIS_HOST)


def worker_origin() -> str:
    return _ORIGIN


def _online_key(user_id: int, companion_id: str) -> str:
    return f"im:ws:online:{int(user_id)}:{str(companion_id)[:8]}"


def mark_user_session_online(user_id: int, companion_id: str) -> None:
    if not bridge_enabled():
        return
    r = get_redis_client()
    if not r:
        return
    try:
        r.setex(_online_key(user_id, companion_id), max(30, _ONLINE_TTL_S), "1")
    except Exception:
        logger.debug("im_ws online mark failed", exc_info=True)


def mark_user_session_offline(user_id: int, companion_id: str) -> None:
    if not bridge_enabled():
        return
    r = get_redis_client()
    if not r:
        return
    try:
        r.delete(_online_key(user_id, companion_id))
    except Exception:
        logger.debug("im_ws online clear failed", exc_info=True)


def is_user_session_online(user_id: int, companion_id: str) -> bool:
    if not bridge_enabled():
        return False
    r = get_redis_client()
    if not r:
        return False
    try:
        return bool(r.exists(_online_key(user_id, companion_id)))
    except Exception:
        return False


def publish_bridge_sync(envelope: Dict[str, Any]) -> bool:
    """任意线程可调用；成功投递到 Redis 即 True（不代表对端已收到 WS）。"""
    if not bridge_enabled():
        return False
    r = get_redis_client()
    if not r:
        return False
    try:
        body = dict(envelope)
        body.setdefault("origin", _ORIGIN)
        r.publish(IM_WS_CHANNEL, json.dumps(body, ensure_ascii=False))
        return True
    except Exception:
        logger.warning("im_ws bridge publish failed", exc_info=True)
        return False


async def publish_bridge(envelope: Dict[str, Any]) -> bool:
    return await asyncio.to_thread(publish_bridge_sync, envelope)


async def publish_admin_im(user_id: int, companion_id: str, payload: dict) -> bool:
    return await publish_bridge(
        {
            "kind": KIND_ADMIN_IM,
            "user_id": int(user_id),
            "companion_id": str(companion_id)[:8],
            "payload": payload,
        }
    )


async def publish_admin_im_inbox(admin_id: int, payload: dict) -> bool:
    return await publish_bridge(
        {
            "kind": KIND_ADMIN_IM_INBOX,
            "admin_id": int(admin_id),
            "payload": payload,
        }
    )


async def publish_user_inbox(user_id: int, payload: dict) -> bool:
    return await publish_bridge(
        {
            "kind": KIND_USER_INBOX,
            "user_id": int(user_id),
            "payload": payload,
        }
    )


def publish_user_assistant(
    user_id: int, companion_id: str, text: str, image_url: str = ""
) -> bool:
    return publish_bridge_sync(
        {
            "kind": KIND_USER_ASSISTANT,
            "user_id": int(user_id),
            "companion_id": str(companion_id)[:8],
            "text": text,
            "image_url": (image_url or "").strip(),
        }
    )


def publish_user_typing(user_id: int, companion_id: str) -> bool:
    return publish_bridge_sync(
        {
            "kind": KIND_USER_TYPING,
            "user_id": int(user_id),
            "companion_id": str(companion_id)[:8],
        }
    )


async def _dispatch(envelope: Dict[str, Any]) -> None:
    if (envelope.get("origin") or "") == _ORIGIN:
        return
    kind = (envelope.get("kind") or "").strip()

    if kind == KIND_ADMIN_IM_INBOX:
        try:
            admin_id = int(envelope.get("admin_id"))
        except (TypeError, ValueError):
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        from services.im_admin import broadcast_admin_im_inbox_local

        await broadcast_admin_im_inbox_local(admin_id, payload)
        return

    if kind == KIND_USER_INBOX:
        try:
            uid = int(envelope.get("user_id"))
        except (TypeError, ValueError):
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        from services.user_chat_unread import broadcast_user_inbox_local

        await broadcast_user_inbox_local(uid, payload)
        return

    try:
        uid = int(envelope.get("user_id"))
    except (TypeError, ValueError):
        return
    cid = str(envelope.get("companion_id") or "").strip()[:8]
    if not cid:
        return

    if kind == KIND_ADMIN_IM:
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        from services.im_admin import broadcast_admin_im_local

        await broadcast_admin_im_local(uid, cid, payload)
        return

    if kind == KIND_USER_ASSISTANT:
        text = (envelope.get("text") or "").strip()
        image_url = (envelope.get("image_url") or "").strip()
        if not text and not image_url:
            return
        from api.companion_ws import deliver_assistant_message_local

        await deliver_assistant_message_local(uid, cid, text, image_url=image_url)
        return

    if kind == KIND_USER_TYPING:
        from api.companion_ws import deliver_typing_local

        await deliver_typing_local(uid, cid)
        return


async def _subscriber_loop(stop: asyncio.Event) -> None:
    if not bridge_enabled():
        logger.info("IM WS bridge disabled (IM_WS_BRIDGE off or REDIS_HOST empty)")
        return

    import redis.asyncio as aioredis

    backoff = 1.0
    while not stop.is_set():
        client = None
        pubsub = None
        try:
            client = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await client.ping()
            pubsub = client.pubsub()
            await pubsub.subscribe(IM_WS_CHANNEL)
            logger.info(
                "IM WS bridge subscribed channel=%s origin=%s",
                IM_WS_CHANNEL,
                _ORIGIN,
            )
            backoff = 1.0
            while not stop.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                raw = msg.get("data")
                try:
                    envelope = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(envelope, dict):
                    continue
                try:
                    await _dispatch(envelope)
                except Exception:
                    logger.exception("IM WS bridge dispatch failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "IM WS bridge subscriber error; retry in %.1fs",
                backoff,
                exc_info=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(30.0, backoff * 2)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(IM_WS_CHANNEL)
                except Exception:
                    pass
                try:
                    close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
                    if close is not None:
                        res = close()
                        if asyncio.iscoroutine(res):
                            await res
                except Exception:
                    pass
            if client is not None:
                try:
                    close = getattr(client, "aclose", None) or getattr(client, "close", None)
                    if close is not None:
                        res = close()
                        if asyncio.iscoroutine(res):
                            await res
                except Exception:
                    pass


async def start_im_ws_bridge() -> None:
    """每个 uvicorn worker 的 lifespan 启动时调用。"""
    global _sub_task, _stop_event
    if _sub_task is not None and not _sub_task.done():
        return
    if not bridge_enabled():
        logger.info("IM WS bridge not started")
        return
    _stop_event = asyncio.Event()
    _sub_task = asyncio.create_task(
        _subscriber_loop(_stop_event),
        name="im-ws-bridge",
    )
    logger.info("IM WS bridge task started origin=%s", _ORIGIN)


async def stop_im_ws_bridge() -> None:
    global _sub_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    task = _sub_task
    _sub_task = None
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    _stop_event = None
