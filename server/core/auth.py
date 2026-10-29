import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
import os
from dotenv import load_dotenv

from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env", override=True)
# Redis 配置
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = os.getenv("REDIS_PORT", "")
REDIS_DB = os.getenv("REDIS_DB", "")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
WS_TICKET_TTL = 60

logger = logging.getLogger(__name__)


# Token 过期时间（7天）
TOKEN_EXPIRE_HOURS = 7 * 24

# Redis 连接池
redis_client: Optional[redis.Redis] = None


def init_redis():
  """初始化 Redis 连接（统一用 REDIS_HOST:PORT）。"""
  global redis_client
  import time as _t
  t0 = _t.monotonic()
  host = (REDIS_HOST or "").strip().strip('"')
  print(f"[init_redis] REDIS_HOST={host}:{REDIS_PORT}", flush=True)
  if not host:
      logger.warning("Redis 未配置（REDIS_HOST 为空）")
      return
  redis_client = redis.Redis(
      host=host,
      port=int(REDIS_PORT or 6379),
      db=int(REDIS_DB or 0),
      password=(REDIS_PASSWORD or "").strip().strip('"') or None,
      decode_responses=True,
      socket_connect_timeout=5,
      retry_on_timeout=True,
  )
  try:
      from core.redis_client import allow_writes_if_rdb_blocked
      allow_writes_if_rdb_blocked(redis_client)
  except Exception:
      pass
  print(f"[init_redis] done ({(_t.monotonic()-t0)*1000:.1f}ms)", flush=True)


def get_redis() -> redis.Redis:
  """获取 Redis 连接；失败时重建一次。"""
  global redis_client
  if redis_client is None:
      init_redis()
  return redis_client


def _redis_call(fn):
  """执行 Redis 操作；连接断开时重建再试一次。RDB 停写时放开后再试。"""
  global redis_client
  try:
      return fn(get_redis())
  except (redis.ConnectionError, redis.TimeoutError, OSError):
      redis_client = None
      return fn(get_redis())
  except redis.ResponseError as e:
      from core.redis_client import allow_writes_if_rdb_blocked, is_rdb_misconf
      if not is_rdb_misconf(e):
          raise
      r = get_redis()
      if r is None or not allow_writes_if_rdb_blocked(r, log_change=True):
          raise
      return fn(r)


def generate_token(user_id: int) -> str:
  """生成 Token 并存储到 Redis"""
  salt = secrets.token_hex(16)
  raw = f"{user_id}:{datetime.now(timezone.utc).isoformat()}:{salt}"
  token = hashlib.md5(raw.encode()).hexdigest()

  def _set(r):
      r.setex(f"token:{token}", TOKEN_EXPIRE_HOURS * 3600, str(user_id))
      return token

  return _redis_call(_set)


def verify_token(token: str) -> Optional[int]:
  """验证 Token，返回 user_id 或 None"""
  if not token:
      return None

  def _get(r):
      user_id_str = r.get(f"token:{token}")
      return int(user_id_str) if user_id_str else None

  try:
      return _redis_call(_get)
  except Exception:
      return None


def delete_token(token: str) -> bool:
  """删除 Token（用于登出）"""
  try:
      return _redis_call(lambda r: r.delete(f"token:{token}") > 0)
  except Exception:
      return False


def set_user_banned_flag(user_id: int, banned: bool) -> None:
    """封禁标记：中间件据此拒绝已登录请求；与 users.account_status 同步。"""
    key = f"user_banned:{int(user_id)}"

    def _set(r):
        if banned:
            # 持久标记（无 TTL），与 DB account_status 双写
            r.set(key, "1")
        else:
            r.delete(key)

    try:
        _redis_call(_set)
    except Exception:
        logger.warning("set_user_banned_flag failed user_id=%s", user_id, exc_info=True)


def set_user_deleted_flag(user_id: int, deleted: bool) -> None:
    """注销标记：与 users.account_status=deleted 同步。"""
    key = f"user_deleted:{int(user_id)}"

    def _set(r):
        if deleted:
            r.set(key, "1")
        else:
            r.delete(key)

    try:
        _redis_call(_set)
    except Exception:
        logger.warning("set_user_deleted_flag failed user_id=%s", user_id, exc_info=True)


def normalize_account_status(raw: Optional[str]) -> str:
    """统一账号状态：active / banned / deleted / closed（兼容 ban）。"""
    st = (raw or "active").strip().lower()
    if st in ("ban", "banned"):
        return "banned"
    if st in ("closed", "已销户", "deactivated"):
        return "closed"
    if st in ("deleted", "cancelled", "canceled", "注销"):
        return "deleted"
    if st in ("active", "正常"):
        return "active"
    return "active"


