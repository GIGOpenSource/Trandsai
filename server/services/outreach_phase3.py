"""
运营触达 Phase 3：
- O-20 沉默召回 D1/D3/D7/14
- O-22 多角色优先级引擎
- O-23 亲密度升级 / 共同记忆里程碑 / 纪念日
- O-24 漏斗统计
- O-25 屏蔽 / 不感兴趣
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, desc, func, or_

from core.database import (
    CompanionORM,
    OutreachJobORM,
    ShortTermMessageORM,
    UserCompanionStateORM,
    UserOutreachPreferenceORM,
    get_db,
    serialize_datetime,
)

logger = logging.getLogger(__name__)

JOB_SILENCE_D1 = "silence_d1"
JOB_SILENCE_D3 = "silence_d3"
JOB_SILENCE_D7 = "silence_d7"
JOB_SILENCE_D14 = "silence_d14"
JOB_INTIMACY_UP = "event_intimacy_up"
JOB_MEMORY_MILESTONE = "event_memory"
JOB_ANNIVERSARY = "event_anniversary"

_SILENCE_JOBS = (JOB_SILENCE_D1, JOB_SILENCE_D3, JOB_SILENCE_D7, JOB_SILENCE_D14)
_EVENT_JOBS = (JOB_INTIMACY_UP, JOB_MEMORY_MILESTONE, JOB_ANNIVERSARY)
PHASE3_JOB_TYPES = _SILENCE_JOBS + _EVENT_JOBS

# 沉默天数 → job_type；扫描容差（小时）
_SILENCE_SPECS = (
    (1, JOB_SILENCE_D1, 8),
    (3, JOB_SILENCE_D3, 12),
    (7, JOB_SILENCE_D7, 18),
    (14, JOB_SILENCE_D14, 24),
)

_INTIMACY_THRESHOLDS = (10.0, 30.0, 50.0, 80.0)
_TURN_MILESTONES = (10, 50, 100, 200)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- O-25 屏蔽 / 不感兴趣 ----------

def _pref_cid(companion_id: str) -> str:
    """companions.id / user_outreach_preferences.companion_id 均为 8 位。"""
    return (companion_id or "").strip()[:8]


def set_outreach_preference(
    *,
    user_id: int,
    companion_id: str,
    action: str,
) -> dict:
    """action: block | dislike | clear

    clear：删除偏好行（最终态无记录 = 可见）；不改亲密度。
    block/dislike：写入偏好并隐藏列表；亲密度清零。
    """
    action = (action or "").strip().lower()
    cid = _pref_cid(companion_id)
    if action not in ("block", "dislike", "clear"):
        return {"ok": False, "reason": "invalid_action"}
    if not user_id or not cid:
        return {"ok": False, "reason": "missing"}

    with get_db() as db:
        if action == "clear":
            # 硬删：含历史误写入的 action='clear' 行；不匹配则 0 行也算成功
            deleted = (
                db.query(UserOutreachPreferenceORM)
                .filter(
                    UserOutreachPreferenceORM.user_id == int(user_id),
                    UserOutreachPreferenceORM.companion_id == cid,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                "[outreach] preference clear user=%s companion=%s deleted=%s",
                user_id,
                cid,
                deleted,
            )
            return {"ok": True, "action": "clear"}

        row = (
            db.query(UserOutreachPreferenceORM)
            .filter_by(user_id=int(user_id), companion_id=cid)
            .first()
        )
        if row:
            row.action = action
            row.updated_at = _utcnow()
        else:
            db.add(
                UserOutreachPreferenceORM(
                    user_id=int(user_id),
                    companion_id=cid,
                    action=action,
                )
            )
        if action == "block":
            db.query(OutreachJobORM).filter(
                OutreachJobORM.user_id == int(user_id),
                OutreachJobORM.companion_id == cid,
                OutreachJobORM.status == "pending",
            ).update(
                {"status": "cancelled", "skip_reason": "blocked", "updated_at": _utcnow()},
                synchronize_session=False,
            )
        if action in ("block", "dislike"):
            db.query(UserCompanionStateORM).filter(
                UserCompanionStateORM.user_id == int(user_id),
                UserCompanionStateORM.companion_id == cid,
            ).update(
                {UserCompanionStateORM.affection: 0},
                synchronize_session=False,
            )

    if action in ("block", "dislike"):
        try:
            from core.state import get_companion_manager

            cm = get_companion_manager()
            c = cm.get(cid) if cm else None
            if c is not None and getattr(c, "state", None) is not None:
                c.state.affection = 0.0
        except Exception as e:
            logger.debug("[outreach] zero in-memory affection failed: %s", e)
        # 列表不再展示 → 清掉该会话未读，避免总角标虚高
        try:
            from services.user_chat_unread import clear_user_chat_unread

            clear_user_chat_unread(int(user_id), cid, notify=True)
        except Exception as e:
            logger.debug("[outreach] clear unread on block/dislike failed: %s", e)
    return {"ok": True, "action": action}


def get_outreach_preference(user_id: int, companion_id: str) -> Optional[str]:
    """返回当前生效偏好：block / dislike；无记录或 clear 后返回 None。"""
    cid = _pref_cid(companion_id)
    if not user_id or not cid:
        return None
    with get_db() as db:
        row = (
            db.query(UserOutreachPreferenceORM)
            .filter_by(user_id=int(user_id), companion_id=cid)
            .first()
        )
        if not row:
            return None
        act = (row.action or "").strip().lower()
        if act in ("block", "dislike"):
            return act
        # 脏数据 action='clear' 等：视为无偏好并顺带清理
        if act and act not in ("block", "dislike"):
            try:
                db.delete(row)
            except Exception:
                pass
        return None


def is_outreach_blocked(db, user_id: int, companion_id: str) -> bool:
    cid = _pref_cid(companion_id)
    row = (
        db.query(UserOutreachPreferenceORM)
        .filter_by(user_id=int(user_id), companion_id=cid)
        .first()
    )
    return bool(row and (row.action or "").strip().lower() == "block")


def is_outreach_disliked(db, user_id: int, companion_id: str) -> bool:
    cid = _pref_cid(companion_id)
    row = (
        db.query(UserOutreachPreferenceORM)
        .filter_by(user_id=int(user_id), companion_id=cid)
        .first()
    )
    return bool(row and (row.action or "").strip().lower() == "dislike")


def list_blocked_companion_ids(db, user_id: int) -> set[str]:
    rows = (
        db.query(UserOutreachPreferenceORM.companion_id)
        .filter(
            UserOutreachPreferenceORM.user_id == int(user_id),
            UserOutreachPreferenceORM.action == "block",
        )
        .all()
    )
    return {_pref_cid(r[0]) for r in rows if r[0]}


# ---------- O-22 优先级引擎 ----------

def score_companion_for_outreach(
    db,
    *,
    user_id: int,
    companion_id: str,
    current_session_id: Optional[str] = None,
) -> float:
    """当前会话 > 自建 > 亲密度 > 刚互动 > S 级；dislike 强降权，block 应已过滤。"""
    if is_outreach_blocked(db, user_id, companion_id):
        return -1e9
    score = 0.0
    if current_session_id and companion_id == current_session_id:
        score += 1000.0

    comp = db.query(CompanionORM).filter_by(id=companion_id).first()
    if comp:
        if str(comp.created_by or "") == str(user_id):
            score += 500.0
        tier = (getattr(comp, "tier", None) or "featured").strip().lower()
        if tier == "s":
            score += 100.0
        elif tier == "featured":
            score += 50.0
        elif tier == "ops":
            # 运营责任智能体：可参与触达排序，权重介于 featured 与 candidate 之间
            score += 40.0

    state = (
        db.query(UserCompanionStateORM)
        .filter_by(user_id=user_id, companion_id=companion_id)
        .first()
    )
    if state:
        score += float(state.affection or 0) * 2.0
        score += min(80.0, float(state.turns or 0) * 0.5)

    last = (
        db.query(func.max(ShortTermMessageORM.created_at))
        .filter(
            ShortTermMessageORM.user_id == user_id,
            ShortTermMessageORM.companion_id == companion_id,
        )
        .scalar()
    )
    if last:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        hours = max(0.0, (_utcnow() - last).total_seconds() / 3600.0)
        # 越近互动分越高；超过 14 天衰减为 0
        score += max(0.0, 200.0 - hours * (200.0 / (14 * 24)))

    if is_outreach_disliked(db, user_id, companion_id):
        score -= 800.0

    return score


def pick_best_companion(
    db,
    user_id: int,
    candidate_ids: list[str],
    *,
    current_session_id: Optional[str] = None,
) -> Optional[str]:
    best_id = None
    best_score = -1e18
    for cid in candidate_ids:
        if not cid:
            continue
        s = score_companion_for_outreach(
            db, user_id=user_id, companion_id=cid, current_session_id=current_session_id
        )
        if s > best_score:
            best_score = s
            best_id = cid
    if best_score < -1e8:
        return None
    return best_id


# ---------- O-20 沉默召回 ----------

def _last_user_msg_at(db, user_id: int, companion_id: str) -> Optional[datetime]:
    ts = (
        db.query(func.max(ShortTermMessageORM.created_at))
        .filter(
            ShortTermMessageORM.user_id == user_id,
            ShortTermMessageORM.companion_id == companion_id,
            ShortTermMessageORM.role == "user",
        )
        .scalar()
    )
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _already_sent_silence(db, user_id: int, companion_id: str, job_type: str) -> bool:
    """同一沉默档位每个 (user, companion) 只发一次。"""
    row = (
        db.query(OutreachJobORM.id)
        .filter(
            OutreachJobORM.user_id == user_id,
            OutreachJobORM.companion_id == companion_id,
            OutreachJobORM.job_type == job_type,
            OutreachJobORM.status.in_(("pending", "processing", "sent")),
        )
        .first()
    )
    return row is not None


def scan_and_schedule_silence_recalls(limit_users: int = 200) -> dict:
    """扫描沉默用户并调度 D1/D3/D7/14；每用户每天最多 1 条沉默召回。"""
    from services.outreach import (
        adjust_to_evening_window,
        get_user_timezone,
        _count_recent_sent,
    )

    stats = {"scanned": 0, "scheduled": 0, "skipped": 0}
    now = _utcnow()
    since_day = now - timedelta(hours=24)

    with get_db() as db:
        # 最近有过互动的用户（有 user_companion_states）
        user_ids = [
            r[0]
            for r in (
                db.query(UserCompanionStateORM.user_id)
                .group_by(UserCompanionStateORM.user_id)
                .order_by(desc(func.max(UserCompanionStateORM.updated_at)))
                .limit(limit_users)
                .all()
            )
        ]
        stats["scanned"] = len(user_ids)

        for uid in user_ids:
            # 用户日沉默触达上限
            silence_today = (
                db.query(func.count(OutreachJobORM.id))
                .filter(
                    OutreachJobORM.user_id == uid,
                    OutreachJobORM.job_type.in_(_SILENCE_JOBS),
                    OutreachJobORM.status.in_(("pending", "sent")),
                    or_(
                        OutreachJobORM.sent_at >= since_day,
                        and_(
                            OutreachJobORM.status == "pending",
                            OutreachJobORM.created_at >= since_day,
                        ),
                    ),
                )
                .scalar()
                or 0
            )
            if silence_today >= 1:
                stats["skipped"] += 1
                continue

            states = (
                db.query(UserCompanionStateORM)
                .filter(UserCompanionStateORM.user_id == uid)
                .all()
            )
            # 按沉默档位从短到长匹配；每用户只选一个角色
            matched: Optional[tuple] = None  # (days, job_type, companion_id)
            candidates_by_bucket: dict[str, list[str]] = {j: [] for _, j, _ in _SILENCE_SPECS}

            for st in states:
                cid = st.companion_id
                if is_outreach_blocked(db, uid, cid):
                    continue
                last = _last_user_msg_at(db, uid, cid)
                if not last:
                    continue
                idle_h = (now - last).total_seconds() / 3600.0
                # 达到阈值且该档尚未发送则入桶（避免「错过 ±tol 窗口永久跳过」）
                for days, job_type, _tol_h in _SILENCE_SPECS:
                    target_h = days * 24
                    if idle_h >= target_h and not _already_sent_silence(db, uid, cid, job_type):
                        if not is_outreach_disliked(db, uid, cid):
                            candidates_by_bucket[job_type].append(cid)
                        break

            for days, job_type, _ in _SILENCE_SPECS:
                cands = candidates_by_bucket.get(job_type) or []
                if not cands:
                    continue
                best = pick_best_companion(db, uid, cands)
                if not best:
                    continue
                matched = (days, job_type, best)
                break

            if not matched:
                continue

            days, job_type, companion_id = matched
            if _count_recent_sent(db, user_id=uid, companion_id=companion_id, since=since_day) > 0:
                stats["skipped"] += 1
                continue

            tz = get_user_timezone(uid)
            delay = random.randint(5 * 60, 90 * 60)
            execute_at = adjust_to_evening_window(now + timedelta(seconds=delay), tz)
            name = ""
            row = db.query(CompanionORM).filter_by(id=companion_id).first()
            if row:
                name = row.name or ""

            job = OutreachJobORM(
                job_type=job_type,
                user_id=uid,
                companion_id=companion_id,
                status="pending",
                execute_at=execute_at.replace(tzinfo=None),
                payload={
                    "companion_name": name,
                    "silence_days": days,
                    "tz": tz,
                },
            )
            db.add(job)
            stats["scheduled"] += 1
            logger.info(
                "outreach_scheduled type=%s user=%s companion=%s days=%s",
                job_type,
                uid,
                companion_id,
                days,
            )

    return stats


# ---------- O-23 事件触达 ----------

def maybe_schedule_intimacy_event(
    *,
    user_id: int,
    companion_id: str,
    old_affection: float,
    new_affection: float,
    companion_name: str = "",
) -> Optional[int]:
    crossed = None
    for th in _INTIMACY_THRESHOLDS:
        if old_affection < th <= new_affection:
            crossed = th
            break
    if crossed is None:
        return None
    return _schedule_event_job(
        user_id=user_id,
        companion_id=companion_id,
        job_type=JOB_INTIMACY_UP,
        companion_name=companion_name,
        payload_extra={"threshold": crossed, "affection": new_affection},
        dedupe_key=f"intimacy_{int(crossed)}",
    )


def maybe_schedule_memory_milestone(
    *,
    user_id: int,
    companion_id: str,
    turns: int,
    companion_name: str = "",
) -> Optional[int]:
    hit = None
    for m in _TURN_MILESTONES:
        if turns == m:
            hit = m
            break
    if hit is None:
        return None
    return _schedule_event_job(
        user_id=user_id,
        companion_id=companion_id,
        job_type=JOB_MEMORY_MILESTONE,
        companion_name=companion_name,
        payload_extra={"turns": hit},
        dedupe_key=f"turns_{hit}",
    )


def scan_and_schedule_anniversaries(limit: int = 300) -> dict:
    """首次用户消息的周年日召回（按用户当地时区的月-日）。"""
    from services.outreach import adjust_to_evening_window, get_user_timezone, _count_recent_sent
    from zoneinfo import ZoneInfo

    stats = {"scheduled": 0, "scanned": 0}
    now = _utcnow()
    with get_db() as db:
        # 取每个 (user, companion) 最早用户消息；不做过早 LIMIT，避免漏周年用户
        rows = (
            db.query(
                ShortTermMessageORM.user_id,
                ShortTermMessageORM.companion_id,
                func.min(ShortTermMessageORM.created_at).label("first_at"),
            )
            .filter(
                ShortTermMessageORM.role == "user",
                ShortTermMessageORM.user_id.isnot(None),
            )
            .group_by(ShortTermMessageORM.user_id, ShortTermMessageORM.companion_id)
            .all()
        )
        by_user: dict[int, list[tuple[str, datetime]]] = {}
        for uid, cid, first_at in rows:
            if not uid or not cid or not first_at:
                continue
            if first_at.tzinfo is None:
                first_at = first_at.replace(tzinfo=timezone.utc)
            years = (now - first_at).days // 365
            if years < 1:
                continue
            tz_name = get_user_timezone(int(uid))
            try:
                local_now = now.astimezone(ZoneInfo(tz_name))
                local_first = first_at.astimezone(ZoneInfo(tz_name))
            except Exception:
                local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
                local_first = first_at.astimezone(ZoneInfo("Asia/Shanghai"))
            if local_first.month == local_now.month and local_first.day == local_now.day:
                by_user.setdefault(int(uid), []).append((cid, first_at))

        # 控制本轮调度上限
        scheduled_budget = limit
        for uid, pairs in by_user.items():
            if scheduled_budget <= 0:
                break
            stats["scanned"] += 1
            cands = [c for c, _ in pairs if not is_outreach_disliked(db, uid, c)]
            best = pick_best_companion(db, uid, cands)
            if not best:
                continue
            if is_outreach_blocked(db, uid, best):
                continue
            # 今年是否已发（用户当地年）
            tz_name = get_user_timezone(uid)
            try:
                local_now = now.astimezone(ZoneInfo(tz_name))
            except Exception:
                local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
            year_start_local = local_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            year_start = year_start_local.astimezone(timezone.utc)
            exists = (
                db.query(OutreachJobORM.id)
                .filter(
                    OutreachJobORM.user_id == uid,
                    OutreachJobORM.companion_id == best,
                    OutreachJobORM.job_type == JOB_ANNIVERSARY,
                    OutreachJobORM.status.in_(("pending", "sent")),
                    OutreachJobORM.created_at >= year_start.replace(tzinfo=None),
                )
                .first()
            )
            if exists:
                continue
            since_24h = now - timedelta(hours=24)
            if _count_recent_sent(db, user_id=uid, companion_id=best, since=since_24h) > 0:
                continue
            tz = tz_name
            execute_at = adjust_to_evening_window(now + timedelta(minutes=random.randint(10, 120)), tz)
            name = ""
            row = db.query(CompanionORM).filter_by(id=best).first()
            if row:
                name = row.name or ""
            first_at = next(f for c, f in pairs if c == best)
            years = max(1, (now - first_at).days // 365)
            db.add(
                OutreachJobORM(
                    job_type=JOB_ANNIVERSARY,
                    user_id=uid,
                    companion_id=best,
                    status="pending",
                    execute_at=execute_at.replace(tzinfo=None),
                    payload={
                        "companion_name": name,
                        "years": years,
                        "tz": tz,
                    },
                )
            )
            stats["scheduled"] += 1
            scheduled_budget -= 1
            logger.info("outreach_scheduled type=%s user=%s companion=%s years=%s", JOB_ANNIVERSARY, uid, best, years)
    return stats


def _schedule_event_job(
    *,
    user_id: int,
    companion_id: str,
    job_type: str,
    companion_name: str = "",
    payload_extra: Optional[dict] = None,
    dedupe_key: str = "",
) -> Optional[int]:
    from services.outreach import adjust_to_send_window, get_user_timezone, _count_recent_sent

    with get_db() as db:
        if is_outreach_blocked(db, user_id, companion_id):
            return None
        if is_outreach_disliked(db, user_id, companion_id):
            # 事件类对 dislike 仍跳过（尊重不感兴趣）
            return None
        since_24h = _utcnow() - timedelta(hours=24)
        if _count_recent_sent(db, user_id=user_id, companion_id=companion_id, since=since_24h) > 0:
            return None
        if dedupe_key:
            exists = (
                db.query(OutreachJobORM.id)
                .filter(
                    OutreachJobORM.user_id == user_id,
                    OutreachJobORM.companion_id == companion_id,
                    OutreachJobORM.job_type == job_type,
                    OutreachJobORM.status.in_(("pending", "processing", "sent")),
                )
                .all()
            )
            for (jid,) in exists:
                job = db.query(OutreachJobORM).filter_by(id=jid).first()
                if job and (job.payload or {}).get("dedupe_key") == dedupe_key:
                    return jid

        tz = get_user_timezone(user_id)
        execute_at = adjust_to_send_window(
            _utcnow() + timedelta(seconds=random.randint(60, 600)), tz
        )
        payload = {
            "companion_name": companion_name or "",
            "tz": tz,
            "dedupe_key": dedupe_key,
            **(payload_extra or {}),
        }
        job = OutreachJobORM(
            job_type=job_type,
            user_id=user_id,
            companion_id=companion_id,
            status="pending",
            execute_at=execute_at.replace(tzinfo=None),
            payload=payload,
        )
        db.add(job)
        db.flush()
        logger.info(
            "outreach_scheduled type=%s id=%s user=%s companion=%s",
            job_type,
            job.id,
            user_id,
            companion_id,
        )
        return job.id


def run_phase3_periodic_scans() -> dict:
    """供 scheduler 周期调用：沉默召回 + 纪念日。"""
    out = {}
    try:
        out["silence"] = scan_and_schedule_silence_recalls()
    except Exception as e:
        logger.warning("[phase3] silence scan failed: %s", e)
        out["silence"] = {"error": str(e)}
    try:
        out["anniversary"] = scan_and_schedule_anniversaries()
    except Exception as e:
        logger.warning("[phase3] anniversary scan failed: %s", e)
        out["anniversary"] = {"error": str(e)}
    return out


# ---------- O-24 漏斗统计 ----------

def get_outreach_funnel(days: int = 7) -> dict[str, Any]:
    days = max(1, min(90, int(days or 7)))
    since = _utcnow() - timedelta(days=days)
    with get_db() as db:
        by_type_status = (
            db.query(
                OutreachJobORM.job_type,
                OutreachJobORM.status,
                func.count(OutreachJobORM.id),
            )
            .filter(OutreachJobORM.created_at >= since)
            .group_by(OutreachJobORM.job_type, OutreachJobORM.status)
            .all()
        )
        sent_rows = (
            db.query(OutreachJobORM)
            .filter(
                OutreachJobORM.status == "sent",
                OutreachJobORM.sent_at >= since,
            )
            .all()
        )
        # 批量探测回复：一次查出所有相关用户消息，再内存匹配（避免 N+1）
        reply_keys: set[tuple[int, str]] = set()
        if sent_rows:
            user_ids = {j.user_id for j in sent_rows if j.user_id}
            companion_ids = {j.companion_id for j in sent_rows if j.companion_id}
            min_sent = min(
                (j.sent_at for j in sent_rows if j.sent_at),
                default=None,
            )
            if min_sent is not None and user_ids and companion_ids:
                msg_rows = (
                    db.query(
                        ShortTermMessageORM.user_id,
                        ShortTermMessageORM.companion_id,
                        ShortTermMessageORM.created_at,
                    )
                    .filter(
                        ShortTermMessageORM.user_id.in_(user_ids),
                        ShortTermMessageORM.companion_id.in_(companion_ids),
                        ShortTermMessageORM.role == "user",
                        ShortTermMessageORM.created_at >= min_sent,
                    )
                    .all()
                )
                # 按 (user, companion) 收集消息时间
                from collections import defaultdict

                msgs: dict[tuple[int, str], list] = defaultdict(list)
                for uid, cid, created in msg_rows:
                    msgs[(int(uid), str(cid))].append(created)

                for job in sent_rows:
                    if not job.sent_at:
                        continue
                    sent_at = job.sent_at
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    key = (int(job.user_id), str(job.companion_id))
                    for created in msgs.get(key, []):
                        c = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
                        if c >= sent_at:
                            reply_keys.add((job.id, job.job_type or ""))
                            break

        replied = len({jid for jid, _ in reply_keys})
        moment_sent = sum(1 for j in sent_rows if j.job_type in ("like_dm", "comment_dm"))
        moment_replied = sum(
            1 for jid, jt in reply_keys if jt in ("like_dm", "comment_dm")
        )

        pref_counts = (
            db.query(UserOutreachPreferenceORM.action, func.count(UserOutreachPreferenceORM.id))
            .group_by(UserOutreachPreferenceORM.action)
            .all()
        )

        matrix: dict[str, dict[str, int]] = {}
        for jt, st, cnt in by_type_status:
            matrix.setdefault(jt or "unknown", {})[st or "unknown"] = int(cnt)

        total_sent = len(sent_rows)
        return {
            "days": days,
            "since": serialize_datetime(since),
            "by_type_status": matrix,
            "totals": {
                "sent": total_sent,
                "replied": replied,
                "reply_rate": round(replied / total_sent, 4) if total_sent else 0.0,
                "moment_path_sent": moment_sent,
                "moment_path_replied": moment_replied,
                "moment_path_reply_rate": round(moment_replied / moment_sent, 4) if moment_sent else 0.0,
            },
            "preferences": {a: int(c) for a, c in pref_counts},
        }
