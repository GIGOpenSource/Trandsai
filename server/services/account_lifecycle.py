"""账号注销冷静期 / 解除注销 / 期满销户。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from core.database import (
    ButtonClickORM,
    FactORM,
    FeedbackMessageORM,
    FeedbackThreadORM,
    MomentCommentORM,
    MomentLikeORM,
    PageViewORM,
    PostCommentORM,
    PostLikeORM,
    PostORM,
    ShortTermMessageORM,
    UserCompanionStateORM,
    UserORM,
    UserRelationSummaryORM,
)

logger = logging.getLogger(__name__)

DELETION_GRACE_DAYS = 7

# active=正常 / banned=封禁 / deleted=注销冷静期 / closed=已销户（标识已释放）
STATUS_ACTIVE = "active"
STATUS_BANNED = "banned"
STATUS_DELETED = "deleted"
STATUS_CLOSED = "closed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def deletion_purge_at(deleted_at: Optional[datetime]) -> Optional[datetime]:
    dt = _as_aware(deleted_at)
    if not dt:
        return None
    return dt + timedelta(days=DELETION_GRACE_DAYS)


def days_left_until_purge(deleted_at: Optional[datetime], *, now: Optional[datetime] = None) -> int:
    purge_at = deletion_purge_at(deleted_at)
    if not purge_at:
        return DELETION_GRACE_DAYS
    now = _as_aware(now) or _utcnow()
    secs = (purge_at - now).total_seconds()
    if secs <= 0:
        return 0
    # 向上取整天数，至少显示 1（未到期时）
    days = int((secs + 86399) // 86400)
    return max(1, min(DELETION_GRACE_DAYS, days))


def is_grace_expired(user: UserORM, *, now: Optional[datetime] = None) -> bool:
    purge_at = deletion_purge_at(getattr(user, "deleted_at", None))
    if not purge_at:
        # 历史 deleted 无 deleted_at：视为已到期，下次登录销户
        return True
    now = _as_aware(now) or _utcnow()
    return now >= purge_at


def schedule_account_deletion(db: Session, user: UserORM) -> dict:
    """用户主动注销：进入 7 天冷静期（保留登录标识与业务数据）。"""
    now = _utcnow()
    user.account_status = STATUS_DELETED
    user.deleted_at = now
    user.token = ""
    user.token_expire = None
    # 冷静期内保留 password_hash / platform / provider_user_id，便于恢复
    db.flush()
    return {
        "account_status": STATUS_DELETED,
        "deleted_at": now.isoformat(),
        "purge_at": deletion_purge_at(now).isoformat() if deletion_purge_at(now) else None,
        "grace_days": DELETION_GRACE_DAYS,
    }


def restore_account_from_deletion(db: Session, user: UserORM) -> dict:
    """解除注销：恢复 active。"""
    user.account_status = STATUS_ACTIVE
    user.deleted_at = None
    db.flush()
    return {"account_status": STATUS_ACTIVE}


def purge_user_related_data(db: Session, user_id: int) -> None:
    """销户时清理关联数据（保留 users 行供后台展示）。"""
    db.query(UserCompanionStateORM).filter(UserCompanionStateORM.user_id == user_id).delete()
    db.query(ShortTermMessageORM).filter(ShortTermMessageORM.user_id == user_id).delete()
    db.query(FactORM).filter(FactORM.user_id == user_id).delete()
    db.query(UserRelationSummaryORM).filter(UserRelationSummaryORM.user_id == user_id).delete()
    db.query(MomentLikeORM).filter(MomentLikeORM.user_id == user_id).delete()
    db.query(PostLikeORM).filter(PostLikeORM.user_id == user_id).delete()
    db.query(MomentCommentORM).filter(MomentCommentORM.user_id == user_id).delete()
    db.query(PostCommentORM).filter(PostCommentORM.user_id == user_id).delete()
    db.query(PostORM).filter(PostORM.user_id == user_id).delete()

    threads = db.query(FeedbackThreadORM).filter(FeedbackThreadORM.user_id == user_id).all()
    for t in threads:
        db.query(FeedbackMessageORM).filter(FeedbackMessageORM.thread_id == t.id).delete()
        db.delete(t)

    db.query(PageViewORM).filter(PageViewORM.user_id == user_id).delete()
    db.query(ButtonClickORM).filter(ButtonClickORM.user_id == user_id).delete()


def finalize_account_closure(db: Session, user: UserORM) -> dict:
    """冷静期结束：销户 + 清空登录唯一标识 + 清理关联数据。"""
    uid = int(user.id)
    purge_user_related_data(db, uid)
    user.account_status = STATUS_CLOSED
    if not getattr(user, "deleted_at", None):
        user.deleted_at = _utcnow()
    # 释放登录标识，允许同 Google/Apple/邮箱重新注册新号
    user.username = f"closed_{uid}"
    user.password_hash = ""
    user.platform = "local"
    user.provider_user_id = None
    user.token = ""
    user.token_expire = None
    db.flush()
    logger.info("account closed user_id=%s", uid)
    return {"account_status": STATUS_CLOSED, "user_id": uid}


def pending_deletion_payload(user: UserORM) -> dict:
    deleted_at = _as_aware(getattr(user, "deleted_at", None)) or _utcnow()
    purge_at = deletion_purge_at(deleted_at)
    left = days_left_until_purge(deleted_at)
    return {
        "detail": "account_pending_deletion",
        "account_status": STATUS_DELETED,
        "can_restore": True,
        "grace_days": DELETION_GRACE_DAYS,
        "days_left": left,
        "deleted_at": deleted_at.isoformat(),
        "purge_at": purge_at.isoformat() if purge_at else None,
        "user_id": int(user.id),
    }


def handle_deleted_user_on_login(db: Session, user: UserORM) -> tuple[str, dict]:
    """
    登录命中已注销用户。
    返回 (action, payload)：
      - pending: 冷静期内，应 401 提示可恢复
      - closed: 已期满并完成销户，应 403
      - closed_already: 本来就是 closed
    """
    st = (getattr(user, "account_status", None) or "").strip().lower()
    if st in ("closed", "deactivated") or st == STATUS_CLOSED:
        return "closed_already", {
            "detail": "account_closed",
            "account_status": STATUS_CLOSED,
        }
    if st not in ("deleted", "cancelled", "canceled"):
        return "ok", {}

    if is_grace_expired(user):
        finalize_account_closure(db, user)
        return "closed", {
            "detail": "account_closed",
            "account_status": STATUS_CLOSED,
            "message_key": "account_closed",
        }
    return "pending", pending_deletion_payload(user)