def get_user_account_status(user_id: int) -> str:
    """读取用户账号状态（优先 Redis 标记，再查 DB）。"""
    if not user_id:
        return "active"
    try:
        if _redis_call(lambda r: r.exists(f"user_closed:{int(user_id)}") == 1):
            return "closed"
        if _redis_call(lambda r: r.exists(f"user_deleted:{int(user_id)}") == 1):
            return "deleted"
        if _redis_call(lambda r: r.exists(f"user_banned:{int(user_id)}") == 1):
            return "banned"
    except Exception:
        pass
    try:
        from core.database import UserORM, get_db

        with get_db() as db:
            row = db.query(UserORM.account_status).filter(UserORM.id == int(user_id)).first()
            if not row:
                return "active"
            st = normalize_account_status(row[0])
            if st == "banned":
                set_user_banned_flag(user_id, True)
            elif st == "deleted":
                set_user_deleted_flag(user_id, True)
            elif st == "closed":
                set_user_closed_flag(user_id, True)
            return st
    except Exception:
        logger.warning("get_user_account_status failed user_id=%s", user_id, exc_info=True)
        return "active"


def set_user_closed_flag(user_id: int, closed: bool) -> None:
    """已销户标记：与 users.account_status=closed 同步。"""
    key = f"user_closed:{int(user_id)}"

    def _set(r):
        if closed:
            r.set(key, "1")
        else:
            r.delete(key)

    try:
        _redis_call(_set)
    except Exception:
        logger.warning("set_user_closed_flag failed user_id=%s", user_id, exc_info=True)


def is_user_banned_flag(user_id: int) -> bool:
    """优先 Redis；未命中时回查 DB 并回填 Redis（防 Redis 重启丢标记）。"""
    return get_user_account_status(user_id) == "banned"


def is_user_account_blocked(user_id: int) -> Optional[str]:
    """若账号不可用返回 banned|deleted|closed，否则 None。"""
    st = get_user_account_status(user_id)
    if st in ("banned", "deleted", "closed"):
        return st
    return None


def apply_account_status_flags(user_id: int, status: str) -> None:
    """按目标状态同步 Redis 标记并在封禁/注销/销户时吊销 token。"""
    st = normalize_account_status(status)
    if st == "banned":
        set_user_banned_flag(user_id, True)
        set_user_deleted_flag(user_id, False)
        set_user_closed_flag(user_id, False)
        revoke_user_tokens(user_id)
    elif st == "deleted":
        set_user_deleted_flag(user_id, True)
        set_user_banned_flag(user_id, False)
        set_user_closed_flag(user_id, False)
        revoke_user_tokens(user_id)
    elif st == "closed":
        set_user_closed_flag(user_id, True)
        set_user_deleted_flag(user_id, False)
        set_user_banned_flag(user_id, False)
        revoke_user_tokens(user_id)
    else:
        set_user_banned_flag(user_id, False)
        set_user_deleted_flag(user_id, False)
        set_user_closed_flag(user_id, False)

def revoke_user_tokens(user_id: int) -> int:
    """吊销该用户全部 Redis token（封禁时调用）。返回删除数量。"""
    uid = str(int(user_id))

    def _clear(r):
        deleted = 0
        # token:{md5} -> user_id；量级可控时用 scan
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="token:*", count=200)
            for k in keys or []:
                try:
                    if r.get(k) == uid:
                        deleted += int(r.delete(k) or 0)
                except Exception:
                    continue
            if not cursor:
                break
        return deleted

    try:
        return int(_redis_call(_clear) or 0)
    except Exception:
        logger.warning("revoke_user_tokens failed user_id=%s", user_id, exc_info=True)
        return 0


_WS_TICKET_LUA_GETDEL = """
local v = redis.call('GET', KEYS[1])
if v then
  redis.call('DEL', KEYS[1])
end
return v
"""


def issue_ws_ticket(user_id: int, ttl: int = WS_TICKET_TTL) -> str:
    """签发一次性 WS 票据，写入 Redis，TTL 默认 60 秒。"""
    import logging as _logging
    _log = _logging.getLogger("ws_ticket_debug")

    ticket = secrets.token_urlsafe(32)

    def _set(r):
        r.setex(f"ws_ticket:{ticket}", int(ttl), str(user_id))
        _log.info(
            "[TICKET-DEBUG] issue_ws_ticket: user_id=%s ttl=%s ticket_prefix=%s",
            user_id, ttl, ticket[:8] + "...",
        )
        return ticket

    return _redis_call(_set)


