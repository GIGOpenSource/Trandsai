"""用户资料举报 / 帖子举报：理由复用 companion_report.REPORT_REASONS。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from core.database import (
    PostORM,
    PostReportORM,
    UserORM,
    UserReportORM,
    get_db,
    serialize_datetime,
)
from services.companion_report import (
    _priority_for,
    _resolve_reason,
    list_report_reasons,
    reason_meta,
)

USER_REPORT_SOURCES = frozenset({"profile", "chat", "moments"})
POST_REPORT_SOURCES = frozenset({"feed", "detail", "profile", "moments"})


def list_ugc_report_reasons() -> List[Dict[str, Any]]:
    return list_report_reasons()


# ----- user reports -----


def _user_report_to_dict(
    report: UserReportORM,
    reporter: Optional[UserORM],
    reported: Optional[UserORM],
) -> Dict[str, Any]:
    meta = reason_meta(report.reason_code or "", reason_id=getattr(report, "reason_id", None)) or {}
    return {
        "id": int(report.id),
        "reporter_user_id": int(report.reporter_user_id),
        "reporter_nickname": (
            ((reporter.nickname if reporter else "") or (reporter.username if reporter else "") or "").strip()
        ),
        "reported_user_id": int(report.reported_user_id),
        "reported_nickname": (
            ((reported.nickname if reported else "") or (reported.username if reported else "") or "").strip()
        ),
        "reported_avatar_url": (reported.avatar_url if reported else "") or "",
        "reason_id": int(getattr(report, "reason_id", None) or meta.get("id") or 0) or None,
        "reason_code": report.reason_code or "",
        "reason_label_zh": meta.get("label_zh") or report.reason_code,
        "reason_label_en": meta.get("label_en") or report.reason_code,
        "priority": report.priority or meta.get("priority") or "",
        "internal_action": meta.get("internal_action") or "",
        "remark": report.remark or "",
        "content": report.content or "",
        "source": report.source or "profile",
        "status": report.status or "pending",
        "auto_hidden": bool(report.auto_hidden),
        "reported_account_status": (
            (getattr(reported, "account_status", None) if reported else "") or "active"
        ),
        "review_note": report.review_note or "",
        "reviewed_by": report.reviewed_by or "",
        "reviewed_at": serialize_datetime(report.reviewed_at),
        "created_at": serialize_datetime(report.created_at),
        "updated_at": serialize_datetime(report.updated_at),
    }


def submit_user_report(
    *,
    reporter_user_id: int,
    reported_user_id: int,
    reason_code: str = "",
    reason_id: Optional[int] = None,
    remark: str = "",
    content: str = "",
    source: str = "profile",
) -> Dict[str, Any]:
    if int(reporter_user_id) == int(reported_user_id):
        return {"ok": False, "error": "cannot_report_self"}

    meta, err = _resolve_reason(reason_id=reason_id, reason_code=reason_code)
    if err or not meta:
        return {"ok": False, "error": err or "invalid_reason_code"}

    code = str(meta["reason_code"])
    rid = int(meta["id"])
    src = (source or "profile").strip().lower() or "profile"
    note = (remark or "").strip()
    text = (content or "").strip()
    if src not in USER_REPORT_SOURCES:
        return {"ok": False, "error": "invalid_source"}
    if len(note) > 2000:
        return {"ok": False, "error": "remark_too_long"}
    if len(text) > 5000:
        return {"ok": False, "error": "content_too_long"}
    if code == "other" and not note and not text:
        return {"ok": False, "error": "remark_or_content_required"}

    with get_db() as db:
        reported = db.query(UserORM).filter(UserORM.id == int(reported_user_id)).first()
        if not reported:
            return {"ok": False, "error": "user_not_found"}

        pending = (
            db.query(UserReportORM)
            .filter(
                UserReportORM.reporter_user_id == int(reporter_user_id),
                UserReportORM.reported_user_id == int(reported_user_id),
                UserReportORM.status == "pending",
            )
            .order_by(UserReportORM.id.desc())
            .first()
        )
        if pending:
            return {
                "ok": True,
                "report_id": int(pending.id),
                "status": pending.status,
                "auto_hidden": True,
                "duplicate": True,
            }

        before = (getattr(reported, "account_status", None) or "active").strip() or "active"
        priority = _priority_for(code, reason_id=rid)
        row = UserReportORM(
            reporter_user_id=int(reporter_user_id),
            reported_user_id=int(reported_user_id),
            reason_id=rid,
            reason_code=code,
            remark=note[:2000],
            content=text[:5000],
            source=src[:20],
            status="pending",
            priority=priority,
            auto_hidden=True,
            account_status_before=before[:20],
        )
        db.add(row)
        db.flush()

        # P0：提交即封禁待复审
        if priority == "P0":
            from core.auth import apply_account_status_flags

            reported.account_status = "banned"
            apply_account_status_flags(int(reported.id), "banned")

        db.commit()
        db.refresh(row)
        return {
            "ok": True,
            "report_id": int(row.id),
            "status": row.status,
            "auto_hidden": True,
            "duplicate": False,
            "reason_id": rid,
            "reason_code": code,
        }


def list_user_reports(
    *,
    status: Optional[str] = None,
    reported_user_id: Optional[int] = None,
    reporter_user_id: Optional[int] = None,
    reason_code: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    st = (status or "").strip().lower()
    rc = (reason_code or "").strip()

    with get_db() as db:
        from sqlalchemy.orm import aliased

        Reporter = aliased(UserORM)
        Reported = aliased(UserORM)
        q = (
            db.query(UserReportORM, Reporter, Reported)
            .outerjoin(Reporter, UserReportORM.reporter_user_id == Reporter.id)
            .outerjoin(Reported, UserReportORM.reported_user_id == Reported.id)
        )
        count_q = db.query(func.count(UserReportORM.id))
        if st and st != "all":
            q = q.filter(UserReportORM.status == st)
            count_q = count_q.filter(UserReportORM.status == st)
        if reported_user_id:
            q = q.filter(UserReportORM.reported_user_id == int(reported_user_id))
            count_q = count_q.filter(UserReportORM.reported_user_id == int(reported_user_id))
        if reporter_user_id:
            q = q.filter(UserReportORM.reporter_user_id == int(reporter_user_id))
            count_q = count_q.filter(UserReportORM.reporter_user_id == int(reporter_user_id))
        if rc:
            q = q.filter(UserReportORM.reason_code == rc)
            count_q = count_q.filter(UserReportORM.reason_code == rc)
        total = int(count_q.scalar() or 0)
        rows = (
            q.order_by(UserReportORM.created_at.desc(), UserReportORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return (
            [_user_report_to_dict(r, reporter, reported) for r, reporter, reported in rows],
            total,
        )


def get_user_report(report_id: int) -> Optional[Dict[str, Any]]:
    from sqlalchemy.orm import aliased

    with get_db() as db:
        Reporter = aliased(UserORM)
        Reported = aliased(UserORM)
        row = (
            db.query(UserReportORM, Reporter, Reported)
            .outerjoin(Reporter, UserReportORM.reporter_user_id == Reporter.id)
            .outerjoin(Reported, UserReportORM.reported_user_id == Reported.id)
            .filter(UserReportORM.id == int(report_id))
            .first()
        )
        if not row:
            return None
        report, reporter, reported = row
        return _user_report_to_dict(report, reporter, reported)


def approve_user_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核通过 = 举报成立：封禁被举报用户。"""
    with get_db() as db:
        report = db.query(UserReportORM).filter(UserReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}
        user = db.query(UserORM).filter(UserORM.id == int(report.reported_user_id)).first()
        if not user:
            return {"ok": False, "error": "user_not_found"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        report.status = "approved"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now
        report.updated_at = now

        from core.auth import apply_account_status_flags

        user.account_status = "banned"
        apply_account_status_flags(int(user.id), "banned")
        db.commit()
        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "approved",
            "reported_user_id": int(report.reported_user_id),
        }


