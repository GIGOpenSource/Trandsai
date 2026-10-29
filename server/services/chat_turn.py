"""REST / 共用：扣费、组装 AgentJob、落库助手回复。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from core.agent_queue import AgentJob, new_job_id
from core.config import detect_leave_intent, split_response
from core.database import get_db
from core.state import get_session, set_session
from services.agent_utils import build_dialogue_time_context
from services.companion_manager import hydrate_user_affection_turns
from services.dialogue_phase2 import (
    merge_open_threads,
    push_deny_fingerprints,
    push_deny_hook,
    resolve_relation_stage,
    seed_deny_from_recent,
)
from services.relation_card import (
    RELATION_CARD_ENABLED,
    RELATION_CARD_FALLBACK_FULL_MEMORY,
    is_card_empty,
    load_card,
    merge_facts,
    render_card,
    save_card,
    touch_state,
)

logger = logging.getLogger(__name__)


def need_vip_ws_payload(exc: "ChatTurnError", messages: Optional[list] = None) -> dict:
    """超额已落库后仍下发付费墙；messages 为刚写入的用户气泡。"""
    from services.memory import UNPAID_SEND_STATUS

    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if exc.status_code == 402:
        payload: dict[str, Any] = {
            "type": "need_vip",
            "reason": "credits_insufficient",
            "balance": detail.get("balance", 0),
        }
    else:
        payload = {
            "type": "need_vip",
            "reason": detail.get("reason") or "no_free_turns_and_no_credits",
        }
    payload["accepted"] = True
    payload["send_status"] = UNPAID_SEND_STATUS
    payload["messages"] = list(messages or [])
    return payload


def unpaid_http_detail(exc: "ChatTurnError", messages: Optional[list] = None) -> dict:
    from services.memory import UNPAID_SEND_STATUS

    detail = exc.detail if isinstance(exc.detail, dict) else {"reason": str(exc.detail)}
    out = dict(detail)
    out["accepted"] = True
    out["send_status"] = UNPAID_SEND_STATUS
    out["messages"] = list(messages or [])
    return out


class ChatTurnError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _companion_pronoun(companion) -> str:
    g = (getattr(companion.profile, "gender", None) or "").strip().lower()
    if g in ("male", "男", "m"):
        return "他"
    if g in ("female", "女", "f"):
        return "她"
    return "TA"


def _companion_city_str(companion) -> Optional[str]:
    city = (getattr(companion.profile, "city", None) or "").strip()
    return city or None


def charge_chat_turn(user_id: int, companion_id: Optional[str] = None) -> dict:
    """扣免费轮次或积分。失败抛 ChatTurnError(402/403)。

    WebSocket 与 REST 应共用本函数；入队/Agent 失败时调用 refund_chat_turn 退回。
    免费次数按智能体计；返回 extra：use_free、free_turns_remaining、companion_id、credits_charged。
    """
    from services.credit_service import (
        check_chat_permission,
        consume_credits,
        consume_free_turn,
        get_cost_config,
        get_user_vip_status,
    )

    cid = (companion_id or "").strip()[:8] or None
    chat_perm = None
    use_free = False
    extra: dict[str, Any] = {}
    if cid:
        extra["companion_id"] = cid
    with get_db() as db:
        chat_perm = check_chat_permission(db, user_id, cid)
        if chat_perm.get("allowed"):
            if chat_perm.get("use_free"):
                ft = consume_free_turn(db, user_id, cid)
                if ft.get("ok"):
                    use_free = True
                    extra["free_turns_remaining"] = ft.get("free_turns_remaining", 0)
                    extra["free_turns_limit"] = ft.get("free_turns_limit", 0)
                    extra["free_turns_used"] = ft.get("free_turns_used", 0)
                    extra["report_free_quota_reached"] = bool(
                        ft.get("report_free_quota_reached")
                    )
                else:
                    chat_perm["use_free"] = False
            if not use_free:
                cost = get_cost_config().get("chat_cost", 3)
                cr = consume_credits(db, user_id, cost, "chat", "对话扣费")
                if not cr.get("ok"):
                    chat_perm = {
                        "allowed": False,
                        "reason": "credits_insufficient_concurrent",
                        "balance": cr.get("balance", 0),
                    }
                else:
                    extra["credits_charged"] = int(cost)
        db.commit()

    if chat_perm and not chat_perm.get("allowed"):
        reason = chat_perm.get("reason", "")
        if reason in ("credits_insufficient", "credits_insufficient_concurrent"):
            raise ChatTurnError(
                402,
                {
                    "reason": "credits_insufficient",
                    "balance": chat_perm.get("balance", 0),
                },
            )
        if reason in ("no_free_turns_and_no_credits",):
            with get_db() as vip_db:
                vip_status = get_user_vip_status(vip_db, user_id)
            if vip_status.get("is_vip"):
                raise ChatTurnError(
                    402,
                    {
                        "reason": "credits_insufficient",
                        "balance": vip_status.get("effective_balance", 0),
                    },
                )
            raise ChatTurnError(
                403,
                {"reason": "no_free_turns_and_no_credits", "need_vip": True},
            )
        raise ChatTurnError(403, {"reason": reason or "forbidden"})
    extra["use_free"] = use_free
    extra.setdefault("credits_charged", 0)
    return extra


def refund_chat_turn(user_id: int, charge_meta: Optional[dict]) -> None:
    """入队失败或 Agent 异常时退回本轮扣费（免费轮次或积分）。"""
    if not charge_meta:
        return
    try:
        from services.credit_service import refund_credits, restore_free_turn

        with get_db() as db:
            if charge_meta.get("use_free"):
                cid = (charge_meta.get("companion_id") or "").strip()[:8] or None
                restore_free_turn(db, user_id, cid)
            else:
                amount = int(charge_meta.get("credits_charged") or 0)
                if amount > 0:
                    refund_credits(db, user_id, amount, "chat_refund", "对话失败退回")
            db.commit()
    except Exception:
        logger.exception("refund_chat_turn failed user_id=%s", user_id)


async def prepare_agent_job(
    *,
    companion,
    user_id: int,
    text: str,
    language: str,
    user_gender: str = "",
    tz: str = "",
    tz_offset: Optional[int] = None,
    connection_id: str = "",
    source: str = "rest",
    append_user_message: bool = True,
    app_version: str = "",
) -> AgentJob:
    mem = companion.memory_for(user_id)
    # 接管释放补答：用户消息已在接管分支落库，勿再 append
    if append_user_message:
        mem.add_user_message(text)
    has_leave_intent = detect_leave_intent(text)

    memory_tier = os.getenv("AGENT_MEMORY_TIER", "full")
    relation_card_text = ""
    card: dict = {}
    open_threads: list = []
    deny_hooks: list = []
    companion_id = companion.profile.id
    if RELATION_CARD_ENABLED:
        card = load_card(user_id, companion_id)
        if not is_card_empty(card):
            relation_card_text = render_card(card, lang=language)
        open_threads = list(card.get("open_threads") or [])
        deny_hooks = list(card.get("deny_hooks") or [])
    use_card = bool(relation_card_text)
    fallback_full = RELATION_CARD_FALLBACK_FULL_MEMORY and is_card_empty(card)
    memory_text = mem.build_prompt_context(
        query=text,
        tier=memory_tier,
        user_id=user_id,
        relation_card_text=relation_card_text if use_card else "",
        truncate_episodes=use_card and not fallback_full,
        lang=language,
    )
    recent_assistant: list = []
    recent_user: list = []
    try:
        from services.memory import MEMORY_RECENT_MESSAGES, llm_visible_messages
        from services.user_facing_lang import recent_user_texts_from_messages

        recent_msgs = llm_visible_messages(mem.short_term.get_recent(MEMORY_RECENT_MESSAGES))
        for m in recent_msgs:
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                recent_assistant.append(m["content"])
        recent_assistant = recent_assistant[-16:]
        recent_user = recent_user_texts_from_messages(recent_msgs)
    except Exception:
        recent_assistant = []
        recent_user = []
    deny_hooks = seed_deny_from_recent(deny_hooks, recent_assistant)

    session_meta = get_session(companion_id, user_id=user_id) or {}
    idle_seconds = None
    raw_prev_ts = session_meta.get("last_user_ts")
    if raw_prev_ts:
        try:
            prev_ts = datetime.fromisoformat(str(raw_prev_ts).replace("Z", "+00:00"))
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=timezone.utc)
            idle_seconds = (datetime.now(timezone.utc) - prev_ts).total_seconds()
        except Exception:
            idle_seconds = None
    last_extreme_ts = str(session_meta.get("last_extreme_ts") or "")
    reply_lang_override = str(session_meta.get("reply_lang_override") or "")
    session_meta["last_user_ts"] = datetime.now(timezone.utc).isoformat()
    session_meta["lang"] = language
    tz_pay = (tz or "").strip()
    if tz_pay:
        session_meta["client_tz"] = tz_pay[:120]
        try:
            from services.outreach import remember_user_timezone

            remember_user_timezone(user_id, tz_pay[:120])
        except Exception:
            pass
    if tz_offset is not None:
        try:
            session_meta["client_tz_offset"] = int(tz_offset)
        except (TypeError, ValueError):
            pass
    await set_session(companion_id, session_meta, user_id=user_id)

    hydrate_user_affection_turns(companion, user_id)
    tz_iana = tz_pay or (session_meta.get("client_tz") or "").strip() or None
    tz_off = session_meta.get("client_tz_offset")
    current_time = build_dialogue_time_context(
        language=language,
        tz_iana=tz_iana,
        tz_offset_minutes=tz_off if isinstance(tz_off, int) else None,
        companion_city=_companion_city_str(companion),
    )
    summary_due = mem.summary.should_update()

    return AgentJob(
        job_id=new_job_id(),
        connection_id=connection_id,
        companion_id=companion_id,
        user_id=user_id,
        user_text_for_agent=text,
        combined_user_plain=text,
        profile=companion.profile.model_dump(),
        companion_state=companion.state.model_dump(),
        memory_text=memory_text,
        language=language,
        current_time=current_time,
        user_gender=user_gender or "",
        summary_due=summary_due,
        has_leave_intent=has_leave_intent,
        burst_count=1,
        priority=int(os.getenv("PAID_USER_PRIORITY", "0")),
        open_threads=open_threads,
        deny_hooks=deny_hooks,
        recent_assistant=recent_assistant,
        recent_user=recent_user,
        idle_seconds=float(idle_seconds) if idle_seconds is not None else -1.0,
        last_extreme_ts=last_extreme_ts,
        reply_lang_override=reply_lang_override,
        source=source,
        app_version=(app_version or "")[:32],
    )


def persist_agent_result(companion, job: AgentJob, result: dict) -> dict:
    """REST：写入助手气泡与回合状态，返回可供 poll 的 result。"""
    user_id = job.user_id
    companion_id = job.companion_id
    language = job.language
    mem = companion.memory_for(user_id)
    text = (result or {}).get("response") or ""
    bubbles: list[str] = []
    if text:
        for part in split_response(text, max_chars=220):
            cleaned = (part or "").strip()
            if not cleaned:
                continue
            mem.add_assistant_message(cleaned)
            bubbles.append(cleaned)
    actual = "\n\n".join(bubbles) if bubbles else text
    pronoun = _companion_pronoun(companion)
    if actual:
        mem.commit_turn(
            job.combined_user_plain,
            actual,
            pronoun=pronoun,
            partial=False,
        )

    if result:
        companion.state.mood = result.get("mood") or companion.state.mood
        _old_aff = float(getattr(companion.state, "affection", 0) or 0)
        if result.get("affection") is not None:
            companion.state.affection = result["affection"]
        companion.state.turns += 1
        if result.get("evolved_personality"):
            companion.state.evolved_personality = result["evolved_personality"]
        if result.get("evolved_background"):
            companion.state.evolved_background = result["evolved_background"]
        if result.get("evolved_speech_style"):
            companion.state.evolved_speech_style = result["evolved_speech_style"]
        if result.get("new_facts") and not result.get("facts_async_pending"):
            mem.facts.add_facts(result["new_facts"])
        if mem.summary.should_update() and result.get("new_summary"):
            mem.summary.update(result["new_summary"])
            companion.state.summary = result["new_summary"]
        else:
            companion.state.summary = mem.summary.get_summary()

        try:
            from services.outreach import schedule_turn_idle_recall

            schedule_turn_idle_recall(
                user_id=user_id,
                companion_id=companion_id,
                turns=int(companion.state.turns or 0),
                companion_name=getattr(companion.profile, "name", "") or "",
                tz_name=None,
            )
        except Exception:
            pass
        try:
            from services.outreach_phase3 import (
                maybe_schedule_intimacy_event,
                maybe_schedule_memory_milestone,
            )

            _cname = getattr(companion.profile, "name", "") or ""
            maybe_schedule_intimacy_event(
                user_id=user_id,
                companion_id=companion_id,
                old_affection=_old_aff,
                new_affection=float(companion.state.affection or 0),
                companion_name=_cname,
            )
            maybe_schedule_memory_milestone(
                user_id=user_id,
                companion_id=companion_id,
                turns=int(companion.state.turns or 0),
                companion_name=_cname,
            )
        except Exception:
            pass

        if RELATION_CARD_ENABLED:
            c = touch_state(
                load_card(user_id, companion_id),
                mood=companion.state.mood,
                affection=companion.state.affection,
                summary=companion.state.summary,
            )
            if result.get("new_facts") and not result.get("facts_async_pending"):
                c = merge_facts(c, result.get("new_facts") or [], lang=language)
            picked = (result.get("picked_hook") or "").strip()
            if picked:
                c = push_deny_hook(c, picked)
                c = merge_open_threads(c, new_thread=picked)
            c = push_deny_fingerprints(c, actual or text)
            from services.affection_floor import effective_prompt_affection

            stage = (result.get("relation_stage") or "").strip() or resolve_relation_stage(
                int(getattr(companion.state, "turns", 0) or 0),
                effective_prompt_affection(
                    float(getattr(companion.state, "affection", 0) or 0)
                ),
            )
            c["stage"] = stage
            save_card(user_id, companion_id, c)

        companion.save_state(user_id=user_id)

        if "reply_lang_override" in result:
            from services.user_facing_lang import apply_sticky_to_session
            from core.session_store import session_set
            from core import state as _state

            sess = apply_sticky_to_session(
                get_session(companion_id, user_id=user_id) or {},
                result.get("reply_lang_override"),
            )
            key = _state._mem_key(companion_id, user_id)
            _state._companion_sessions[key] = sess.copy()
            session_set(companion_id, sess, user_id=user_id)

    return {
        "text": actual,
        "response": actual,
        "bubbles": bubbles,
        "mood": getattr(companion.state, "mood", None),
        "affection": getattr(companion.state, "affection", None),
        "turns": getattr(companion.state, "turns", None),
    }
