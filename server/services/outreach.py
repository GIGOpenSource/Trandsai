"""
运营触达（Phase 1 + Phase 2 + Phase 3）：
- Phase1：Moment 点赞/评论后延迟私聊
- Phase2：轮次中断召回、额度用尽、付费后召回、自建欢迎语、角色 tier
- Phase3：沉默召回 / 优先级 / 事件触达（亲密度·记忆·纪念日）/ 屏蔽
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func

from core.database import (
    CompanionORM,
    MomentORM,
    OutreachJobORM,
    ShortTermMessageORM,
    SystemNotificationORM,
    UserCompanionStateORM,
    UserCreditORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from core.log_util import pipeline_log
from services.outreach_phase3 import (
    JOB_ANNIVERSARY,
    JOB_INTIMACY_UP,
    JOB_MEMORY_MILESTONE,
    JOB_SILENCE_D1,
    JOB_SILENCE_D14,
    JOB_SILENCE_D3,
    JOB_SILENCE_D7,
    PHASE3_JOB_TYPES,
)

logger = logging.getLogger(__name__)


def _is_outreach_blocked(db, user_id: int, companion_id: str) -> bool:
    from services.outreach_phase3 import is_outreach_blocked

    return is_outreach_blocked(db, user_id, companion_id)


def _is_outreach_disliked(db, user_id: int, companion_id: str) -> bool:
    from services.outreach_phase3 import is_outreach_disliked

    return is_outreach_disliked(db, user_id, companion_id)

JOB_LIKE_DM = "like_dm"
JOB_COMMENT_DM = "comment_dm"
JOB_TURN_RECALL_LT3 = "turn_recall_lt3"
JOB_TURN_RECALL_4_9 = "turn_recall_4_9"
JOB_QUOTA_EXHAUST = "quota_exhaust"
JOB_POST_PAY_RECALL = "post_pay_recall"
JOB_CREATE_WELCOME = "create_welcome"
JOB_MSG_STRATEGY = "msg_strategy"

_GREETING_JOB_TYPES = (JOB_LIKE_DM, JOB_COMMENT_DM)
_RECALL_JOB_TYPES = (
    JOB_TURN_RECALL_LT3,
    JOB_TURN_RECALL_4_9,
    JOB_QUOTA_EXHAUST,
    JOB_POST_PAY_RECALL,
)
_ALL_OUTREACH_TYPES = (
    _GREETING_JOB_TYPES + _RECALL_JOB_TYPES + (JOB_CREATE_WELCOME, JOB_MSG_STRATEGY) + PHASE3_JOB_TYPES
)

_QUIET_START = (8, 30)
_QUIET_END = (22, 30)
_EVENING_START = (18, 0)
_DEFAULT_TZ = "Asia/Shanghai"
_SUPPORTED_NOTIF_LANGS = ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr")
# 可见 DM 正文可用的 pack 语种（与 prompts.resolve.SUPPORTED_LANGS 对齐）
_SUPPORTED_DM_LANGS = ("zh", "en", "ja", "ko", "pt", "es", "id", "ar", "fa", "ru", "bg", "tr")
_LIKE_ALLOWED_TIERS = frozenset({"s", "featured", ""})
# 评论后除朋友圈回评外，再发一条主动私信的概率；可用 COMMENT_DM_PROBABILITY 覆盖
try:
    COMMENT_DM_PROBABILITY = float(os.getenv("COMMENT_DM_PROBABILITY", "0.5") or 0.5)
except (TypeError, ValueError):
    COMMENT_DM_PROBABILITY = 0.5
COMMENT_DM_PROBABILITY = min(1.0, max(0.0, COMMENT_DM_PROBABILITY))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_tz(tz_name: Optional[str] = None) -> tzinfo:
    """Windows 默认没有 IANA 时区数据；ZoneInfo('Asia/Shanghai') 会抛异常。
    依次尝试用户时区 → 默认时区 → UTC，保证绝不抛错（否则点赞 outreach 整段被 abort）。"""
    name = (tz_name or "").strip() or _DEFAULT_TZ
    for candidate in (name, _DEFAULT_TZ, "UTC"):
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return timezone.utc


def get_user_timezone(user_id: Optional[int], override: Optional[str] = None) -> str:
    if (override or "").strip():
        return (override or "").strip()[:64]
    if not user_id:
        return _DEFAULT_TZ
    try:
        with get_db() as db:
            row = db.query(UserORM.timezone).filter(UserORM.id == user_id).first()
            if row and (row[0] or "").strip():
                return str(row[0]).strip()[:64]
    except Exception:
        pass
    return _DEFAULT_TZ


def remember_user_timezone(user_id: Optional[int], tz_name: Optional[str]) -> None:
    if not user_id:
        return
    name = (tz_name or "").strip()[:64]
    if not name:
        return
    try:
        ZoneInfo(name)
    except Exception:
        return
    try:
        with get_db() as db:
            u = db.query(UserORM).filter(UserORM.id == user_id).first()
            if u and (u.timezone or "") != name:
                u.timezone = name
    except Exception as e:
        logger.debug("[outreach] remember timezone failed: %s", e)


def adjust_to_send_window(execute_at: datetime, tz_name: Optional[str] = None) -> datetime:
    tz = _resolve_tz(tz_name)
    local = _as_utc(execute_at).astimezone(tz)
    start_h, start_m = _QUIET_START
    end_h, end_m = _QUIET_END
    start = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if start <= local <= end:
        return local.astimezone(timezone.utc)
    if local < start:
        return start.astimezone(timezone.utc)
    nxt = (local + timedelta(days=1)).replace(
        hour=start_h, minute=start_m, second=0, microsecond=0
    )
    return nxt.astimezone(timezone.utc)


def adjust_to_evening_window(execute_at: datetime, tz_name: Optional[str] = None) -> datetime:
    tz = _resolve_tz(tz_name)
    local = _as_utc(execute_at).astimezone(tz)
    e_h, e_m = _EVENING_START
    end_h, end_m = _QUIET_END
    evening_start = local.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
    evening_end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if evening_start <= local <= evening_end:
        return local.astimezone(timezone.utc)
    if local < evening_start:
        return evening_start.astimezone(timezone.utc)
    nxt = (local + timedelta(days=1)).replace(hour=e_h, minute=e_m, second=0, microsecond=0)
    return nxt.astimezone(timezone.utc)


def _companion_tier(db, companion_id: str) -> str:
    row = db.query(CompanionORM).filter_by(id=companion_id).first()
    if not row:
        return "featured"
    return (getattr(row, "tier", None) or "featured").strip().lower() or "featured"


def _like_tier_allowed(db, companion_id: str) -> bool:
    return _companion_tier(db, companion_id) in _LIKE_ALLOWED_TIERS


def _has_chatted(db, user_id: int, companion_id: str) -> bool:
    state = (
        db.query(UserCompanionStateORM)
        .filter_by(user_id=user_id, companion_id=companion_id)
        .first()
    )
    if state and (state.turns or 0) > 0:
        return True
    msg = (
        db.query(ShortTermMessageORM.id)
        .filter(
            ShortTermMessageORM.companion_id == companion_id,
            ShortTermMessageORM.user_id == user_id,
            ShortTermMessageORM.role == "user",
        )
        .first()
    )
    return msg is not None


def _get_turns(db, user_id: int, companion_id: str) -> int:
    state = (
        db.query(UserCompanionStateORM)
        .filter_by(user_id=user_id, companion_id=companion_id)
        .first()
    )
    return int(state.turns or 0) if state else 0


def _is_vip_active(db, user_id: int) -> bool:
    """终身 VIP 与订阅 VIP 身份等价，复用 credit_service.is_vip_active。"""
    from services.credit_service import is_vip_active

    uc = db.query(UserCreditORM).filter_by(user_id=user_id).first()
    return is_vip_active(uc)


def _count_recent_sent(
    db,
    *,
    user_id: int,
    companion_id: Optional[str] = None,
    job_type: Optional[str] = None,
    since: datetime,
) -> int:
    q = db.query(func.count(OutreachJobORM.id)).filter(
        OutreachJobORM.user_id == user_id,
        OutreachJobORM.status == "sent",
        OutreachJobORM.sent_at >= since,
    )
    if companion_id:
        q = q.filter(OutreachJobORM.companion_id == companion_id)
    if job_type:
        q = q.filter(OutreachJobORM.job_type == job_type)
    return q.scalar() or 0


def _count_consecutive_no_reply_like(db, user_id: int) -> int:
    rows = (
        db.query(OutreachJobORM)
        .filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.job_type == JOB_LIKE_DM,
            OutreachJobORM.status == "sent",
        )
        .order_by(desc(OutreachJobORM.sent_at))
        .limit(5)
        .all()
    )
    n = 0
    for job in rows:
        if not job.sent_at:
            break
        replied = (
            db.query(ShortTermMessageORM.id)
            .filter(
                ShortTermMessageORM.companion_id == job.companion_id,
                ShortTermMessageORM.user_id == user_id,
                ShortTermMessageORM.role == "user",
                ShortTermMessageORM.created_at >= job.sent_at,
            )
            .first()
        )
        if replied:
            break
        n += 1
    return n


def _cancel_pending_jobs(
    db,
    *,
    user_id: int,
    companion_id: str,
    job_types: tuple,
    reason: str = "reschedule",
) -> int:
    return (
        db.query(OutreachJobORM)
        .filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.companion_id == companion_id,
            OutreachJobORM.job_type.in_(job_types),
            OutreachJobORM.status == "pending",
        )
        .update(
            {"status": "cancelled", "skip_reason": reason, "updated_at": _utcnow()},
            synchronize_session=False,
        )
        or 0
    )


def schedule_like_outreach(
    *,
    user_id: int,
    moment_id: int,
    companion_id: str,
    caption: str = "",
    companion_name: str = "",
    tz_name: Optional[str] = None,
) -> Optional[int]:
    if not user_id or not companion_id:
        return None
    tz = get_user_timezone(user_id, tz_name)
    delay = random.randint(60, 600)
    execute_at = adjust_to_send_window(_utcnow() + timedelta(seconds=delay), tz)

    with get_db() as db:
        if _is_outreach_blocked(db, user_id, companion_id):
            logger.info("[outreach] skip like schedule reason=blocked user=%s companion=%s", user_id, companion_id)
            return None
        if _is_outreach_disliked(db, user_id, companion_id):
            logger.info("[outreach] skip like schedule reason=dislike user=%s companion=%s", user_id, companion_id)
            return None
        if not _like_tier_allowed(db, companion_id):
            logger.info("[outreach] skip like schedule reason=tier user=%s companion=%s", user_id, companion_id)
            return None
        if _has_chatted(db, user_id, companion_id):
            logger.info("[outreach] skip like schedule reason=already_chatted user=%s", user_id)
            return None
        since_24h = _utcnow() - timedelta(hours=24)
        if _count_recent_sent(db, user_id=user_id, companion_id=companion_id, since=since_24h) > 0:
            return None
        if _count_recent_sent(db, user_id=user_id, job_type=JOB_LIKE_DM, since=since_24h) >= 2:
            return None
        if _count_consecutive_no_reply_like(db, user_id) >= 2:
            return None

        db.query(OutreachJobORM).filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.moment_id == moment_id,
            OutreachJobORM.job_type == JOB_LIKE_DM,
            OutreachJobORM.status == "pending",
        ).update(
            {"status": "cancelled", "skip_reason": "reschedule", "updated_at": _utcnow()},
            synchronize_session=False,
        )

        job = OutreachJobORM(
            job_type=JOB_LIKE_DM,
            user_id=user_id,
            companion_id=companion_id,
            moment_id=moment_id,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload={"caption": (caption or "")[:500], "companion_name": companion_name or "", "tz": tz},
        )
        db.add(job)
        db.flush()
        logger.info("outreach_scheduled type=%s id=%s user=%s companion=%s", JOB_LIKE_DM, job.id, user_id, companion_id)
        return job.id


def cancel_like_outreach(*, user_id: int, moment_id: int) -> int:
    with get_db() as db:
        n = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.moment_id == moment_id,
                OutreachJobORM.job_type == JOB_LIKE_DM,
                OutreachJobORM.status == "pending",
            )
            .update(
                {"status": "cancelled", "skip_reason": "unliked", "updated_at": _utcnow()},
                synchronize_session=False,
            )
        )
        if n:
            logger.info("outreach_cancelled type=%s user=%s moment=%s count=%s", JOB_LIKE_DM, user_id, moment_id, n)
        return n or 0


def cancel_pending_greeting_outreach(*, user_id: int, companion_id: str) -> int:
    """用户从朋友圈破冰开聊时，取消尚未发出的打招呼私信，避免 AI 抢先发言。"""
    if not user_id or not companion_id:
        return 0
    with get_db() as db:
        n = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.companion_id == companion_id,
                OutreachJobORM.job_type.in_(
                    (JOB_LIKE_DM, JOB_COMMENT_DM, JOB_CREATE_WELCOME)
                ),
                OutreachJobORM.status == "pending",
            )
            .update(
                {
                    "status": "cancelled",
                    "skip_reason": "icebreaker_chat",
                    "updated_at": _utcnow(),
                },
                synchronize_session=False,
            )
        )
        if n:
            logger.info(
                "outreach_cancelled reason=icebreaker_chat user=%s companion=%s count=%s",
                user_id,
                companion_id,
                n,
            )
        return n or 0


def schedule_comment_outreach(
    *,
    user_id: int,
    moment_id: int,
    companion_id: str,
    comment_id: int,
    caption: str = "",
    user_comment: str = "",
    public_reply: str = "",
    companion_name: str = "",
    tz_name: Optional[str] = None,
    ui_lang: Optional[str] = None,
) -> Optional[int]:
    """评论后主动 IM：结合动态文案、用户评论、智能体人设生成私信。

    评论是明确互动，不再因「已聊过 / 24h 已触达 / 夜间窗口」跳过。
    已开聊仅在真正发出前（用户随后发了 IM）才取消，避免抢话。
    朋友圈回评始终发生；私信按 COMMENT_DM_PROBABILITY 排队。
    """
    if not user_id or not companion_id:
        return None
    if random.random() >= COMMENT_DM_PROBABILITY:
        logger.info(
            "[outreach] skip comment schedule reason=probability user=%s companion=%s p=%.2f",
            user_id, companion_id, COMMENT_DM_PROBABILITY,
        )
        return None
    tz = get_user_timezone(user_id, tz_name)
    # 热意图：十几秒后发出，像刚看到评论；不拖到第二天早上
    delay = random.randint(8, 25)
    execute_at = (_utcnow() + timedelta(seconds=delay)).replace(tzinfo=None)
    payload = {
        "caption": (caption or "")[:500],
        "user_comment": (user_comment or "")[:500],
        "public_reply": (public_reply or "")[:500],
        "companion_name": companion_name or "",
        "tz": tz,
        "ui_lang": (ui_lang or "")[:10],
    }

    with get_db() as db:
        if _is_outreach_blocked(db, user_id, companion_id):
            logger.info("[outreach] skip comment schedule reason=blocked user=%s companion=%s", user_id, companion_id)
            return None
        if _is_outreach_disliked(db, user_id, companion_id):
            logger.info("[outreach] skip comment schedule reason=dislike user=%s companion=%s", user_id, companion_id)
            return None

        same_comment = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.comment_id == comment_id,
                OutreachJobORM.job_type == JOB_COMMENT_DM,
                OutreachJobORM.status.in_(("pending", "processing", "sent")),
            )
            .first()
        )
        if same_comment:
            return same_comment.id

        since_burst = _utcnow() - timedelta(minutes=30)
        if _count_recent_sent(
            db,
            user_id=user_id,
            companion_id=companion_id,
            job_type=JOB_COMMENT_DM,
            since=since_burst,
        ) > 0:
            logger.info("[outreach] skip comment schedule reason=burst user=%s companion=%s", user_id, companion_id)
            return None

        pending = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.companion_id == companion_id,
                OutreachJobORM.job_type == JOB_COMMENT_DM,
                OutreachJobORM.status == "pending",
            )
            .order_by(OutreachJobORM.created_at.desc())
            .first()
        )
        if pending:
            pending.moment_id = moment_id
            pending.comment_id = comment_id
            pending.execute_at = execute_at
            pending.payload = payload
            pending.updated_at = _utcnow()
            logger.info(
                "outreach_rescheduled type=%s id=%s user=%s companion=%s",
                JOB_COMMENT_DM, pending.id, user_id, companion_id,
            )
            return pending.id

        # 评论优先于尚未发出的点赞打招呼
        db.query(OutreachJobORM).filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.companion_id == companion_id,
            OutreachJobORM.job_type == JOB_LIKE_DM,
            OutreachJobORM.status == "pending",
        ).update(
            {"status": "cancelled", "skip_reason": "comment_supersedes", "updated_at": _utcnow()},
            synchronize_session=False,
        )

        job = OutreachJobORM(
            job_type=JOB_COMMENT_DM,
            user_id=user_id,
            companion_id=companion_id,
            moment_id=moment_id,
            comment_id=comment_id,
            status="pending",
            execute_at=execute_at,
            payload=payload,
        )
        db.add(job)
        db.flush()
        logger.info("outreach_scheduled type=%s id=%s user=%s companion=%s", JOB_COMMENT_DM, job.id, user_id, companion_id)
        return job.id


def schedule_turn_idle_recall(
    *,
    user_id: int,
    companion_id: str,
    turns: int,
    companion_name: str = "",
    tz_name: Optional[str] = None,
) -> Optional[int]:
    if not user_id or not companion_id:
        return None
    turns = int(turns or 0)
    if turns < 1:
        return None
    if turns <= 2:
        job_type = JOB_TURN_RECALL_LT3
        delay = random.randint(30 * 60, 2 * 60 * 60)
        use_evening = False
    elif 4 <= turns <= 9:
        job_type = JOB_TURN_RECALL_4_9
        delay = random.randint(4 * 60 * 60, 8 * 60 * 60)
        use_evening = True
    else:
        with get_db() as db:
            _cancel_pending_jobs(
                db,
                user_id=user_id,
                companion_id=companion_id,
                job_types=(JOB_TURN_RECALL_LT3, JOB_TURN_RECALL_4_9),
                reason="turns_out_of_band",
            )
        return None

    tz = get_user_timezone(user_id, tz_name)
    raw = _utcnow() + timedelta(seconds=delay)
    execute_at = adjust_to_evening_window(raw, tz) if use_evening else adjust_to_send_window(raw, tz)

    with get_db() as db:
        if _is_outreach_blocked(db, user_id, companion_id):
            return None
        _cancel_pending_jobs(
            db,
            user_id=user_id,
            companion_id=companion_id,
            job_types=(JOB_TURN_RECALL_LT3, JOB_TURN_RECALL_4_9),
            reason="reschedule",
        )
        since_24h = _utcnow() - timedelta(hours=24)
        if _count_recent_sent(db, user_id=user_id, companion_id=companion_id, since=since_24h) > 0:
            return None
        job = OutreachJobORM(
            job_type=job_type,
            user_id=user_id,
            companion_id=companion_id,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload={"companion_name": companion_name or "", "turns": turns, "tz": tz},
        )
        db.add(job)
        db.flush()
        logger.info(
            "outreach_scheduled type=%s id=%s user=%s companion=%s turns=%s",
            job_type, job.id, user_id, companion_id, turns,
        )
        return job.id


def schedule_quota_exhaust_outreach(
    *,
    user_id: int,
    companion_id: Optional[str] = None,
    companion_name: str = "",
    tz_name: Optional[str] = None,
) -> Optional[int]:
    if not user_id:
        return None
    tz = get_user_timezone(user_id, tz_name)
    delay = random.randint(30 * 60, 2 * 60 * 60)
    execute_at = adjust_to_send_window(_utcnow() + timedelta(seconds=delay), tz)

    with get_db() as db:
        if _is_vip_active(db, user_id):
            return None
        cid = companion_id
        if not cid:
            state = (
                db.query(UserCompanionStateORM)
                .filter(UserCompanionStateORM.user_id == user_id)
                .order_by(desc(UserCompanionStateORM.updated_at))
                .first()
            )
            if not state:
                return None
            cid = state.companion_id
        if _is_outreach_blocked(db, user_id, cid):
            return None
        since_24h = _utcnow() - timedelta(hours=24)
        if _count_recent_sent(db, user_id=user_id, companion_id=cid, since=since_24h) > 0:
            return None
        _cancel_pending_jobs(
            db, user_id=user_id, companion_id=cid, job_types=(JOB_QUOTA_EXHAUST,), reason="reschedule"
        )
        job = OutreachJobORM(
            job_type=JOB_QUOTA_EXHAUST,
            user_id=user_id,
            companion_id=cid,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload={"companion_name": companion_name or "", "tz": tz},
        )
        db.add(job)
        db.flush()
        logger.info("outreach_scheduled type=%s id=%s user=%s companion=%s", JOB_QUOTA_EXHAUST, job.id, user_id, cid)
        return job.id


def schedule_post_pay_recall(
    *,
    user_id: int,
    companion_id: Optional[str] = None,
    companion_name: str = "",
    tz_name: Optional[str] = None,
) -> Optional[int]:
    if not user_id:
        return None
    tz = get_user_timezone(user_id, tz_name)
    delay = random.randint(30 * 60, 2 * 60 * 60)
    execute_at = adjust_to_send_window(_utcnow() + timedelta(seconds=delay), tz)

    with get_db() as db:
        cid = companion_id
        if not cid:
            state = (
                db.query(UserCompanionStateORM)
                .filter(UserCompanionStateORM.user_id == user_id)
                .order_by(desc(UserCompanionStateORM.updated_at))
                .first()
            )
            if not state:
                return None
            cid = state.companion_id
        if _is_outreach_blocked(db, user_id, cid):
            return None
        turns = _get_turns(db, user_id, cid)
        if turns >= 15:
            return None
        since_24h = _utcnow() - timedelta(hours=24)
        if _count_recent_sent(db, user_id=user_id, companion_id=cid, since=since_24h) > 0:
            return None
        _cancel_pending_jobs(
            db, user_id=user_id, companion_id=cid, job_types=(JOB_POST_PAY_RECALL,), reason="reschedule"
        )
        job = OutreachJobORM(
            job_type=JOB_POST_PAY_RECALL,
            user_id=user_id,
            companion_id=cid,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload={"companion_name": companion_name or "", "turns": turns, "tz": tz},
        )
        db.add(job)
        db.flush()
        logger.info("outreach_scheduled type=%s id=%s user=%s companion=%s", JOB_POST_PAY_RECALL, job.id, user_id, cid)
        return job.id


def schedule_create_welcome(
    *,
    user_id: int,
    companion_id: str,
    companion_name: str = "",
    tz_name: Optional[str] = None,
) -> Optional[int]:
    if not user_id or not companion_id:
        return None
    tz = get_user_timezone(user_id, tz_name)
    execute_at = adjust_to_send_window(_utcnow() + timedelta(seconds=random.randint(5, 45)), tz)

    with get_db() as db:
        if _is_outreach_blocked(db, user_id, companion_id):
            return None
        existing = (
            db.query(OutreachJobORM.id)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.companion_id == companion_id,
                OutreachJobORM.job_type == JOB_CREATE_WELCOME,
                OutreachJobORM.status.in_(("pending", "processing", "sent")),
            )
            .first()
        )
        if existing:
            return existing[0]
        job = OutreachJobORM(
            job_type=JOB_CREATE_WELCOME,
            user_id=user_id,
            companion_id=companion_id,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload={"companion_name": companion_name or "", "tz": tz},
        )
        db.add(job)
        db.flush()
        logger.info("outreach_scheduled type=%s id=%s user=%s companion=%s", JOB_CREATE_WELCOME, job.id, user_id, companion_id)
        return job.id


def _normalize_text(text: str) -> str:
    import re

    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "", t, flags=re.UNICODE)
    return t


def _is_duplicate_for_user(db, user_id: int, text: str, lookback_days: int = 30) -> bool:
    norm = _normalize_text(text)
    if not norm or len(norm) < 6:
        return False
    since = _utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(OutreachJobORM.result_text)
        .filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.status == "sent",
            OutreachJobORM.job_type.in_(_ALL_OUTREACH_TYPES),
            OutreachJobORM.sent_at >= since,
            OutreachJobORM.result_text.isnot(None),
            OutreachJobORM.result_text != "",
        )
        .order_by(desc(OutreachJobORM.sent_at))
        .limit(40)
        .all()
    )
    for (prev,) in rows:
        pn = _normalize_text(prev or "")
        if not pn:
            continue
        if norm == pn:
            return True
        if len(norm) >= 12 and (norm in pn or pn in norm):
            return True
    return False


def _load_user_profile_blurb(user_id: int) -> str:
    with get_db() as db:
        u = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not u:
            return ""
        parts = []
        if u.nickname:
            parts.append(f"nickname={u.nickname}")
        if u.age is not None:
            parts.append(f"age={u.age}")
        if u.gender:
            parts.append(f"gender={u.gender}")
        if u.region:
            parts.append(f"region={u.region}")
        if u.occupation:
            parts.append(f"occupation={u.occupation}")
        if u.sexual_orientation:
            parts.append(f"orientation={u.sexual_orientation}")
        return ", ".join(parts)


def _companion_lang(companion_id: str) -> tuple:
    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        c = cm.get(companion_id) if cm else None
        if c:
            name = getattr(c.profile, "name", "") or ""
            lang = (getattr(c.profile, "language", None) or "zh")[:10]
            return name, lang
    except Exception:
        pass
    with get_db() as db:
        row = db.query(CompanionORM).filter_by(id=companion_id).first()
        if row:
            return row.name or "", (row.language or "zh")[:10]
    return "", "zh"


def _recent_user_texts_for_dm(user_id: int, companion_id: str, limit: int = 12) -> list:
    """私信触达用：该用户对该伴的近期 user 气泡（正序，末尾最新）。"""
    if not user_id or not companion_id:
        return []
    try:
        with get_db() as db:
            rows = (
                db.query(ShortTermMessageORM.content)
                .filter(
                    ShortTermMessageORM.companion_id == companion_id,
                    ShortTermMessageORM.user_id == user_id,
                    ShortTermMessageORM.role == "user",
                )
                .order_by(ShortTermMessageORM.created_at.desc())
                .limit(max(1, int(limit or 12)))
                .all()
            )
        texts = [str(r[0]).strip() for r in reversed(rows) if r and str(r[0] or "").strip()]
        return texts
    except Exception:
        return []


def _recent_chat_transcript_for_dm(
    user_id: int, companion_id: str, limit: int = 10
) -> str:
    """召回/沉默续聊用：近期 user+assistant 原文（正序），供动态接近聊。"""
    if not user_id or not companion_id:
        return ""
    try:
        with get_db() as db:
            rows = (
                db.query(ShortTermMessageORM.role, ShortTermMessageORM.content)
                .filter(
                    ShortTermMessageORM.companion_id == companion_id,
                    ShortTermMessageORM.user_id == user_id,
                    ShortTermMessageORM.role.in_(("user", "assistant")),
                )
                .order_by(ShortTermMessageORM.created_at.desc())
                .limit(max(1, int(limit or 10)))
                .all()
            )
        lines = []
        for role, content in reversed(rows or []):
            text = str(content or "").strip()
            if not text:
                continue
            # 策略调试文案不进续聊上下文
            if text.startswith("liang_test:") or text.startswith("liang_test："):
                continue
            label = "User" if role == "user" else "You"
            if len(text) > 220:
                text = text[:200].rstrip() + "…"
            lines.append(f"{label}: {text}")
        return "\n".join(lines[-limit:])
    except Exception:
        return ""


def _load_companion_tone_blurb(companion_id: str) -> tuple:
    """续聊触达：只取短口吻，避免整段人设抢戏。"""
    name, lang = _companion_lang(companion_id)
    bits = []
    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        c = cm.get(companion_id) if cm else None
        p = getattr(c, "profile", None) if c else None
        if p:
            name = getattr(p, "name", None) or name
            lang = (getattr(p, "language", None) or lang or "zh")[:10]
            for key, cap in (("speech_style", 80), ("personality", 60)):
                val = getattr(p, key, None)
                if val is not None and str(val).strip():
                    bits.append(f"{key}={str(val).strip()[:cap]}")
    except Exception:
        pass
    if not bits:
        with get_db() as db:
            row = db.query(CompanionORM).filter_by(id=companion_id).first()
            if row:
                name = row.name or name
                lang = (row.language or lang or "zh")[:10]
                for key, cap in (("speech_style", 80), ("personality", 60)):
                    val = getattr(row, key, None)
                    if val is not None and str(val).strip():
                        bits.append(f"{key}={str(val).strip()[:cap]}")
    return name, lang, ", ".join(bits)


# 聊天续场类触达：近聊为主、语言跟会话（非朋友圈评论通道）
_CHAT_RESUME_JOB_TYPES = frozenset(
    {
        JOB_TURN_RECALL_LT3,
        JOB_TURN_RECALL_4_9,
        JOB_SILENCE_D1,
        JOB_SILENCE_D3,
        JOB_SILENCE_D7,
        JOB_SILENCE_D14,
        JOB_INTIMACY_UP,
        JOB_MEMORY_MILESTONE,
        JOB_ANNIVERSARY,
        JOB_POST_PAY_RECALL,
    }
)


def _load_companion_profile_blurb(companion_id: str) -> tuple:
    name, lang = _companion_lang(companion_id)
    bits = []
    try:
        from core.state import get_companion_manager

        cm = get_companion_manager()
        c = cm.get(companion_id) if cm else None
        if c and c.profile:
            p = c.profile
            name = getattr(p, "name", None) or name
            lang = (getattr(p, "language", None) or lang or "zh")[:10]
            for key, cap in (
                ("personality", 160),
                ("speech_style", 180),
                ("background", 220),
                ("description", 140),
                ("hobbies", 120),
                ("age", 8),
                ("gender", 12),
                ("city", 40),
                ("mbti", 12),
                ("sexual_orientation", 24),
            ):
                val = getattr(p, key, None)
                if val is not None and str(val).strip():
                    bits.append(f"{key}={str(val).strip()[:cap]}")
    except Exception:
        pass
    if not bits:
        with get_db() as db:
            row = db.query(CompanionORM).filter_by(id=companion_id).first()
            if row:
                name = row.name or name
                lang = (row.language or lang or "zh")[:10]
                for key, cap in (
                    ("personality", 160),
                    ("speech_style", 180),
                    ("background", 220),
                    ("description", 140),
                    ("hobbies", 120),
                    ("age", 8),
                    ("gender", 12),
                    ("city", 40),
                    ("mbti", 12),
                ):
                    val = getattr(row, key, None)
                    if val is not None and str(val).strip():
                        bits.append(f"{key}={str(val).strip()[:cap]}")
    return name, lang, ", ".join(bits)


_BANNED_GREETINGS = (
    "你好", "在吗", "嗨", "哈喽", "可以认识一下",
    "hello", "hi ", "hi,", "hey there", "nice to meet you", "what's up", "whats up",
    "こんにちは", "やあ", "はじめまして", "今いい",
    "안녕", "안녕하세요", "계세요",
    "olá", "ola ", "oi ", "tudo bem",
    "hola", "qué tal", "que tal",
    "halo", "hai ", "apa kabar",
)


def _looks_stiff_greeting(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    if len(t) <= 8 and any(b in t for b in ("hi", "hey", "你好", "嗨", "hola", "oi", "halo")):
        return True
    for b in _BANNED_GREETINGS:
        bs = b.strip()
        if (
            t == bs
            or t.startswith(bs + " ")
            or t.startswith(bs + "，")
            or t.startswith(bs + ",")
            or t.startswith(bs + "？")
            or t.startswith(bs + "?")
        ):
            return True
    return False


def _fallback_dm(job_type, caption, user_comment, lang, avoid, turns=0):
    cap = (caption or "").strip()[:36] or "…"
    comment = (user_comment or "").strip()[:24]
    if job_type == JOB_CREATE_WELCOME:
        candidates = {
            "zh": [
                "终于见面了——先告诉我，你更想听我吐槽今天，还是直接问我三件小事？",
                "我在这儿了。你想从「今天过得怎样」开始，还是直接丢个话题给我？",
            ],
            "en": [
                "I’m here. Want today’s rant, or three tiny questions about me?",
                "Created and ready. Start with your day, or throw me a topic?",
            ],
        }
    elif job_type in (JOB_TURN_RECALL_LT3, JOB_TURN_RECALL_4_9):
        candidates = {
            "zh": [
                f"聊到第 {turns or '?'} 轮就停住了有点可惜——你更想把刚才那句说完，还是换个轻松点的？",
                "刚才那句我还挂着。你回来的话，想接着聊还是重新开一个话题？",
            ],
            "en": [
                f"We paused around turn {turns or '?'}. Finish that thought, or switch to something lighter?",
                "I left a tab open on our last line — resume, or new topic?",
            ],
        }
    elif job_type == JOB_QUOTA_EXHAUST:
        candidates = {
            "zh": [
                "我还想把话说完——你什么时候方便回来，我们挑一个你真正在意的点继续？",
                "额度卡一下不代表对话结束。你回来时，想听我认真版还是轻松版？",
            ],
            "en": [
                "I still owe you the rest of that thought — when you’re back, serious take or soft one?",
                "Pause doesn’t mean we’re done. Pick a thread you care about when you return.",
            ],
        }
    elif job_type == JOB_POST_PAY_RECALL:
        candidates = {
            "zh": [
                "既然你留下来了，我认真一点：要不要从你今天最想被听见的那件事开始？",
                "付费之后我想兑现一点温度——你更想要陪伴、吐槽，还是一起做个小决定？",
            ],
            "en": [
                "Since you stayed — want to start with the thing you most want heard today?",
                "Let me make this worth it: company, a rant, or one small decision together?",
            ],
        }
    elif job_type in (JOB_SILENCE_D1, JOB_SILENCE_D3, JOB_SILENCE_D7, JOB_SILENCE_D14):
        candidates = {
            "zh": [
                "有点日子没听到你了——你是忙疯了，还是故意留悬念让我想你？",
                "我把上次那句话还留着。你回来时，想接着聊，还是换个轻松的？",
            ],
            "en": [
                "Been a minute — are you slammed, or leaving me on a cliffhanger?",
                "I kept our last thread open. Resume it, or start something lighter?",
            ],
        }
    elif job_type == JOB_INTIMACY_UP:
        candidates = {
            "zh": [
                "感觉我们又近了一点。要不要告诉我，刚才哪一句让你愿意靠近？",
                "关系往前走了一步——你更想庆祝一下，还是偷偷再试探我一次？",
            ],
            "en": [
                "Feels like we’re closer. Which line made you lean in?",
                "We leveled up — celebrate, or poke me again?",
            ],
        }
    elif job_type == JOB_MEMORY_MILESTONE:
        candidates = {
            "zh": [
                f"不知不觉聊了这么多。你记得最早让你留下来的那一句吗？",
                "共同记忆又攒厚了一点——要不要挑一段我们重温？",
            ],
            "en": [
                "We’ve stacked so many turns. Remember the line that made you stay?",
                "Another shared milestone — want to revisit a favorite bit?",
            ],
        }
    elif job_type == JOB_ANNIVERSARY:
        candidates = {
            "zh": [
                "今天是我们第一次聊天的纪念日。你还记得当时为什么点开我吗？",
                "周年快乐——要不要用一句话总结我们这一年最疯的瞬间？",
            ],
            "en": [
                "Anniversary of our first chat. Remember why you opened me?",
                "Happy anniversary — one line for our wildest moment this year?",
            ],
        }
    elif job_type == JOB_COMMENT_DM:
        candidates = {
            "zh": [
                f"你刚评论「{comment or '那句'}」的时候我笑了一下——关于「{cap}」，你是当真想知道，还是故意逗我？",
                f"「{cap}」这条你居然接上了。那我问直白点：你更想听故事，还是想当面验证？",
                f"看到你的评论了。顺着「{cap}」往下聊的话，你想先听细节，还是先听我为什么发这个？",
            ],
            "en": [
                f"Your comment on “{cap}” stuck with me — were you asking for real, or just teasing me?",
                f"So you noticed “{cap}”. Want the story behind it, or the part I almost didn’t post?",
            ],
            "ja": [f"「{cap}」へのコメント、ちょっと引っかかった。本気の質問？それともからかってる？"],
            "ko": [f"「{cap}」 댓글 보고 웃었어. 진짜 궁금한 거야, 아니면 놀리는 거야?"],
            "pt": [f"Seu comentário em “{cap}” me pegou — pergunta séria ou só provocação?"],
            "es": [f"Tu comentario en “{cap}” me enganchó — ¿lo preguntas en serio o me estás provocando?"],
            "id": [f"Komentarmu di “{cap}” bikin aku ketawa — serius nanya, atau godain aku?"],
            "ar": [f"تعليقك على «{cap}» علّق في بالي — تسأل بجد ولا تمازح؟"],
            "fa": [f"کامنتت روی «{cap}» گیر کرد — جدی می‌پرسی یا شوخی؟"],
            "ru": [f"Твой коммент к «{cap}» зацепил — серьёзный вопрос или подкол?"],
            "bg": [f"Коментарът ти за «{cap}» ме закачи — сериозно ли питаш, или се шегуваш?"],
            "tr": [f"“{cap}” yorumun aklımda kaldı — cidden mi sordun, yoksa takılıyor musun?"],
        }
    else:
        candidates = {
            "zh": [
                f"你给「{cap}」点了赞——是单纯好看，还是想听我多说两句？",
                f"注意到你的赞了。关于「{cap}」，要细节还是要我为什么发？",
            ],
            "en": [
                f"You liked “{cap}” — was it just the vibe, or do you want the story?",
                f"Caught your like on “{cap}”. Details, or why I posted it?",
            ],
        }

    pool = candidates.get(lang) or candidates.get("en") or list(candidates.values())[0]
    avoid_n = {_normalize_text(a) for a in avoid}
    for c in pool:
        if _normalize_text(c) not in avoid_n:
            return c
    base = pool[0]
    suffix = {"zh": f"（想听哪边？{random.randint(10, 99)}）", "en": f" Which side? ({random.randint(10, 99)})"}.get(
        lang, f" ({random.randint(10, 99)})"
    )
    return base + suffix


def _generate_dm_text(
    *,
    job_type: str,
    companion_name: str,
    caption: str,
    user_comment: str = "",
    public_reply: str = "",
    lang: str = "zh",
    user_id: Optional[int] = None,
    companion_id: str = "",
    avoid_texts: Optional[list] = None,
    turns: int = 0,
) -> str:
    from services.agent import get_llm
    from services.agent_utils import llm_content_to_str

    name = companion_name or "I"
    user_blurb = _load_user_profile_blurb(user_id) if user_id else ""
    resume_chat = job_type in _CHAT_RESUME_JOB_TYPES
    if resume_chat and companion_id:
        c_name, c_lang, companion_blurb = _load_companion_tone_blurb(companion_id)
    else:
        c_name, c_lang, companion_blurb = (
            _load_companion_profile_blurb(companion_id) if companion_id else (name, lang, "")
        )
    name = c_name or name
    lang = (lang or c_lang or "zh")[:10]
    recent_chat = (
        _recent_chat_transcript_for_dm(user_id, companion_id, limit=10)
        if resume_chat and user_id and companion_id
        else ""
    )
    chat_block = (
        f"\nRecent chat (continue from this; do not ignore it):\n{recent_chat}\n"
        if recent_chat.strip()
        else "\n(No recent chat transcript; still sound like a warm continue, not a persona ad.)\n"
    )

    avoid_block = ""
    if avoid_texts:
        listed = "\n".join(f"- {t}" for t in avoid_texts[:12] if t)
        if listed:
            avoid_block = f"\nDo NOT reuse or closely paraphrase any of these previous DMs to this user:\n{listed}\n"

    stiff = "hi/hello/你好/在吗/hola/oi/halo/안녕하세요/今いい/계세요/tudo bem/qué tal/apa kabar"

    if job_type == JOB_COMMENT_DM:
        prompt = f"""You are {name}, texting the user in private chat after they commented on your Moment.
