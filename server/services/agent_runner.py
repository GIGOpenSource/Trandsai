"""Agent 统一入口：v1/v2 路由 + fallback。"""
import asyncio
import logging
import os

from services.agent import run_agent as run_pipeline_v1
from services.agent_pipeline_v2 import run_memory_update, run_pipeline_v2

logger = logging.getLogger(__name__)


def run_agent(
    user_input: str,
    profile: dict,
    companion_state: dict,
    memory_text: str,
    knowledge_text: str = "",
    language: str = "zh",
    current_time: str = "",
    user_gender: str = "",
    summary_due: bool = False,
    extract_due: bool = True,
) -> dict:
    version = os.getenv("AGENT_PIPELINE", "v1").lower()
    kwargs = dict(
        user_input=user_input,
        profile=profile,
        companion_state=companion_state,
        memory_text=memory_text,
        knowledge_text=knowledge_text,
        language=language,
        current_time=current_time,
        user_gender=user_gender,
        summary_due=summary_due,
        extract_due=extract_due,
    )
    if version == "v2":
        try:
            return run_pipeline_v2(**kwargs)
        except Exception as e:
            logger.warning("Pipeline v2 failed, fallback to v1: %s", e, exc_info=True)
    return run_pipeline_v1(**kwargs)


async def run_memory_update_async(snapshot: dict) -> dict:
    from core.executor import AGENT_POOL

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(AGENT_POOL, run_memory_update, snapshot)
