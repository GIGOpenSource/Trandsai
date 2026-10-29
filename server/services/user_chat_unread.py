"""用户聊天未读：Messages 总角标 + 会话列表每条智能体角标。

存储：
  im:user:unread:{user_id}  HASH  companion_id → count
总未读 = 可见会话未读之和（排除拉黑 / 不感兴趣 / 举报隐藏 / 下架，与 Messages 列表一致）。

Redis 不可用时退化为进程内字典（单 worker 可用）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Set

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_HASH_KEY = "im:user:unread:{uid}"
_WATCH_MAX = 200
INBOX_HEARTBEAT_SEC = 25
INBOX_IDLE_TIMEOUT_SEC = 90

_LUA_INCR = """
local cid = ARGV[1]
local delta = tonumber(ARGV[2])
local cur = tonumber(redis.call('HINCRBY', KEYS[1], cid, delta) or '0') or 0
if cur < 0 then
  redis.call('HSET', KEYS[1], cid, 0)
  cur = 0
end
if cur == 0 then
  redis.call('HDEL', KEYS[1], cid)
end
local total = 0
local all = redis.call('HGETALL', KEYS[1])
for i = 2, #all, 2 do
  total = total + (tonumber(all[i]) or 0)
end
return {cur, total}
"""

_LUA_CLEAR = """
local cid = ARGV[1]
local prev = tonumber(redis.call('HGET', KEYS[1], cid) or '0') or 0
redis.call('HDEL', KEYS[1], cid)
local total = 0
local all = redis.call('HGETALL', KEYS[1])
for i = 2, #all, 2 do
  total = total + (tonumber(all[i]) or 0)