def verify_ws_ticket(ticket: str) -> Optional[int]:
    """校验一次性 WS 票据并删除，返回 user_id 或 None。"""
    import logging as _logging
    _log = _logging.getLogger("ws_ticket_debug")

    if not ticket:
        _log.warning("[TICKET-DEBUG] verify_ws_ticket: 空 ticket")
        return None

    _t_prefix = ticket[:8] + "..." if len(ticket) > 8 else ticket
    _log.info("[TICKET-DEBUG] verify_ws_ticket: 开始验证 prefix=%s len=%s", _t_prefix, len(ticket))

    def _pop(r):
        key = f"ws_ticket:{ticket}"
        # 先检查 key 是否存在（不消费），记录 TTL
        try:
            _ttl = r.ttl(key)
            _log.info("[TICKET-DEBUG]   Redis 检查 key='%s...': ttl=%s (负数=不存在)", key[:30], _ttl)
        except Exception as _e:
            _log.warning("[TICKET-DEBUG]   Redis ttl 查询异常: %s", _e)

        getdel = getattr(r, "getdel", None)
        if callable(getdel):
            _log.info("[TICKET-DEBUG]   使用 getdel (原子读删)")
            raw = getdel(key)
        else:
            _log.info("[TICKET-DEBUG]   使用 Lua eval (回退)")
            raw = r.eval(_WS_TICKET_LUA_GETDEL, 1, key)

        if raw:
            _uid = int(raw)
            _log.info("[TICKET-DEBUG]   ✅ ticket 有效 → user_id=%s", _uid)
            return _uid
        else:
            _log.warning("[TICKET-DEBUG]   ❌ ticket 无效/已消费/已过期 (raw=%s)", repr(raw))
            return None

    try:
        result = _redis_call(_pop)
        _log.info("[TICKET-DEBUG] verify_ws_ticket 最终结果: %s", result)
        return result
    except Exception as _e:
        _log.error("[TICKET-DEBUG] verify_ws_ticket Redis 异常: %s", _e, exc_info=True)
        return None


# ===== 管理员 Token（Redis 存储，24小时有效）=====

ADMIN_TOKEN_EXPIRE_SECONDS = 24 * 3600  # 24 小时


def generate_admin_token(password_hash: str, payload: Optional[dict] = None) -> str:
    """生成管理员 Token 并存储到 Redis，返回 token。payload 含 role_key / user_id / username。"""
    salt = secrets.token_hex(16)
    raw = f"admin:{password_hash}:{datetime.now(timezone.utc).isoformat()}:{salt}"
    token = hashlib.sha256(raw.encode()).hexdigest()[:32]
    data = {"role_key": "admin"}
    if payload:
        data.update({k: v for k, v in payload.items() if v is not None})
    value = json.dumps(data, ensure_ascii=False)

    def _set(r):
        r.setex(f"admin_token:{token}", ADMIN_TOKEN_EXPIRE_SECONDS, value)
        return token

    return _redis_call(_set)


def get_admin_token_payload(token: str) -> dict:
    """读取管理员 Token 载荷；旧 token 值为 admin 时视为超级管理员。"""
    if not token:
        return {}

    def _get(r):
        return r.get(f"admin_token:{token}")

    try:
        raw = _redis_call(_get)
    except Exception:
        return {}
    if not raw:
        return {}
    if raw == "admin":
        return {"role_key": "admin"}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"role_key": "admin"}


def verify_admin_token(token: str) -> bool:
    """验证管理员 Token 是否有效"""
    if not token:
        return False
    try:
        return _redis_call(lambda r: r.exists(f"admin_token:{token}") == 1)
    except Exception:
        return False


def delete_admin_token(token: str) -> bool:
    """删除单个管理员 Token"""
    try:
        return _redis_call(lambda r: r.delete(f"admin_token:{token}") > 0)
    except Exception:
        return False


def clear_all_admin_tokens() -> int:
    """清空所有管理员 Token（修改密码后调用），返回删除数量"""
    def _clear(r):
        keys = r.keys("admin_token:*")
        if keys:
            return r.delete(*keys)
        return 0

    try:
        return _redis_call(_clear)
    except Exception:
        return 0


def revoke_admin_tokens_for_user(user_id: int, *, keep_token: Optional[str] = None) -> int:
    """按管理员 user_id 吊销其 admin_token；keep_token 非空时保留该会话。"""
    if not user_id:
        return 0
    uid = int(user_id)
    keep = (keep_token or "").strip()
    keep_key = f"admin_token:{keep}" if keep else None

    def _revoke(r):
        keys = r.keys("admin_token:*") or []
        deleted = 0
        for key in keys:
            key_s = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
            if keep_key and key_s == keep_key:
                continue
            raw = r.get(key)
            if not raw or raw == "admin":
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            try:
                if int(data.get("user_id") or 0) == uid:
                    r.delete(key)
                    deleted += 1
            except (TypeError, ValueError):
                continue
        return deleted

    try:
        return int(_redis_call(_revoke) or 0)
    except Exception:
        logger.warning("revoke_admin_tokens_for_user failed user_id=%s", uid, exc_info=True)
        return 0