"""Agent 任务队列：ARQ 入队 + Redis job 状态（可供 REST poll）+ Pub/Sub 回传 WS。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from urllib.parse import quote, unquote, urlparse

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


def arq_redis_settings(url: str | None = None):
    """Parse ARQ Redis DSN and decode user/password (%40 / %23 etc.)."""
    from arq.connections import RedisSettings

    raw = (url or ARQ_REDIS_URL or "redis://localhost:6379/1").strip()
    parsed = urlparse(raw)
    path = (parsed.path or "/0").lstrip("/")
    database = int(path.split("/")[0] or 0)
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if username == "":
        username = None
    return RedisSettings(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        database=database,
        username=username,
        password=password,
    )

_REDIS_HOST_RAW = os.getenv("REDIS_HOST", "").strip().strip('"')
_REDIS_PORT_RAW = int(os.getenv("REDIS_PORT", "6379") or 6379)
_REDIS_PASSWORD_RAW = (os.getenv("REDIS_PASSWORD", "") or "").strip().strip('"')

if _REDIS_HOST_RAW:
    # 密码含 @/# 时必须 URL 编码，否则 host 会被解析成「2026」之类
    if _REDIS_PASSWORD_RAW:
        _pwd = f":{quote(_REDIS_PASSWORD_RAW, safe='')}@"
    else:
        _pwd = ""
    ARQ_REDIS_URL = f"redis://{_pwd}{_REDIS_HOST_RAW}:{_REDIS_PORT_RAW}/1"
else:
    ARQ_REDIS_URL = ""
_raw_queue = os.getenv("AGENT_QUEUE", "").strip().lower()
if _raw_queue in ("0", "false", "no"):
    AGENT_QUEUE_ENABLED = False
elif _raw_queue in ("1", "true", "yes"):
    AGENT_QUEUE_ENABLED = True
else:
    # 未显式开启则默认关闭：避免「能入队但无 agent-worker」时干等超时、消息不回
    AGENT_QUEUE_ENABLED = False
AGENT_JOB_TIMEOUT = float(os.getenv("AGENT_JOB_TIMEOUT", "120"))
RESULT_CHANNEL_PREFIX = "agent:ws:"
JOB_QUEUE_NAME = "agent"
JOB_QUEUE_PRIORITY = "agent:priority"
JOB_STATUS_PREFIX = "agent:job:"
JOB_STATUS_TTL = int(os.getenv("AGENT_JOB_TTL", "600"))

_JOB_MEM: dict[str, dict[str, Any]] = {}


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
    has_leave_intent: bool = False
    burst_count: int = 1
    open_threads: list = field(default_factory=list)
    deny_hooks: list = field(default_factory=list)
    recent_assistant: list = field(default_factory=list)
    recent_user: list = field(default_factory=list)
    idle_seconds: float = -1.0
    last_extreme_ts: str = ""
    reply_lang_override: str = ""
    source: str = "ws"
    app_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentJob:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def queue_enabled() -> bool:
    # 无独立 agent-worker 时入队只会干等超时；默认关闭。需要时设 AGENT_QUEUE=1 并启动 worker。
    return False
    # return AGENT_QUEUE_ENABLED and bool(ARQ_REDIS_URL)


def new_job_id() -> str:
    return str(uuid.uuid4())


def result_channel(connection_id: str) -> str:
    return f"{RESULT_CHANNEL_PREFIX}{connection_id}"


def _queue_name_for_priority(priority: int) -> str:
    return JOB_QUEUE_PRIORITY if priority > 0 else JOB_QUEUE_NAME


# 复用连接：每消息新建 ARQ pool / Redis client 会拖慢热路径并耗尽连接
_arq_pool = None
_arq_pool_lock: Optional[asyncio.Lock] = None
_aio_client = None


def _get_arq_pool_lock() -> asyncio.Lock:
    """延迟创建 Lock，避免 Python 3.9 在无 event loop 时 import 失败。"""
    global _arq_pool_lock
    if _arq_pool_lock is None:
        _arq_pool_lock = asyncio.Lock()
    return _arq_pool_lock


async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is not None:
        return _arq_pool
    async with _get_arq_pool_lock():
        if _arq_pool is None:
            from arq import create_pool

            _arq_pool = await create_pool(arq_redis_settings(ARQ_REDIS_URL))
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
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="queued",
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


def set_job_status(
    job_id: str,
    *,
    user_id: int,
    companion_id: str,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> dict:
    payload = {
        "job_id": job_id,
        "user_id": int(user_id),
        "companion_id": companion_id,
        "status": status,
        "result": result,
        "error": error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    prev = _JOB_MEM.get(job_id) or {}
    r = get_redis_client()
    if r:
        try:
            raw = r.get(f"{JOB_STATUS_PREFIX}{job_id}")
            if raw:
                prev = json.loads(raw) or prev
        except Exception:
            pass
    if prev.get("created_at"):
        payload["created_at"] = prev["created_at"]
    _JOB_MEM[job_id] = payload
    if r:
        try:
            r.setex(
                f"{JOB_STATUS_PREFIX}{job_id}",
                JOB_STATUS_TTL,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            logger.warning("job status redis write failed for %s", job_id, exc_info=True)
    return payload


def get_job_status(job_id: str) -> Optional[dict]:
    r = get_redis_client()
    if r:
        try:
            raw = r.get(f"{JOB_STATUS_PREFIX}{job_id}")
            if raw:
                return json.loads(raw)
        except Exception:
            logger.warning("job status redis read failed for %s", job_id, exc_info=True)
    return _JOB_MEM.get(job_id)
