"""ARQ Agent Worker：从队列取任务、跑 Pipeline、Pub/Sub 回传结果。"""
from __future__ import annotations

import logging
import os

from arq.connections import RedisSettings

from core.agent_queue import (
    AGENT_JOB_TIMEOUT,
    ARQ_REDIS_URL,
    JOB_QUEUE_NAME,
    JOB_QUEUE_PRIORITY,
    AgentJob,
    publish_agent_result,
)
from services.agent_runner import run_agent

logger = logging.getLogger(__name__)

MAX_JOBS = int(os.getenv("AGENT_WORKER_MAX_JOBS", "10"))
WORKER_QUEUE = os.getenv("AGENT_WORKER_QUEUE", JOB_QUEUE_NAME)


async def worker_on_startup(ctx) -> None:
    logger.info("Agent worker started (queue=%s, also configured: %s)", WORKER_QUEUE, JOB_QUEUE_PRIORITY)


async def process_agent_job(ctx, job_dict: dict) -> dict:
    job = AgentJob.from_dict(job_dict)
    logger.info(
        "Processing agent job %s companion=%s user=%s priority=%s",
        job.job_id,
        job.companion_id,
        job.user_id,
        job.priority,
    )
    try:
        result = run_agent(
            user_input=job.user_text_for_agent,
            profile=job.profile,
            companion_state=job.companion_state,
            memory_text=job.memory_text,
            knowledge_text="",
            language=job.language,
            current_time=job.current_time,
            user_gender=job.user_gender,
            summary_due=job.summary_due,
            extract_due=job.extract_due,
        )
        await publish_agent_result(
            job.connection_id,
            job.job_id,
            ok=True,
            result=result,
        )
        return result
    except Exception as e:
        logger.error("Agent job %s failed: %s", job.job_id, e, exc_info=True)
        await publish_agent_result(
            job.connection_id,
            job.job_id,
            ok=False,
            error=str(e),
        )
        raise


class WorkerSettings:
    functions = [process_agent_job]
    redis_settings = RedisSettings.from_dsn(ARQ_REDIS_URL or "redis://localhost:6379/1")
    max_jobs = MAX_JOBS
    job_timeout = int(AGENT_JOB_TIMEOUT)
    queue_name = WORKER_QUEUE
    on_startup = worker_on_startup
