import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis

# Redis 配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_PASSWORD = None

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