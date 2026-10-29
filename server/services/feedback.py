"""Feedback 官方客服：每用户一条长期会话。不自动发消息，等人工业务回复。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.database import FeedbackMessageORM, FeedbackThreadORM, UserORM
from core.i18n import (
    FEEDBACK_ENTRY_MSGID,
    localize_feedback_auto_reply,
)

logger = logging.getLogger(__name__)

AUTO_REPLY_DELAY_S = max(10, int(os.getenv("FEEDBACK_AUTO_REPLY_DELAY_S", "60") or 60))
OFFICIAL_SENDERS = ("system", "admin")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def serialize_message(row: FeedbackMessageORM, lang: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": row.id,
        "sender": row.sender,
        "content": localize_feedback_auto_reply(row.content or "", lang=lang),
        "created_at": _iso(row.created_at),
    }


def get_user_thread(db: Session, user_id: int) -> Optional[FeedbackThreadORM]:
    return (
        db.query(FeedbackThreadORM)
        .filter(FeedbackThreadORM.user_id == int(user_id))
        .order_by(FeedbackThreadORM.id.asc())
        .first()
    )


def get_or_create_user_thread(db: Session, user: UserORM) -> FeedbackThreadORM:
    thread = get_user_thread(db, int(user.id))
    if thread:
        name = (user.nickname or user.username or "").strip()
        if name and thread.user_name != name[:32]:
            thread.user_name = name[:32]
        return thread
    thread = FeedbackThreadORM(
        user_id=int(user.id),
        user_name=((user.nickname or user.username or "")[:32]),
        status="open",
        welcome_sent=False,
    )
    db.add(thread)
    db.flush()
    return thread


def ensure_welcome(db: Session, thread: FeedbackThreadORM) -> Optional[FeedbackMessageORM]:
    """已关闭：Feedback 不再自动发欢迎语，只等人工业务回复。"""
    return None


def _unread_count(
    db: Session,
    thread: FeedbackThreadORM,
    *,
    senders: Tuple[str, ...],
    last_read_at: Optional[datetime],
) -> int:
    q = db.query(func.count(FeedbackMessageORM.id)).filter(
        FeedbackMessageORM.thread_id == thread.id,
        FeedbackMessageORM.sender.in_(senders),
    )
    if last_read_at is not None:
        q = q.filter(FeedbackMessageORM.created_at > last_read_at)
    return int(q.scalar() or 0)


def user_unread_count(db: Session, thread: FeedbackThreadORM) -> int:
    return _unread_count(
        db,
        thread,
        senders=OFFICIAL_SENDERS,
        last_read_at=getattr(thread, "user_last_read_at", None),
    )


def admin_unread_count(db: Session, thread: FeedbackThreadORM) -> int:
    return _unread_count(
        db,
        thread,
        senders=("user",),
        last_read_at=getattr(thread, "admin_last_read_at", None),
    )


def mark_user_read(thread: FeedbackThreadORM) -> None:
    thread.user_last_read_at = _utc_now()


def mark_admin_read(thread: FeedbackThreadORM) -> None:
    thread.admin_last_read_at = _utc_now()


def last_message_row(db: Session, thread_id: int) -> Optional[FeedbackMessageORM]:
    return (
        db.query(FeedbackMessageORM)
        .filter(FeedbackMessageORM.thread_id == thread_id)
        .order_by(FeedbackMessageORM.created_at.desc(), FeedbackMessageORM.id.desc())
        .first()
    )


def empty_inbox_payload(lang: Optional[str] = None) -> Dict[str, Any]:
    """从未发过 / 已清空：列表仍置顶客服，只是没有最近聊天时间。"""
    return {
        "thread_id": None,
        "pinned": True,
        "has_chat": False,
        "status": "open",
        "entry_name": localize_feedback_auto_reply(FEEDBACK_ENTRY_MSGID, lang=lang),
        "unread_count": 0,
        "last_message": "",
        "last_message_sender": "",
        "last_message_at": None,
        "updated_at": None,
    }


def inbox_payload(
    db: Session,
    thread: Optional[FeedbackThreadORM],
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    if not thread:
        return empty_inbox_payload(lang)
    last = last_message_row(db, thread.id)
    has_chat = bool(last)
    return {
        "thread_id": thread.id,
        "pinned": True,
        "has_chat": has_chat,
        "status": thread.status,
        "entry_name": localize_feedback_auto_reply(FEEDBACK_ENTRY_MSGID, lang=lang),
        "unread_count": user_unread_count(db, thread) if has_chat else 0,
        "last_message": localize_feedback_auto_reply(last.content, lang=lang) if last else "",
        "last_message_sender": last.sender if last else "",
        "last_message_at": _iso(last.created_at) if last else None,
        "updated_at": _iso(thread.updated_at),
    }


def clear_thread_messages(db: Session, thread: FeedbackThreadORM) -> int:
    """用户清空客服聊天记录。列表仍置顶，has_chat=false 故不展示最近聊天时间。"""
    n = (
        db.query(FeedbackMessageORM)
        .filter(FeedbackMessageORM.thread_id == thread.id)
        .delete(synchronize_session=False)
    )
    now = _utc_now()
    thread.last_user_msg_at = None
    thread.auto_reply_after_user_at = None
    thread.status = "open"
    thread.updated_at = now
    mark_user_read(thread)
    mark_admin_read(thread)
    return int(n or 0)


def list_messages(
    db: Session,
    thread: FeedbackThreadORM,
    *,
    lang: Optional[str] = None,
    since_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    q = db.query(FeedbackMessageORM).filter(FeedbackMessageORM.thread_id == thread.id)
    if since_id:
        q = q.filter(FeedbackMessageORM.id > int(since_id))
    rows = q.order_by(FeedbackMessageORM.created_at.asc(), FeedbackMessageORM.id.asc()).all()
    return [serialize_message(m, lang=lang) for m in rows]


def send_user_message(db: Session, thread: FeedbackThreadORM, content: str) -> FeedbackMessageORM:
    now = _utc_now()
    msg = FeedbackMessageORM(thread_id=thread.id, sender="user", content=content)
    db.add(msg)
    thread.last_user_msg_at = now
    thread.status = "open"
    thread.updated_at = now
    mark_user_read(thread)
    db.flush()
    return msg


def send_admin_reply(db: Session, thread: FeedbackThreadORM, content: str) -> FeedbackMessageORM:
    now = _utc_now()
    msg = FeedbackMessageORM(thread_id=thread.id, sender="admin", content=content)
    db.add(msg)
    thread.status = "replied"
    thread.updated_at = now
    mark_admin_read(thread)
    db.flush()
    return msg


def _has_official_after(db: Session, thread_id: int, after_at: datetime) -> bool:
    row = (
        db.query(FeedbackMessageORM.id)
        .filter(
            FeedbackMessageORM.thread_id == thread_id,
            FeedbackMessageORM.sender.in_(OFFICIAL_SENDERS),
            FeedbackMessageORM.created_at > after_at,
        )
        .first()
    )
    return row is not None


def thread_due_for_auto_reply(
    db: Session,
    thread: FeedbackThreadORM,
    now: Optional[datetime] = None,
) -> bool:
    last_user = getattr(thread, "last_user_msg_at", None)
    if not last_user:
        return False
    if last_user.tzinfo is None:
        last_user = last_user.replace(tzinfo=timezone.utc)
    now = now or _utc_now()
    if now < last_user + timedelta(seconds=AUTO_REPLY_DELAY_S):
        return False
    already = getattr(thread, "auto_reply_after_user_at", None)
    if already is not None:
        if already.tzinfo is None:
            already = already.replace(tzinfo=timezone.utc)
        if already >= last_user:
            return False
    if _has_official_after(db, thread.id, last_user):
        return False
    return True


def fire_auto_reply(db: Session, thread: FeedbackThreadORM) -> Optional[FeedbackMessageORM]:
    """已关闭：不再 1 分钟自动回复。"""
    return None


def fire_due_auto_replies(db: Session, *, limit: int = 50) -> int:
    """已关闭：扫描器即使误启动也不会插入 system 消息。"""
    return 0


def admin_list_threads(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    has_unread: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    q = db.query(FeedbackThreadORM)
    unread_expr = (
        db.query(func.count(FeedbackMessageORM.id))
        .filter(
            FeedbackMessageORM.thread_id == FeedbackThreadORM.id,
            FeedbackMessageORM.sender == "user",
            or_(
                FeedbackThreadORM.admin_last_read_at.is_(None),
                FeedbackMessageORM.created_at > FeedbackThreadORM.admin_last_read_at,
            ),
        )
        .correlate(FeedbackThreadORM)
        .as_scalar()
    )
    if status and status.strip():
        q = q.filter(FeedbackThreadORM.status == status.strip())
    if user_id:
        q = q.filter(FeedbackThreadORM.user_id == int(user_id))
    if has_unread is True:
        q = q.filter(unread_expr > 0)
    elif has_unread is False:
        q = q.filter(func.coalesce(unread_expr, 0) == 0)
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        user_ids = [
            r[0]
            for r in db.query(UserORM.id)
            .filter(
                or_(
                    UserORM.username.ilike(like),
                    UserORM.nickname.ilike(like),
                )
            )
            .all()
        ]
        clauses = [FeedbackThreadORM.user_name.ilike(like)]
        if user_ids:
            clauses.append(FeedbackThreadORM.user_id.in_(user_ids))
        q = q.filter(or_(*clauses))

    q = q.order_by(
        FeedbackThreadORM.last_user_msg_at.desc().nullslast(),
        FeedbackThreadORM.updated_at.desc(),
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    user_map: Dict[int, UserORM] = {}
    uids = [t.user_id for t in rows if t.user_id]
    if uids:
        for u in db.query(UserORM).filter(UserORM.id.in_(uids)).all():
            user_map[int(u.id)] = u

    items: List[Dict[str, Any]] = []
    for t in rows:
        last = last_message_row(db, t.id)
        unread = admin_unread_count(db, t)
        u = user_map.get(int(t.user_id)) if t.user_id else None
        items.append(
            {
                "id": t.id,
                "user_id": t.user_id,
                "user_name": t.user_name,
                "username": (u.username if u else "") or "",
                "nickname": (u.nickname if u else "") or "",
                "language": (u.language if u else "") or "",
                "status": t.status,
                "created_at": _iso(t.created_at),
                "updated_at": _iso(t.updated_at),
                "last_user_msg_at": _iso(getattr(t, "last_user_msg_at", None)),
                "last_message": last.content if last else "",
                "last_message_sender": last.sender if last else "",
                "last_message_at": _iso(last.created_at) if last else None,
                "unread_count": unread,
            }
        )
    return items, total
