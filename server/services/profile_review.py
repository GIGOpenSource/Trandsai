"""用户资料审核：修改先入待审，通过后才写入 users 公开字段。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm.attributes import flag_modified

from core.database import UserORM, UserProfileReviewORM, get_db, serialize_datetime
from core.gender import canonical_preferred_gender, canonical_user_gender
from services.recommend_tags import canonical_orientation

_PROFILE_BACKGROUND_MAX = 3
_PROFILE_BACKGROUND_URL_MAX = 500


def normalize_profile_backgrounds(raw) -> list[str]:
    """与智能体背景图相同：URL 数组或单字符串，去空去重，最多 3 张。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        items = [s] if s else []
    elif isinstance(raw, (list, tuple)):
        items = [str(x or "").strip() for x in raw]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for u in items:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u[:_PROFILE_BACKGROUND_URL_MAX])
        if len(out) >= _PROFILE_BACKGROUND_MAX:
            break
    return out

# 性别 / 年龄 / 性取向 / 匹配偏好：直接写入 users，无需人工审核
IMMEDIATE_PROFILE_FIELDS = frozenset({
    "gender",
    "age",
    "sexual_orientation",
    "preferred_gender",
    "chat_purpose",
})

# 名字、地区、职业、头像：通过前仅自己可见待审值，仍走人工审核
REVIEW_PROFILE_FIELDS = frozenset({
    "nickname",
    "avatar_url",
    "region",
    "occupation",
    "profile_backgrounds",
})

REVIEWABLE_FIELDS = IMMEDIATE_PROFILE_FIELDS | REVIEW_PROFILE_FIELDS


def _normalize_field(field: str, value: Any) -> Any:
    if field == "nickname":
        return (value or "").strip()[:32]
    if field == "avatar_url":
        return (value or "").strip()[:500]
    if field == "profile_backgrounds":
        return normalize_profile_backgrounds(value)
    if field == "region":
        return (value or "").strip()[:120]
    if field == "occupation":
        return (value or "").strip()[:100]
    if field == "age":
        if value is None:
            return None
        try:
            ai = int(value)
        except (TypeError, ValueError):
            return None
        return ai if 0 <= ai <= 1000 else None
    if field == "gender":
        return canonical_user_gender(value)
    if field == "preferred_gender":
        return canonical_preferred_gender(value)
    if field == "sexual_orientation":
        return canonical_orientation(value)
    if isinstance(value, str):
        return value.strip()
    return value


def snapshot_user(user: UserORM) -> dict:
    return {
        "nickname": user.nickname or "",
        "gender": user.gender or "",
        "sexual_orientation": user.sexual_orientation or "",
        "preferred_gender": user.preferred_gender or "",
        "chat_purpose": user.chat_purpose or "",
        "avatar_url": user.avatar_url or "",
        "profile_backgrounds": normalize_profile_backgrounds(
            getattr(user, "profile_backgrounds", None)
        ),
        "age": user.age,
        "region": (user.region or "").strip(),
        "occupation": (user.occupation or "").strip(),
    }


def apply_snapshot_to_user(user: UserORM, data: dict) -> None:
    for field in REVIEWABLE_FIELDS:
        if field not in data:
            continue
        setattr(user, field, _normalize_field(field, data[field]))
        if field == "profile_backgrounds":
            try:
                flag_modified(user, "profile_backgrounds")
            except Exception:
                pass


def get_pending_review(db, user_id: int) -> Optional[UserProfileReviewORM]:
    return (
        db.query(UserProfileReviewORM)
        .filter(
            UserProfileReviewORM.user_id == user_id,
            UserProfileReviewORM.status == "pending",
        )
        .order_by(UserProfileReviewORM.id.desc())
        .first()
    )


def get_latest_reject_reason(db, user_id: int) -> str:
    """最近一次驳回原因（无 pending 时前端可提示）。"""
    row = (
        db.query(UserProfileReviewORM)
        .filter(
            UserProfileReviewORM.user_id == user_id,
            UserProfileReviewORM.status == "rejected",
        )
        .order_by(UserProfileReviewORM.id.desc())
        .first()
    )
    return (row.reject_reason or "").strip() if row else ""


