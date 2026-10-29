"""C4：档案任务（Facts/Summary/Evolve）进 Redis 队列，不挡首包。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from core.redis_client import get_redis_client, redis_enabled

logger = logging.getLogger(__name__)

ARCHIVE_QUEUE_KEY = os.getenv("ARCHIVE_QUEUE_KEY", "agent:archive:queue")
ARCHIVE_QUEUE_ENABLED = os.getenv("ARCHIVE_QUEUE", "1").lower() in ("1", "true", "yes")


def archive_queue_enabled() -> bool:
    return ARCHIVE_QUEUE_ENABLED and redis_enabled()


def enqueue_archive_job(job: Dict[str, Any]) -> bool:
    if not archive_queue_enabled():
        return False
    r = get_redis_client()
    if not r:
        return False
    try:
        r.lpush(ARCHIVE_QUEUE_KEY, json.dumps(job, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning("enqueue archive failed: %s", e)
        return False


def pop_archive_job(timeout: int = 2) -> Optional[Dict[str, Any]]:
    if not archive_queue_enabled():
        return None
    r = get_redis_client()
    if not r:
        return None
    try:
        item = r.brpop(ARCHIVE_QUEUE_KEY, timeout=timeout)
        if not item:
            return None
        _, raw = item
        return json.loads(raw)
    except Exception as e:
        logger.warning("pop archive failed: %s", e)
        return None


def archive_queue_depth() -> int:
    r = get_redis_client()
    if not r:
        return 0
    try:
        return int(r.llen(ARCHIVE_QUEUE_KEY))
    except Exception:
        return 0
