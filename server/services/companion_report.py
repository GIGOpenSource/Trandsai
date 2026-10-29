"""智能体举报：理由字典、提交、审核、举报人隐藏。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func

from core.database import (
    CompanionORM,
    CompanionReportORM,
    ShortTermMessageORM,
    UserORM,
    get_db,
    serialize_datetime,
)

logger = logging.getLogger(__name__)

REPORT_REASONS: Tuple[Dict[str, Any], ...] = (
    {
        "id": 1,
        "reason_code": "minor_safety",
        "label_zh": "涉及未成年人",
        "label_en": "Involving Minors",
        "internal_action": "即时全网下架 + 人工复审，红线",
        "priority": "P0",
    },
    {
        "id": 2,
        "reason_code": "sensitive",
        "label_zh": "敏感话题",
        "label_en": "Sensitive Topics",
        "internal_action": "含 NSFW / 政治宗教等，进审核队列",
        "priority": "P1",
    },
    {
        "id": 3,
        "reason_code": "disturbing",
        "label_zh": "令人不适的内容",
        "label_en": "Disturbing Content",
        "internal_action": "含暴力 / 血腥 / 自残，进审核队列",
        "priority": "P1",
    },
    {
        "id": 4,
        "reason_code": "copyright",
        "label_zh": "版权侵权",
        "label_en": "Copyright Infringement",
        "internal_action": "进审核队列",
        "priority": "P1",
    },
    {
        "id": 5,
        "reason_code": "other",
        "label_zh": "其他",
        "label_en": "Other",
        "internal_action": "选填说明，进通用队列",
        "priority": "P1",
    },
)

REPORT_REASON_CODES = {r["reason_code"] for r in REPORT_REASONS}
REPORT_REASON_IDS = {int(r["id"]) for r in REPORT_REASONS}
REPORT_SCENES = frozenset({"companion", "chat"})  # companion=背景图资料举报；chat=对话举报
REPORT_SOURCES = frozenset({"profile", "chat", "moments"})
REPORT_STATUSES = frozenset({"pending", "approved", "rejected"})
SCENE_LABELS = {
    "companion": {"zh": "资料/背景图", "en": "Profile / Background"},
    "chat": {"zh": "对话", "en": "Chat message"},
}


def reason_meta(reason_code: str = "", *, reason_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if reason_id is not None:
        try:
            rid = int(reason_id)
        except (TypeError, ValueError):
            rid = None
        if rid is not None:
            for row in REPORT_REASONS:
                if int(row["id"]) == rid:
                    return dict(row)
    code = (reason_code or "").strip()
    for row in REPORT_REASONS:
        if row["reason_code"] == code:
            return dict(row)
    return None


def list_report_reasons() -> List[Dict[str, Any]]:
    return [dict(r) for r in REPORT_REASONS]


def _priority_for(reason_code: str, *, reason_id: Optional[int] = None) -> str:
    meta = reason_meta(reason_code, reason_id=reason_id)
    return (meta or {}).get("priority") or "P1"


def _resolve_reason(
    *,
    reason_id: Optional[int] = None,
    reason_code: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """解析理由；优先 reason_id（分类 id），其次 reason_code。失败返回 (None, error)。"""
    meta = None
    if reason_id is not None and str(reason_id).strip() != "":
        try:
            rid = int(reason_id)
        except (TypeError, ValueError):
            return None, "invalid_reason_id"
        meta = reason_meta(reason_id=rid)
        if not meta:
            return None, "invalid_reason_id"
    else:
        code = (reason_code or "").strip()
        if not code:
            return None, "invalid_reason_code"
        meta = reason_meta(code)
        if not meta:
            return None, "invalid_reason_code"
    return meta, None


def hidden_companion_ids_for_user(db, user_id: int) -> Set[str]:
    """仅 auto_hidden=True 的历史举报仍整卡隐藏；新对话/背景图举报不再隐藏智能体。"""
    if not user_id:
        return set()
    rows = (
        db.query(CompanionReportORM.companion_id)
        .filter(
            CompanionReportORM.reporter_user_id == int(user_id),
            CompanionReportORM.status.in_(("pending", "approved")),
            CompanionReportORM.auto_hidden.is_(True),
        )
        .all()
    )
    return {str(r[0]).strip() for r in rows if r and r[0]}


def globally_inactive_companion_ids(db) -> Set[str]:
    """全网下架（is_active=false）的智能体。"""
    rows = (
        db.query(CompanionORM.id)
        .filter(CompanionORM.is_active.is_(False))
        .all()
    )
    return {str(r[0]).strip() for r in rows if r and r[0]}


def list_excluded_companion_ids_for_user(
    db, user_id: int, *, include_outreach: bool = True
) -> Set[str]:
    """C 端列表/Feed 需排除：举报人隐藏 + 全网下架；默认再排除 block/dislike。

    include_outreach=False：不按触达偏好过滤（「我创建的」列表仍要展示）。
    """
    from core.database import UserOutreachPreferenceORM

    excluded = set(hidden_companion_ids_for_user(db, int(user_id)))
    excluded |= globally_inactive_companion_ids(db)
    if include_outreach and user_id:
        outreach = {
            str(r[0]).strip()[:8]
            for r in db.query(UserOutreachPreferenceORM.companion_id)
            .filter(
                UserOutreachPreferenceORM.user_id == int(user_id),
                # 仅 block/dislike 隐藏；clear 后无行或脏数据 action=clear 均不隐藏
                func.lower(UserOutreachPreferenceORM.action).in_(("block", "dislike")),
            )
            .all()
            if r and r[0]
        }
        excluded |= outreach
    return excluded


def is_companion_hidden_for_user(user_id: int, companion_id: str) -> bool:
    cid = (companion_id or "").strip()
    if not user_id or not cid:
        return False
    with get_db() as db:
        return cid in hidden_companion_ids_for_user(db, int(user_id))


def _apply_companion_moderation(
    db,
    companion: CompanionORM,
    *,
    moderation_status: str,
    is_active: Optional[bool] = None,
) -> None:
    companion.moderation_status = (moderation_status or "published").strip()[:20]
    if is_active is not None:
        companion.is_active = bool(is_active)


def _sync_companion_cache(companion_id: str, *, is_active: bool, moderation_status: str) -> None:
    cid = (companion_id or "").strip()
    if not cid:
        return
    try:
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
            if row:
                row.is_active = bool(is_active)
                row.moderation_status = (moderation_status or "published").strip()[:20]
        from core.state import get_companion_manager

        cm = get_companion_manager()
        if cm and cid in cm._companions:
            cm._companions.pop(cid, None)
        try:
            from services.companion_cache_bridge import (
                ACTION_REFRESH,
                publish_companion_invalidate,
            )

            publish_companion_invalidate(cid, action=ACTION_REFRESH)
        except Exception:
            logger.debug(
                "companion cache invalidate after report sync skipped companion=%s",
                cid,
                exc_info=True,
            )
    except Exception:
        logger.debug("companion report cache sync skipped companion=%s", cid, exc_info=True)


def filter_companion_list_items(
    items: List[Dict[str, Any]],
    user_id: int,
    *,
    include_outreach: bool = True,
) -> List[Dict[str, Any]]:
    """C 端列表：过滤举报人隐藏 + 已下架智能体（与 SQL 层排除规则一致）。"""
    if not items or not user_id:
        return items
    with get_db() as db:
        excluded = list_excluded_companion_ids_for_user(
            db, int(user_id), include_outreach=include_outreach
        )
    out: List[Dict[str, Any]] = []
    for item in items:
        pid = ((item.get("profile") or {}).get("id") or "").strip()
        if not pid or pid in excluded:
            continue
        out.append(item)
    return out


def assert_companion_visible_to_user(user_id: int, companion_id: str) -> Optional[str]:
    """不可见时返回 error code，否则 None。"""
    cid = (companion_id or "").strip()
    if is_companion_hidden_for_user(user_id, cid):
        return "companion_hidden_by_report"
    with get_db() as db:
        row = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
        if not row or row.is_deleted:
            return "companion_not_found"
        if not getattr(row, "is_active", True):
            return "companion_not_available"
    return None


def _resolve_reported_message(
    db,
    *,
    companion_id: str,
    reporter_user_id: int,
    message_id: Optional[int],
    content: str,
) -> Tuple[Optional[ShortTermMessageORM], Optional[str]]:
    """定位被举报的对话消息；优先 message_id，其次同房匹配 content。"""
    cid = (companion_id or "").strip()[:8]
    mid = None
    if message_id is not None and str(message_id).strip() != "":
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            return None, "invalid_message_id"

    if mid is not None:
        row = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.id == mid,
                ShortTermMessageORM.companion_id == cid,
                ShortTermMessageORM.user_id == int(reporter_user_id),
            )
            .first()
        )
        if not row:
            return None, "message_not_found"
        if getattr(row, "is_hidden", False):
            return None, "message_already_hidden"
        return row, None

    text = (content or "").strip()
    if not text:
        return None, "message_id_or_content_required"
    row = (
        db.query(ShortTermMessageORM)
        .filter(
            ShortTermMessageORM.companion_id == cid,
            ShortTermMessageORM.user_id == int(reporter_user_id),
            ShortTermMessageORM.content == text,
            (ShortTermMessageORM.is_hidden.is_(False)) | (ShortTermMessageORM.is_hidden.is_(None)),
        )
        .order_by(ShortTermMessageORM.id.desc())
        .first()
    )
    if not row:
        return None, "message_not_found"
    return row, None


def _hide_chat_message(db, message: ShortTermMessageORM) -> None:
    message.is_hidden = True


def _clear_companion_backgrounds(db, companion: CompanionORM) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from core.database import CompanionProfileReviewORM

    companion.profile_backgrounds = []
    flag_modified(companion, "profile_backgrounds")
    pending_reviews = (
        db.query(CompanionProfileReviewORM)
        .filter(
            CompanionProfileReviewORM.companion_id == companion.id,
            CompanionProfileReviewORM.status == "pending",
        )
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for rev in pending_reviews:
        rev.status = "rejected"
        rev.reject_reason = "cleared_by_profile_report"
        rev.reviewed_by = "system"
        rev.reviewed_at = now


def _purge_message_room_cache(companion_id: str, user_id: int) -> None:
    cid = (companion_id or "").strip()[:8]
    uid = int(user_id)
    try:
        from core.chat_cache import purge_room_cache

        purge_room_cache(cid, user_id=uid)
    except Exception:
        logger.debug(
            "purge room cache after hide message skipped companion=%s user=%s",
            cid,
            uid,
            exc_info=True,
        )
    try:
        from services import memory as memory_mod

        key = f"{cid}:{uid}"
        with memory_mod._ring_buffer_lock:
            memory_mod._ring_buffers.pop(key, None)
    except Exception:
        logger.debug(
            "purge ring buffer after hide message skipped companion=%s user=%s",
            cid,
            uid,
            exc_info=True,
        )


def submit_companion_report(
    *,
    reporter_user_id: int,
    companion_id: str,
    reason_code: str = "",
    reason_id: Optional[int] = None,
    remark: str = "",
    content: str = "",
    message_id: Optional[int] = None,
    scene: str = "companion",
    source: str = "profile",
) -> Dict[str, Any]:
    """提交智能体举报（对话 / 资料背景图共用）。

    - scene=chat：绑定 message_id（或 content 定位），通过后隐藏该条消息
    - scene=companion：资料/背景图举报，通过后清空背景图；不下架智能体
    """
    cid = (companion_id or "").strip()[:8]
    scn = (scene or "companion").strip().lower()
    src = (source or "profile").strip().lower()
    note = (remark or "").strip()
    text = (content or "").strip()

    meta, err = _resolve_reason(reason_id=reason_id, reason_code=reason_code)
    if err or not meta:
        return {"ok": False, "error": err or "invalid_reason_code"}

    code = str(meta["reason_code"])
    rid = int(meta["id"])

    if scn not in REPORT_SCENES:
        return {"ok": False, "error": "invalid_scene"}
    if not (source or "").strip():
        src = "chat" if scn == "chat" else "profile"
    if src not in REPORT_SOURCES:
        return {"ok": False, "error": "invalid_source"}
    if len(note) > 2000:
        return {"ok": False, "error": "remark_too_long"}
    if len(text) > 5000:
        return {"ok": False, "error": "content_too_long"}
    if code == "other" and not note and not text:
        return {"ok": False, "error": "remark_or_content_required"}

    with get_db() as db:
        companion = (
            db.query(CompanionORM)
            .filter(CompanionORM.id == cid, CompanionORM.is_deleted.isnot(True))
            .first()
        )
        if not companion:
            return {"ok": False, "error": "companion_not_found"}

        resolved_mid: Optional[int] = None
        if scn == "chat":
            msg, msg_err = _resolve_reported_message(
                db,
                companion_id=cid,
                reporter_user_id=int(reporter_user_id),
                message_id=message_id,
                content=text,
            )
            if msg_err or not msg:
                return {"ok": False, "error": msg_err or "message_not_found"}
            resolved_mid = int(msg.id)
            if not text:
                text = (msg.content or "").strip()[:5000]

        # 去重：对话按 message_id；资料按 scene=companion
        pending_q = db.query(CompanionReportORM).filter(
            CompanionReportORM.reporter_user_id == int(reporter_user_id),
            CompanionReportORM.companion_id == cid,
            CompanionReportORM.status == "pending",
            CompanionReportORM.scene == scn,
        )
        if scn == "chat" and resolved_mid is not None:
            pending_q = pending_q.filter(CompanionReportORM.message_id == resolved_mid)
        pending = pending_q.order_by(CompanionReportORM.id.desc()).first()
        if pending:
            return {
                "ok": True,
                "report_id": int(pending.id),
                "status": pending.status,
                "auto_hidden": False,
                "duplicate": True,
                "scene": scn,
                "message_id": getattr(pending, "message_id", None),
                "message": "report_already_pending",
            }

        priority = _priority_for(code, reason_id=rid)
        before_status = (getattr(companion, "moderation_status", None) or "published").strip()

        row = CompanionReportORM(
            reporter_user_id=int(reporter_user_id),
            companion_id=cid,
            reason_id=rid,
            reason_code=code,
            remark=note[:2000],
            content=text[:5000],
            message_id=resolved_mid,
            scene=scn[:20],
            source=src[:20],
            status="pending",
            priority=priority,
            auto_hidden=False,
            companion_status_before=before_status[:20],
        )
        db.add(row)
        db.flush()
        # 对话/背景图举报：不整卡下架；仅进审核池
        db.commit()
        db.refresh(row)

        return {
            "ok": True,
            "report_id": int(row.id),
            "status": row.status,
            "auto_hidden": False,
            "duplicate": False,
            "reason_id": rid,
            "reason_code": code,
            "scene": scn,
            "message_id": resolved_mid,
            "message": "report_submitted",
        }


def _report_to_dict(
    report: CompanionReportORM,
    reporter: Optional[UserORM],
    companion: Optional[CompanionORM],
) -> Dict[str, Any]:
    meta = reason_meta(report.reason_code or "", reason_id=getattr(report, "reason_id", None)) or {}
    reporter_nickname = ""
    if reporter:
        reporter_nickname = (reporter.nickname or reporter.username or "").strip()
    scn = (getattr(report, "scene", None) or "companion").strip() or "companion"
    labels = SCENE_LABELS.get(scn) or SCENE_LABELS["companion"]
    backgrounds = []
    if companion is not None:
        from services.companion_manager import normalize_profile_backgrounds

        backgrounds = normalize_profile_backgrounds(
            getattr(companion, "profile_backgrounds", None)
        )
    return {
        "id": int(report.id),
        "reporter_user_id": int(report.reporter_user_id),
        "reporter_nickname": reporter_nickname,
        "companion_id": report.companion_id or "",
        "companion_name": (companion.name if companion else "") or "",
        "companion_avatar_url": (companion.avatar_url if companion else "") or "",
        "companion_profile_backgrounds": backgrounds,
        "reason_id": int(getattr(report, "reason_id", None) or meta.get("id") or 0) or None,
        "reason_code": report.reason_code or "",
        "reason_label_zh": meta.get("label_zh") or report.reason_code,
        "reason_label_en": meta.get("label_en") or report.reason_code,
        "priority": report.priority or meta.get("priority") or "",
        "internal_action": meta.get("internal_action") or "",
        "remark": report.remark or "",
        "content": getattr(report, "content", None) or "",
        "message_id": getattr(report, "message_id", None),
        "scene": scn,
        "scene_label_zh": labels["zh"],
        "scene_label_en": labels["en"],
        "source": report.source or "",
        "status": report.status or "pending",
        "auto_hidden": bool(report.auto_hidden),
        "companion_moderation_status": (
            (companion.moderation_status if companion else "") or "published"
        ),
        "companion_is_active": bool(getattr(companion, "is_active", True)) if companion else True,
        "review_note": report.review_note or "",
        "reviewed_by": report.reviewed_by or "",
        "reviewed_at": serialize_datetime(report.reviewed_at),
        "created_at": serialize_datetime(report.created_at),
        "updated_at": serialize_datetime(report.updated_at),
        "approve_effect": (
            "hide_message" if scn == "chat" else "clear_profile_backgrounds"
        ),
        "chat_detail": {
            "user_id": int(report.reporter_user_id),
            "companion_id": report.companion_id or "",
            "message_id": getattr(report, "message_id", None),
            "messages_api": "/api/admin/im/sessions/messages",
        },
    }


def list_companion_reports(
    *,
    status: Optional[str] = None,
    scene: Optional[str] = None,
    companion_id: Optional[str] = None,
    reporter_user_id: Optional[int] = None,
    reason_code: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    st = (status or "").strip().lower()
    scn = (scene or "").strip().lower()
    cid = (companion_id or "").strip()
    rc = (reason_code or "").strip()

    with get_db() as db:
        q = db.query(CompanionReportORM, UserORM, CompanionORM).outerjoin(
            UserORM, CompanionReportORM.reporter_user_id == UserORM.id
        ).outerjoin(
            CompanionORM, CompanionReportORM.companion_id == CompanionORM.id
        )
        count_q = db.query(func.count(CompanionReportORM.id))

        if st and st != "all":
            q = q.filter(CompanionReportORM.status == st)
            count_q = count_q.filter(CompanionReportORM.status == st)
        if scn and scn != "all":
            q = q.filter(CompanionReportORM.scene == scn)
            count_q = count_q.filter(CompanionReportORM.scene == scn)
        if cid:
            q = q.filter(CompanionReportORM.companion_id == cid)
            count_q = count_q.filter(CompanionReportORM.companion_id == cid)
        if reporter_user_id:
            q = q.filter(CompanionReportORM.reporter_user_id == int(reporter_user_id))
            count_q = count_q.filter(CompanionReportORM.reporter_user_id == int(reporter_user_id))
        if rc:
            q = q.filter(CompanionReportORM.reason_code == rc)
            count_q = count_q.filter(CompanionReportORM.reason_code == rc)

        total = int(count_q.scalar() or 0)
        rows = (
            q.order_by(CompanionReportORM.created_at.desc(), CompanionReportORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [_report_to_dict(report, reporter, companion) for report, reporter, companion in rows]
        return items, total


def get_companion_report(report_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        row = (
            db.query(CompanionReportORM, UserORM, CompanionORM)
            .outerjoin(UserORM, CompanionReportORM.reporter_user_id == UserORM.id)
            .outerjoin(CompanionORM, CompanionReportORM.companion_id == CompanionORM.id)
            .filter(CompanionReportORM.id == int(report_id))
            .first()
        )
        if not row:
            return None
        report, reporter, companion = row
        return _report_to_dict(report, reporter, companion)


def approve_companion_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核通过 = 举报成立：

    - scene=chat → 隐藏该条对话（C 端不展示）
    - scene=companion → 清空资料背景图（不下架智能体）
    """
    with get_db() as db:
        report = db.query(CompanionReportORM).filter(CompanionReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}

        companion = db.query(CompanionORM).filter(CompanionORM.id == report.companion_id).first()
        if not companion:
            return {"ok": False, "error": "companion_not_found"}

        scn = (getattr(report, "scene", None) or "companion").strip() or "companion"
        effect = "clear_profile_backgrounds"
        if scn == "chat":
            msg, msg_err = _resolve_reported_message(
                db,
                companion_id=report.companion_id,
                reporter_user_id=int(report.reporter_user_id),
                message_id=getattr(report, "message_id", None),
                content=report.content or "",
            )
            if msg_err == "message_already_hidden" and getattr(report, "message_id", None):
                msg = (
                    db.query(ShortTermMessageORM)
                    .filter(ShortTermMessageORM.id == int(report.message_id))
                    .first()
                )
                msg_err = None if msg else "message_not_found"
            if msg_err or not msg:
                return {"ok": False, "error": msg_err or "message_not_found"}
            _hide_chat_message(db, msg)
            report.message_id = int(msg.id)
            effect = "hide_message"
        else:
            _clear_companion_backgrounds(db, companion)
            effect = "clear_profile_backgrounds"

        now = datetime.now(timezone.utc)
        report.status = "approved"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now.replace(tzinfo=None)
        report.updated_at = now.replace(tzinfo=None)
        db.commit()

        if scn == "chat":
            _purge_message_room_cache(report.companion_id, int(report.reporter_user_id))
        else:
            try:
                from core.state import get_companion_manager

                get_companion_manager().update(
                    report.companion_id, {"profile_backgrounds": []}
                )
            except Exception:
                logger.debug(
                    "sync empty backgrounds after report approve skipped companion=%s",
                    report.companion_id,
                    exc_info=True,
                )

        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "approved",
            "companion_id": report.companion_id,
            "scene": scn,
            "effect": effect,
            "message_id": getattr(report, "message_id", None),
        }


def reject_companion_report(report_id: int, *, reviewer: str = "admin", review_note: str = "") -> Dict[str, Any]:
    """审核驳回 = 举报不成立：对话/背景图均保持原样（不下架也不恢复整卡）。"""
    with get_db() as db:
        report = db.query(CompanionReportORM).filter(CompanionReportORM.id == int(report_id)).first()
        if not report:
            return {"ok": False, "error": "report_not_found"}
        if report.status != "pending":
            return {"ok": False, "error": "report_not_pending"}

        now = datetime.now(timezone.utc)
        report.status = "rejected"
        report.review_note = (review_note or "").strip()[:500]
        report.reviewed_by = (reviewer or "admin").strip()[:64]
        report.reviewed_at = now.replace(tzinfo=None)
        report.updated_at = now.replace(tzinfo=None)
        db.commit()

        return {
            "ok": True,
            "report_id": int(report.id),
            "status": "rejected",
            "companion_id": report.companion_id,
            "scene": getattr(report, "scene", None) or "companion",
        }
