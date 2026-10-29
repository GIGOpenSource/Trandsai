"""Redis 客户端。

Session / 限流 / Agent jobs 使用 REDIS_URL（本模块）。
登录 Token 等认证数据可能单独走 REDIS_HOST/PORT（core.auth），
当 REDIS_HOST 为空且 REDIS_URL 已设置时，auth 也会回退到 REDIS_URL。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import redis

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "").strip().strip('"')
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379") or 6379)
REDIS_PASSWORD = (os.getenv("REDIS_PASSWORD", "") or "").strip().strip('"')
REDIS_DB = int(os.getenv("REDIS_DB", "0") or 0)
REDIS_MAX_CONNECTIONS = max(1, int(os.getenv("REDIS_MAX_CONNECTIONS", "32") or 32))
_RETRY_AFTER_S = 5.0

_pool: Optional[redis.ConnectionPool] = None
_client: Optional[redis.Redis] = None
_use_redis: Optional[bool] = None
_last_fail_ts: float = 0.0


def _build_redis_client() -> Optional[redis.Redis]:
    """构建 Redis 客户端（连接池复用，避免每 worker 无界建连）。"""
    global _pool
    if not REDIS_HOST:
        return None
    if _pool is None:
        _pool = redis.ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            retry_on_timeout=True,
            max_connections=REDIS_MAX_CONNECTIONS,
        )
    return redis.Redis(connection_pool=_pool)


def is_rdb_misconf(exc: BaseException) -> bool:
    return isinstance(exc, redis.ResponseError) and "MISCONF" in str(exc)


def allow_writes_if_rdb_blocked(r: redis.Redis, *, log_change: bool = False) -> bool:
    """RDB 快照失败时默认停写。关掉 stop-writes-on-bgsave-error，登录 token 才能 setex。"""
    try:
        r.config_set("stop-writes-on-bgsave-error", "no")
        if log_change:
            logger.warning("Redis stop-writes-on-bgsave-error disabled after RDB persist failure")
        return True
    except Exception as e:
        logger.warning("Cannot disable Redis stop-writes-on-bgsave-error: %s", e)
        return False


def reset_redis_client() -> None:
    """连接异常时重置客户端，下次 get 会重建连接池。"""
    global _pool, _client, _use_redis, _last_fail_ts
    _client = None
    _pool = None
    _use_redis = None
    _last_fail_ts = 0.0


def get_redis_client() -> Optional[redis.Redis]:
    """返回 Redis 连接；未配置或暂时不可用时返回 None（失败后约 5s 再试）。"""
    global _client, _use_redis, _last_fail_ts
    now = time.monotonic()
    if _client is not None:
        return _client
    if _last_fail_ts and (now - _last_fail_ts) < _RETRY_AFTER_S:
        return None
    try:
        _client = _build_redis_client()
        if _client is None:
            return None
        _client.ping()
        allow_writes_if_rdb_blocked(_client)
        _use_redis = True
        _last_fail_ts = 0.0
        logger.info(
            "Redis connected: %s:%s pool_max=%s",
            REDIS_HOST,
            REDIS_PORT,
            REDIS_MAX_CONNECTIONS,
        )
    except Exception as e:
        logger.warning("Redis unavailable: %s", e)
        reset_redis_client()
        _last_fail_ts = now
        return None
    return _client


def redis_enabled() -> bool:
    return get_redis_client() is not None
