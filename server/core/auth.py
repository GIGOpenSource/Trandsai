import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis

# Redis 配置
REDIS_HOST ="101.32.179.223"
REDIS_PORT = 6379
MAX_CONNECTIONS=20
REDIS_DB = 0
REDIS_PASSWORD ="Redis@2026#0119"

# Token 过期时间（7天）
TOKEN_EXPIRE_HOURS = 7 * 24

# Redis 连接池
redis_client: Optional[redis.Redis] = None


def init_redis():
  """初始化 Redis 连接"""
  global redis_client
  redis_client = redis.Redis(
      host=REDIS_HOST,
      port=REDIS_PORT,
      db=REDIS_DB,
      password=REDIS_PASSWORD,
      decode_responses=True,
      socket_connect_timeout=5,
      retry_on_timeout=True,
  )


def get_redis() -> redis.Redis:
  """获取 Redis 连接"""
  global redis_client
  if redis_client is None:
      init_redis()
  return redis_client


def generate_token(user_id: int) -> str:
  """生成 Token 并存储到 Redis"""
  salt = secrets.token_hex(16)
  raw = f"{user_id}:{datetime.now(timezone.utc).isoformat()}:{salt}"
  token = hashlib.md5(raw.encode()).hexdigest()

  r = get_redis()
  r.setex(
      f"token:{token}",
      TOKEN_EXPIRE_HOURS * 3600,
      str(user_id)
  )
  return token


def verify_token(token: str) -> Optional[int]:
  """验证 Token，返回 user_id 或 None"""
  if not token:
      return None

  try:
      r = get_redis()
      user_id_str = r.get(f"token:{token}")
      if user_id_str:
          return int(user_id_str)
  except Exception:
      pass
  return None


def delete_token(token: str) -> bool:
  """删除 Token（用于登出）"""
  try:
      r = get_redis()
      return r.delete(f"token:{token}") > 0
  except Exception:
      return False


# ===== 管理员 Token（Redis 存储，24小时有效）=====

ADMIN_TOKEN_EXPIRE_SECONDS = 24 * 3600  # 24 小时


def generate_admin_token(password_hash: str) -> str:
    """生成管理员 Token 并存储到 Redis，返回 token"""
    salt = secrets.token_hex(16)
    raw = f"admin:{password_hash}:{datetime.now(timezone.utc).isoformat()}:{salt}"
    token = hashlib.sha256(raw.encode()).hexdigest()[:32]

    r = get_redis()
    r.setex(
        f"admin_token:{token}",
        ADMIN_TOKEN_EXPIRE_SECONDS,
        "admin"
    )
    return token


def verify_admin_token(token: str) -> bool:
    """验证管理员 Token 是否有效"""
    if not token:
        return False
    try:
        r = get_redis()
        return r.exists(f"admin_token:{token}") == 1
    except Exception:
        return False


def delete_admin_token(token: str) -> bool:
    """删除单个管理员 Token"""
    try:
        r = get_redis()
        return r.delete(f"admin_token:{token}") > 0
    except Exception:
        return False


def clear_all_admin_tokens() -> int:
    """清空所有管理员 Token（修改密码后调用），返回删除数量"""
    try:
        r = get_redis()
        keys = r.keys("admin_token:*")
        if keys:
            return r.delete(*keys)
        return 0
    except Exception:
        return 0