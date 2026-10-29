"""
聊天短期记忆 Redis 桥接层。

热路径：add / get_recent 只走 Redis，聊天不被 PostgreSQL 写入延迟阻塞。
冷路径：后台 worker 批量 flush 到 PostgreSQL 持久化。

Room 键规范（按登录 token 解析出的 user_id 隔离，不把明文 token 写入 key）：
  room:{companion_id}:u:{user_id}:msgs
  room:{companion_id}:u:{user_id}:count
  room:flush:queue / room:flush:processing / room:flush:lock

TTL：默认 CHAT_REDIS_TTL_SECONDS=604800（7 天）；写入追加并续期，热读/激活亦续期。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.redis_client import get_redis_client, redis_enabled, reset_redis_client

logger = logging.getLogger(__name__)

CHAT_REDIS_BRIDGE = os.getenv("CHAT_REDIS_BRIDGE", "1").lower() in ("1", "true", "yes")
CHAT_BUFFER_MAX = int(os.getenv("CHAT_BUFFER_MAX", "200"))
CHAT_REDIS_TTL = int(os.getenv("CHAT_REDIS_TTL_SECONDS", "604800"))  # 7d

# 业务前缀 room: — 热读按用户房间隔离，flush 全局队列同属 room 域
FLUSH_QUEUE_KEY = "room:flush:queue"
FLUSH_PROCESSING_KEY = "room:flush:processing"
FLUSH_LOCK_KEY = "room:flush:lock"
FLUSH_LOCK_TTL = int(os.getenv("CHAT_FLUSH_LOCK_TTL", "30"))


def bridge_enabled() -> bool:
    return CHAT_REDIS_BRIDGE and redis_enabled()


def room_key(companion_id: str, user_id: int) -> str:
    """用户+伴侣会话房间：room:{companion_id}:u:{user_id}"""
    return f"room:{companion_id}:u:{int(user_id)}"


def _msgs_key(companion_id: str, user_id: int) -> str:
    return f"{room_key(companion_id, user_id)}:msgs"


def _count_key(companion_id: str, user_id: int) -> str:
    return f"{room_key(companion_id, user_id)}:count"


def _touch_room(r, companion_id: str, user_id: int) -> None:
    """再次激活（读/写）时重置 TTL，供轮询热读继续命中。"""
    msgs_key = _msgs_key(companion_id, user_id)
    count_key = _count_key(companion_id, user_id)
    pipe = r.pipeline(transaction=False)
    pipe.expire(msgs_key, CHAT_REDIS_TTL)
    pipe.expire(count_key, CHAT_REDIS_TTL)
    pipe.execute()


def _legacy_msgs_key(companion_id: str, user_id: int) -> str:
    """迁移前旧键：chat:msgs:{companion_id}:{user_id}"""
    return f"chat:msgs:{companion_id}:{int(user_id)}"


def _legacy_count_key(companion_id: str, user_id: int) -> str:
    return f"chat:count:{companion_id}:{int(user_id)}"


def _cleared_at_key(companion_id: str, user_id: int) -> str:
    return f"{room_key(companion_id, user_id)}:cleared_at"


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def mark_room_cleared(companion_id: str, user_id: int) -> str:
    """记录清空时刻。此后 timestamp 不晚于该时刻的 flush 条目不得写入 PG。

    Redis 不可用时抛错，避免只删 PG、队列仍把旧消息写回。
    """
    ts = _now_iso()
    r = get_redis_client()
    if not r:
        raise RuntimeError("Redis unavailable; cannot mark room cleared")
    r.set(_cleared_at_key(companion_id, int(user_id)), ts, ex=CHAT_REDIS_TTL)
    return ts


def get_room_cleared_at(companion_id: str, user_id: Optional[int]) -> Optional[datetime]:
    if user_id is None:
        return None
    r = get_redis_client()
    if not r:
        return None
    raw = r.get(_cleared_at_key(companion_id, int(user_id)))
    if not raw:
        return None
    return _parse_iso_dt(raw)


def get_rooms_cleared_at(
    companion_ids: List[str],
    user_id: int,
) -> Dict[str, datetime]:
    """批量读取清空墓碑，供会话列表过滤 last_message。"""
    if not companion_ids:
        return {}
    r = get_redis_client()
    if not r:
        return {}
    uid = int(user_id)
    pipe = r.pipeline(transaction=False)
    for cid in companion_ids:
        pipe.get(_cleared_at_key(str(cid), uid))
    try:
        raws = pipe.execute()
    except Exception:
        logger.debug("batch get cleared_at failed", exc_info=True)
        return {}
    out: Dict[str, datetime] = {}
    for cid, raw in zip(companion_ids, raws):
        dt = _parse_iso_dt(raw)
        if dt is not None:
            out[str(cid)] = dt
    return out


def message_at_or_before_clear(
    timestamp: Any,
    cleared_at: Optional[datetime],
) -> bool:
    """True = 该条属于清空前（含清空瞬间），读路径应丢弃。"""
    if cleared_at is None:
        return False
    ts = _parse_iso_dt(timestamp)
    if ts is None:
        return True
    return ts <= cleared_at


def should_drop_flush_item(item: Optional[Dict[str, Any]]) -> bool:
    """清空聊天之后、清空时刻之前（含）的队列消息视为作废。"""
    if not item:
        return True
    cid = item.get("companion_id")
    uid = item.get("user_id")
    if not cid or uid is None:
        return False
    cleared_at = get_room_cleared_at(str(cid), int(uid))
    if cleared_at is None:
        return False
    return message_at_or_before_clear(item.get("timestamp"), cleared_at)


def _migrate_legacy_room(r, companion_id: str, user_id: int) -> bool:
    """若新 room 键为空且存在旧 chat:msgs 键，则 rename/拷贝到 room: 并续期。"""
    msgs_key = _msgs_key(companion_id, user_id)
    if r.llen(msgs_key) > 0:
        return False
    legacy = _legacy_msgs_key(companion_id, user_id)
    if not r.exists(legacy):
        return False
    # 优先 RENAME（同 DB 原子）；失败则 DUMP 式拷贝
    try:
        r.rename(legacy, msgs_key)
    except Exception:
        raw_list = r.lrange(legacy, 0, -1)
        if not raw_list:
            return False
        pipe = r.pipeline(transaction=True)
        for raw in raw_list[-CHAT_BUFFER_MAX:]:
            pipe.rpush(msgs_key, raw)
        pipe.expire(msgs_key, CHAT_REDIS_TTL)
        pipe.execute()
        r.delete(legacy)

    count_key = _count_key(companion_id, user_id)
    legacy_count = _legacy_count_key(companion_id, user_id)
    if r.exists(legacy_count) and not r.exists(count_key):
        try:
            r.rename(legacy_count, count_key)
        except Exception:
            val = r.get(legacy_count)
            if val is not None:
                r.set(count_key, val)
            r.delete(legacy_count)
    if not r.exists(count_key):
        r.set(count_key, r.llen(msgs_key))
    _touch_room(r, companion_id, user_id)
    logger.info(
        "Migrated legacy chat cache %s → %s",
        legacy,
        msgs_key,
    )
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_message(
    companion_id: str,
    role: str,
    content: str,
    user_id: Optional[int] = None,
    send_status: str = "",
) -> Dict[str, Any]:
    """写入 Redis room 并加入 flush 队列；返回消息条目。须有 token 对应用户。"""
    if user_id is None:
        raise ValueError("room redis write requires user_id from auth token")
    r = get_redis_client()
    if not r:
        raise RuntimeError("Redis unavailable for chat cache")

    uid = int(user_id)
    status = (send_status or "").strip()[:20]
    _migrate_legacy_room(r, companion_id, uid)
    temp_id = f"t{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    entry: Dict[str, Any] = {
        "id": None,
        "temp_id": temp_id,
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
        "user_id": uid,
        "send_status": status,
    }
    msgs_key = _msgs_key(companion_id, uid)
    count_key = _count_key(companion_id, uid)
    pipe = r.pipeline(transaction=True)
    pipe.rpush(msgs_key, json.dumps(entry, ensure_ascii=False))
    pipe.ltrim(msgs_key, -CHAT_BUFFER_MAX, -1)
    pipe.expire(msgs_key, CHAT_REDIS_TTL)
    flush_item = {
        "companion_id": companion_id,
        "user_id": uid,
        "role": role,
        "content": content,
        "timestamp": entry["timestamp"],
        "temp_id": temp_id,
    }
    if status:
        flush_item["send_status"] = status
    pipe.rpush(
        FLUSH_QUEUE_KEY,
        json.dumps(flush_item, ensure_ascii=False),
    )
    pipe.execute()
    # count 在 warm_from_db 后可能被 delete，再 incr 会从 1 低估；与 llen 对齐
    try:
        llen = int(r.llen(msgs_key) or 0)
        raw_c = r.get(count_key)
        if raw_c is None:
            r.set(count_key, llen)
        else:
            cur = int(raw_c)
            if cur < llen:
                r.set(count_key, llen)
            else:
                r.incr(count_key)
        r.expire(count_key, CHAT_REDIS_TTL)
    except Exception:
        logger.debug("chat count heal failed for %s", msgs_key, exc_info=True)
    return entry


def get_recent(
    companion_id: str,
    n: int = 60,
    offset: int = 0,
    user_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """从 Redis room 热读；缺 user_id / offset>0 时返回 None 交由 PG。"""
    if offset > 0 or user_id is None:
        return None
    r = get_redis_client()
    if not r:
        return None
    uid = int(user_id)
    try:
        _migrate_legacy_room(r, companion_id, uid)
    except Exception:
        logger.debug("legacy room migrate on read failed", exc_info=True)
    msgs_key = _msgs_key(companion_id, uid)
    raw_list = r.lrange(msgs_key, -n, -1)
    if not raw_list:
        return []
    _touch_room(r, companion_id, uid)
    cleared_at = get_room_cleared_at(companion_id, uid)
    out: List[Dict[str, Any]] = []
    seq = 1
    for raw in raw_list:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if message_at_or_before_clear(item.get("timestamp"), cleared_at):
            continue
        if item.get("id") is None:
            # 稳定 id：优先 temp_id，避免按窗口位置分配 -1/-2 导致前端合并错乱
            item["id"] = item.get("temp_id") or f"tmp-{seq}"
            seq += 1
        out.append(item)
    return out


def get_last_assistant_content(
    companion_id: str,
    user_id: Optional[int] = None,
) -> Optional[str]:
    if user_id is None:
        return None
    r = get_redis_client()
    if not r:
        return None
    uid = int(user_id)
    raw_list = r.lrange(_msgs_key(companion_id, uid), -30, -1)
    if raw_list:
        _touch_room(r, companion_id, uid)
    cleared_at = get_room_cleared_at(companion_id, uid)
    for raw in reversed(raw_list):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if message_at_or_before_clear(item.get("timestamp"), cleared_at):
            continue
        if item.get("role") == "assistant" and (item.get("content") or "").strip():
            return item["content"]
    return None


def get_total_count(companion_id: str, user_id: Optional[int] = None) -> Optional[int]:
    """返回热房间消息总数估计；count 键缺失/低估时回退 llen，再不行交由 PG。"""
    if user_id is None:
        return None
    r = get_redis_client()
    if not r:
        return None
    uid = int(user_id)
    msgs_key = _msgs_key(companion_id, uid)
    count_key = _count_key(companion_id, uid)
    try:
        llen = int(r.llen(msgs_key) or 0)
        val = r.get(count_key)
    except Exception as e:
        logger.warning("get_total_count redis failed companion=%s user=%s: %s", companion_id, uid, e)
        reset_redis_client()
        return None
    n: Optional[int] = None
    if val is not None:
        try:
            n = int(val)
        except (TypeError, ValueError):
            n = None
    if n is not None and n > 0:
        # warm 后 count 被删再 incr 会低估；至少不低于当前热列表长度
        if llen > n:
            try:
                r.set(count_key, llen)
                r.expire(count_key, CHAT_REDIS_TTL)
            except Exception:
                pass
            n = llen
        _touch_room(r, companion_id, uid)
        return n
    if llen > 0:
        _touch_room(r, companion_id, uid)
        return llen
    return None


def warm_from_db(
    companion_id: str,
    messages: List[Dict[str, Any]],
    user_id: Optional[int] = None,
) -> None:
    """从 PostgreSQL 回填预热 Redis room（cache miss）。"""
    if not messages or user_id is None:
        return
    r = get_redis_client()
    if not r:
        return
    uid = int(user_id)
    key = _msgs_key(companion_id, uid)
    if r.llen(key) > 0:
        # 房间已有热数据：仅续期，不覆盖追加列表
        _touch_room(r, companion_id, uid)
        return
    # PG 回填为权威热数据；清掉旧 chat:msgs 键，避免下次读到残缺旧缓存
    r.delete(_legacy_msgs_key(companion_id, uid), _legacy_count_key(companion_id, uid))
    pipe = r.pipeline(transaction=True)
    for m in messages[-CHAT_BUFFER_MAX:]:
        if isinstance(m, dict) and m.get("user_id") is None:
            m = {**m, "user_id": uid}
        pipe.rpush(key, json.dumps(m, ensure_ascii=False))
    pipe.expire(key, CHAT_REDIS_TTL)
    # 不在此写入 count：页面预热条数 ≠ 会话总条数，总数仍走 PG / incr
    pipe.delete(_count_key(companion_id, uid))
    pipe.execute()


def purge_room_cache(companion_id: str, user_id: Optional[int] = None) -> None:
    """只删热房间键，不打清空墓碑。heal / 回填前用，避免误丢后续 flush。"""
    if user_id is None:
        return
    r = get_redis_client()
    if not r:
        return
    uid = int(user_id)
    r.delete(
        _msgs_key(companion_id, uid),
        _count_key(companion_id, uid),
        _legacy_msgs_key(companion_id, uid),
        _legacy_count_key(companion_id, uid),
    )


def clear(companion_id: str, user_id: Optional[int] = None) -> None:
    if user_id is None:
        return
    uid = int(user_id)
    # 先打时间戳，避免并发 flush 把队列里的旧消息写回 PG（失败则整次清空中止）
    mark_room_cleared(companion_id, uid)
    purge_room_cache(companion_id, uid)


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
