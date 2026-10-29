"""IM 回复模式：AI 立即回 / 优先人工等待超时后交 AI。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.database import (
    ImHumanFirstPendingORM,
    ImReplySettingsORM,
    get_db,
    serialize_datetime,
)

logger = logging.getLogger(__name__)

REPLY_MODE_AI = "ai"
REPLY_MODE_HUMAN_FIRST = "human_first"
REPLY_MODES = (REPLY_MODE_AI, REPLY_MODE_HUMAN_FIRST)

WAIT_SECONDS_MIN = 5
WAIT_SECONDS_MAX = 600
WAIT_SECONDS_DEFAULT = 30
SETTINGS_ROW_ID = 1


def clamp_wait_seconds(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = WAIT_SECONDS_DEFAULT
    return max(WAIT_SECONDS_MIN, min(WAIT_SECONDS_MAX, n))


def normalize_reply_mode(raw: Any) -> str:
    v = (str(raw or "")).strip().lower()
    aliases = {
        "ai": REPLY_MODE_AI,
        "ai_reply": REPLY_MODE_AI,
        "human_first": REPLY_MODE_HUMAN_FIRST,
        "human": REPLY_MODE_HUMAN_FIRST,
        "prefer_human": REPLY_MODE_HUMAN_FIRST,
    }
    mode = aliases.get(v, "")
    if mode not in REPLY_MODES:
        raise ValueError("invalid_reply_mode")
    return mode


def settings_to_dict(row: ImReplySettingsORM) -> dict:
    mode = row.reply_mode if row.reply_mode in REPLY_MODES else REPLY_MODE_AI
    return {
        "reply_mode": mode,
        "wait_seconds": clamp_wait_seconds(row.wait_seconds),
        "updated_by": row.updated_by or "",
        "updated_at": serialize_datetime(row.updated_at),
    }


def get_or_create_settings() -> dict:
    with get_db() as db:
        row = (
            db.query(ImReplySettingsORM)
            .filter(ImReplySettingsORM.id == SETTINGS_ROW_ID)
            .first()
        )
        if not row:
            row = ImReplySettingsORM(
                id=SETTINGS_ROW_ID,
                reply_mode=REPLY_MODE_AI,
                wait_seconds=WAIT_SECONDS_DEFAULT,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return settings_to_dict(row)


def save_settings(
    *,
    reply_mode: str,
    wait_seconds: int,
    updated_by: str = "",
) -> dict:
    mode = normalize_reply_mode(reply_mode)
    wait = clamp_wait_seconds(wait_seconds)
    with get_db() as db:
        row = (
            db.query(ImReplySettingsORM)
            .filter(ImReplySettingsORM.id == SETTINGS_ROW_ID)
            .first()
        )
        if not row:
            row = ImReplySettingsORM(id=SETTINGS_ROW_ID)
            db.add(row)
        row.reply_mode = mode
        row.wait_seconds = wait
        row.updated_by = (updated_by or "").strip()[:64]
        row.updated_at = datetime.now(timezone.utc)
        if mode == REPLY_MODE_AI:
            db.query(ImHumanFirstPendingORM).delete(synchronize_session=False)
        db.commit()
        db.refresh(row)
        return settings_to_dict(row)


def _is_session_taken_over(user_id: int, companion_id: str) -> bool:
    from services.im_admin import is_session_taken_over

    return is_session_taken_over(int(user_id), companion_id)


def _companion_has_ops_admin(companion_id: str) -> bool:
    from services.im_admin import get_session_ops_admin

    with get_db() as db:
        return get_session_ops_admin(db, companion_id) is not None


def should_defer_auto_reply(user_id: int, companion_id: str) -> Tuple[bool, int]:
    """
    是否暂缓自动回复（快速回复 + AI）。
    条件：全局 human_first + 该智能体有责任运营 + 当前未接管。
    无责任人时不等待（没人可接，直接走自动回复）。
    """
    settings = get_or_create_settings()
    if settings.get("reply_mode") != REPLY_MODE_HUMAN_FIRST:
        return False, 0
    cid = (companion_id or "").strip()[:8]
    if not cid:
        return False, 0
    if _is_session_taken_over(int(user_id), cid):
        return False, 0
    if not _companion_has_ops_admin(cid):
        return False, 0
    return True, int(settings.get("wait_seconds") or WAIT_SECONDS_DEFAULT)


def upsert_pending(user_id: int, companion_id: str, wait_seconds: int) -> None:
    cid = (companion_id or "").strip()[:8]
    uid = int(user_id)
    wait = clamp_wait_seconds(wait_seconds)
    now = datetime.now(timezone.utc)
    due = now + timedelta(seconds=wait)
    with get_db() as db:
        row = (
            db.query(ImHumanFirstPendingORM)
            .filter(
                ImHumanFirstPendingORM.user_id == uid,
                ImHumanFirstPendingORM.companion_id == cid,
            )
            .first()
        )
        if row:
            row.due_at = due
            row.created_at = now
        else:
            db.add(
                ImHumanFirstPendingORM(
                    user_id=uid,
                    companion_id=cid,
                    due_at=due,
                    created_at=now,
                )
            )
        db.commit()


def cancel_pending(user_id: int, companion_id: str) -> bool:
    cid = (companion_id or "").strip()[:8]
    uid = int(user_id)
    with get_db() as db:
        n = (
            db.query(ImHumanFirstPendingORM)
            .filter(
                ImHumanFirstPendingORM.user_id == uid,
                ImHumanFirstPendingORM.companion_id == cid,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return bool(n)


def try_defer_human_first(user_id: int, companion_id: str) -> bool:
    """命中优先人工则写入等待队列并返回 True（调用方应跳过 QR/AI）。"""
    defer, wait = should_defer_auto_reply(user_id, companion_id)
    if not defer:
        return False
    upsert_pending(user_id, companion_id, wait)
    logger.info(
        "[im_reply_mode] defer AI user=%s companion=%s wait=%ss",
        user_id,
        companion_id,
        wait,
    )
    return True


def claim_due_pending(limit: int = 50) -> List[Tuple[int, str]]:
    """领取已到期等待项（skip_locked，多 worker 不重复）。"""
    now = datetime.now(timezone.utc)
    claimed: List[Tuple[int, str]] = []
    with get_db() as db:
        rows = (
            db.query(ImHumanFirstPendingORM)
            .filter(ImHumanFirstPendingORM.due_at <= now)
            .order_by(ImHumanFirstPendingORM.due_at.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, int(limit)))
            .all()
        )
        for row in rows:
            claimed.append((int(row.user_id), str(row.companion_id)[:8]))
            db.delete(row)
        db.commit()
    return claimed


async def fire_due_human_first_jobs(limit: int = 50) -> int:
    """到期：仍未接管且用户消息未回 → 交 AI。已接管则丢弃（暂停 AI）。"""
    from services.im_admin import (
        catchup_ai_after_takeover_release,
        is_session_taken_over,
    )

    claimed = claim_due_pending(limit=limit)
    fired = 0
    for uid, cid in claimed:
        try:
            if is_session_taken_over(uid, cid):
                logger.info(
                    "[im_reply_mode] due skipped (taken over) user=%s companion=%s",
                    uid,
                    cid,
                )
                continue
            await catchup_ai_after_takeover_release(uid, cid)
            fired += 1
        except Exception:
            logger.exception(
                "[im_reply_mode] due AI failed user=%s companion=%s",
                uid,
                cid,
            )
    return fired