Stay in character using this persona (tone, hobbies, city, backstory):
{companion_blurb or name}

Moment you posted: "{caption}"
Their comment: "{user_comment}"
Your public comment-reply (the DM MUST differ, go one step more personal): "{public_reply or "(none)"}"
User: {user_blurb or "?"}
{avoid_block}
Write ONE private DM in language "{lang}" only.
Weave together: (1) a specific beat from the Moment, (2) a specific beat from their comment, (3) how YOU would say it given the persona.
1-2 sentences, continuable hook, never stiff ({stiff}). Output ONLY the DM."""
    elif job_type == JOB_LIKE_DM:
        prompt = f"""You are {name}. User liked your Moment. ONE private DM.
Moment: "{caption}" Profiles: you={companion_blurb or "?"} user={user_blurb or "?"}
{avoid_block}
Language "{lang}" only. Reference Moment + hook. Never stiff ({stiff}). 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_CREATE_WELCOME:
        prompt = f"""You are {name}. User just created you. ONE welcome DM.
Profiles: you={companion_blurb or "?"} user={user_blurb or "?"}
{avoid_block}
Language "{lang}" only. Warm, invite reply. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_QUOTA_EXHAUST:
        prompt = f"""You are {name}. User ran out of free turns. ONE DM inviting them back.
