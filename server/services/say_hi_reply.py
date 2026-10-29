"""Say Hi 写入 user 消息后，补一轮智能体回复（按正常对话扣费，不重复写用户消息）。"""
from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def billing_fields_from_charge_meta(charge_meta: Optional[dict]) -> dict:
    """与 WS `free_turn_used.remaining` 对齐的扣费回执字段。"""
    meta = charge_meta or {}
    use_say_hi_daily = bool(meta.get("use_say_hi_daily_free"))
    use_free = bool(meta.get("use_free"))
    remaining = int(meta.get("free_turns_remaining") or 0)
    say_hi_daily_remaining = int(meta.get("say_hi_daily_remaining") or 0)
    out: dict[str, Any] = {
        "use_free": use_free,
        "use_say_hi_daily_free": use_say_hi_daily,
        "free_turns_remaining": remaining,
        "remaining": remaining,
        "say_hi_daily_remaining": say_hi_daily_remaining,
        "credits_charged": int(meta.get("credits_charged") or 0),
    }
    if meta.get("say_hi_daily_limit") is not None:
        out["say_hi_daily_limit"] = int(meta.get("say_hi_daily_limit") or 0)
    if use_free:
        out["free_turn_used"] = True
    if use_say_hi_daily:
        out["say_hi_daily_free_used"] = True
    if meta.get("say_hi_daily_exhausted") is not None:
        out["say_hi_daily_exhausted"] = bool(meta.get("say_hi_daily_exhausted"))
    if meta.get("need_confirm_use_chat_free"):
        out["need_confirm_use_chat_free"] = True
    if meta.get("report_free_quota_reached"):
        out["report_free_quota_reached"] = True
    if meta.get("free_turns_used") is not None:
        out["free_turns_used"] = int(meta.get("free_turns_used") or 0)
    if meta.get("free_turns_limit") is not None:
        out["free_turns_limit"] = int(meta.get("free_turns_limit") or 0)
    return out


def current_free_turns_remaining(
    user_id: int, companion_id: Optional[str] = None
) -> int:
    try:
        from services.credit_service import (
            get_companion_free_turns_remaining,
        )
        from core.database import get_db

        cid = (companion_id or "").strip()[:8]
        with get_db() as db:
            return get_companion_free_turns_remaining(db, int(user_id), cid)
    except Exception:
        logger.exception("[say_hi] read free_turns_remaining failed user=%s", user_id)
        return 0


def enrich_say_hi_response(
    result: dict,
    *,
    user_id: int,
    charge_meta: Optional[dict] = None,
    need_paywall: Optional[bool] = None,
    companion_id: Optional[str] = None,
) -> dict:
    """把免费次数等扣费信息挂到 say_hi 响应（不改业务字段）。

    前端额度判断（不靠 HTTP 状态码）：
      - say_hi_daily_remaining: 今日还剩几次免费 Say Hi
      - say_hi_daily_exhausted: 今日免费 Say Hi 是否已用尽
      - need_confirm_use_chat_free: 日额用尽，需用户确认后才扣聊天免费
      - need_paywall: 是否需要付费拦截（聊天免费+积分都不够才为 true）
    """
    from services.say_hi_quota import get_say_hi_daily_snapshot

    out = dict(result or {})
    daily = get_say_hi_daily_snapshot(user_id)
    if charge_meta is not None:
        out.update(billing_fields_from_charge_meta(charge_meta))
        if out.get("say_hi_daily_remaining") is None:
            out["say_hi_daily_remaining"] = daily["say_hi_daily_remaining"]
        if out.get("say_hi_daily_limit") is None:
            out["say_hi_daily_limit"] = daily["say_hi_daily_limit"]
    else:
        cid = companion_id or (out.get("companion_id") or "")
        remaining = current_free_turns_remaining(user_id, cid)
        out.setdefault("use_free", False)
        out.setdefault("use_say_hi_daily_free", False)
        out.setdefault("free_turns_remaining", remaining)
        out.setdefault("remaining", remaining)
        out.setdefault("credits_charged", 0)
        out.setdefault("say_hi_daily_remaining", daily["say_hi_daily_remaining"])
        out.setdefault("say_hi_daily_limit", daily["say_hi_daily_limit"])

    daily_left = int(out.get("say_hi_daily_remaining") or 0)
    out["say_hi_daily_remaining"] = daily_left
    out["say_hi_daily_exhausted"] = bool(
        out.get("say_hi_daily_exhausted")
        or daily.get("say_hi_daily_exhausted")
        or daily_left <= 0
    )
    out["need_confirm_use_chat_free"] = bool(
        out.get("need_confirm_use_chat_free")
        or (result or {}).get("need_confirm_use_chat_free")
    )
    # 是否拦截：显式传入优先；否则看 quota_blocked；成功路径默认 false
    if need_paywall is not None:
        out["need_paywall"] = bool(need_paywall)
    elif "need_paywall" in out:
        out["need_paywall"] = bool(out.get("need_paywall"))
    else:
        out["need_paywall"] = bool(out.get("quota_blocked"))
    if out["need_confirm_use_chat_free"]:
        out["need_paywall"] = False
    return out


