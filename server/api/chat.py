"""REST 异步聊天：POST /turns 入队，GET /jobs 轮询。"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core.agent_queue import (
    AGENT_JOB_TIMEOUT,
    enqueue_agent_job,
    get_job_status,
    new_job_id,
    queue_enabled,
    set_job_status,
    wait_agent_result,
)
from core.concurrency import get_user_lock
from core.dependencies import require_permissions
from core.executor import AGENT_POOL
from core.permissions import IsAuthenticated
from core.rate_limit import check_chat_rate_limit
from core.state import get_companion_manager, get_session
from services.agent_runner import run_agent
from services.chat_turn import (
    ChatTurnError,
    charge_chat_turn,
    persist_agent_result,
    prepare_agent_job,
    refund_chat_turn,
    unpaid_http_detail,
)
from services.prompts.resolve import resolve_lang

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["聊天"])


class TurnBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    lang: Optional[str] = None
    user_gender: str = ""
    tz: str = ""
    tz_offset: Optional[int] = None


def _idle_seconds(job) -> Optional[float]:
    if job.idle_seconds is None or job.idle_seconds < 0:
        return None
    return float(job.idle_seconds)


def _public_job(payload: dict) -> dict:
    return {
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "result": payload.get("result"),
        "error": payload.get("error"),
    }


async def _run_job_inprocess(job) -> dict:
    loop = asyncio.get_event_loop()
    set_job_status(
        job.job_id,
        user_id=job.user_id,
        companion_id=job.companion_id,
        status="running",
    )
    return await loop.run_in_executor(
        AGENT_POOL,
        functools.partial(
            run_agent,
            job.user_text_for_agent,
            job.profile,
            job.companion_state,
            job.memory_text,
            "",
            job.language,
            job.current_time,
            job.user_gender,
            job.summary_due,
            job.extract_due,
            job.has_leave_intent,
            job.burst_count,
            job.open_threads,
            job.deny_hooks,
            job.recent_assistant,
            getattr(job, "recent_user", None) or [],
            _idle_seconds(job),
            job.last_extreme_ts,
            getattr(job, "reply_lang_override", None) or "",
            job.user_id,
            getattr(job, "app_version", None) or "",
        ),
    )


async def _complete_rest_turn(
    job, companion, lock, *, enqueued: bool, charge_meta: Optional[dict] = None
) -> None:
    ok = False
    try:
        result = None
        if enqueued:
            payload = await wait_agent_result(
                job.connection_id, job.job_id, timeout=AGENT_JOB_TIMEOUT
            )
            if not payload or not payload.get("ok"):
                err = (payload or {}).get("error") or "queue_failed"
                set_job_status(
                    job.job_id,
                    user_id=job.user_id,
                    companion_id=job.companion_id,
                    status="failed",
                    error=err,
                )
                return
            result = payload.get("result") or {}
        else:
            result = await _run_job_inprocess(job)
        public = persist_agent_result(companion, job, result or {})
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="done",
            result=public,
        )
        ok = True
    except Exception as e:
        logger.exception("REST chat job %s failed", job.job_id)
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="failed",
            error=str(e),
        )
    finally:
        if not ok:
            refund_chat_turn(job.user_id, charge_meta)
        try:
            lock.release()
        except Exception:
            pass


@router.post("/{companion_id}/turns", status_code=202)
async def submit_turn(
    companion_id: str,
    body: TurnBody,
    request: Request,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    if not check_chat_rate_limit(user_id):
        raise HTTPException(status_code=429, detail="rate_limited")

    cm = get_companion_manager()
    companion = cm.get(companion_id, include_deleted=True) if cm else None
    if not companion:
        raise HTTPException(status_code=404, detail="companion_not_found")
    if bool(companion.profile.is_deleted) or cm.is_soft_deleted(companion_id):
        raise HTTPException(status_code=404, detail="companion_deleted")

    lock = await get_user_lock(user_id, companion_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.05)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="turn_in_progress")

    handed_off = False
    charge_meta = None
    try:
        try:
            charge_meta = charge_chat_turn(user_id, companion_id)
        except ChatTurnError as e:
            stored = companion.memory_for(user_id).persist_unpaid_user_messages([text])
            raise HTTPException(
                status_code=e.status_code,
                detail=unpaid_http_detail(e, stored),
            ) from e

        session_lang = (get_session(companion_id, user_id=user_id) or {}).get("lang")
        language = resolve_lang(
            body_lang=body.lang,
            accept_language=accept_language,
            session_lang=session_lang,
        )
        try:
            from services.im_admin import is_session_taken_over
            from services.quick_reply import persist_picked_quick_reply, pick_quick_reply

            taken = is_session_taken_over(user_id, companion_id)
            picked = None if taken else pick_quick_reply(
                companion_id=companion_id,
                language=language,
                user_text=text,
                user_id=user_id,
            )
            content = ((picked or {}).get("content") or "").strip()
            if content:
                companion.memory_for(user_id).add_user_message(text)
                persist_picked_quick_reply(
                    companion,
                    user_id=user_id,
                    companion_id=companion_id,
                    user_text=text,
                    picked=picked,
                )
                job_id = new_job_id()
                public = {
                    "text": content,
                    "response": content,
                    "bubbles": [content],
                    "quick_reply": True,
                    "quick_reply_id": picked.get("id"),
                    "mood": getattr(companion.state, "mood", None),
                    "affection": getattr(companion.state, "affection", None),
                    "turns": getattr(companion.state, "turns", None),
                }
                set_job_status(
                    job_id,
                    user_id=user_id,
                    companion_id=companion_id,
                    status="done",
                    result=public,
                )
                handed_off = True
                try:
                    lock.release()
                except Exception:
                    pass
                return {"job_id": job_id, "status": "done"}
        except Exception:
            logger.exception(
                "REST quick_reply branch failed user=%s companion=%s",
                user_id,
                companion_id,
            )
        from services.version_prompt import log_app_version

        app_version = log_app_version(
            "rest_turn",
            headers=request.headers,
            query=request.query_params,
            incoming=x_app_version or "",
            user_id=user_id,
        ) or (x_app_version or "")
        job = await prepare_agent_job(
            companion=companion,
            user_id=user_id,
            text=text,
            language=language,
            user_gender=body.user_gender or "",
            tz=body.tz or "",
            tz_offset=body.tz_offset,
            connection_id="",
            source="rest",
            app_version=app_version,
        )
        job.connection_id = f"rest:{job.job_id}"
        set_job_status(
            job.job_id,
            user_id=job.user_id,
            companion_id=job.companion_id,
            status="queued",
        )
        enqueued = False
        if queue_enabled():
            enqueued = await enqueue_agent_job(job)
        asyncio.create_task(
            _complete_rest_turn(
                job, companion, lock, enqueued=enqueued, charge_meta=charge_meta
            )
        )
        handed_off = True
        return {"job_id": job.job_id, "status": "queued"}
    finally:
        if not handed_off:
            refund_chat_turn(user_id, charge_meta)
            try:
                lock.release()
            except Exception:
                pass


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    user_id: int = Depends(require_permissions(IsAuthenticated)),
):
    payload = get_job_status(job_id)
    if not payload:
        raise HTTPException(status_code=404, detail="job_not_found")
    owner = payload.get("user_id")
    if owner is not None and int(owner) != int(user_id):
        raise HTTPException(status_code=403, detail="forbidden")
    return _public_job(payload)
