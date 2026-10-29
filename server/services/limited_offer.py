"""限时优惠会话：登录且未付费的用户首次拉状态即开始倒计时；付费或到期后停止。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.app_key import DEFAULT_APP_KEY, normalize_app_key
from core.database import UserLimitedOfferORM, get_db

logger = logging.getLogger(__name__)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def remaining_sec(row: Optional[UserLimitedOfferORM], now: Optional[datetime] = None) -> int:
    if not row or row.used_at or not row.expires_at:
        return 0
    now = now or datetime.now(timezone.utc)
    exp = _aware(row.expires_at)
    return max(0, int((exp - now).total_seconds()))


def _get_or_create(db, user_id: int, app_key: str) -> UserLimitedOfferORM:
    key = normalize_app_key(app_key) or DEFAULT_APP_KEY
    row = (
        db.query(UserLimitedOfferORM)
        .filter(
            UserLimitedOfferORM.user_id == int(user_id),
            UserLimitedOfferORM.app_key == key,
        )
        .first()
    )
    if row:
        return row
    row = UserLimitedOfferORM(
        user_id=int(user_id),
        app_key=key,
        product_id=None,
        last_scene="",
    )
    db.add(row)
    db.flush()
    return row


def ensure_started(
    *,
    user_id: int,
    app_key: str,
    product_id: Optional[int] = None,
    duration_sec: int = 0,
) -> UserLimitedOfferORM:
    """未付费用户第一次拉限时状态时开始倒计时；已开始/已用/到期不重开。"""
    now = datetime.now(timezone.utc)
    with get_db() as db:
        row = _get_or_create(db, user_id, app_key)
        if product_id:
            row.product_id = int(product_id)
        if not row.used_at and not row.started_at and int(duration_sec or 0) > 0:
            dur = max(1, int(duration_sec))
            row.started_at = now
            row.expires_at = now + timedelta(seconds=dur)
            if not row.first_paywall_at:
                row.first_paywall_at = now
        row.updated_at = now
        db.flush()
        db.expunge(row)
        return row


def load_offer(user_id: int, app_key: str) -> Optional[UserLimitedOfferORM]:
    key = normalize_app_key(app_key) or DEFAULT_APP_KEY
    with get_db() as db:
        row = (
            db.query(UserLimitedOfferORM)
            .filter(
                UserLimitedOfferORM.user_id == int(user_id),
                UserLimitedOfferORM.app_key == key,
            )
            .first()
        )
        if row:
            db.expunge(row)
        return row


def mark_limited_offer_used(db, user_id: Optional[int]) -> None:
    if not user_id:
        return
    now = datetime.now(timezone.utc)
    rows = (
        db.query(UserLimitedOfferORM)
        .filter(
            UserLimitedOfferORM.user_id == int(user_id),
            UserLimitedOfferORM.used_at.is_(None),
        )
        .all()
    )
    for row in rows:
        row.used_at = now
        row.updated_at = now


def status_flags(row: Optional[UserLimitedOfferORM], *, paid: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    left = remaining_sec(row, now) if not paid else 0
    used = bool(row and row.used_at) or paid
    started = bool(row and row.started_at)
    expired = started and left <= 0 and not used
    show = bool(started and left > 0 and not used)
    reason = ""
    if used or paid:
        reason = "already_paid"
    elif expired:
        reason = "expired"
    elif not started:
        reason = "not_started"
    return {
        "show": show,
        "first_paywall": bool(row and row.first_paywall_at),
        "started": started and not used,
        "remaining_sec": left if show else 0,
        "expires_at": _aware(row.expires_at).isoformat() if row and row.expires_at and show else None,
        "product_id": int(row.product_id) if row and row.product_id and show else None,
        "session_reason": reason,
    }
