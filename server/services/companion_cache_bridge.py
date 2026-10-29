"""智能体进程缓存：跨 uvicorn worker / 多副本失效广播（Redis Pub/Sub）。

改资料后 PUBLISH；各 worker 订阅后对本机内存 refresh / pop。
识别「是否自己」：每进程启动时生成 origin = `{pid}:{uuid8}`，与 IM bridge 相同。
收到自己发出的消息则跳过（本机内存已是最新）。
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

COMPANION_CACHE_BRIDGE = os.getenv("COMPANION_CACHE_BRIDGE", "1").lower() in (
    "1",
    "true",
    "yes",
)
COMPANION_CACHE_CHANNEL = (
    os.getenv("COMPANION_CACHE_CHANNEL", "companions:invalidate") or "companions:invalidate"
).strip()

# 本 worker 身份：pid 便于日志；uuid 避免同机重启 pid 撞车
_ORIGIN = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
_sub_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

ACTION_REFRESH = "refresh"  # 资料/头像/软删标记等：原地刷 DB
ACTION_CREATE = "create"  # 新建：本机没有则从 DB 装入
ACTION_DELETE = "delete"  # 硬删：从内存去掉
ACTION_AGENT_CONFIG = "agent_config"  # 仅清 agent 配置 TTL 缓存


def bridge_enabled() -> bool:
    return bool(COMPANION_CACHE_BRIDGE and REDIS_HOST)


def worker_origin() -> str:
    """当前 uvicorn worker 的广播身份（日志 / 调试用）。"""
    return _ORIGIN


def publish_companion_invalidate(
    companion_id: str,
    *,
    action: str = ACTION_REFRESH,
) -> bool:
    """写路径调用：通知其它 worker。成功投递 Redis 即 True。"""
    cid = (companion_id or "").strip()
    if not cid or not bridge_enabled():
        return False
    r = get_redis_client()
    if not r:
        return False
    try:
        envelope = {
            "origin": _ORIGIN,
            "companion_id": cid[:64],
            "action": (action or ACTION_REFRESH).strip() or ACTION_REFRESH,
        }
        r.publish(COMPANION_CACHE_CHANNEL, json.dumps(envelope, ensure_ascii=False))
        return True
    except Exception:
        logger.warning(
            "companion cache invalidate publish failed companion=%s action=%s",
            cid,
            action,
            exc_info=True,
        )
        return False


def apply_companion_invalidate_local(companion_id: str, action: str = ACTION_REFRESH) -> None:
    """在本进程应用失效（订阅回调；也可单测直接调）。"""
    cid = (companion_id or "").strip()
    if not cid:
        return
    action = (action or ACTION_REFRESH).strip() or ACTION_REFRESH

    try:
        from services.agent_utils import invalidate_agent_config_cache

        invalidate_agent_config_cache(cid)
    except Exception:
        logger.debug("invalidate_agent_config_cache failed companion=%s", cid, exc_info=True)

    if action == ACTION_AGENT_CONFIG:
        return

    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        if not cm:
            return

        if action == ACTION_DELETE:
            cm._companions.pop(cid, None)
            return

        if action == ACTION_CREATE:
            if cid not in cm._companions:
                cm.get(cid, include_deleted=True)
            return

        # refresh / soft_delete / restore / 默认：有对象则原地刷（WS 仍持同一引用）
        hit = cm._companions.get(cid)
        if hit is not None:
            hit.refresh_profile_from_db()
        else:
            # 本机没有缓存条目：下次 get 会从 DB 加载；create 场景用 ACTION_CREATE
            pass
    except Exception:
        logger.warning(
            "companion cache apply failed companion=%s action=%s",
            cid,
            action,
            exc_info=True,
        )


def _dispatch(envelope: Dict[str, Any]) -> None:
    if (envelope.get("origin") or "") == _ORIGIN:
        return
    cid = str(envelope.get("companion_id") or "").strip()
    if not cid:
        return
    action = str(envelope.get("action") or ACTION_REFRESH).strip() or ACTION_REFRESH
    apply_companion_invalidate_local(cid, action)


async def _subscriber_loop(stop: asyncio.Event) -> None:
    if not bridge_enabled():
        logger.info("Companion cache bridge disabled (off or REDIS_HOST empty)")
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
            await pubsub.subscribe(COMPANION_CACHE_CHANNEL)
            logger.info(
                "Companion cache bridge subscribed channel=%s origin=%s",
                COMPANION_CACHE_CHANNEL,
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
                    _dispatch(envelope)
                except Exception:
                    logger.exception("Companion cache bridge dispatch failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Companion cache bridge subscriber error; retry in %.1fs",
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
                    await pubsub.unsubscribe(COMPANION_CACHE_CHANNEL)
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


async def start_companion_cache_bridge() -> None:
    """每个 uvicorn worker 的 lifespan 启动时调用。"""
    global _sub_task, _stop_event
    if _sub_task is not None and not _sub_task.done():
        return
    if not bridge_enabled():
        logger.info("Companion cache bridge not started")
        return
    _stop_event = asyncio.Event()
    _sub_task = asyncio.create_task(
        _subscriber_loop(_stop_event),
        name="companion-cache-bridge",
    )
    logger.info("Companion cache bridge task started origin=%s", _ORIGIN)


async def stop_companion_cache_bridge() -> None:
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
