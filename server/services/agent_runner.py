"""Agent 统一入口：v1/v2 路由 + fallback + 回合耗时汇总。"""
import asyncio
import logging
import os
import time
from typing import Optional

from services.agent import run_agent as run_pipeline_v1
from services.agent_pipeline_v2 import run_memory_update, run_pipeline_v2
from services.llm import turn_timing

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
    has_leave_intent: bool = False,
    burst_count: int = 1,
    open_threads: list = None,
    deny_hooks: list = None,
    recent_assistant: list = None,
    recent_user: list = None,
    idle_seconds: float = None,
    last_extreme_ts: str = "",
    reply_lang_override: str = "",
    user_id: Optional[int] = None,
    app_version: str = "",
) -> dict:
    from services.affection_floor import bind_prompt_ads_user, reset_prompt_ads_user
    from services.version_prompt import (
        bind_app_version,
        bind_registered_at,
        bind_version_user_id,
        hydrate_user_version_context,
        log_app_version,
        reset_app_version,
        reset_registered_at,
        reset_version_user_id,
    )

    ads_token = bind_prompt_ads_user(user_id=user_id)
    resolved_ver, registered_at = hydrate_user_version_context(user_id, app_version)
    ver_token = bind_app_version(resolved_ver)
    reg_token = bind_registered_at(registered_at)
    uid_token = bind_version_user_id(user_id)
    log_app_version("agent_turn", incoming=resolved_ver or app_version or "", user_id=user_id)
    try:
        return _run_agent_inner(
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
            has_leave_intent=has_leave_intent,
            burst_count=burst_count,
            open_threads=open_threads,
            deny_hooks=deny_hooks,
            recent_assistant=recent_assistant,
            recent_user=recent_user,
            idle_seconds=idle_seconds,
            last_extreme_ts=last_extreme_ts,
            reply_lang_override=reply_lang_override,
        )
    finally:
        reset_version_user_id(uid_token)
        reset_registered_at(reg_token)
        reset_app_version(ver_token)
        reset_prompt_ads_user(ads_token)


def _run_agent_inner(
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
    has_leave_intent: bool = False,
    burst_count: int = 1,
    open_threads: list = None,
    deny_hooks: list = None,
    recent_assistant: list = None,
    recent_user: list = None,
    idle_seconds: float = None,
    last_extreme_ts: str = "",
    reply_lang_override: str = "",
) -> dict:
    # 默认 v2（意图 JSON + 质量改写）；失败仍 fallback v1
    version = os.getenv("AGENT_PIPELINE", "v2").lower()
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
        has_leave_intent=has_leave_intent,
        burst_count=burst_count,
        open_threads=open_threads or [],
        deny_hooks=deny_hooks or [],
        recent_assistant=recent_assistant or [],
        recent_user=recent_user or [],
        idle_seconds=idle_seconds,
        last_extreme_ts=last_extreme_ts or "",
        reply_lang_override=reply_lang_override or "",
    )
    turn_timing.begin_turn({
        "pipeline": version,
        "language": language,
        "turns": int((companion_state or {}).get("turns") or 0),
        "burst_count": int(burst_count or 1),
        "summary_due": bool(summary_due),
        "user_chars": len(user_input or ""),
        "memory_chars": len(memory_text or ""),
    })
    t0 = time.perf_counter()
    try:
        if version == "v2":
            try:
                result = run_pipeline_v2(**kwargs)
            except Exception as e:
                logger.warning("Pipeline v2 failed, fallback to v1: %s", e, exc_info=True)
                result = run_pipeline_v1(**kwargs)
        else:
            result = run_pipeline_v1(**kwargs)
    except Exception:
        turn_timing.end_turn()
        raise

    timing = turn_timing.end_turn()
    timing["agent_wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if isinstance(result, dict):
        # v1 可能已塞过 timing；以外层汇总为准（含 runner wall）
        prev = result.get("timing") if isinstance(result.get("timing"), dict) else {}
        merged = {**prev, **timing}
        if prev.get("stages_ms") or timing.get("stages_ms"):
            merged["stages_ms"] = {
                **(prev.get("stages_ms") or {}),
                **(timing.get("stages_ms") or {}),
            }
        if prev.get("llm_calls") or timing.get("llm_calls"):
            # prefer the fuller list (runner end_turn owns the thread-local list)
            merged["llm_calls"] = timing.get("llm_calls") or prev.get("llm_calls") or []
        result["timing"] = merged
        logger.info(
            "agent_runner pipeline=%s light=%s wall_ms=%s llm_calls=%s stages=%s",
            version,
            result.get("light_path"),
            merged.get("agent_wall_ms"),
            [
                {
                    "node": c.get("node"),
                    "wait_ms": c.get("wait_ms"),
                    "call_ms": c.get("call_ms"),
                    "model": c.get("model"),
                }
                for c in (merged.get("llm_calls") or [])
            ],
            merged.get("stages_ms"),
        )
    return result



async def run_memory_update_async(snapshot: dict) -> dict:
    from core.executor import AGENT_POOL

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(AGENT_POOL, run_memory_update, snapshot)
