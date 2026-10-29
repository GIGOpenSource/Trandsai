"""IM 运营未读：Redis 多级存储（会话角标 + 智能体汇总 + 运营总计）。

结构：
  im:ops:unread:s:{ops_admin_id}:{companion_id}  HASH  user_id → count   （会话）
  im:ops:unread:c:{ops_admin_id}                 HASH  companion_id → n （智能体汇总）
  im:ops:unread:t                                HASH  ops_admin_id → total
  im:ops:unread:n                                HASH  ops_admin_id → 有未读会话数

ops_admin_id=0（UNASSIGNED_OPS_ADMIN_ID）表示「未分配责任运营」池，仅超管汇总可见；
指定责任人后应迁到真实 ops。

角标：列表用会话级；IM 侧栏用智能体汇总；全局角标用 t（轮询 summary）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# 无责任运营时未读挂靠的占位 ops（非真实 users.id）
UNASSIGNED_OPS_ADMIN_ID = 0

_SESSION_KEY = "im:ops:unread:s:{ops}:{cid}"
_COMPANION_KEY = "im:ops:unread:c:{ops}"
_TOTAL_KEY = "im:ops:unread:t"
_SESSION_COUNT_KEY = "im:ops:unread:n"


def effective_ops_admin_id(ops_admin_id: Optional[int]) -> int:
    """列表/读写用：无责任人时读未分配池。"""
    if ops_admin_id is None:
        return UNASSIGNED_OPS_ADMIN_ID
    return int(ops_admin_id)

# KEYS[1]=session hash, KEYS[2]=total, KEYS[3]=session_count, KEYS[4]=companion hash
# ARGV[1]=user_id, ARGV[2]=delta, ARGV[3]=ops_admin_id, ARGV[4]=companion_id
_LUA_INCR = """
local uid = ARGV[1]
local delta = tonumber(ARGV[2])
local ops = ARGV[3]
local cid = ARGV[4]
local prev = tonumber(redis.call('HGET', KEYS[1], uid) or '0') or 0
local cur = redis.call('HINCRBY', KEYS[1], uid, delta)
redis.call('HINCRBY', KEYS[2], ops, delta)
redis.call('HINCRBY', KEYS[4], cid, delta)
if prev <= 0 and cur > 0 then
  redis.call('HINCRBY', KEYS[3], ops, 1)
end
return cur
"""

# KEYS[1]=session hash, KEYS[2]=total, KEYS[3]=session_count, KEYS[4]=companion hash
# ARGV[1]=user_id, ARGV[2]=ops_admin_id, ARGV[3]=companion_id
_LUA_CLEAR = """
local uid = ARGV[1]
local ops = ARGV[2]
local cid = ARGV[3]
local prev = tonumber(redis.call('HGET', KEYS[1], uid) or '0') or 0
if prev <= 0 then
  redis.call('HDEL', KEYS[1], uid)
  return 0
end
redis.call('HDEL', KEYS[1], uid)
redis.call('HINCRBY', KEYS[2], ops, -prev)
local tot = tonumber(redis.call('HGET', KEYS[2], ops) or '0') or 0
if tot <= 0 then
  redis.call('HDEL', KEYS[2], ops)
end
local cc = tonumber(redis.call('HINCRBY', KEYS[4], cid, -prev) or '0') or 0
if cc <= 0 then
  redis.call('HDEL', KEYS[4], cid)
end
local sc = tonumber(redis.call('HGET', KEYS[3], ops) or '0') or 0
if sc > 0 then
  redis.call('HINCRBY', KEYS[3], ops, -1)
else
  redis.call('HDEL', KEYS[3], ops)
end
return prev
"""

# 清某运营下某智能体全部会话未读
# KEYS[1]=session hash, KEYS[2]=total, KEYS[3]=session_count, KEYS[4]=companion hash
# ARGV[1]=ops_admin_id, ARGV[2]=companion_id
# 返回 {cleared_unread, cleared_sessions}
_LUA_CLEAR_COMPANION = """
local ops = ARGV[1]
local cid = ARGV[2]
local entries = redis.call('HGETALL', KEYS[1])
local sum = 0
local sc = 0
for i = 1, #entries, 2 do
  local v = tonumber(entries[i + 1]) or 0
  if v > 0 then
    sum = sum + v
    sc = sc + 1
  end