def say_hi_quota_blocked_response(
    *,
    user_id: int,
    companion_id: str,
    charge_detail: Any = None,
) -> dict:
    """额度不足（免费+积分都不够）：仍 HTTP 200，need_paywall=true。"""
    detail = normalize_charge_error_detail(charge_detail)
    body = {
        "is_say_hi": False,
        "message": None,
        "companion_id": (companion_id or "").strip()[:8],
        "already": False,
        "quota_blocked": True,
        "need_paywall": True,
        "reason": detail.get("reason"),
        "need_vip": bool(detail.get("need_vip")),
        "balance": detail.get("balance"),
    }
    return enrich_say_hi_response(
        body,
        user_id=user_id,
        charge_meta=None,
        need_paywall=True,
        companion_id=companion_id,
    )


def normalize_charge_error_detail(detail: Any) -> dict:
    """付费拦截 body，与聊天 WS need_vip 对齐。"""
    if isinstance(detail, dict):
        body = dict(detail)
    else:
        body = {"reason": str(detail or "forbidden")}
    body.setdefault("free_turns_remaining", 0)
    body.setdefault("remaining", 0)
    body.setdefault("say_hi_daily_remaining", 0)
    reason = str(body.get("reason") or "")
    if reason in ("credits_insufficient", "credits_insufficient_concurrent"):
        body["reason"] = "credits_insufficient"
        body.setdefault("need_vip", False)
    elif reason == "no_free_turns_and_no_credits":
        body.setdefault("need_vip", True)
    return body


def schedule_say_hi_assistant_reply(
    *,
    user_id: int,
    companion_id: str,
    user_text: str,
    charge_meta: Optional[dict] = None,
) -> None:
    """后台触发：接管则只通知运营；优先人工则入队等待；否则走 AI 补答。

    charge_meta 由调用方在写入前扣费得到；AI/快回失败时退回。
    """
    cid = (companion_id or "").strip()[:8]
    uid = int(user_id)
    text = (user_text or "").strip()
    if not cid or not uid or not text:
        return

    try:
        from services.im_admin import ensure_user_companion_state

        ensure_user_companion_state(uid, cid)
    except Exception:
        logger.exception("[say_hi] ensure_user_companion_state failed user=%s cid=%s", uid, cid)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[say_hi] no event loop; refund and skip reply user=%s cid=%s", uid, cid)
        _refund(uid, charge_meta)
        return

    loop.create_task(
        _run_say_hi_assistant_reply(uid, cid, text, charge_meta),
        name=f"say-hi-reply-{uid}-{cid}",
    )


