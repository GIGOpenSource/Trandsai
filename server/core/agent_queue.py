"""Agent 任务队列（阶段三）：ARQ + Redis Pub/Sub 回传结果。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

ARQ_REDIS_URL = (
    os.getenv("ARQ_REDIS_URL", "").strip()
    or os.getenv("REDIS_URL", "").strip()
)
AGENT_QUEUE_ENABLED = os.getenv("AGENT_QUEUE", "").lower() in ("1", "true", "yes")
AGENT_JOB_TIMEOUT = float(os.getenv("AGENT_JOB_TIMEOUT", "120"))
RESULT_CHANNEL_PREFIX = "agent:ws:"
JOB_QUEUE_NAME = "agent"
JOB_QUEUE_PRIORITY = "agent:priority"


@dataclass
class AgentJob:
    job_id: str
    connection_id: str
    companion_id: str
    user_id: int
    user_text_for_agent: str
    combined_user_plain: str
    profile: dict
    companion_state: dict
    memory_text: str
    language: str
    current_time: str
    user_gender: str
    summary_due: bool
    extract_due: bool = True
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentJob:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def queue_enabled() -> bool:
    return AGENT_QUEUE_ENABLED and bool(ARQ_REDIS_URL)


def new_job_id() -> str:
    return str(uuid.uuid4())


def result_channel(connection_id: str) -> str:
    return f"{RESULT_CHANNEL_PREFIX}{connection_id}"


def _queue_name_for_priority(priority: int) -> str:
    return JOB_QUEUE_PRIORITY if priority > 0 else JOB_QUEUE_NAME


# 复用连接：每消息新建 ARQ pool / Redis client 会拖慢热路径并耗尽连接
_arq_pool = None
_arq_pool_lock = asyncio.Lock()
_aio_client = None


async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is not None:
        return _arq_pool
    async with _arq_pool_lock:
        if _arq_pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            _arq_pool = await create_pool(RedisSettings.from_dsn(ARQ_REDIS_URL))
    return _arq_pool


def _get_aio_client():
    global _aio_client
    if _aio_client is None:
        import redis.asyncio as aioredis

        _aio_client = aioredis.from_url(ARQ_REDIS_URL, decode_responses=True)
    return _aio_client


async def enqueue_agent_job(job: AgentJob) -> bool:
    if not queue_enabled():
        return False
    try:
        pool = await _get_arq_pool()
        await pool.enqueue_job(
            "process_agent_job",
            job.to_dict(),
            _queue_name=_queue_name_for_priority(job.priority),
            _job_id=job.job_id,
        )
        return True
    except Exception as e:
        global _arq_pool
        _arq_pool = None
        logger.warning("Agent job enqueue failed, fallback to direct: %s", e)
        return False


def agent_queue_depth() -> int:
    """Best-effort ARQ queue depth (default + priority queues)."""
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        if not r:
            return 0
        total = 0
        for q in (JOB_QUEUE_NAME, JOB_QUEUE_PRIORITY):
            total += int(r.llen(f"arq:queue:{q}") or 0)
        return total
    except Exception:
        return 0


async def wait_agent_result(
    connection_id: str,
    job_id: str,
    timeout: float | None = None,
) -> Optional[dict]:
    """Wait for worker Pub/Sub on the WS connection channel."""
    if not ARQ_REDIS_URL:
        return None
    timeout = timeout if timeout is not None else AGENT_JOB_TIMEOUT
    channel = result_channel(connection_id)

    try:
        client = _get_aio_client()
    except ImportError:
        logger.warning("redis asyncio unavailable for agent result wait")
        return None

    pubsub = client.pubsub()
    await pubsub.subscribe(channel)

    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Agent job %s on %s timed out after %.0fs",
                    job_id,
                    connection_id,
                    timeout,
                )
                return None
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=min(remaining, 5.0),
            )
            if msg is None:
                continue
            if msg.get("type") != "message":
                continue
            try:
                payload = json.loads(msg["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            if payload.get("job_id") == job_id:
                return payload
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            pass


async def publish_agent_result(
    connection_id: str,
    job_id: str,
    ok: bool,
    result: dict | None = None,
    error: str = "",
) -> None:
    """Worker side: publish to connection-scoped channel."""
    if not ARQ_REDIS_URL:
        return
    try:
        client = _get_aio_client()
        payload = json.dumps(
            {"job_id": job_id, "ok": ok, "result": result, "error": error},
            ensure_ascii=False,
        )
        await client.publish(result_channel(connection_id), payload)
    except Exception as e:
        logger.error(
            "Failed to publish agent result for job %s conn %s: %s",
            job_id,
            connection_id,
            e,
        )
