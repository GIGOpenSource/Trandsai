"""
聊天短期记忆 Redis 桥接层。

热路径：add / get_recent 只走 Redis，聊天不被 PostgreSQL 写入延迟阻塞。
冷路径：后台 worker 批量 flush 到 PostgreSQL 持久化。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.redis_client import get_redis_client, redis_enabled

logger = logging.getLogger(__name__)

CHAT_REDIS_BRIDGE = os.getenv("CHAT_REDIS_BRIDGE", "1").lower() in ("1", "true", "yes")
CHAT_BUFFER_MAX = int(os.getenv("CHAT_BUFFER_MAX", "200"))
CHAT_REDIS_TTL = int(os.getenv("CHAT_REDIS_TTL_SECONDS", "604800"))  # 7d
FLUSH_QUEUE_KEY = "chat:flush:queue"
FLUSH_PROCESSING_KEY = "chat:flush:processing"
FLUSH_LOCK_KEY = "chat:flush:lock"
FLUSH_LOCK_TTL = int(os.getenv("CHAT_FLUSH_LOCK_TTL", "30"))


def bridge_enabled() -> bool:
    return CHAT_REDIS_BRIDGE and redis_enabled()


def _msgs_key(companion_id: str) -> str:
    return f"chat:msgs:{companion_id}"


def _count_key(companion_id: str) -> str:
    return f"chat:count:{companion_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_message(companion_id: str, role: str, content: str) -> Dict[str, Any]:
    """写入 Redis 并加入 flush 队列；返回消息条目。"""
    r = get_redis_client()
    if not r:
        raise RuntimeError("Redis unavailable for chat cache")

    temp_id = f"t{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    entry: Dict[str, Any] = {
        "id": None,
        "temp_id": temp_id,
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
    }
    pipe = r.pipeline(transaction=True)
    pipe.rpush(_msgs_key(companion_id), json.dumps(entry, ensure_ascii=False))
    pipe.ltrim(_msgs_key(companion_id), -CHAT_BUFFER_MAX, -1)
    pipe.expire(_msgs_key(companion_id), CHAT_REDIS_TTL)
    pipe.incr(_count_key(companion_id))
    pipe.expire(_count_key(companion_id), CHAT_REDIS_TTL)
    pipe.rpush(
        FLUSH_QUEUE_KEY,
        json.dumps(
            {
                "companion_id": companion_id,
                "role": role,
                "content": content,
                "timestamp": entry["timestamp"],
                "temp_id": temp_id,
            },
            ensure_ascii=False,
        ),
    )
    pipe.execute()
    return entry


def get_recent(companion_id: str, n: int = 60, offset: int = 0) -> Optional[List[Dict[str, Any]]]:
    """从 Redis 读取最近消息；offset>0 时返回 None 交由 PG 分页。"""
    if offset > 0:
        return None
    r = get_redis_client()
    if not r:
        return None
    raw_list = r.lrange(_msgs_key(companion_id), -n, -1)
    if not raw_list:
        return []
    out: List[Dict[str, Any]] = []
    seq = 1
    for raw in raw_list:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if item.get("id") is None:
            item["id"] = -(seq)
            seq += 1
        out.append(item)
    return out


def get_last_assistant_content(companion_id: str) -> Optional[str]:
    r = get_redis_client()
    if not r:
        return None
    raw_list = r.lrange(_msgs_key(companion_id), -30, -1)
    for raw in reversed(raw_list):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if item.get("role") == "assistant" and (item.get("content") or "").strip():
            return item["content"]
    return None


def get_total_count(companion_id: str) -> Optional[int]:
    r = get_redis_client()
    if not r:
        return None
    val = r.get(_count_key(companion_id))
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    length = r.llen(_msgs_key(companion_id))
    return int(length) if length else 0


def warm_from_db(companion_id: str, messages: List[Dict[str, Any]]) -> None:
    """从 PostgreSQL 预热 Redis 缓冲（启动或 cache miss）。"""
    if not messages:
        return
    r = get_redis_client()
    if not r:
        return
    key = _msgs_key(companion_id)
    if r.llen(key) > 0:
        return
    pipe = r.pipeline(transaction=True)
    for m in messages[-CHAT_BUFFER_MAX:]:
        pipe.rpush(key, json.dumps(m, ensure_ascii=False))
    pipe.expire(key, CHAT_REDIS_TTL)
    pipe.execute()


def clear(companion_id: str) -> None:
    r = get_redis_client()
    if not r:
        return
    r.delete(_msgs_key(companion_id), _count_key(companion_id))


def _requeue_stale_processing(r) -> int:
    """Move orphaned processing entries back to the pending queue."""
    moved = 0
    while True:
        raw = r.rpoplpush(FLUSH_PROCESSING_KEY, FLUSH_QUEUE_KEY)
        if not raw:
            break
        moved += 1
    return moved


def acquire_flush_lock() -> bool:
    """Distributed lock so only one worker flushes at a time."""
    r = get_redis_client()
    if not r:
        return True
    try:
        return bool(r.set(FLUSH_LOCK_KEY, "1", nx=True, ex=FLUSH_LOCK_TTL))
    except Exception:
        return True


def release_flush_lock() -> None:
    r = get_redis_client()
    if not r:
        return
    try:
        r.delete(FLUSH_LOCK_KEY)
    except Exception:
        pass


def pop_flush_batch(batch_size: int = 30) -> List[Dict[str, Any]]:
    """Atomically move items pending → processing (reliable queue)."""
    r = get_redis_client()
    if not r:
        return []
    items: List[Dict[str, Any]] = []
    for _ in range(batch_size):
        raw = r.rpoplpush(FLUSH_QUEUE_KEY, FLUSH_PROCESSING_KEY)
        if not raw:
            break
        try:
            items.append(json.loads(raw))
        except json.JSONDecodeError:
            r.lrem(FLUSH_PROCESSING_KEY, 1, raw)
            continue
    return items


def ack_flush_batch(items: List[Dict[str, Any]]) -> None:
    """Remove successfully persisted items from the processing list."""
    if not items:
        return
    r = get_redis_client()
    if not r:
        return
    pipe = r.pipeline(transaction=True)
    for item in items:
        raw = json.dumps(item, ensure_ascii=False)
        pipe.lrem(FLUSH_PROCESSING_KEY, 1, raw)
    pipe.execute()


def requeue_flush_batch(items: List[Dict[str, Any]]) -> None:
    """Return failed items to the head of the pending queue."""
    if not items:
        return
    r = get_redis_client()
    if not r:
        return
    pipe = r.pipeline(transaction=True)
    for item in reversed(items):
        raw = json.dumps(item, ensure_ascii=False)
        pipe.lrem(FLUSH_PROCESSING_KEY, 1, raw)
        pipe.lpush(FLUSH_QUEUE_KEY, raw)
    pipe.execute()


def flush_queue_depth() -> int:
    r = get_redis_client()
    if not r:
        return 0
    try:
        return int(r.llen(FLUSH_QUEUE_KEY))
    except Exception:
        return 0


def flush_processing_depth() -> int:
    r = get_redis_client()
    if not r:
        return 0
    try:
        return int(r.llen(FLUSH_PROCESSING_KEY))
    except Exception:
        return 0


def recover_flush_queues() -> int:
    """Startup recovery: processing → pending."""
    r = get_redis_client()
    if not r:
        return 0
    return _requeue_stale_processing(r)