end
return {prev, total}
"""

_mem: Dict[int, Dict[str, int]] = {}
_mem_thread_lock = threading.Lock()

_inbox_ws: Dict[int, Set[Any]] = {}
_inbox_lock = asyncio.Lock()
_inbox_loop: Optional[asyncio.AbstractEventLoop] = None


def _hash_key(user_id: int) -> str:
    return _HASH_KEY.format(uid=int(user_id))


def _cid(companion_id: str) -> str:
    return str(companion_id or "").strip()[:8]


def _sum_map(m: Dict[str, int]) -> int:
    return sum(max(0, int(v or 0)) for v in m.values())


def _mem_incr(user_id: int, companion_id: str, delta: int) -> Dict[str, int]:
    uid = int(user_id)
    cid = _cid(companion_id)
    with _mem_thread_lock:
        bucket = _mem.setdefault(uid, {})
        cur = max(0, int(bucket.get(cid) or 0) + int(delta))
        if cur <= 0:
            bucket.pop(cid, None)
            cur = 0
        else:
            bucket[cid] = cur
        total = _sum_map(bucket)
    return {"unread_count": cur, "total_unread": total, "cleared_from": 0}


def _mem_clear(user_id: int, companion_id: str) -> Dict[str, int]:
    uid = int(user_id)
    cid = _cid(companion_id)
    with _mem_thread_lock:
        bucket = _mem.setdefault(uid, {})
        prev = max(0, int(bucket.pop(cid, 0) or 0))
        total = _sum_map(bucket)
    return {"unread_count": 0, "total_unread": total, "cleared_from": prev}


def _mem_map(user_id: int) -> Dict[str, int]:
    with _mem_thread_lock:
        return dict(_mem.get(int(user_id)) or {})


def incr_unread(user_id: int, companion_id: str, *, delta: int = 1) -> Dict[str, int]:
    """某智能体未读 +delta。返回 unread_count / total_unread。"""
    uid = int(user_id)
    cid = _cid(companion_id)
    dlt = int(delta)
    if uid <= 0 or not cid or dlt == 0:
        return {"unread_count": 0, "total_unread": get_total_unread(uid), "cleared_from": 0}
    r = get_redis_client()
    if not r:
        return _mem_incr(uid, cid, dlt)
    try:
        cur, total = r.eval(_LUA_INCR, 1, _hash_key(uid), cid, dlt)
        return {
            "unread_count": max(0, int(cur or 0)),
            "total_unread": max(0, int(total or 0)),
            "cleared_from": 0,
        }
    except Exception:
        logger.warning("user unread incr failed user=%s companion=%s", uid, cid, exc_info=True)
        return _mem_incr(uid, cid, dlt)


def clear_unread(user_id: int, companion_id: str) -> Dict[str, int]:
    uid = int(user_id)
    cid = _cid(companion_id)
    if uid <= 0 or not cid:
        return {"unread_count": 0, "total_unread": get_total_unread(uid), "cleared_from": 0}
    r = get_redis_client()
    if not r:
        return _mem_clear(uid, cid)
    try:
        prev, total = r.eval(_LUA_CLEAR, 1, _hash_key(uid), cid)
        return {
            "unread_count": 0,
            "total_unread": max(0, int(total or 0)),
            "cleared_from": max(0, int(prev or 0)),
        }
    except Exception:
        logger.warning("user unread clear failed user=%s companion=%s", uid, cid, exc_info=True)
        return _mem_clear(uid, cid)


def get_unread(user_id: int, companion_id: str) -> int:
    uid = int(user_id)
    cid = _cid(companion_id)
    r = get_redis_client()
    if not r:
        return max(0, int(_mem_map(uid).get(cid) or 0))
    try:
        raw = r.hget(_hash_key(uid), cid)
        return max(0, int(raw or 0))
    except Exception:
        logger.debug("user unread get failed", exc_info=True)
        return max(0, int(_mem_map(uid).get(cid) or 0))


def get_unread_map(user_id: int) -> Dict[str, int]:
    uid = int(user_id)
    r = get_redis_client()
    if not r:
        return _mem_map(uid)
    try:
        raw = r.hgetall(_hash_key(uid)) or {}
        out: Dict[str, int] = {}
        for k, v in raw.items():
            n = max(0, int(v or 0))
            if n > 0:
                out[str(k)] = n
        return out
    except Exception:
        logger.debug("user unread map failed", exc_info=True)
        return _mem_map(uid)


def _excluded_companion_ids(user_id: int) -> Set[str]:
    """与 Messages 列表一致：举报隐藏 / 下架 / 拉黑 / 不感兴趣 不计入未读。"""
    try:
        from core.database import get_db
        from services.companion_report import list_excluded_companion_ids_for_user

        with get_db() as db:
            raw = list_excluded_companion_ids_for_user(
                db, int(user_id), include_outreach=True
            ) or set()
        return {_cid(x) for x in raw if x}
    except Exception:
        logger.debug("user unread excluded ids failed user=%s", user_id, exc_info=True)
        return set()


def get_visible_unread_map(user_id: int) -> Dict[str, int]:
    """未读 map，已去掉聊天列表不展示的智能体。"""
    m = get_unread_map(user_id)
    if not m:
        return {}
    excluded = _excluded_companion_ids(user_id)
    if not excluded:
        return m
    return {k: v for k, v in m.items() if k not in excluded}


def get_total_unread(user_id: int) -> int:
    """Tab 总未读：只统计仍会出现在聊天列表里的会话。"""
    return _sum_map(get_visible_unread_map(user_id))


def batch_unread(user_id: int, companion_ids: Iterable[str]) -> List[Dict[str, Any]]:
    ids: List[str] = []
    seen = set()
    for raw in companion_ids or ():
        cid = _cid(str(raw))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ids.append(cid)
        if len(ids) >= _WATCH_MAX:
            break
    m = get_unread_map(user_id)
    excluded = _excluded_companion_ids(user_id) if ids else set()
    return [
        {
            "companion_id": cid,
            "unread_count": 0 if cid in excluded else int(m.get(cid) or 0),
        }
        for cid in ids
    ]


def parse_companion_ids(raw: Any) -> List[str]:
    """watch / query 的 companion_ids：list 或逗号分隔字符串。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [x.strip() for x in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw]
    else:
        return []
    ids: List[str] = []
    seen = set()
    for p in parts:
        cid = _cid(p)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ids.append(cid)
        if len(ids) >= _WATCH_MAX:
            break
    return ids


def hello_payload(user_id: int) -> Dict[str, Any]:
    """连上 / 重连后的 hello：未读只属于这个 user_id。"""
    uid = int(user_id)
    return {
        "type": "hello",
        "user_id": uid,
        "total_unread": get_total_unread(uid),
        "watch_required": True,
        "heartbeat_sec": INBOX_HEARTBEAT_SEC,
    }