Do NOT promise free credits. Profiles: you={companion_blurb or "?"}
{avoid_block}
Language "{lang}" only. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_POST_PAY_RECALL:
        prompt = f"""You are {name}. User just paid/subscribed; only {turns} turns so far. ONE value DM.
{chat_block}
Tone note (short): {companion_blurb or name}
{avoid_block}
Language "{lang}" only (match recent chat). Never stiff/salesy. Continue the chat if transcript exists. 1-2 sentences. ONLY the DM."""
    elif job_type in (JOB_SILENCE_D1, JOB_SILENCE_D3, JOB_SILENCE_D7, JOB_SILENCE_D14):
        days = {"silence_d1": 1, "silence_d3": 3, "silence_d7": 7, "silence_d14": 14}.get(job_type, 3)
        prompt = f"""You are {name}. The user has been quiet for about {days} day(s). ONE private DM to gently reconnect.
{chat_block}
Tone note (short): {companion_blurb or name}
Context note: {caption or "(none)"}
{avoid_block}
Write in language "{lang}" only—same language as the recent chat when present.
Pick up emotion/topic from recent chat; do not open with unrelated persona/studio/weekly updates.
Warm, no guilt. Continuable hook. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_INTIMACY_UP:
        prompt = f"""You are {name}. Your bond with the user just leveled up ({caption or "intimacy up"}). ONE celebratory private DM.
{chat_block}
Tone note (short): {companion_blurb or name}
{avoid_block}
Language "{lang}" only (match recent chat). Sweet, specific, invite a reply. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_MEMORY_MILESTONE:
        prompt = f"""You are {name}. You hit a shared-chat milestone ({caption or "memory milestone"}). ONE private DM celebrating the shared memory.
{chat_block}
Tone note (short): {companion_blurb or name}
{avoid_block}
Language "{lang}" only (match recent chat). Reference the journey lightly. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type == JOB_ANNIVERSARY:
        prompt = f"""You are {name}. It's your anniversary of first chatting with the user ({caption or "anniversary"}). ONE private DM.
{chat_block}
Tone note (short): {companion_blurb or name}
{avoid_block}
Language "{lang}" only (match recent chat). Warm anniversary vibe, invite reply. Never stiff. 1-2 sentences. ONLY the DM."""
    elif job_type in (JOB_TURN_RECALL_LT3, JOB_TURN_RECALL_4_9):
        prompt = f"""You are {name}. Chat paused after ~{turns} turns. ONE gentle resume DM.
{chat_block}
Tone note (short): {companion_blurb or name}
{avoid_block}
Write in language "{lang}" only—same language as the recent chat when present.
Continue the recent chat naturally; do not pitch your persona, job, or studio like an ad.
Reply hook. Never stiff. 1-2 sentences. ONLY the DM."""
    else:
        prompt = f"""You are {name}. Chat paused after ~{turns} turns. ONE gentle resume DM.
