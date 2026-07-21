"""Rate limiting with Redis sliding window and in-memory fallback."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from core.redis_client import get_redis_client

CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT_PER_MIN", "10"))
CHAT_RATE_WINDOW = 60.0

_user_chat_timestamps: Dict[str, Deque[float]] = defaultdict(deque)


def _redis_sliding_window(key: str, limit: int, window_sec: float) -> bool:
    r = get_redis_client()
    if not r:
        return True
    now = time.time()
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, now - window_sec)
    pipe.zcard(key)
    pipe.zadd(key, {str(now): now})
    pipe.expire(key, int(window_sec) + 1)
    _, count, _, _ = pipe.execute()
    if count >= limit:
        r.zrem(key, str(now))
        return False
    return True


def check_chat_rate_limit(user_id: int) -> bool:
    """Return True if user is within chat rate limit."""
    if CHAT_RATE_LIMIT <= 0:
        return True

    redis_key = f"rate:chat:{user_id}"
    if get_redis_client():
        return _redis_sliding_window(redis_key, CHAT_RATE_LIMIT, CHAT_RATE_WINDOW)

    key = str(user_id)
    now = time.monotonic()
    q = _user_chat_timestamps[key]
    while q and now - q[0] > CHAT_RATE_WINDOW:
        q.popleft()
    if len(q) >= CHAT_RATE_LIMIT:
        return False
    q.append(now)
    return True
