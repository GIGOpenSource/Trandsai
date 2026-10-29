"""ARQ Agent Worker：从队列取任务、跑 Pipeline、写 job 状态、Pub/Sub 回传。"""
from __future__ import annotations

import logging
import os

from core.agent_queue import (
    AGENT_JOB_TIMEOUT,
    ARQ_REDIS_URL,
    JOB_QUEUE_NAME,
    JOB_QUEUE_PRIORITY,
    AgentJob,
    arq_redis_settings,
    publish_agent_result,
    set_job_status,
)
from services.agent_runner import run_agent

logger = logging.getLogger(__name__)

MAX_JOBS = int(os.getenv("AGENT_WORKER_MAX_JOBS", "10"))
WORKER_QUEUE = os.getenv("AGENT_WORKER_QUEUE", JOB_QUEUE_NAME)


async def worker_on_startup(ctx) -> None:
    logger.info("Agent worker started (queue=%s, also configured: %s)", WORKER_QUEUE, JOB_QUEUE_PRIORITY)


def _idle_seconds(job: AgentJob):
    if job.idle_seconds is None or job.idle_seconds < 0:
        return None
    return float(job.idle_seconds)


async def process_agent_job(ctx, job_dict: dict) -> dict:
    job = AgentJob.from_dict(job_dict)
    logger.info(
        "Processing agent job %s companion=%s user=%s priority=%s source=%s",
        job.job_id,
        job.companion_id,
        job.user_id,
        job.priority,
        job.source,
    )
    set_job_status(
        job.job_id,
        user_id=job.user_id,
        companion_id=job.companion_id,
        status="running",
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
            has_leave_intent=job.has_leave_intent,
            burst_count=job.burst_count,
            open_threads=job.open_threads,
            deny_hooks=job.deny_hooks,
            recent_assistant=job.recent_assistant,
            recent_user=getattr(job, "recent_user", None) or [],
            idle_seconds=_idle_seconds(job),
            last_extreme_ts=job.last_extreme_ts or "",
            reply_lang_override=getattr(job, "reply_lang_override", None) or "",
            user_id=job.user_id,
            app_version=getattr(job, "app_version", None) or "",
        )
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="done",
            result=result,
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
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="failed",
            error=str(e),
        )
        await publish_agent_result(
            job.connection_id,
            job.job_id,
            ok=False,
            error=str(e),
        )
        raise


class WorkerSettings:
    functions = [process_agent_job]
    redis_settings = arq_redis_settings(ARQ_REDIS_URL)
    max_jobs = MAX_JOBS
    job_timeout = int(AGENT_JOB_TIMEOUT)
    queue_name = WORKER_QUEUE
    on_startup = worker_on_startup
