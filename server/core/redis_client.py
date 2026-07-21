"""统一 Redis 客户端（Session / Token / 聊天缓存共用 REDIS_URL）。"""
from __future__ import annotations

import logging
import os
from typing import Optional

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "").strip()

_client: Optional[redis.Redis] = None
_use_redis: Optional[bool] = None


def get_redis_client() -> Optional[redis.Redis]:
    """返回 Redis 连接；未配置或不可用时返回 None。"""
    global _client, _use_redis
    if not REDIS_URL:
        _use_redis = False
        return None
    if _use_redis is False:
        return None
    if _client is None:
        try:
            _client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            _client.ping()
            _use_redis = True
            logger.info("Redis connected: %s", REDIS_URL.split("@")[-1])
        except Exception as e:
            logger.warning("Redis unavailable: %s", e)
            _use_redis = False
            _client = None
            return None
    return _client


def redis_enabled() -> bool:
    return get_redis_client() is not None
