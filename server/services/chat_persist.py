"""
聊天消息异步持久化：Redis flush 队列 → PostgreSQL。

独立后台线程，不阻塞 WS / Agent 热路径。
使用 pending → processing 可靠队列；多 worker 通过 Redis 锁互斥 flush。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from core.chat_cache import (
    ack_flush_batch,
    acquire_flush_lock,
    bridge_enabled,
    flush_queue_depth,
    pop_flush_batch,
    recover_flush_queues,
    release_flush_lock,
    requeue_flush_batch,
)
from core.database import ShortTermMessageORM, get_db

logger = logging.getLogger(__name__)

FLUSH_INTERVAL = float(os.getenv("CHAT_FLUSH_INTERVAL", "2"))
FLUSH_BATCH = int(os.getenv("CHAT_FLUSH_BATCH", "30"))

_worker_started = False
_worker_lock = threading.Lock()
_stop_event = threading.Event()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def flush_batch_to_pg(items: list[dict]) -> int:
    if not items:
        return 0
    written = 0
    with get_db() as db:
        for item in items:
            cid = item.get("companion_id")
            role = item.get("role")
            content = item.get("content")
            if not cid or not role:
                continue
            row = ShortTermMessageORM(
                companion_id=cid,
                role=role,
                content=content or "",
            )
            ts = _parse_ts(item.get("timestamp"))
            if ts:
                row.created_at = ts
            db.add(row)
            written += 1
    return written


def _worker_loop() -> None:
    logger.info(
        "Chat persist worker started (interval=%ss batch=%s)",
        FLUSH_INTERVAL,
        FLUSH_BATCH,
    )
    recovered = recover_flush_queues()
    if recovered:
        logger.info("Recovered %s chat flush items from processing queue", recovered)

    while not _stop_event.is_set():
        try:
            if bridge_enabled() and flush_queue_depth() > 0:
                if not acquire_flush_lock():
                    _stop_event.wait(FLUSH_INTERVAL)
                    continue
                items: list[dict] = []
                try:
                    items = pop_flush_batch(FLUSH_BATCH)
                    if items:
                        n = flush_batch_to_pg(items)
                        # 事务成功即 ack（含被跳过的坏条目，避免毒消息无限 requeue）；
                        # 仅在异常（PG 不可用等）时 requeue 整批。
                        ack_flush_batch(items)
                        if n:
                            logger.debug("Flushed %s chat messages to PostgreSQL", n)
                except Exception:
                    if items:
                        requeue_flush_batch(items)
                    raise
                finally:
                    release_flush_lock()
        except Exception as e:
            logger.warning("Chat persist flush error: %s", e, exc_info=True)
        _stop_event.wait(FLUSH_INTERVAL)


def start_chat_persist_worker() -> None:
    global _worker_started
    if not bridge_enabled():
        logger.info("Chat Redis bridge disabled; persist worker not started")
        return
    with _worker_lock:
        if _worker_started:
            return
        _stop_event.clear()
        t = threading.Thread(target=_worker_loop, name="chat-persist", daemon=True)
        t.start()
        _worker_started = True


def stop_chat_persist_worker() -> None:
    _stop_event.set()
    if not bridge_enabled():
        return
    if not acquire_flush_lock():
        return
    try:
        while flush_queue_depth() > 0:
            items = pop_flush_batch(FLUSH_BATCH)
            if not items:
                break
            try:
                n = flush_batch_to_pg(items)
            except Exception:
                requeue_flush_batch(items)
                raise
            ack_flush_batch(items)
            if n:
                logger.info("Shutdown flush: %s chat messages to PostgreSQL", n)
    except Exception as e:
        logger.warning("Shutdown chat flush error: %s", e)
    finally:
        release_flush_lock()