def _reject_other_pending_reviews(
    db,
    *,
    user_id: int,
    keep_review_id: Optional[int],
    reviewer: str,
    reason: str = "",
    reason_id: Optional[int] = None,
) -> None:
    """兼容历史脏数据：同一用户可能残留多个 pending，统一收敛。"""
    q = db.query(UserProfileReviewORM).filter(
        UserProfileReviewORM.user_id == user_id,
        UserProfileReviewORM.status == "pending",
    )
    if keep_review_id is not None:
        q = q.filter(UserProfileReviewORM.id != keep_review_id)
    others = q.all()
    if not others:
        return
    now = datetime.now(timezone.utc)
    trimmed_reason = (reason or "").strip()[:200]
    rid = int(reason_id) if reason_id is not None else None
    for row in others:
        row.status = "rejected"
        row.reject_reason = trimmed_reason
        row.reject_reason_id = rid
        row.reviewed_by = reviewer
        row.reviewed_at = now


def _strip_immediate_from_pending(db, pending: Optional[UserProfileReviewORM]) -> None:
    if not pending or pending.status != "pending":
        return
    pending_data = pending.pending_data or {}
    remaining = {k: v for k, v in pending_data.items() if k not in IMMEDIATE_PROFILE_FIELDS}
    if remaining == pending_data:
        return
    if remaining:
        pending.pending_data = remaining
        pending.updated_at = datetime.now(timezone.utc)
    else:
        db.delete(pending)


def merge_user_view(user: UserORM, pending: Optional[UserProfileReviewORM] = None) -> dict:
    base = snapshot_user(user)
    base["gender"] = canonical_user_gender(base.get("gender"))
    base["preferred_gender"] = canonical_preferred_gender(base.get("preferred_gender"))
    base["sexual_orientation"] = canonical_orientation(base.get("sexual_orientation"))
    status = None
    pending_data = None
    if pending and pending.status == "pending":
        pending_data = {
            k: v for k, v in (pending.pending_data or {}).items()
            if k in REVIEW_PROFILE_FIELDS
        }
        if pending_data:
            status = "pending"
            for k, v in pending_data.items():
                base[k] = v
        else:
            pending_data = None
    pending_bgs = None
    bg_status = None
    if pending_data and "profile_backgrounds" in pending_data:
        pending_bgs = normalize_profile_backgrounds(pending_data.get("profile_backgrounds"))
        bg_status = "pending"
        base["profile_backgrounds"] = pending_bgs
    return {
        **base,
        "profile_review_status": status,
        "profile_pending": pending_data,
        "profile_backgrounds_pending": pending_bgs,
        "profile_background_review_status": bg_status,
    }


def submit_profile_review(user_id: int, update_data: dict) -> dict:
    """提交资料修改进入审核；返回给前端展示的合并后资料。"""
    cleaned = {}
    for field, value in update_data.items():
        if field not in REVIEWABLE_FIELDS:
            continue
        cleaned[field] = _normalize_field(field, value)

    if not cleaned:
        raise ValueError("没有可更新的字段")

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise LookupError("用户不存在")

        current = snapshot_user(user)
        immediate_changes = {
            k: v for k, v in cleaned.items()
            if k in IMMEDIATE_PROFILE_FIELDS and current.get(k) != v
        }
        review_changes = {
            k: v for k, v in cleaned.items()
            if k in REVIEW_PROFILE_FIELDS and current.get(k) != v
        }

        if immediate_changes:
            apply_snapshot_to_user(user, immediate_changes)
            current = snapshot_user(user)

        pending = get_pending_review(db, user_id)
        _strip_immediate_from_pending(db, pending)
        pending = get_pending_review(db, user_id)

        # 清空背景图立即生效（无可审内容），与智能体背景图一致
        if review_changes.get("profile_backgrounds") == []:
            apply_snapshot_to_user(user, {"profile_backgrounds": []})
            current = snapshot_user(user)
            review_changes.pop("profile_backgrounds", None)
            if pending:
                pdata = {
                    k: v
                    for k, v in (pending.pending_data or {}).items()
                    if k != "profile_backgrounds"
                }
                if pdata:
                    pending.pending_data = pdata
                    pending.updated_at = datetime.now(timezone.utc)
                else:
                    db.delete(pending)
                    pending = None

        if not immediate_changes and not review_changes:
            view = merge_user_view(user, pending)
            return {
                "id": user.id,
                "username": user.username,
                **view,
                "unchanged": True,
            }

        if review_changes:
            if pending:
                merged_pending = dict(pending.pending_data or {})
                merged_pending.update(review_changes)
                pending.pending_data = merged_pending
                pending.current_data = current
                pending.updated_at = datetime.now(timezone.utc)
                pending.reject_reason = ""
                pending.reject_reason_id = None
            else:
                pending = UserProfileReviewORM(
                    user_id=user_id,
                    pending_data=review_changes,
                    current_data=current,
                    status="pending",
                )
                db.add(pending)
        db.flush()

        view = merge_user_view(user, pending)
        return {
            "id": user.id,
            "username": user.username,
            **view,
            "unchanged": False,
        }