def _refund(user_id: int, charge_meta: Optional[dict]) -> None:
    if not charge_meta:
        return
    try:
        if charge_meta.get("use_say_hi_daily_free"):
            from services.say_hi_quota import restore_say_hi_daily_free

            restore_say_hi_daily_free(int(user_id))
            return
        from services.chat_turn import refund_chat_turn

        refund_chat_turn(int(user_id), charge_meta)
    except Exception:
        logger.exception("[say_hi] refund failed user=%s", user_id)


async def _run_say_hi_assistant_reply(
    user_id: int,
    companion_id: str,
    user_text: str,
    charge_meta: Optional[dict] = None,
) -> None:
    uid = int(user_id)
    cid = (companion_id or "").strip()[:8]
    text = (user_text or "").strip()
    ok = False
    try:
        from services.im_admin import (
            broadcast_admin_im,
            is_session_taken_over,
            notify_ops_new_user_message,
        )

        try:
            await notify_ops_new_user_message(uid, cid, text, delta=1)
        except Exception:
            logger.exception("[say_hi] ops notify failed user=%s cid=%s", uid, cid)

        if is_session_taken_over(uid, cid):
            await broadcast_admin_im(
                uid,
                cid,
                {
                    "type": "message",
                    "role": "user",
                    "text": text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source": "say_hi",
                },
            )
            # 与 WS 一致：接管也计一次对话扣费，不退
            ok = True
            logger.info("[say_hi] taken over; charged, skip AI user=%s cid=%s", uid, cid)
            return

        try:
            from services.im_reply_mode import try_defer_human_first

            if try_defer_human_first(uid, cid):
                await broadcast_admin_im(
                    uid,
                    cid,
                    {
                        "type": "message",
                        "role": "user",
                        "text": text,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source": "say_hi_human_first_wait",
                    },
                )
                # 已扣费；到期 catchup 不再扣
                ok = True
                logger.info("[say_hi] human_first deferred user=%s cid=%s", uid, cid)
                return
        except Exception:
            logger.exception("[say_hi] human_first check failed user=%s cid=%s", uid, cid)

        ok = await _deliver_charged_reply(uid, cid, text)
        if ok:
            logger.info("[say_hi] AI reply done user=%s cid=%s", uid, cid)
        else:
            logger.warning("[say_hi] AI reply skipped user=%s cid=%s", uid, cid)
    except Exception:
        logger.exception("[say_hi] assistant reply failed user=%s cid=%s", uid, cid)
        ok = False
    finally:
        if not ok:
            _refund(uid, charge_meta)


async def _typing_keepalive(user_id: int, companion_id: str, stop: asyncio.Event) -> None:
    """生成期间周期性推 typing，与正常聊天页体验一致。"""
    from api.companion_ws import try_push_typing

    while not stop.is_set():
        try:
            try_push_typing(user_id, companion_id)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.5)
            return
        except asyncio.TimeoutError:
            continue


