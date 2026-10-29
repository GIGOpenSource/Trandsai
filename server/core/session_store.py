"""Redis-backed session store with in-process fallback."""
import json
import logging
import os
from typing import Optional, Union

from core.redis_client import get_redis_client, redis_enabled  # noqa: F401

logger = logging.getLogger(__name__)

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "86400"))


def _session_key(companion_id: str, user_id: Optional[Union[int, str]] = None) -> str:
    """按用户隔离会话键，避免多端/多用户抢同一 companion session。"""
    if user_id is None or user_id == "":
        return f"session:{companion_id}"
    return f"session:{user_id}:{companion_id}"


def session_get(companion_id: str, user_id: Optional[Union[int, str]] = None) -> dict:
    r = get_redis_client()
    key = _session_key(companion_id, user_id)
    if r:
        try:
            raw = r.get(key)
            if raw:
                return json.loads(raw)
            # 兼容旧键（无 user_id 前缀）
            if user_id is not None:
                legacy = r.get(f"session:{companion_id}")
                if legacy:
                    data = json.loads(legacy)
                    # 仅当旧会话归属同一用户时复用
                    if str(data.get("user_id") or "") in ("", str(user_id)):
                        return data
        except Exception as e:
            logger.warning("Redis session get failed: %s", e)
    return {}


def session_set(
    companion_id: str,
    data: dict,
    user_id: Optional[Union[int, str]] = None,
) -> None:
    r = get_redis_client()
    uid = user_id if user_id is not None else (data or {}).get("user_id")
    key = _session_key(companion_id, uid)
    if r:
        try:
            r.setex(
                key,
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