def snapshot(user_id: int, companion_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    total = get_total_unread(user_id)
    companions = batch_unread(user_id, companion_ids or [])
    return {"total_unread": int(total), "companions": companions}


def unread_event_payload(user_id: int, companion_id: str, info: Dict[str, int]) -> Dict[str, Any]:
    return {
        "type": "unread",
        "companion_id": _cid(companion_id),
        "unread_count": int(info.get("unread_count") or 0),
        "total_unread": int(info.get("total_unread") or 0),
        "user_id": int(user_id),
    }


def _chat_session_open(user_id: int, companion_id: str) -> bool:
    """用户是否正在该智能体的聊天页（本机 WS 优先；Redis 在线标记作跨 worker 兜底）。

    断线时应立刻清 Redis 标记，否则 AI 仍在生成时会被误判为在会话内而不计未读。
    """
    cid = _cid(companion_id)
    try:
        from api.companion_ws import is_user_chat_connected

        if is_user_chat_connected(int(user_id), cid):
            return True
    except Exception:
        pass
    try:
        from services.im_ws_bridge import is_user_session_online

        if is_user_session_online(int(user_id), cid):
            return True
    except Exception:
        pass
    return False


def note_assistant_unread(
    user_id: int,
    companion_id: str,
    *,
    delivered_live: bool = False,
    delta: int = 1,
) -> Optional[Dict[str, int]]:
    """助手新消息已落库：仅当用户当前不在该会话聊天页时 +未读并推 inbox。

    delivered_live=True：消息已成功推到 /ws/chat（含转投新连接）→ 不计未读。
    拉黑 / 不感兴趣 / 举报隐藏 / 下架：列表不展示 → 不计未读。
    """
    uid = int(user_id or 0)
    cid = _cid(companion_id)
    if uid <= 0 or not cid:
        return None
    if delivered_live:
        return None
    if cid in _excluded_companion_ids(uid):
        return None
    if _chat_session_open(uid, cid):
        return None
    info = incr_unread(uid, cid, delta=max(1, int(delta or 1)))
    # Redis 原始 total 含已屏蔽会话；推送用可见总数
    info["total_unread"] = get_total_unread(uid)
    schedule_broadcast(uid, unread_event_payload(uid, cid, info))
    return info


def clear_user_chat_unread(
    user_id: int,
    companion_id: str,
    *,
    notify: bool = True,
) -> Dict[str, int]:
    """进入某智能体聊天 / 拉黑不感兴趣：只清这一条会话的未读（不会清全部）。"""
    uid = int(user_id or 0)
    cid = _cid(companion_id)
    if uid <= 0 or not cid:
        return {"unread_count": 0, "total_unread": get_total_unread(uid), "cleared_from": 0}
    info = clear_unread(uid, cid)
    info["total_unread"] = get_total_unread(uid)
    if notify and int(info.get("cleared_from") or 0) > 0:
        schedule_broadcast(uid, unread_event_payload(uid, cid, info))
    return info


def bind_inbox_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _inbox_loop
    _inbox_loop = loop


async def register_user_inbox(user_id: int, ws: Any) -> None:
    uid = int(user_id)
    bind_inbox_loop(asyncio.get_running_loop())
    async with _inbox_lock:
        _inbox_ws.setdefault(uid, set()).add(ws)


async def unregister_user_inbox(user_id: int, ws: Any) -> None:
    uid = int(user_id)
    async with _inbox_lock:
        s = _inbox_ws.get(uid)
        if not s:
            return
        s.discard(ws)
        if not s:
            _inbox_ws.pop(uid, None)


async def broadcast_user_inbox_local(user_id: int, payload: dict) -> int:
    from starlette.websockets import WebSocketState

    uid = int(user_id)
    async with _inbox_lock:
        targets = list(_inbox_ws.get(uid) or ())
    if not targets:
        return 0
    dead: List[Any] = []
    ok_n = 0
    raw = json.dumps(payload, ensure_ascii=False)
    for ws in targets:
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            await ws.send_text(raw)
            ok_n += 1
        except Exception:
            dead.append(ws)
    if dead:
        async with _inbox_lock:
            s = _inbox_ws.get(uid)
            if s:
                for ws in dead:
                    s.discard(ws)
                if not s:
                    _inbox_ws.pop(uid, None)
    return ok_n


async def broadcast_user_inbox(user_id: int, payload: dict) -> int:
    ok_n = await broadcast_user_inbox_local(int(user_id), payload)
    try:
        from services.im_ws_bridge import publish_user_inbox

        await publish_user_inbox(int(user_id), payload)
    except Exception:
        logger.exception("user inbox redis bridge publish failed user=%s", user_id)
    return ok_n


def schedule_broadcast(user_id: int, payload: dict) -> None:
    """任意线程：把未读帧推给该用户的 inbox 长连接。"""
    uid = int(user_id)

    async def _go():
        await broadcast_user_inbox(uid, payload)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_go())
        return
    except RuntimeError:
        pass
    loop = _inbox_loop
    if loop is not None and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_go(), loop)
            return
        except Exception:
            logger.debug("user inbox schedule failed user=%s", uid, exc_info=True)
    try:
        from services.im_ws_bridge import KIND_USER_INBOX, publish_bridge_sync

        publish_bridge_sync(
            {
                "kind": KIND_USER_INBOX,
                "user_id": uid,
                "payload": payload,
            }
        )
    except Exception:
        logger.debug("user inbox publish fallback failed user=%s", uid, exc_info=True)
