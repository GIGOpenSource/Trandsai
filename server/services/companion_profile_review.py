"""智能体资料审核：用户侧 profile_backgrounds 先入待审，通过后才公开展示。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified

from core.database import (
    CompanionORM,
    CompanionProfileReviewORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from services.companion_manager import normalize_profile_backgrounds


def get_pending_background_review(
    db, companion_id: str
) -> Optional[CompanionProfileReviewORM]:
    cid = (companion_id or "").strip()[:8]
    if not cid:
        return None
    return (
        db.query(CompanionProfileReviewORM)
        .filter(
            CompanionProfileReviewORM.companion_id == cid,
            CompanionProfileReviewORM.status == "pending",
        )
        .order_by(CompanionProfileReviewORM.id.desc())
        .first()
    )


def pending_backgrounds_for(companion_id: str) -> List[str]:
    with get_db() as db:
        row = get_pending_background_review(db, companion_id)
        if not row:
            return []
        return normalize_profile_backgrounds(row.pending_backgrounds)


def attach_owner_background_review(
    payload: dict, *, companion_id: str, viewer_user_id: Optional[int]
) -> dict:
    """创建者看自己的智能体：附带待审背景图；他人只看已公开 profile_backgrounds。"""
    if not isinstance(payload, dict) or viewer_user_id is None:
        return payload
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
    raw_cb = profile.get("created_by")
    if isinstance(raw_cb, dict):
        created_by = str(raw_cb.get("id") or "").strip()
    else:
        created_by = str(raw_cb or "").strip()
    if created_by != str(viewer_user_id):
        return payload

    with get_db() as db:
        pending = get_pending_background_review(db, companion_id)
        if pending:
            bgs = normalize_profile_backgrounds(pending.pending_backgrounds)
            profile["profile_backgrounds_pending"] = bgs
            profile["profile_background_review_status"] = "pending"
            # 创建者本地预览：公开字段仍是已生效；额外用 pending 覆盖展示用字段
            profile["profile_backgrounds"] = bgs
        else:
            profile["profile_backgrounds_pending"] = None
            profile["profile_background_review_status"] = None
            latest = (
                db.query(CompanionProfileReviewORM)
                .filter(
                    CompanionProfileReviewORM.companion_id == (companion_id or "").strip()[:8],
                    CompanionProfileReviewORM.status == "rejected",
                )
                .order_by(CompanionProfileReviewORM.id.desc())
                .first()
            )
            profile["profile_background_reject_reason"] = (
                (latest.reject_reason or "").strip() if latest else ""
            )
    return payload


def submit_background_review(
    *,
    companion_id: str,
    backgrounds: Any,
    submitter_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """用户提交/更新背景图：写入审核池，不改公开 profile_backgrounds。"""
    cid = (companion_id or "").strip()[:8]
    pending_bgs = normalize_profile_backgrounds(backgrounds)
    if not cid:
        return {"ok": False, "error": "companion_not_found"}

    with get_db() as db:
        companion = (
            db.query(CompanionORM)
            .filter(CompanionORM.id == cid, CompanionORM.is_deleted.isnot(True))
            .first()
        )
        if not companion:
            return {"ok": False, "error": "companion_not_found"}

        current = normalize_profile_backgrounds(getattr(companion, "profile_backgrounds", None))
        if pending_bgs == current:
            # 清空：直接生效（无可审内容）
            if not pending_bgs:
                pending = get_pending_background_review(db, cid)
                if pending:
                    pending.status = "rejected"
                    pending.reject_reason = "cleared_by_user"
                    pending.reviewed_by = "system"
                    pending.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                companion.profile_backgrounds = []
                flag_modified(companion, "profile_backgrounds")
                db.commit()
                return {
                    "ok": True,
                    "unchanged": False,
                    "cleared": True,
                    "profile_backgrounds": [],
                    "profile_background_review_status": None,
                }
            return {
                "ok": True,
                "unchanged": True,
                "profile_backgrounds": current,
                "profile_background_review_status": None,
            }

        # 清空已生效图：立即清空公开字段，并关掉旧 pending
        if not pending_bgs:
            pending = get_pending_background_review(db, cid)
            if pending:
                pending.status = "rejected"
                pending.reject_reason = "cleared_by_user"
                pending.reviewed_by = "system"
                pending.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            companion.profile_backgrounds = []
            flag_modified(companion, "profile_backgrounds")
            db.commit()
            return {
                "ok": True,
                "cleared": True,
                "profile_backgrounds": [],
                "profile_background_review_status": None,
            }

        pending = get_pending_background_review(db, cid)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if pending:
            pending.pending_backgrounds = pending_bgs
            pending.current_backgrounds = current
            pending.submitter_user_id = (
                int(submitter_user_id) if submitter_user_id is not None else pending.submitter_user_id
            )
            pending.updated_at = now
            pending.reject_reason = ""
            pending.reject_reason_id = None
            review_id = int(pending.id)
        else:
            row = CompanionProfileReviewORM(
                companion_id=cid,
                submitter_user_id=int(submitter_user_id) if submitter_user_id is not None else None,
                pending_backgrounds=pending_bgs,
                current_backgrounds=current,
                status="pending",
            )
            db.add(row)
            db.flush()
            review_id = int(row.id)

        db.commit()
        return {
            "ok": True,
            "review_id": review_id,
            "profile_backgrounds": current,
            "profile_backgrounds_pending": pending_bgs,
            "profile_background_review_status": "pending",
        }


def list_companion_profile_reviews(
    status: Optional[str] = "pending",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    st = (status or "").strip().lower()
    with get_db() as db:
        count_q = db.query(func.count(CompanionProfileReviewORM.id))
        q = (
            db.query(CompanionProfileReviewORM, CompanionORM, UserORM)
            .outerjoin(CompanionORM, CompanionProfileReviewORM.companion_id == CompanionORM.id)
            .outerjoin(UserORM, CompanionProfileReviewORM.submitter_user_id == UserORM.id)
        )
        if st and st != "all":
            count_q = count_q.filter(CompanionProfileReviewORM.status == st)
            q = q.filter(CompanionProfileReviewORM.status == st)
        total = int(count_q.scalar() or 0)
        rows = (
            q.order_by(CompanionProfileReviewORM.created_at.desc(), CompanionProfileReviewORM.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        items = []
        for review, companion, user in rows:
            items.append({
                "id": int(review.id),
                "companion_id": review.companion_id or "",
                "companion_name": (companion.name if companion else "") or "",
                "companion_avatar_url": (companion.avatar_url if companion else "") or "",
                "submitter_user_id": review.submitter_user_id,
                "submitter_nickname": (
                    ((user.nickname if user else "") or (user.username if user else "") or "").strip()
                ),
                "pending_backgrounds": normalize_profile_backgrounds(review.pending_backgrounds),
                "current_backgrounds": normalize_profile_backgrounds(review.current_backgrounds),
                "status": review.status or "pending",
                "reject_reason": review.reject_reason or "",
                "reject_reason_id": getattr(review, "reject_reason_id", None),
                "reviewed_by": review.reviewed_by or "",
                "reviewed_at": serialize_datetime(review.reviewed_at),
                "created_at": serialize_datetime(review.created_at),
                "updated_at": serialize_datetime(review.updated_at),
            })
        return items, total


def _reject_other_pending(
    db,
    *,
    companion_id: str,
    keep_review_id: int,
    reviewer: str,
    reason: str = "",
    reason_id: Optional[int] = None,
) -> None:
    others = (
        db.query(CompanionProfileReviewORM)
        .filter(
            CompanionProfileReviewORM.companion_id == companion_id,
            CompanionProfileReviewORM.status == "pending",
            CompanionProfileReviewORM.id != int(keep_review_id),
        )
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in others:
        row.status = "rejected"
        row.reject_reason = (reason or "").strip()[:200]
        row.reject_reason_id = int(reason_id) if reason_id is not None else None
        row.reviewed_by = (reviewer or "admin").strip()[:64]
        row.reviewed_at = now


def approve_companion_profile_review(review_id: int, reviewer: str = "admin") -> dict:
    """通过：把待审背景图写入 companions.profile_backgrounds（公开展示）。"""
    with get_db() as db:
        review = (
            db.query(CompanionProfileReviewORM)
            .filter(CompanionProfileReviewORM.id == int(review_id))
            .first()
        )
        if not review:
            return {"ok": False, "error": "review_not_found"}
        if review.status != "pending":
            return {"ok": False, "error": "review_not_pending"}

        companion = db.query(CompanionORM).filter(CompanionORM.id == review.companion_id).first()
        if not companion:
            return {"ok": False, "error": "companion_not_found"}

        bgs = normalize_profile_backgrounds(review.pending_backgrounds)
        companion.profile_backgrounds = bgs
        flag_modified(companion, "profile_backgrounds")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        review.status = "approved"
        review.reviewed_by = (reviewer or "admin").strip()[:64]
        review.reviewed_at = now
        review.reject_reason = ""
        review.reject_reason_id = None
        _reject_other_pending(
            db,
            companion_id=review.companion_id,
            keep_review_id=review.id,
            reviewer=reviewer,
        )
        db.commit()

        try:
            from core.state import get_companion_manager

            get_companion_manager().update(review.companion_id, {"profile_backgrounds": bgs})
        except Exception:
            pass

        return {
            "ok": True,
            "id": int(review.id),
            "status": "approved",
            "companion_id": review.companion_id,
            "profile_backgrounds": bgs,
        }


def reject_companion_profile_review(
    review_id: int,
    reason: str = "",
    reviewer: str = "admin",
    *,
    reason_id: Optional[int] = None,
) -> dict:
    """驳回：不展示待审背景图，公开字段保持原 approved 集合。"""
    with get_db() as db:
        review = (
            db.query(CompanionProfileReviewORM)
            .filter(CompanionProfileReviewORM.id == int(review_id))
            .first()
        )
        if not review:
            return {"ok": False, "error": "review_not_found"}
        if review.status != "pending":
            return {"ok": False, "error": "review_not_pending"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        review.status = "rejected"
        review.reject_reason = (reason or "").strip()[:200]
        review.reject_reason_id = int(reason_id) if reason_id is not None else None
        review.reviewed_by = (reviewer or "admin").strip()[:64]
        review.reviewed_at = now
        _reject_other_pending(
            db,
            companion_id=review.companion_id,
            keep_review_id=review.id,
            reviewer=reviewer,
            reason=reason,
            reason_id=reason_id,
        )
        db.commit()
        return {
            "ok": True,
            "id": int(review.id),
            "status": "rejected",
            "companion_id": review.companion_id,
        }
