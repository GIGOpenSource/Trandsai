"""Redis-backed session store with in-process fallback."""
import json
import logging
import os
from typing import Optional

from core.redis_client import get_redis_client, redis_enabled  # noqa: F401

logger = logging.getLogger(__name__)

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "86400"))


def session_get(companion_id: str) -> dict:
    r = get_redis_client()
    if r:
        try:
            raw = r.get(f"session:{companion_id}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("Redis session get failed: %s", e)
    return {}


def session_set(companion_id: str, data: dict) -> None:
    r = get_redis_client()
    if r:
        try:
            r.setex(
                f"session:{companion_id}",
                SESSION_TTL,
                json.dumps(data, ensure_ascii=False),
            )
            return
        except Exception as e:
            logger.warning("Redis session set failed: %s", e)


def token_cache_get(token: str) -> Optional[int]:
    r = get_redis_client()
    if not r or not token:
        return None
    try:
        val = r.get(f"user_token:{token}")
        return int(val) if val else None
    except Exception:
        return None


def token_cache_set(token: str, user_id: int, ttl_seconds: int) -> None:
    r = get_redis_client()
    if not r or not token:
        return
    try:
        r.setex(f"user_token:{token}", ttl_seconds, str(user_id))
    except Exception as e:
        logger.warning("Redis token cache set failed: %s", e)


def token_cache_delete(token: str) -> None:
    r = get_redis_client()
    if not r or not token:
        return
    try:
        r.delete(f"user_token:{token}")
    except Exception:
        pass