def reject_user_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核驳回 = 举报不成立：若无其他 pending 且此前因本单 P0 封禁则恢复。"""
    with get_db() as db:
        report = db.query(UserReportORM).filter(UserReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}
        user = db.query(UserORM).filter(UserORM.id == int(report.reported_user_id)).first()
        if not user:
            return {"ok": False, "error": "user_not_found"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        report.status = "rejected"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now
        report.updated_at = now

        other_pending = (
            db.query(func.count(UserReportORM.id))
            .filter(
                UserReportORM.reported_user_id == report.reported_user_id,
                UserReportORM.status == "pending",
                UserReportORM.id != report.id,
            )
            .scalar()
            or 0
        )
        if int(other_pending) == 0 and (report.priority or "") == "P0":
            restore = (report.account_status_before or "active").strip() or "active"
            if restore not in ("active", "banned", "deleted", "closed"):
                restore = "active"
            from core.auth import apply_account_status_flags

            user.account_status = restore
            apply_account_status_flags(int(user.id), restore)

        db.commit()
        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "rejected",
            "reported_user_id": int(report.reported_user_id),
        }


# ----- post reports -----


def _post_report_to_dict(
    report: PostReportORM,
    reporter: Optional[UserORM],
    post: Optional[PostORM],
) -> Dict[str, Any]:
    meta = reason_meta(report.reason_code or "", reason_id=getattr(report, "reason_id", None)) or {}
    images = []
    if post and getattr(post, "images", None):
        images = list(post.images or []) if isinstance(post.images, (list, tuple)) else []
    return {
        "id": int(report.id),
        "reporter_user_id": int(report.reporter_user_id),
        "reporter_nickname": (
            ((reporter.nickname if reporter else "") or (reporter.username if reporter else "") or "").strip()
        ),
        "post_id": int(report.post_id),
        "post_title": (post.title if post else "") or "",
        "post_content": ((post.content if post else "") or "")[:500],
        "post_images": images[:9],
        "post_user_id": int(post.user_id) if post and post.user_id else None,
        "post_user_name": (post.user_name if post else "") or "",
        "reason_id": int(getattr(report, "reason_id", None) or meta.get("id") or 0) or None,
        "reason_code": report.reason_code or "",
        "reason_label_zh": meta.get("label_zh") or report.reason_code,
        "reason_label_en": meta.get("label_en") or report.reason_code,
        "priority": report.priority or meta.get("priority") or "",
        "internal_action": meta.get("internal_action") or "",
        "remark": report.remark or "",
        "content": report.content or "",
        "source": report.source or "feed",
        "status": report.status or "pending",
        "auto_hidden": bool(report.auto_hidden),
        "post_review_status": (getattr(post, "review_status", None) if post else "") or "",
        "review_note": report.review_note or "",
        "reviewed_by": report.reviewed_by or "",
        "reviewed_at": serialize_datetime(report.reviewed_at),
        "created_at": serialize_datetime(report.created_at),
        "updated_at": serialize_datetime(report.updated_at),
    }


def submit_post_report(
    *,
    reporter_user_id: int,
    post_id: int,
    reason_code: str = "",
    reason_id: Optional[int] = None,
    remark: str = "",
    content: str = "",
    source: str = "feed",
) -> Dict[str, Any]:
    meta, err = _resolve_reason(reason_id=reason_id, reason_code=reason_code)
    if err or not meta:
        return {"ok": False, "error": err or "invalid_reason_code"}

    code = str(meta["reason_code"])
    rid = int(meta["id"])
    src = (source or "feed").strip().lower() or "feed"
    note = (remark or "").strip()
    text = (content or "").strip()
    if src not in POST_REPORT_SOURCES:
        return {"ok": False, "error": "invalid_source"}
    if len(note) > 2000:
        return {"ok": False, "error": "remark_too_long"}
    if len(text) > 5000:
        return {"ok": False, "error": "content_too_long"}
    if code == "other" and not note and not text:
        return {"ok": False, "error": "remark_or_content_required"}

    with get_db() as db:
        post = db.query(PostORM).filter(PostORM.id == int(post_id)).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}
        if post.user_id and int(post.user_id) == int(reporter_user_id):
            return {"ok": False, "error": "cannot_report_own_post"}

        pending = (
            db.query(PostReportORM)
            .filter(
                PostReportORM.reporter_user_id == int(reporter_user_id),
                PostReportORM.post_id == int(post_id),
                PostReportORM.status == "pending",
            )
            .order_by(PostReportORM.id.desc())
            .first()
        )
        if pending:
            return {
                "ok": True,
                "report_id": int(pending.id),
                "status": pending.status,
                "auto_hidden": True,
                "duplicate": True,
            }

        before = (getattr(post, "review_status", None) or "approved").strip() or "approved"
        priority = _priority_for(code, reason_id=rid)
        row = PostReportORM(
            reporter_user_id=int(reporter_user_id),
            post_id=int(post_id),
            reason_id=rid,
            reason_code=code,
            remark=note[:2000],
            content=text[:5000],
            source=src[:20],
            status="pending",
            priority=priority,
            auto_hidden=True,
            post_status_before=before[:20],
        )
        db.add(row)
        db.flush()

        # P0：提交即下架帖子
        if priority == "P0":
            post.review_status = "rejected"
            post.reject_reason = "reported_p0"
            post.reviewed_by = "system"
            post.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        db.commit()
        db.refresh(row)
        return {
            "ok": True,
            "report_id": int(row.id),
            "status": row.status,
            "auto_hidden": True,
            "duplicate": False,
            "reason_id": rid,
            "reason_code": code,
        }


def list_post_reports(
    *,
    status: Optional[str] = None,
    post_id: Optional[int] = None,
    reporter_user_id: Optional[int] = None,
    reason_code: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    st = (status or "").strip().lower()
    rc = (reason_code or "").strip()

    with get_db() as db:
        q = (
            db.query(PostReportORM, UserORM, PostORM)
            .outerjoin(UserORM, PostReportORM.reporter_user_id == UserORM.id)
            .outerjoin(PostORM, PostReportORM.post_id == PostORM.id)
        )
        count_q = db.query(func.count(PostReportORM.id))
        if st and st != "all":
            q = q.filter(PostReportORM.status == st)
            count_q = count_q.filter(PostReportORM.status == st)
        if post_id:
            q = q.filter(PostReportORM.post_id == int(post_id))
            count_q = count_q.filter(PostReportORM.post_id == int(post_id))
        if reporter_user_id:
            q = q.filter(PostReportORM.reporter_user_id == int(reporter_user_id))
            count_q = count_q.filter(PostReportORM.reporter_user_id == int(reporter_user_id))
        if rc:
            q = q.filter(PostReportORM.reason_code == rc)
            count_q = count_q.filter(PostReportORM.reason_code == rc)
        total = int(count_q.scalar() or 0)
        rows = (
            q.order_by(PostReportORM.created_at.desc(), PostReportORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return (
            [_post_report_to_dict(r, reporter, post) for r, reporter, post in rows],
            total,
        )


def get_post_report(report_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        row = (
            db.query(PostReportORM, UserORM, PostORM)
            .outerjoin(UserORM, PostReportORM.reporter_user_id == UserORM.id)
            .outerjoin(PostORM, PostReportORM.post_id == PostORM.id)
            .filter(PostReportORM.id == int(report_id))
            .first()
        )
        if not row:
            return None
        report, reporter, post = row
        return _post_report_to_dict(report, reporter, post)


def approve_post_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核通过 = 举报成立：下架帖子（review_status=rejected）。"""
    with get_db() as db:
        report = db.query(PostReportORM).filter(PostReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}
        post = db.query(PostORM).filter(PostORM.id == int(report.post_id)).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        report.status = "approved"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now
        report.updated_at = now

        post.review_status = "rejected"
        post.reject_reason = (review_note or "report_approved").strip()[:200] or "report_approved"
        post.reviewed_by = (reviewer or "admin").strip()[:50]
        post.reviewed_at = now
        db.commit()
        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "approved",
            "post_id": int(report.post_id),
        }