async def _deliver_charged_reply(user_id: int, companion_id: str, pending: str) -> bool:
    """快回或 AI 补答；成功返回 True。不写 user 消息、不二次扣费。

    推送顺序与正常聊天一致：typing → message（客户端只认这两类帧）。
    """
    from core.concurrency import RedisTurnLock, get_user_lock
    from core.database import UserORM, get_db
    from core.executor import AGENT_POOL
    from core.state import get_companion_manager, get_session
    from services.agent_runner import run_agent
    from services.chat_turn import persist_agent_result, prepare_agent_job
    from services.im_admin import broadcast_admin_im, is_session_taken_over
    from services.prompts.resolve import resolve_lang
    from api.companion_ws import try_push_assistant_message, try_push_typing

    uid = int(user_id)
    cid = (companion_id or "").strip()[:8]
    lock = await get_user_lock(uid, cid)
    acquired = False
    typing_stop: Optional[asyncio.Event] = None
    typing_task: Optional[asyncio.Task] = None
    try:
        if isinstance(lock, RedisTurnLock):
            acquired = await lock.acquire(timeout=8.0)
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=8.0)
                acquired = True
            except asyncio.TimeoutError:
                acquired = False
        if not acquired:
            logger.info("[say_hi] reply skipped (lock busy) user=%s cid=%s", uid, cid)
            return False

        if is_session_taken_over(uid, cid):
            return True

        cm = get_companion_manager()
        companion = cm.get(cid, include_deleted=True) if cm else None
        if not companion or bool(getattr(companion.profile, "is_deleted", False)):
            return False
        if cm.is_soft_deleted(cid):
            return False

        # 与 WS 聊天一致：开始生成前先打 typing
        try_push_typing(uid, cid)
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            _typing_keepalive(uid, cid, typing_stop),
            name=f"say-hi-typing-{uid}-{cid}",
        )

        session_meta = get_session(cid, user_id=uid) or {}
        language = resolve_lang(
            body_lang=None,
            accept_language=None,
            session_lang=session_meta.get("lang")
            or getattr(companion.profile, "language", None)
            or "zh",
        )

        try:
            from services.quick_reply import persist_picked_quick_reply, pick_quick_reply

            picked = pick_quick_reply(
                companion_id=cid,
                language=language,
                user_text=pending,
                user_id=uid,
            )
            content = ((picked or {}).get("content") or "").strip()
            if content:
                if typing_stop:
                    typing_stop.set()
                persist_picked_quick_reply(
                    companion,
                    user_id=uid,
                    companion_id=cid,
                    user_text=pending,
                    picked=picked,
                )
                try_push_typing(uid, cid)
                try_push_assistant_message(uid, cid, content)
                await broadcast_admin_im(
                    uid,
                    cid,
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": content,
                        "quick_reply": True,
                        "quick_reply_id": picked.get("id"),
                        "source": "say_hi",
                    },
                )
                return True
        except Exception:
            logger.exception("[say_hi] quick_reply failed user=%s cid=%s", uid, cid)

        user_gender = ""
        try:
            with get_db() as db:
                u = db.query(UserORM).filter(UserORM.id == uid).first()
                if u:
                    user_gender = (getattr(u, "gender", None) or "") or ""
        except Exception:
            pass

        job = await prepare_agent_job(
            companion=companion,
            user_id=uid,
            text=pending,
            language=language,
            user_gender=user_gender,
            tz=(session_meta.get("client_tz") or "") or "",
            tz_offset=session_meta.get("client_tz_offset")
            if isinstance(session_meta.get("client_tz_offset"), int)
            else None,
            connection_id="",
            source="say_hi",
            append_user_message=False,
        )
        job.connection_id = f"say_hi:{job.job_id}"

        if is_session_taken_over(uid, cid):
            return True

        loop = asyncio.get_running_loop()

        def _idle_seconds(j) -> Optional[float]:
            if j.idle_seconds is None or j.idle_seconds < 0:
                return None
            return float(j.idle_seconds)

        result = await loop.run_in_executor(
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
            ),
        )

        if is_session_taken_over(uid, cid):
            return True

        public = persist_agent_result(companion, job, result or {})
        bubbles = list(public.get("bubbles") or [])
        if not bubbles:
            t = (public.get("text") or "").strip()
            if t:
                bubbles = [t]
        if not bubbles:
            return False

        if typing_stop:
            typing_stop.set()

        for bubble in bubbles:
            try_push_typing(uid, cid)
            try_push_assistant_message(uid, cid, bubble)
            await broadcast_admin_im(
                uid,
                cid,
                {
                    "type": "message",
                    "role": "assistant",
                    "text": bubble,
                    "source": "say_hi",
                },
            )
        return True
    except Exception:
        logger.exception("[say_hi] deliver reply failed user=%s cid=%s", uid, cid)
        return False
    finally:
        if typing_stop is not None:
            typing_stop.set()
        if typing_task is not None and not typing_task.done():
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass
        if acquired:
            try:
                lock.release()
            except Exception:
                pass