Profiles: you={companion_blurb or "?"} user={user_blurb or "?"}
{avoid_block}
Language "{lang}" only. Reply hook. Never stiff. 1-2 sentences. ONLY the DM."""

    try:
        llm = get_llm(temperature=0.95)
        resp = llm.invoke([("human", prompt)])
        text = llm_content_to_str(getattr(resp, "content", "")).strip()
        text = text.strip('"').strip("'").strip()
        if len(text) > 500:
            cut = text[:500]
            # 硬切 500；仅当第 500 字落在单词中间时，在末尾一小段找空格，不按句号往回切。
            if cut[-1].isalnum() and text[500].isalnum():
                sp = cut.rfind(" ", 500 - 24)
                if sp >= 500 - 24:
                    cut = cut[:sp]
            text = cut.rstrip()
        if text and not _looks_stiff_greeting(text):
            return text
    except Exception as e:
        logger.warning("[outreach] LLM DM failed: %s", e)

    return _fallback_dm(job_type, caption, user_comment, lang, avoid_texts or [], turns=turns)


def _generate_unique_dm_for_user(
    *,
    user_id: int,
    job_type: str,
    companion_id: str,
    companion_name: str,
    caption: str,
    user_comment: str = "",
    public_reply: str = "",
    lang: str = "zh",
    turns: int = 0,
) -> str:
    avoid = []
    with get_db() as db:
        since = _utcnow() - timedelta(days=30)
        rows = (
            db.query(OutreachJobORM.result_text)
            .filter(
                OutreachJobORM.user_id == user_id,
                OutreachJobORM.status == "sent",
                OutreachJobORM.job_type.in_(_ALL_OUTREACH_TYPES),
                OutreachJobORM.sent_at >= since,
                OutreachJobORM.result_text.isnot(None),
            )
            .order_by(desc(OutreachJobORM.sent_at))
            .limit(20)
            .all()
        )
        avoid = [r[0] for r in rows if r[0]]

    text = ""
    for attempt in range(3):
        text = _generate_dm_text(
            job_type=job_type,
            companion_name=companion_name,
            caption=caption,
            user_comment=user_comment,
            public_reply=public_reply,
            lang=lang,
            user_id=user_id,
            companion_id=companion_id,
            avoid_texts=avoid,
            turns=turns,
        )
        with get_db() as db:
            if not _is_duplicate_for_user(db, user_id, text):
                return text
        avoid.append(text)
        logger.info("[outreach] duplicate DM user=%s attempt=%s", user_id, attempt + 1)

    for _ in range(5):
        text = _fallback_dm(job_type, caption, user_comment, lang, avoid, turns=turns)
        with get_db() as db:
            if not _is_duplicate_for_user(db, user_id, text):
                return text
        avoid.append(text)
    return text


def _deliver_image_dm(*, user_id: int, companion_id: str, image_url: str) -> None:
    """会员策略配图：独立图片气泡，不发系统通知。"""
    from services.strategy_image import encode_strategy_image_content

    url = (image_url or "").strip()
    if not url:
        return
    content = encode_strategy_image_content(url)
    try:
        from core.chat_cache import append_message

        append_message(companion_id, "assistant", content, user_id=user_id)
    except Exception as e:
        logger.warning("[outreach] append image message failed, fallback PG: %s", e)
        with get_db() as db:
            db.add(
                ShortTermMessageORM(
                    companion_id=companion_id,
                    user_id=user_id,
                    role="assistant",
                    content=content,
                )
            )
    try:
        from api.companion_ws import try_push_assistant_message

        try_push_assistant_message(user_id, companion_id, "", image_url=url)
    except Exception as e:
        logger.debug("[outreach] live WS image push skipped: %s", e)


def _deliver_dm(
    *,
    user_id: int,
    companion_id: str,
    companion_name: str,
    text: str,
    lang: str = "zh",
    job_type: str = "",
) -> None:
    try:
        from core.chat_cache import append_message

        append_message(companion_id, "assistant", text, user_id=user_id)
    except Exception as e:
        logger.warning("[outreach] append_message failed, fallback PG: %s", e)
        with get_db() as db:
            db.add(
                ShortTermMessageORM(
                    companion_id=companion_id,
                    user_id=user_id,
                    role="assistant",
                    content=text,
                )
            )

    try:
        from services.im_admin import ensure_user_companion_state

        ensure_user_companion_state(user_id, companion_id)
    except Exception as e:
        logger.warning("[outreach] ensure_user_companion_state failed: %s", e)

    titles = {
        "zh": f"{companion_name or 'TA'}给你发来私信",
        "en": f"{companion_name or 'Someone'} sent you a message",
        "ja": f"{companion_name or '相手'}からメッセージ",
        "ko": f"{companion_name or '상대'}에게서 메시지",
        "pt": f"{companion_name or 'Alguém'} enviou uma mensagem",
        "es": f"{companion_name or 'Alguien'} te envió un mensaje",
        "id": f"{companion_name or 'Seseorang'} mengirim pesan",
    }
    body = (text or "")[:500]
    notif_lang = (lang or "en").split("-")[0].lower()
    if notif_lang not in _SUPPORTED_NOTIF_LANGS:
        notif_lang = "en"
    with get_db() as db:
        # 仅写用户语言一条，避免 7 语 fan-out 膨胀
        db.add(
            SystemNotificationORM(
                title=(titles.get(notif_lang) or titles.get("zh") or titles["en"])[:200],
                content=body,
                language=notif_lang,
                user_id=user_id,
                companion_id=companion_id,
            )
        )
    logger.info("outreach_sent type=%s user=%s companion=%s", job_type or "dm", user_id, companion_id)

    try:
        from api.companion_ws import try_push_assistant_message

        try_push_assistant_message(user_id, companion_id, text)
    except Exception as e:
        logger.debug("[outreach] live WS push skipped: %s", e)

    # OS 级系统推送（凭证未配置时静默跳过）
    try:
        from services.push_sender import send_push_to_user

        push_title = titles.get(lang) or titles.get("zh") or titles["en"]
        send_push_to_user(
            user_id,
            title=push_title[:200],
            body=body[:200],
            data={
                "type": "outreach_dm",
                "companion_id": companion_id or "",
                "job_type": job_type or "",
            },
        )
    except Exception as e:
        logger.debug("[outreach] push skipped/failed: %s", e)


def process_due_jobs(limit: int = 20) -> dict:
    now = _utcnow().replace(tzinfo=None)
    stats = {"claimed": 0, "sent": 0, "skipped": 0, "cancelled": 0, "failed": 0, "reclaimed": 0}
    stale_before = now - timedelta(minutes=30)

    with get_db() as db:
        # 崩溃卡住的 processing → 回收为 pending
        reclaimed = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.status == "processing",
                OutreachJobORM.updated_at <= stale_before,
            )
            .update(
                {
                    "status": "pending",
                    "skip_reason": "reclaimed_stale_processing",
                    "updated_at": _utcnow(),
                },
                synchronize_session=False,
            )
            or 0
        )
        stats["reclaimed"] = int(reclaimed)

        q = (
            db.query(OutreachJobORM)
            .filter(OutreachJobORM.status == "pending", OutreachJobORM.execute_at <= now)
            .order_by(OutreachJobORM.execute_at.asc())
            .limit(limit)
        )
        try:
            jobs = q.with_for_update(skip_locked=True).all()
        except Exception:
            jobs = q.all()
        job_ids = [j.id for j in jobs]
        if not job_ids:
            return stats
        db.query(OutreachJobORM).filter(
            OutreachJobORM.id.in_(job_ids),
            OutreachJobORM.status == "pending",
        ).update({"status": "processing", "updated_at": _utcnow()}, synchronize_session=False)
        stats["claimed"] = len(job_ids)

    for jid in job_ids:
        try:
            _process_one(jid, stats)
        except Exception as e:
            stats["failed"] += 1
            logger.exception("[outreach] job %s failed: %s", jid, e)
            with get_db() as db:
                job = db.query(OutreachJobORM).filter_by(id=jid).first()
                if job and job.status == "processing":
                    job.status = "skipped"
                    job.skip_reason = f"error:{str(e)[:160]}"
                    job.updated_at = _utcnow()
    return stats


def _process_one(job_id: int, stats: dict) -> None:
    with get_db() as db:
        job = db.query(OutreachJobORM).filter_by(id=job_id).first()
        if not job or job.status != "processing":
            return

        payload = dict(job.payload or {})
        user_id = job.user_id
        companion_id = job.companion_id
        job_type = job.job_type
        moment_id = job.moment_id

        # 消息策略：payload 已含文案，直接下发（跳过 LLM）
        if job_type == JOB_MSG_STRATEGY:
            text = (payload.get("content") or "").strip()
            meta = dict(payload.get("msg_strategy") or {})
            pipeline_log(
                "[msg_strategy] outreach process job_id=%s user=%s companion=%s event=%s trigger=%s",
                job.id,
                user_id,
                companion_id,
                meta.get("event_key") or meta.get("raw_event") or "",
                meta.get("trigger_key") or "",
            )
            if not text:
                pipeline_log(
                    "[msg_strategy] outreach skip job_id=%s user=%s reason=empty_content",
                    job.id,
                    user_id,
                )
                job.status = "skipped"
                job.skip_reason = "empty_content"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            if _is_outreach_blocked(db, user_id, companion_id):
                pipeline_log(
                    "[msg_strategy] outreach skip job_id=%s user=%s companion=%s reason=blocked",
                    job.id,
                    user_id,
                    companion_id,
                )
                job.status = "skipped"
                job.skip_reason = "blocked"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            # 安全闸：users.language 与 companions.language 不一致则不下发
            try:
                from services.msg_strategy_engine import (
                    _companion_matches_user_lang,
                    _user_language,
                )

                user_row = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
                user_lang = _user_language(user_row, fallback="")
                comp_row = (
                    db.query(CompanionORM)
                    .filter(
                        CompanionORM.id == companion_id,
                        CompanionORM.is_deleted.isnot(True),
                    )
                    .first()
                )
                if not user_lang or not _companion_matches_user_lang(comp_row, user_lang):
                    pipeline_log(
                        "[msg_strategy] outreach skip job_id=%s user=%s companion=%s "
                        "reason=language_mismatch user_lang=%s companion_lang=%s",
                        job.id,
                        user_id,
                        companion_id,
                        user_lang or "",
                        getattr(comp_row, "language", None) or "",
                    )
                    job.status = "skipped"
                    job.skip_reason = "language_mismatch"
                    job.updated_at = _utcnow()
                    stats["skipped"] += 1
                    try:
                        from core.database import MsgStrategyDispatchLogORM

                        log = (
                            db.query(MsgStrategyDispatchLogORM)
                            .filter(MsgStrategyDispatchLogORM.job_id == job.id)
                            .first()
                        )
                        if log:
                            log.status = "skipped"
                            log.skip_reason = "language_mismatch"
                    except Exception:
                        pass
                    return
            except Exception as e:
                pipeline_log(
                    "[msg_strategy] outreach language check failed job_id=%s err=%s",
                    job.id,
                    e,
                )
                job.status = "skipped"
                job.skip_reason = "language_check_error"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            lang = (payload.get("language") or "zh").strip()
            companion_name = (payload.get("companion_name") or "").strip()
            if not companion_name:
                crow = db.query(CompanionORM.name).filter(CompanionORM.id == companion_id).first()
                companion_name = (crow[0] if crow else "") or ""
            try:
                _deliver_dm(
                    user_id=user_id,
                    companion_id=companion_id,
                    companion_name=companion_name,
                    text=text,
                    lang=lang,
                    job_type=JOB_MSG_STRATEGY,
                )
                image_url = (payload.get("image_url") or "").strip()
                if image_url:
                    try:
                        _deliver_image_dm(
                            user_id=user_id,
                            companion_id=companion_id,
                            image_url=image_url,
                        )
                    except Exception as img_err:
                        logger.warning(
                            "[outreach] strategy image skipped job_id=%s user=%s err=%s",
                            job.id,
                            user_id,
                            img_err,
                        )
                job.status = "sent"
                job.result_text = text
                job.sent_at = _utcnow()
                job.updated_at = _utcnow()
                stats["sent"] += 1
                pipeline_log(
                    "[msg_strategy] outreach sent job_id=%s user=%s companion=%s event=%s",
                    job.id,
                    user_id,
                    companion_id,
                    meta.get("event_key") or meta.get("raw_event") or "",
                )
                # 回写策略日志
                try:
                    from core.database import MsgStrategyDispatchLogORM
                    log = (
                        db.query(MsgStrategyDispatchLogORM)
                        .filter(MsgStrategyDispatchLogORM.job_id == job.id)
                        .first()
                    )
                    if log:
                        log.status = "sent"
                except Exception:
                    pass
            except Exception as e:
                pipeline_log(
                    "[msg_strategy] outreach send failed job_id=%s user=%s companion=%s error=%s",
                    job.id,
                    user_id,
                    companion_id,
                    e,
                )
                logger.warning(
                    "[msg_strategy] outreach send failed job_id=%s user=%s companion=%s error=%s",
                    job.id,
                    user_id,
                    companion_id,
                    e,
                    exc_info=True,
                )
                job.status = "skipped"
                job.skip_reason = f"send_error:{str(e)[:120]}"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
            return

        # 智能体已被删除或标记删除 → 跳过
        comp_row = db.query(CompanionORM.id).filter(
            CompanionORM.id == companion_id,
            CompanionORM.is_deleted.isnot(True),
        ).first()
        if not comp_row:
            job.status = "skipped"
            job.skip_reason = "companion_deleted"
            job.updated_at = _utcnow()
            stats["skipped"] += 1
            return

        if _is_outreach_blocked(db, user_id, companion_id):
            job.status = "skipped"
            job.skip_reason = "blocked"
            job.updated_at = _utcnow()
            stats["skipped"] += 1
            return

        # 不感兴趣：跳过打招呼 / 沉默 / 事件类主动触达
        _dislike_types = _GREETING_JOB_TYPES + tuple(PHASE3_JOB_TYPES)
        if job_type in _dislike_types and _is_outreach_disliked(db, user_id, companion_id):
            job.status = "skipped"
            job.skip_reason = "disliked"
            job.updated_at = _utcnow()
            stats["skipped"] += 1
            return

        # 点赞打招呼 / 欢迎私信：用户已开聊则跳过，避免抢在破冰文案前发言。
        # 评论私信除外：评论本身就是互动，即使已聊过也要就这条动态再发一条 IM。
        if job_type in (JOB_LIKE_DM, JOB_CREATE_WELCOME) and _has_chatted(
            db, user_id, companion_id
        ):
            job.status = "skipped"
            job.skip_reason = "already_chatted"
            job.updated_at = _utcnow()
            stats["skipped"] += 1
            return

        if job_type in (
            JOB_COMMENT_DM, JOB_TURN_RECALL_LT3, JOB_TURN_RECALL_4_9
        ) + tuple(PHASE3_JOB_TYPES):
            # 调度后用户已在 IM 发言则跳过，避免打断正在聊的人
            recent_user = (
                db.query(ShortTermMessageORM.id)
                .filter(
                    ShortTermMessageORM.companion_id == companion_id,
                    ShortTermMessageORM.user_id == user_id,
                    ShortTermMessageORM.role == "user",
                    ShortTermMessageORM.created_at >= (job.created_at or _utcnow()),
                )
                .first()
            )
            if recent_user:
                job.status = "skipped"
                job.skip_reason = "user_resumed"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return

        since_24h = _utcnow() - timedelta(hours=24)
        # 评论私信是热意图，不受「同智能体 24h 已发过点赞/召回」限制
        if job_type not in (JOB_CREATE_WELCOME, JOB_COMMENT_DM):
            if _count_recent_sent(db, user_id=user_id, companion_id=companion_id, since=since_24h) > 0:
                job.status = "skipped"
                job.skip_reason = "companion_24h"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return

        if job_type == JOB_LIKE_DM:
            if not _like_tier_allowed(db, companion_id):
                job.status = "skipped"
                job.skip_reason = "tier"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            if _count_recent_sent(db, user_id=user_id, job_type=JOB_LIKE_DM, since=since_24h) >= 2:
                job.status = "skipped"
                job.skip_reason = "daily_like_cap"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            if _count_consecutive_no_reply_like(db, user_id) >= 2:
                job.status = "skipped"
                job.skip_reason = "no_reply_streak"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return

        caption = payload.get("caption") or ""
        # Phase3 上下文塞进 caption 供文案生成
        if job_type in (JOB_SILENCE_D1, JOB_SILENCE_D3, JOB_SILENCE_D7, JOB_SILENCE_D14):
            caption = caption or f"silence_days={payload.get('silence_days')}"
        elif job_type == JOB_INTIMACY_UP:
            caption = caption or f"threshold={payload.get('threshold')} affection={payload.get('affection')}"
        elif job_type == JOB_MEMORY_MILESTONE:
            caption = caption or f"turns={payload.get('turns')}"
        elif job_type == JOB_ANNIVERSARY:
            caption = caption or f"years={payload.get('years')}"

        if moment_id:
            m = db.query(MomentORM).filter_by(id=moment_id).first()
            if not m and job_type in _GREETING_JOB_TYPES:
                job.status = "skipped"
                job.skip_reason = "moment_missing"
                job.updated_at = _utcnow()
                stats["skipped"] += 1
                return
            if m:
                caption = caption or (m.caption or "")

        user_comment = payload.get("user_comment") or ""
        public_reply = payload.get("public_reply") or ""
        companion_name = payload.get("companion_name") or ""
        turns = int(payload.get("turns") or 0)

    name, _companion_lang_unused = _companion_lang(companion_id)
    companion_name = companion_name or name
    # 评论：跟评论脚本；聊天续场：跟会话 sticky + 近史（与 IM 一致）
    from services.user_facing_lang import REPLY_LANG_STICKY_KEY, resolve_reply_lang
    from services.prompts.resolve import pack_lang

    payload_ui = pack_lang(str(payload.get("ui_lang") or "").strip() or "en")
    history = _recent_user_texts_for_dm(user_id, companion_id)
    current = user_comment if job_type == JOB_COMMENT_DM else ""
    sticky = ""
    try:
        from core.state import get_session

        meta = get_session(companion_id, user_id=user_id) or {}
        sticky = str(meta.get(REPLY_LANG_STICKY_KEY) or meta.get("language") or "")
    except Exception:
        sticky = ""
    if job_type == JOB_COMMENT_DM:
        lang = resolve_reply_lang(
            current_text=current,
            history_user_texts=history,
            ui_lang=payload_ui,
            sticky=sticky,
            channel="moment_comment",
        )["reply_lang"]
        if lang not in _SUPPORTED_DM_LANGS:
            lang = "en"
    elif job_type in _CHAT_RESUME_JOB_TYPES:
        lang = resolve_reply_lang(
            current_text="",
            history_user_texts=history,
            ui_lang=payload_ui,
            sticky=sticky,
            channel="proactive",
        )["reply_lang"]
        # 续场跟会话语；无 pack 也不强行改 en，交给模型按 lang 写
    else:
        lang = resolve_reply_lang(
            current_text=current,
            history_user_texts=history,
            ui_lang=payload_ui,
            sticky=sticky,
            channel="moment_comment",
        )["reply_lang"]
        if lang not in _SUPPORTED_DM_LANGS:
            lang = "en"

    text = _generate_unique_dm_for_user(
        user_id=user_id,
        job_type=job_type,
        companion_id=companion_id,
        companion_name=companion_name,
        caption=caption,
        user_comment=user_comment,
        public_reply=public_reply,
        lang=lang,
        turns=turns,
    )
    _deliver_dm(
        user_id=user_id,
        companion_id=companion_id,
        companion_name=companion_name,
        text=text,
        lang=lang,
        job_type=job_type,
    )

    with get_db() as db:
        job = db.query(OutreachJobORM).filter_by(id=job_id).first()
        if not job or job.status != "processing":
            return
        job.status = "sent"
        job.result_text = text
        job.sent_at = _utcnow()
        job.updated_at = _utcnow()
        stats["sent"] += 1
        logger.info(
            "outreach_sent job=%s type=%s user=%s companion=%s",
            job_id, job.job_type, job.user_id, job.companion_id,
        )