def reject_post_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核驳回 = 举报不成立：若无其他 pending 且此前因 P0 下架则恢复。"""
    with get_db() as db:
        report = db.query(PostReportORM).filter(PostReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}
        post = db.query(PostORM).filter(PostORM.id == int(report.post_id)).first()
        if not post:
            return {"ok": False, "error": "post_not_found"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        report.status = "rejected"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now
        report.updated_at = now

        other_pending = (
            db.query(func.count(PostReportORM.id))
            .filter(
                PostReportORM.post_id == report.post_id,
                PostReportORM.status == "pending",
                PostReportORM.id != report.id,
            )
            .scalar()
            or 0
        )
        if int(other_pending) == 0 and (report.priority or "") == "P0":
            restore = (report.post_status_before or "approved").strip() or "approved"
            if restore not in ("pending", "approved", "rejected"):
                restore = "approved"
            post.review_status = restore
            post.reject_reason = ""
            post.reviewed_by = (reviewer or "admin").strip()[:50]
            post.reviewed_at = now

        db.commit()
        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "rejected",
            "post_id": int(report.post_id),
        }


def hidden_post_ids_for_user(user_id: int) -> List[int]:
    """举报人对 pending/approved 举报的帖子立即隐藏。"""
    with get_db() as db:
        rows = (
            db.query(PostReportORM.post_id)
            .filter(
                PostReportORM.reporter_user_id == int(user_id),
                PostReportORM.status.in_(("pending", "approved")),
            )
            .all()
        )
        return [int(r[0]) for r in rows if r and r[0]]