end
redis.call('DEL', KEYS[1])
redis.call('HDEL', KEYS[4], cid)
if sum > 0 then
  redis.call('HINCRBY', KEYS[2], ops, -sum)
  local tot = tonumber(redis.call('HGET', KEYS[2], ops) or '0') or 0
  if tot <= 0 then
    redis.call('HDEL', KEYS[2], ops)
  end
end
if sc > 0 then
  local cur_sc = tonumber(redis.call('HGET', KEYS[3], ops) or '0') or 0
  local nxt = cur_sc - sc
  if nxt <= 0 then
    redis.call('HDEL', KEYS[3], ops)
  else
    redis.call('HSET', KEYS[3], ops, nxt)
  end
end
return {sum, sc}
"""


def _session_key(ops_admin_id: int, companion_id: str) -> str:
    return _SESSION_KEY.format(ops=int(ops_admin_id), cid=str(companion_id)[:8])


def _companion_key(ops_admin_id: int) -> str:
    return _COMPANION_KEY.format(ops=int(ops_admin_id))


def redis_available() -> bool:
    return get_redis_client() is not None


def get_session_unread(ops_admin_id: int, companion_id: str, user_id: int) -> int:
    r = get_redis_client()
    if not r:
        return 0
    try:
        raw = r.hget(_session_key(ops_admin_id, companion_id), str(int(user_id)))
        return max(0, int(raw or 0))
    except Exception:
        logger.debug("im unread get failed", exc_info=True)
        return 0


def batch_get_session_unread(
    items: Iterable[Tuple[int, str, Optional[int]]],
) -> Dict[Tuple[int, str], int]:
    """items: (user_id, companion_id, ops_admin_id)。无 ops 时读未分配池 (0)。"""
    out: Dict[Tuple[int, str], int] = {}
    pending: List[Tuple[int, str, int]] = []
    for user_id, companion_id, ops_admin_id in items:
        cid = str(companion_id)[:8]
        key = (int(user_id), cid)
        pending.append((int(user_id), cid, effective_ops_admin_id(ops_admin_id)))
        out[key] = 0

    if not pending:
        return out
    r = get_redis_client()
    if not r:
        return out
    try:
        pipe = r.pipeline(transaction=False)
        for user_id, cid, ops in pending:
            pipe.hget(_session_key(ops, cid), str(user_id))
        results = pipe.execute()
        for (user_id, cid, _ops), raw in zip(pending, results):
            out[(user_id, cid)] = max(0, int(raw or 0))
    except Exception:
        logger.warning("im unread batch get failed", exc_info=True)
    return out


def incr_session_unread(
    ops_admin_id: int,
    companion_id: str,
    user_id: int,
    *,
    delta: int = 1,
) -> Optional[int]:
    """会话未读 +delta，同步更新智能体/运营汇总；返回会话新值。"""
    r = get_redis_client()
    if not r:
        return None
    delta = max(1, int(delta or 1))
    cid = str(companion_id)[:8]
    ops = int(ops_admin_id)
    try:
        cur = r.eval(
            _LUA_INCR,
            4,
            _session_key(ops, cid),
            _TOTAL_KEY,
            _SESSION_COUNT_KEY,
            _companion_key(ops),
            str(int(user_id)),
            str(delta),
            str(ops),
            cid,
        )
        return max(0, int(cur or 0))
    except Exception:
        logger.warning("im unread incr failed", exc_info=True)
        return None


def clear_session_unread(
    ops_admin_id: int,
    companion_id: str,
    user_id: int,
) -> int:
    """清零会话未读，同步扣减智能体/运营汇总；返回清掉前的数值。"""
    r = get_redis_client()
    if not r:
        return 0
    cid = str(companion_id)[:8]
    ops = int(ops_admin_id)
    try:
        prev = r.eval(
            _LUA_CLEAR,
            4,
            _session_key(ops, cid),
            _TOTAL_KEY,
            _SESSION_COUNT_KEY,
            _companion_key(ops),
            str(int(user_id)),
            str(ops),
            cid,
        )
        return max(0, int(prev or 0))
    except Exception:
        logger.warning("im unread clear failed", exc_info=True)
        return 0


def clear_companion_unread(ops_admin_id: int, companion_id: str) -> Dict[str, int]:
    """清零某运营下某智能体的全部会话未读。返回 cleared_unread / cleared_sessions。"""
    r = get_redis_client()
    cid = str(companion_id)[:8]
    ops = int(ops_admin_id)
    empty = {"cleared_unread": 0, "cleared_sessions": 0, "companion_id": cid}
    if not r:
        return empty
    try:
        raw = r.eval(
            _LUA_CLEAR_COMPANION,
            4,
            _session_key(ops, cid),
            _TOTAL_KEY,
            _SESSION_COUNT_KEY,
            _companion_key(ops),
            str(ops),
            cid,
        )
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return {
                "cleared_unread": max(0, int(raw[0] or 0)),
                "cleared_sessions": max(0, int(raw[1] or 0)),
                "companion_id": cid,
            }
        return empty
    except Exception:
        logger.warning("im unread clear companion failed", exc_info=True)
        return empty


def clear_companion_unread_for_all_ops(companion_id: str) -> Dict[str, int]:
    """清除所有运营在该智能体下的未读（含汇总键漂移的脏 session key）。"""
    cid = str(companion_id)[:8]
    r = get_redis_client()
    if not r:
        return {"cleared_unread": 0, "cleared_sessions": 0, "companion_id": cid}
    total_u = 0
    total_s = 0
    try:
        ops_ids: set[int] = set()
        for k in (r.hgetall(_TOTAL_KEY) or {}).keys():
            if str(k).isdigit() or str(k) == "0":
                try:
                    ops_ids.add(int(k))
                except (TypeError, ValueError):
                    pass
        # 扫 session key，避免换责后 t 已空但仍残留 s:{ops}:{cid}
        for key in _scan_keys(r, f"im:ops:unread:s:*:{cid}"):
            parsed = _parse_session_key(key)
            if parsed:
                ops_ids.add(parsed[0])
        for ops in ops_ids:
            part = clear_companion_unread(ops, cid)
            total_u += int(part.get("cleared_unread") or 0)
            total_s += int(part.get("cleared_sessions") or 0)
    except Exception:
        logger.warning("im unread clear companion all-ops failed", exc_info=True)
    return {
        "cleared_unread": total_u,
        "cleared_sessions": total_s,
        "companion_id": cid,
    }


def migrate_companion_unread_to_ops(
    companion_id: str,
    target_ops_admin_id: int,
) -> Dict[str, int]:
    """将该智能体下所有 ops（含未分配池 0）的未读合并迁到 target_ops。

    指定责任人：target=真实 ops；取消责任：target=UNASSIGNED_OPS_ADMIN_ID。
    """
    cid = str(companion_id)[:8]
    target = int(target_ops_admin_id)
    empty = {
        "migrated_unread": 0,
        "migrated_sessions": 0,
        "companion_id": cid,
        "target_ops_admin_id": target,
    }
    r = get_redis_client()
    if not r or not cid:
        return empty

    per_user: Dict[int, int] = {}
    try:
        for key in _scan_keys(r, f"im:ops:unread:s:*:{cid}"):
            parsed = _parse_session_key(key)
            if not parsed:
                continue
            entries = r.hgetall(key) or {}
            for uid_s, val_s in entries.items():
                try:
                    uid = int(uid_s)
                    n = int(val_s or 0)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    per_user[uid] = per_user.get(uid, 0) + n
        # 先清光（含 target 自己），再按合计写入 target，避免重复累加
        clear_companion_unread_for_all_ops(cid)
        migrated_sessions = 0
        migrated_unread = 0
        for uid, n in per_user.items():
            cur = incr_session_unread(target, cid, uid, delta=max(1, int(n)))
            if cur is not None:
                migrated_sessions += 1
                migrated_unread += int(n)
        return {
            "migrated_unread": migrated_unread,
            "migrated_sessions": migrated_sessions,
            "companion_id": cid,
            "target_ops_admin_id": target,
        }
    except Exception:
        logger.warning(
            "im unread migrate companion failed companion=%s target=%s",
            cid,
            target,
            exc_info=True,
        )
        return empty


def clear_session_unread_for_all_ops(companion_id: str, user_id: int) -> int:
    """清除所有运营下某 (companion, user) 会话未读；返回合计清掉条数。"""
    cid = str(companion_id)[:8]
    uid = int(user_id)
    r = get_redis_client()
    if not r or not cid or not uid:
        return 0
    cleared = 0
    try:
        ops_ids: set[int] = set()
        for k in (r.hgetall(_TOTAL_KEY) or {}).keys():
            if str(k).isdigit():
                ops_ids.add(int(k))
        for key in _scan_keys(r, f"im:ops:unread:s:*:{cid}"):
            parsed = _parse_session_key(key)
            if parsed:
                ops_ids.add(parsed[0])
        for ops in ops_ids:
            cleared += clear_session_unread(ops, cid, uid)
    except Exception:
        logger.warning(
            "im unread clear session all-ops failed companion=%s user=%s",
            cid,
            uid,
            exc_info=True,
        )
    return cleared


def iter_unread_session_entries(
    *,
    ops_admin_id: Optional[int] = None,
    companion_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """扫描会话 Hash，返回 [{ops_admin_id, companion_id, user_id, count}, ...]。"""
    r = get_redis_client()
    if not r:
        return []
    wanted: Optional[set[str]] = None
    if companion_ids is not None:
        wanted = {str(c)[:8] for c in companion_ids if str(c).strip()}
        if not wanted:
            return []
    out: List[Dict[str, Any]] = []
    try:
        if ops_admin_id is not None:
            pattern = f"im:ops:unread:s:{int(ops_admin_id)}:*"
        else:
            pattern = "im:ops:unread:s:*"
        for key in _scan_keys(r, pattern):
            parsed = _parse_session_key(key)
            if not parsed:
                continue
            ops, cid = parsed
            if wanted is not None and cid not in wanted:
                continue
            entries = r.hgetall(key) or {}
            for uid_s, val_s in entries.items():
                try:
                    n = int(val_s or 0)
                    uid = int(uid_s)
                except (TypeError, ValueError):
                    continue
                if n <= 0:
                    continue
                out.append(
                    {
                        "ops_admin_id": int(ops),
                        "companion_id": cid,
                        "user_id": uid,
                        "count": n,
                    }
                )
    except Exception:
        logger.warning("im unread iter sessions failed", exc_info=True)
    return out


def unread_user_ids_by_companion(
    companion_ids: Iterable[str],
    *,
    ops_admin_id: Optional[int] = None,
) -> Dict[str, List[int]]:
    """companion_id[:8] → 有未读的 user_id 列表（去重，count 降序）。"""
    buckets: Dict[str, Dict[int, int]] = {}
    for item in iter_unread_session_entries(
        ops_admin_id=ops_admin_id, companion_ids=companion_ids
    ):
        cid = str(item["companion_id"])[:8]
        uid = int(item["user_id"])
        buckets.setdefault(cid, {})
        buckets[cid][uid] = buckets[cid].get(uid, 0) + int(item["count"])
    out: Dict[str, List[int]] = {}
    for cid, umap in buckets.items():
        out[cid] = [uid for uid, _n in sorted(umap.items(), key=lambda x: -x[1])]
    return out


def _parse_session_key(key: str) -> Optional[Tuple[int, str]]:
    """im:ops:unread:s:{ops}:{cid} → (ops_admin_id, companion_id)。"""
    prefix = "im:ops:unread:s:"
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix) :]
    if ":" not in rest:
        return None
    ops_s, cid = rest.rsplit(":", 1)
    try:
        return int(ops_s), str(cid)[:8]
    except (TypeError, ValueError):
        return None


def _scan_keys(r, pattern: str) -> List[str]:
    keys: List[str] = []
    cursor = 0
    while True:
        cursor, batch = r.scan(cursor=cursor, match=pattern, count=200)
        keys.extend(batch or [])
        if cursor == 0:
            break
    return keys


def _sum_session_hash(r, key: str) -> Tuple[int, int]:
    """返回 (unread_sum, sessions_with_unread)。"""
    try:
        entries = r.hgetall(key) or {}
    except Exception:
        return 0, 0
    total = 0
    sessions = 0
    for val in entries.values():
        n = max(0, int(val or 0))
        if n > 0:
            total += n
            sessions += 1
    return total, sessions


def recompute_ops_unread(ops_admin_id: int) -> Tuple[int, int, Dict[str, int]]:
    """以会话 Hash 为准重算某运营的未读（summary 可信来源）。"""
    r = get_redis_client()
    if not r:
        return 0, 0, {}
    ops = int(ops_admin_id)
    pattern = f"im:ops:unread:s:{ops}:*"
    total = 0
    session_count = 0
    by_companion: Dict[str, int] = {}
    try:
        for key in _scan_keys(r, pattern):
            parsed = _parse_session_key(key)
            if not parsed or parsed[0] != ops:
                continue
            _ops, cid = parsed
            part_total, part_sessions = _sum_session_hash(r, key)
            if part_total <= 0:
                # 空 hash 或全 0：顺手删掉脏 key
                try:
                    r.delete(key)
                except Exception:
                    pass
                continue
            total += part_total
            session_count += part_sessions
            by_companion[cid] = by_companion.get(cid, 0) + part_total
    except Exception:
        logger.warning("im unread recompute ops failed ops=%s", ops, exc_info=True)
        return get_ops_total_unread(ops)[0], get_ops_total_unread(ops)[1], get_companion_unread_map(ops)
    return total, session_count, by_companion


def recompute_all_ops_unread() -> Tuple[int, int, Dict[str, int]]:
    """超管：扫描全部会话 Hash 重算。"""
    r = get_redis_client()
    if not r:
        return 0, 0, {}
    total = 0
    session_count = 0
    by_companion: Dict[str, int] = {}
    try:
        for key in _scan_keys(r, "im:ops:unread:s:*"):
            parsed = _parse_session_key(key)
            if not parsed:
                continue
            _ops, cid = parsed
            part_total, part_sessions = _sum_session_hash(r, key)
            if part_total <= 0:
                try:
                    r.delete(key)
                except Exception:
                    pass
                continue
            total += part_total
            session_count += part_sessions
            by_companion[cid] = by_companion.get(cid, 0) + part_total
    except Exception:
        logger.warning("im unread recompute all failed", exc_info=True)
        return get_all_ops_unread_summary()[0], get_all_ops_unread_summary()[1], get_all_companion_unread_map()
    return total, session_count, by_companion


def sync_ops_aggregate_keys(
    ops_admin_id: int,
    *,
    total: int,
    session_count: int,
    by_companion: Dict[str, int],
) -> None:
    """将会话级重算结果写回 t/c/n，修复汇总键漂移。"""
    r = get_redis_client()
    if not r:
        return
    ops = str(int(ops_admin_id))
    try:
        pipe = r.pipeline(transaction=False)
        if total > 0:
            pipe.hset(_TOTAL_KEY, ops, int(total))
        else:
            pipe.hdel(_TOTAL_KEY, ops)
        if session_count > 0:
            pipe.hset(_SESSION_COUNT_KEY, ops, int(session_count))
        else:
            pipe.hdel(_SESSION_COUNT_KEY, ops)
        ck = _companion_key(int(ops_admin_id))
        pipe.delete(ck)
        for cid, n in by_companion.items():
            if int(n) > 0:
                pipe.hset(ck, str(cid)[:8], int(n))
        pipe.execute()
    except Exception:
        logger.debug("im unread sync aggregates failed ops=%s", ops, exc_info=True)


def sync_all_aggregate_keys_from_sessions() -> None:
    """超管 summary 后：按各 ops 重算并同步 t/c/n。"""
    r = get_redis_client()
    if not r:
        return
    ops_ids: set[int] = set()
    try:
        for key in _scan_keys(r, "im:ops:unread:s:*"):
            parsed = _parse_session_key(key)
            if parsed:
                ops_ids.add(parsed[0])
        stale_ops = {int(k) for k in (r.hgetall(_TOTAL_KEY) or {}).keys() if str(k).isdigit()}
        ops_ids |= stale_ops
        for ops in ops_ids:
            total, sc, by_c = recompute_ops_unread(ops)
            sync_ops_aggregate_keys(ops, total=total, session_count=sc, by_companion=by_c)
    except Exception:
        logger.debug("im unread sync all aggregates failed", exc_info=True)


def get_ops_total_unread(ops_admin_id: int) -> Tuple[int, int]:
    """返回 (total_unread, session_count)。"""
    r = get_redis_client()
    if not r:
        return 0, 0
    ops = str(int(ops_admin_id))
    try:
        pipe = r.pipeline(transaction=False)
        pipe.hget(_TOTAL_KEY, ops)
        pipe.hget(_SESSION_COUNT_KEY, ops)
        tot, sc = pipe.execute()
        return max(0, int(tot or 0)), max(0, int(sc or 0))
    except Exception:
        logger.debug("im unread total get failed", exc_info=True)
        return 0, 0


def get_companion_unread_map(ops_admin_id: int) -> Dict[str, int]:
    """某运营：companion_id → 未读合计（仅 >0）。"""
    r = get_redis_client()
    if not r:
        return {}
    try:
        raw = r.hgetall(_companion_key(int(ops_admin_id))) or {}
        out: Dict[str, int] = {}
        for cid, val in raw.items():
            n = max(0, int(val or 0))
            if n > 0:
                out[str(cid)[:8]] = n
        return out
    except Exception:
        logger.debug("im unread companion map failed", exc_info=True)
        return {}


def get_all_ops_unread_summary() -> Tuple[int, int]:
    """超管：所有运营合计 (total_unread, session_count)。"""
    r = get_redis_client()
    if not r:
        return 0, 0
    try:
        pipe = r.pipeline(transaction=False)
        pipe.hgetall(_TOTAL_KEY)
        pipe.hgetall(_SESSION_COUNT_KEY)
        totals, counts = pipe.execute()
        total_unread = sum(max(0, int(v or 0)) for v in (totals or {}).values())
        session_count = sum(max(0, int(v or 0)) for v in (counts or {}).values())
        return total_unread, session_count
    except Exception:
        logger.debug("im unread all summary failed", exc_info=True)
        return 0, 0


def get_all_companion_unread_map() -> Dict[str, int]:
    """超管：合并所有运营的智能体未读（同 companion 相加）。"""
    r = get_redis_client()
    if not r:
        return {}
    try:
        totals = r.hgetall(_TOTAL_KEY) or {}
        ops_ids = [int(k) for k in totals.keys() if str(k).isdigit()]
        if not ops_ids:
            return {}
        pipe = r.pipeline(transaction=False)
        for ops in ops_ids:
            pipe.hgetall(_companion_key(ops))
        maps = pipe.execute()
        merged: Dict[str, int] = {}
        for raw in maps:
            for cid, val in (raw or {}).items():
                n = max(0, int(val or 0))
                if n <= 0:
                    continue
                key = str(cid)[:8]
                merged[key] = merged.get(key, 0) + n
        return merged
    except Exception:
        logger.debug("im unread all companion map failed", exc_info=True)
        return {}


def _by_companion_list(cmap: Dict[str, int]) -> List[Dict[str, Any]]:
    items = [
        {"companion_id": cid, "unread_count": int(n)}
        for cid, n in cmap.items()
        if int(n) > 0
    ]
    items.sort(key=lambda x: (-x["unread_count"], x["companion_id"]))
    return items


def build_unread_summary(*, ops_admin_id: Optional[int], is_super: bool) -> Dict[str, Any]:
    """供 GET unread-summary：以会话 Hash 重算，并回写汇总键防漂移。"""
    if is_super:
        total, sc, by_c = recompute_all_ops_unread()
        sync_all_aggregate_keys_from_sessions()
    elif ops_admin_id is None:
        return {
            "total_unread": 0,
            "session_count": 0,
            "by_companion": [],
        }
    else:
        ops = int(ops_admin_id)
        total, sc, by_c = recompute_ops_unread(ops)
        sync_ops_aggregate_keys(ops, total=total, session_count=sc, by_companion=by_c)
    return {
        "total_unread": int(total),
        "session_count": int(sc),
        "by_companion": _by_companion_list(by_c),
    }