def list_profile_reviews(status: Optional[str] = "pending", limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    with get_db() as db:
        from sqlalchemy import func

        count_q = db.query(func.count(UserProfileReviewORM.id))
        q = db.query(UserProfileReviewORM, UserORM).outerjoin(
            UserORM, UserProfileReviewORM.user_id == UserORM.id
        )
        if status:
            count_q = count_q.filter(UserProfileReviewORM.status == status)
            q = q.filter(UserProfileReviewORM.status == status)
        total = count_q.scalar() or 0
        rows = (
            q.order_by(UserProfileReviewORM.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        result = []
        for review, user in rows:
            result.append({
                "id": review.id,
                "user_id": review.user_id,
                "username": user.username if user else "",
                "user_name": (user.nickname if user else "") or (user.username if user else ""),
                "pending_data": {
                    k: v
                    for k, v in (review.pending_data or {}).items()
                    if k in REVIEW_PROFILE_FIELDS
                },
                "current_data": review.current_data or {},
                "status": review.status,
                "reject_reason": review.reject_reason or "",
                "reject_reason_id": getattr(review, "reject_reason_id", None),
                "reviewed_by": review.reviewed_by or "",
                "reviewed_at": serialize_datetime(review.reviewed_at),
                "created_at": serialize_datetime(review.created_at),
                "updated_at": serialize_datetime(review.updated_at),
            })
        return result, total


def approve_profile_review(review_id: int, reviewer: str = "admin") -> dict:
    with get_db() as db:
        review = db.query(UserProfileReviewORM).filter(UserProfileReviewORM.id == review_id).first()
        if not review:
            return {"ok": False, "error": "review_not_found"}
        if review.status != "pending":
            return {"ok": False, "error": "review_not_pending"}
        user = db.query(UserORM).filter(UserORM.id == review.user_id).first()
        if not user:
            return {"ok": False, "error": "user_not_found"}
        apply_snapshot_to_user(
            user,
            {
                k: v
                for k, v in (review.pending_data or {}).items()
                if k in REVIEW_PROFILE_FIELDS
            },
        )
        review.status = "approved"
        review.reviewed_by = reviewer
        review.reviewed_at = datetime.now(timezone.utc)
        review.reject_reason = ""
        review.reject_reason_id = None
        _reject_other_pending_reviews(
            db,
            user_id=review.user_id,
            keep_review_id=review.id,
            reviewer=reviewer,
            reason="",
            reason_id=None,
        )
        from services.review_notifications import push_review_approved_notifications

        push_review_approved_notifications(db, review.user_id, "profile")
        db.flush()
        return {"ok": True, "id": review.id, "status": "approved"}


def reject_profile_review(
    review_id: int,
    reason: str = "",
    reviewer: str = "admin",
    *,
    reason_id: Optional[int] = None,
) -> dict:
    with get_db() as db:
        review = db.query(UserProfileReviewORM).filter(UserProfileReviewORM.id == review_id).first()
        if not review:
            return {"ok": False, "error": "review_not_found"}
        if review.status != "pending":
            return {"ok": False, "error": "review_not_pending"}
        review.status = "rejected"
        review.reject_reason = (reason or "").strip()[:200]
        review.reject_reason_id = int(reason_id) if reason_id is not None else None
        review.reviewed_by = reviewer
        review.reviewed_at = datetime.now(timezone.utc)
        _reject_other_pending_reviews(
            db,
            user_id=review.user_id,
            keep_review_id=review.id,
            reviewer=reviewer,
            reason=reason,
            reason_id=reason_id,
        )
        from services.review_notifications import push_review_rejected_notifications

        push_review_rejected_notifications(
            db, review.user_id, "profile", reason=review.reject_reason
        )
        db.flush()
        return {"ok": True, "id": review.id, "status": "rejected"}
