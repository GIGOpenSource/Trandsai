"""Admin IM session list / takeover / ops assign. Registers on admin_common.router."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, exists, func, nullslast, select

from api.admin_common import (
    _get_error_msg,
    admin_auth_required,
    get_admin_lang,
    router,
)
from core.auth import verify_admin_token
from core.response import ok
from core.database import (
    AdminImAuditLogORM,
    CompanionORM,
    CreditTransactionORM,
    ShortTermMessageORM,
    UserCompanionStateORM,
    UserCreditORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from services.im_admin import (
    actor_is_super_admin,
    admin_user_brief,
    assign_companion_ops_admin,
    batch_admin_user_brief,
    broadcast_admin_im,
    build_companion_ops_map,
    can_access_companion,
    can_assign_ops,
    can_release_session,
    can_takeover_session,
    companion_profile_payload,
    get_session_ops_admin,
    idle_remaining_seconds,
    is_admin_account,
    list_companion_ids_for_ops_admin,
    lookup_companion_ops_map,
    notify_ops_unread_cleared,
    ops_unread_summary_for_actor,
    persist_operator_message,
    push_operator_to_user_app,
    register_admin_im_inbox,
    register_admin_im_ws,
    release_takeover_session,
    resolve_admin_actor,
    takeover_idle_timeout_seconds,
    touch_takeover_activity,
    unregister_admin_im_inbox,
    unregister_admin_im_ws,
    write_im_audit,
    _utc_now_naive,
)
from services.im_translate import (
    baidu_to_lang_from_accept,
    fill_translated_content,
    translate_one,
)
from services.im_session_time import filename_utc_stamp, resolve_list_time_range
from services.strategy_image import annotate_chat_message, decode_strategy_image_url, is_strategy_image_content

IM_MSG_PAGE_SIZE_MAX = 50
from services.memory import ShortTermMemory

logger = logging.getLogger(__name__)


# ===== request bodies =====


class ImSessionKeyBody(BaseModel):
    user_id: int = Field(..., ge=1)
    companion_id: str = Field(..., min_length=1, max_length=32)


class ImAssignOpsBody(ImSessionKeyBody):
    ops_admin_id: Optional[int] = Field(
        None, description="责任运营后台账号 ID；null 表示清空"
    )


class ImSendMessageBody(ImSessionKeyBody):
    content: str = Field(..., min_length=1, max_length=8000)


class ImTranslateBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=6000, description="要点击翻译的原文")


class ImReplyModeBody(BaseModel):
    reply_mode: str = Field(..., description="human_first | ai")
    wait_seconds: Optional[int] = Field(None, ge=1, le=600)


def _norm_cid(companion_id: str, lang: str = "zh") -> str:
    cid = (companion_id or "").strip()[:8]
    if not cid:
        raise HTTPException(status_code=400, detail=_get_error_msg("session_not_found", lang))
    return cid


def _parse_companion_id_filter(raw: Any) -> List[str]:
    """解析会话列表 companion_id 多选。

    支持：
    - list / tuple（FastAPI 重复 query）
    - 逗号分隔：a,b,c
    - JSON / 方括号串：["a","b"] 或 [a,b]
    """
    if raw is None:
        return []

    chunks: List[Any] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    chunks = parsed
                else:
                    chunks = [text]
            except Exception:
                # [a,b] 非严格 JSON：去掉括号再按逗号拆
                chunks = [text[1:-1]]
        else:
            chunks = [text]
    elif isinstance(raw, (list, tuple, set)):
        chunks = list(raw)
    else:
        chunks = [raw]

    out: List[str] = []
    seen = set()
    for item in chunks:
        if item is None:
            continue
        if isinstance(item, (list, tuple, set)):
            for nested in _parse_companion_id_filter(list(item)):
                if nested not in seen:
                    seen.add(nested)
                    out.append(nested)
            continue
        text = str(item).strip().strip('"').strip("'")
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            for nested in _parse_companion_id_filter(text):
                if nested not in seen:
                    seen.add(nested)
                    out.append(nested)
            continue
        for part in text.replace("，", ",").replace(";", ",").split(","):
            cid = part.strip().strip('"').strip("'")[:8]
            if cid and cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


def _get_ucs(db, user_id: int, cid: str) -> UserCompanionStateORM:
    ucs = (
        db.query(UserCompanionStateORM)
        .filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == cid,
        )
        .first()
    )
    if not ucs:
        raise HTTPException(status_code=404, detail=_get_error_msg("session_not_found", "zh"))
    return ucs


def _require_session_access(
    actor: dict,
    db,
    companion_id: str,
    lang: str = "zh",
) -> None:
    """非超管只能操作自己负责的智能体下会话。"""
    if can_access_companion(actor, db, companion_id):
        return
    raise HTTPException(status_code=403, detail=_get_error_msg("im_session_forbidden", lang))


def _apply_ops_scope_filter(
    db,
    actor: dict,
    filters: list,
    ops_admin_id: Optional[int],
) -> Optional[int]:
    """非超管仅看自己责任智能体会话；超管可看全部并可按 ops_admin_id 筛。"""
    if actor_is_super_admin(actor):
        if ops_admin_id is not None:
            owned = list_companion_ids_for_ops_admin(db, int(ops_admin_id))
            if owned:
                filters.append(UserCompanionStateORM.companion_id.in_(owned))
            else:
                filters.append(UserCompanionStateORM.companion_id == "")
        return ops_admin_id
    aid = actor.get("admin_id")
    if aid is None:
        filters.append(UserCompanionStateORM.companion_id == "")
        return -1
    # 非超管忽略 query ops_admin_id，强制本人责任智能体
    owned = list_companion_ids_for_ops_admin(db, int(aid))
    if owned:
        filters.append(UserCompanionStateORM.companion_id.in_(owned))
    else:
        filters.append(UserCompanionStateORM.companion_id == "")
    return int(aid)


def _backfill_ucs_from_messages(
    db,
    *,
    user_id: Optional[int] = None,
    companion_ids: Optional[List[str]] = None,
    user_nickname: Optional[str] = None,
    companion_name: Optional[str] = None,
    limit: int = 500,
) -> int:
    """为仅有 short_term_messages、无 user_companion_states 的会话补行（历史触达/群消息）。"""
    missing_ucs = ~exists(
        select(1).where(
            and_(
                UserCompanionStateORM.user_id == ShortTermMessageORM.user_id,
                UserCompanionStateORM.companion_id == ShortTermMessageORM.companion_id,
            )
        )
    )
    q = db.query(ShortTermMessageORM.user_id, ShortTermMessageORM.companion_id).filter(
        ShortTermMessageORM.user_id.isnot(None),
        missing_ucs,
    )
    if user_id is not None:
        q = q.filter(ShortTermMessageORM.user_id == user_id)
    if companion_ids:
        q = q.filter(ShortTermMessageORM.companion_id.in_(companion_ids))
    if user_nickname:
        nn = user_nickname.strip()
        if nn:
            q = q.join(UserORM, UserORM.id == ShortTermMessageORM.user_id).filter(
                UserORM.nickname.ilike(f"%{nn}%")
            )
    if companion_name:
        cn = companion_name.strip()
        if cn:
            q = q.join(CompanionORM, CompanionORM.id == ShortTermMessageORM.companion_id).filter(
                CompanionORM.name.ilike(f"%{cn}%")
            )

    pairs = q.distinct().limit(max(1, min(limit, 500))).all()
    if not pairs:
        return 0
    now = datetime.now(timezone.utc)
    for uid, cid in pairs:
        db.add(
            UserCompanionStateORM(
                user_id=int(uid),
                companion_id=str(cid)[:8],
                affection=0,
                turns=0,
                updated_at=now,
            )
        )
    return len(pairs)


def _msg_stats_for_pairs(
    db, pairs: List[Tuple[int, str]]
) -> Dict[Tuple[int, str], Tuple[int, Optional[datetime], str]]:
    """按 (user_id, companion_id) 聚合 message_count / last_at / last_content。"""
    out: Dict[Tuple[int, str], Tuple[int, Optional[datetime], str]] = {}
    if not pairs:
        return out
    # 分批 IN 查询 count + max(created_at)
    user_ids = list({p[0] for p in pairs})
    cids = list({p[1] for p in pairs})
    stats = (
        db.query(
            ShortTermMessageORM.user_id,
            ShortTermMessageORM.companion_id,
            func.count(ShortTermMessageORM.id).label("cnt"),
            func.max(ShortTermMessageORM.created_at).label("last_at"),
        )
        .filter(
            ShortTermMessageORM.user_id.in_(user_ids),
            ShortTermMessageORM.companion_id.in_(cids),
        )
        .group_by(ShortTermMessageORM.user_id, ShortTermMessageORM.companion_id)
        .all()
    )
    pair_set = set(pairs)
    last_at_map: Dict[Tuple[int, str], Optional[datetime]] = {}
    for s in stats:
        key = (int(s.user_id), s.companion_id)
        if key not in pair_set:
            continue
        out[key] = (int(s.cnt or 0), s.last_at, "")
        last_at_map[key] = s.last_at

    # 最近一条正文：按 last_at 对齐取一条（同秒碰撞可接受）
    need_content = [(k, t) for k, t in last_at_map.items() if t is not None]
    for (uid, cid), last_at in need_content:
        row = (
            db.query(ShortTermMessageORM.content)
            .filter(
                ShortTermMessageORM.user_id == uid,
                ShortTermMessageORM.companion_id == cid,
                ShortTermMessageORM.created_at == last_at,
            )
            .order_by(ShortTermMessageORM.id.desc())
            .first()
        )
        cnt, la, _ = out.get((uid, cid), (0, last_at, ""))
        out[(uid, cid)] = (cnt, la, (row[0] if row else "") or "")
    return out


def _last_user_message_subq(db):
    """各 (user_id, companion_id) 最近一条 user 消息时间，用于 IM 列表排序/会话更新列。"""
    return (
        db.query(
            ShortTermMessageORM.user_id,
            ShortTermMessageORM.companion_id,
            func.max(ShortTermMessageORM.created_at).label("last_user_at"),
        )
        .filter(
            ShortTermMessageORM.role == "user",
            ShortTermMessageORM.user_id.isnot(None),
        )
        .group_by(ShortTermMessageORM.user_id, ShortTermMessageORM.companion_id)
        .subquery()
    )


def _session_opened_at_subq(db):
    """各会话第一条消息时间 = 对话开启时间。"""
    return (
        db.query(
            ShortTermMessageORM.user_id,
            ShortTermMessageORM.companion_id,
            func.min(ShortTermMessageORM.created_at).label("opened_at"),
        )
        .filter(ShortTermMessageORM.user_id.isnot(None))
        .group_by(ShortTermMessageORM.user_id, ShortTermMessageORM.companion_id)
        .subquery()
    )


def _export_msg_content(raw: Optional[str]) -> str:
    text = raw or ""
    if is_strategy_image_content(text):
        return decode_strategy_image_url(text) or text
    return text


def _export_sender_label(role: Optional[str]) -> str:
    r = (role or "").strip().lower()
    if r == "user":
        return "用户"
    if r == "operator":
        return "运营"
    return "智能体"


def _xlsx_response(headers: List[str], rows: List[list], filename: str) -> StreamingResponse:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "聊天记录"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    ascii_name = "im_chat_export.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
            )
        },
    )


def _resolve_time_or_400(
    time_mode: Optional[str],
    start_at: Optional[str],
    end_at: Optional[str],
):
    try:
        return resolve_list_time_range(time_mode, start_at, end_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _list_sessions_impl(
    *,
    token: str,
    page: int,
    page_size: int,
    user_id: Optional[int],
    companion_id: Any,
    user_nickname: Optional[str],
    companion_name: Optional[str],
    ops_admin_id: Optional[int],
    takeover_only: Optional[bool],
    time_mode: Optional[str] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
) -> dict:
    actor = resolve_admin_actor(token)
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    offset = (page - 1) * page_size
    mode, t_start, t_end, t_end_excl = _resolve_time_or_400(time_mode, start_at, end_at)

    filters = []
    if user_id is not None:
        filters.append(UserCompanionStateORM.user_id == user_id)
    companion_ids = _parse_companion_id_filter(companion_id)
    if companion_ids:
        # 多选：精确匹配 companion_id IN (...)
        filters.append(UserCompanionStateORM.companion_id.in_(companion_ids))
    if user_nickname:
        nn = user_nickname.strip()
        if nn:
            filters.append(UserORM.nickname.ilike(f"%{nn}%"))
    if companion_name:
        cn = companion_name.strip()
        if cn:
            filters.append(CompanionORM.name.ilike(f"%{cn}%"))

    with get_db() as db:
        _apply_ops_scope_filter(db, actor, filters, ops_admin_id)
        if takeover_only is True:
            filters.append(UserCompanionStateORM.takeover_by.isnot(None))
        elif takeover_only is False:
            filters.append(UserCompanionStateORM.takeover_by.is_(None))

        backfilled = _backfill_ucs_from_messages(
            db,
            user_id=user_id,
            companion_ids=companion_ids or None,
            user_nickname=user_nickname,
            companion_name=companion_name,
        )
        if backfilled:
            logger.info("IM list backfilled %s user_companion_states row(s)", backfilled)

        last_user_sq = _last_user_message_subq(db)
        last_user_join = and_(
            last_user_sq.c.user_id == UserCompanionStateORM.user_id,
            last_user_sq.c.companion_id == UserCompanionStateORM.companion_id,
        )
        time_filters = []
        if t_start is not None and t_end_excl is not None:
            time_filters = [
                last_user_sq.c.last_user_at.isnot(None),
                last_user_sq.c.last_user_at >= t_start,
                last_user_sq.c.last_user_at < t_end_excl,
            ]

        base = (
            db.query(UserCompanionStateORM)
            .outerjoin(UserORM, UserORM.id == UserCompanionStateORM.user_id)
            .outerjoin(CompanionORM, CompanionORM.id == UserCompanionStateORM.companion_id)
            .outerjoin(last_user_sq, last_user_join)
        )
        if filters:
            base = base.filter(*filters)
        if time_filters:
            base = base.filter(*time_filters)
        total = base.count()

        list_q = (
            db.query(
                UserCompanionStateORM,
                UserORM,
                CompanionORM,
                last_user_sq.c.last_user_at,
            )
            .outerjoin(UserORM, UserORM.id == UserCompanionStateORM.user_id)
            .outerjoin(CompanionORM, CompanionORM.id == UserCompanionStateORM.companion_id)
            .outerjoin(last_user_sq, last_user_join)
        )
        if filters:
            list_q = list_q.filter(*filters)
        if time_filters:
            list_q = list_q.filter(*time_filters)

        # 有智能体筛选时：把 Redis 未读会话置顶，避免按 last_user_at 沉底后
        # 前端只拉前 50 条导致「有角标却打不开」。
        order_clauses = [
            nullslast(desc(last_user_sq.c.last_user_at)),
            desc(UserCompanionStateORM.companion_id),
            desc(UserCompanionStateORM.user_id),
        ]
        if companion_ids:
            try:
                from services.im_ops_unread import unread_user_ids_by_companion

                scope_ops = None if actor_is_super_admin(actor) else actor.get("admin_id")
                unread_map = unread_user_ids_by_companion(
                    companion_ids, ops_admin_id=scope_ops
                )
                unread_uids: List[int] = []
                seen_uids: set[int] = set()
                for cid in companion_ids:
                    for uid in unread_map.get(str(cid)[:8], []):
                        if uid not in seen_uids:
                            seen_uids.add(uid)
                            unread_uids.append(int(uid))
                if unread_uids:
                    order_clauses.insert(
                        0,
                        case(
                            (UserCompanionStateORM.user_id.in_(unread_uids), 0),
                            else_=1,
                        ),
                    )
            except Exception:
                logger.warning("IM list unread pin failed", exc_info=True)

        rows = (
            list_q.order_by(*order_clauses)
            .offset(offset)
            .limit(page_size)
            .all()
        )

        pairs = [(ucs.user_id, ucs.companion_id) for ucs, _, _, _ in rows]
        msg_stats = _msg_stats_for_pairs(db, pairs)

        page_companion_ids = [str(ucs.companion_id) for ucs, _, _, _ in rows]
        ops_map = build_companion_ops_map(db, page_companion_ids)
        ops_admin_ids = {aid for aid in ops_map.values() if aid is not None}
        ops_brief_map = batch_admin_user_brief(db, ops_admin_ids)

        takeover_ids = {
            int(ucs.takeover_by)
            for ucs, _, _, _ in rows
            if ucs.takeover_by is not None
        }
        takeover_map = {}
        if takeover_ids:
            for u in db.query(UserORM).filter(UserORM.id.in_(list(takeover_ids))).all():
                takeover_map[u.id] = {
                    "id": u.id,
                    "username": u.username or "",
                    "nickname": u.nickname or "",
                }

        items = []
        unread_keys = [
            (
                int(ucs.user_id),
                str(ucs.companion_id)[:8],
                lookup_companion_ops_map(ops_map, str(ucs.companion_id)),
            )
            for ucs, _, _, _ in rows
        ]
        from services.im_ops_unread import batch_get_session_unread

        unread_map = batch_get_session_unread(unread_keys)

        for ucs, u, c, last_user_at in rows:
            mc, last_at, last_content = msg_stats.get(
                (ucs.user_id, ucs.companion_id), (0, None, "")
            )
            resolved_ops_id = lookup_companion_ops_map(ops_map, str(ucs.companion_id))
            ops_brief = (
                ops_brief_map.get(int(resolved_ops_id))
                if resolved_ops_id is not None
                else None
            )
            tb = None
            if ucs.takeover_by is not None:
                tb = takeover_map.get(int(ucs.takeover_by)) or {
                    "id": int(ucs.takeover_by),
                    "username": "",
                    "nickname": "",
                }
            unread = unread_map.get(
                (int(ucs.user_id), str(ucs.companion_id)[:8]), 0
            )
            items.append(
                {
                    "user_id": ucs.user_id,
                    "username": (u.username if u else None) or "",
                    "nickname": (u.nickname if u else None) or "",
                    "companion_id": ucs.companion_id,
                    "companion_name": (c.name if c else None) or "",
                    "companion_avatar_url": (getattr(c, "avatar_url", None) if c else None) or "",
                    "turns": ucs.turns if ucs.turns is not None else 0,
                    "affection": float(ucs.affection) if ucs.affection is not None else 0.0,
                    "session_updated_at": serialize_datetime(last_user_at),
                    "message_count": mc,
                    "last_message": (last_content or "")[:200],
                    "last_message_at": serialize_datetime(last_at),
                    "ops_admin_id": resolved_ops_id,
                    "ops_admin": ops_brief,
                    "takeover_by": ucs.takeover_by,
                    "takeover_admin": tb,
                    "takeover_at": serialize_datetime(ucs.takeover_at),
                    "takeover_active_at": serialize_datetime(
                        getattr(ucs, "takeover_active_at", None)
                    ),
                    "is_taken_over": ucs.takeover_by is not None,
                    "can_takeover": can_takeover_session(
                        actor, resolved_ops_id, db=db, companion_id=str(ucs.companion_id)
                    ),
                    "can_release": can_release_session(actor, ucs.takeover_by),
                    "can_assign_ops": can_assign_ops(actor),
                    "idle_timeout_seconds": takeover_idle_timeout_seconds(),
                    "idle_remaining_seconds": idle_remaining_seconds(
                        getattr(ucs, "takeover_active_at", None),
                        ucs.takeover_at,
                    )
                    if ucs.takeover_by is not None
                    else None,
                    "ops_unread_count": int(unread),
                }
            )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "time_mode": mode,
        "start_at": serialize_datetime(t_start) if t_start else None,
        "end_at": serialize_datetime(t_end) if t_end else None,
        "items": items,
    }


# ===== list / messages / delete =====


@router.get("/api/admin/im/sessions", summary="IM 会话列表")
async def admin_list_chat_sessions(
    _token: str = Depends(admin_auth_required),
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[int] = None,
    companion_id: Optional[List[str]] = Query(
        None,
        description="智能体 ID 多选：重复参数或逗号分隔，如 companion_id=a&companion_id=b",
    ),
    companion_ids: Optional[List[str]] = Query(
        None,
        description="同 companion_id，便于前端传数组字段名",
    ),
    user_nickname: Optional[str] = None,
    companion_name: Optional[str] = None,
    ops_admin_id: Optional[int] = None,
    takeover_only: Optional[bool] = None,
    time_mode: Optional[str] = Query(
        None,
        description="last_2_days（默认）/ all / custom；不传且无 start_at/end_at 时为近 2 天",
    ),
    start_at: Optional[str] = Query(None, description="自定义开始 UTC，ISO8601"),
    end_at: Optional[str] = Query(None, description="自定义结束 UTC，ISO8601，精度到秒"),
):
    merged: List[str] = []
    if companion_id:
        merged.extend(companion_id)
    if companion_ids:
        merged.extend(companion_ids)
    return _list_sessions_impl(
        token=_token,
        page=page,
        page_size=page_size,
        user_id=user_id,
        companion_id=merged or None,
        user_nickname=user_nickname,
        companion_name=companion_name,
        ops_admin_id=ops_admin_id,
        takeover_only=takeover_only,
        time_mode=time_mode,
        start_at=start_at,
        end_at=end_at,
    )


@router.get("/api/admin/im/sessions/export", summary="导出 IM 聊天记录 xlsx")
async def admin_export_chat_sessions(
    _token: str = Depends(admin_auth_required),
    start_at: Optional[str] = Query(None, description="必填，对话开启时间起 UTC"),
    end_at: Optional[str] = Query(None, description="必填，对话开启时间止 UTC"),
    user_id: Optional[int] = None,
    companion_id: Optional[List[str]] = Query(None),
    companion_ids: Optional[List[str]] = Query(None),
    user_nickname: Optional[str] = None,
    companion_name: Optional[str] = None,
    ops_admin_id: Optional[int] = None,
    takeover_only: Optional[bool] = None,
):
    """按对话开启时间圈定会话，导出这些会话的全部消息。叠加列表其它筛选，不受分页限制。"""
    if not (start_at or "").strip() or not (end_at or "").strip():
        raise HTTPException(status_code=400, detail="请选择开始和结束时间")
    _, t_start, t_end, t_end_excl = _resolve_time_or_400("custom", start_at, end_at)
    actor = resolve_admin_actor(_token)
    merged: List[str] = []
    if companion_id:
        merged.extend(companion_id)
    if companion_ids:
        merged.extend(companion_ids)
    companion_ids_f = _parse_companion_id_filter(merged or None)

    filters = []
    if user_id is not None:
        filters.append(UserCompanionStateORM.user_id == user_id)
    if companion_ids_f:
        filters.append(UserCompanionStateORM.companion_id.in_(companion_ids_f))
    if user_nickname:
        nn = user_nickname.strip()
        if nn:
            filters.append(UserORM.nickname.ilike(f"%{nn}%"))
    if companion_name:
        cn = companion_name.strip()
        if cn:
            filters.append(CompanionORM.name.ilike(f"%{cn}%"))

    with get_db() as db:
        _apply_ops_scope_filter(db, actor, filters, ops_admin_id)
        if takeover_only is True:
            filters.append(UserCompanionStateORM.takeover_by.isnot(None))
        elif takeover_only is False:
            filters.append(UserCompanionStateORM.takeover_by.is_(None))

        opened_sq = _session_opened_at_subq(db)
        opened_join = and_(
            opened_sq.c.user_id == UserCompanionStateORM.user_id,
            opened_sq.c.companion_id == UserCompanionStateORM.companion_id,
        )
        sess_q = (
            db.query(
                UserCompanionStateORM,
                UserORM,
                CompanionORM,
                opened_sq.c.opened_at,
            )
            .outerjoin(UserORM, UserORM.id == UserCompanionStateORM.user_id)
            .outerjoin(CompanionORM, CompanionORM.id == UserCompanionStateORM.companion_id)
            .join(opened_sq, opened_join)
            .filter(
                opened_sq.c.opened_at >= t_start,
                opened_sq.c.opened_at < t_end_excl,
            )
        )
        if filters:
            sess_q = sess_q.filter(*filters)
        sessions = sess_q.order_by(
            desc(opened_sq.c.opened_at),
            desc(UserCompanionStateORM.companion_id),
            desc(UserCompanionStateORM.user_id),
        ).all()
        if not sessions:
            raise HTTPException(status_code=400, detail="该时间区间没有聊天记录")

        pairs = [(ucs.user_id, ucs.companion_id) for ucs, _, _, _ in sessions]
        page_cids = [str(ucs.companion_id) for ucs, _, _, _ in sessions]
        ops_map = build_companion_ops_map(db, page_cids)
        ops_ids = {aid for aid in ops_map.values() if aid is not None}
        ops_brief_map = batch_admin_user_brief(db, ops_ids)

        user_ids = list({p[0] for p in pairs})
        cids = list({p[1] for p in pairs})
        msgs = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.user_id.in_(user_ids),
                ShortTermMessageORM.companion_id.in_(cids),
            )
            .order_by(
                ShortTermMessageORM.user_id.asc(),
                ShortTermMessageORM.companion_id.asc(),
                ShortTermMessageORM.created_at.asc(),
                ShortTermMessageORM.id.asc(),
            )
            .all()
        )
        pair_set = {(int(a), str(b)[:8]) for a, b in pairs}
        by_pair: Dict[Tuple[int, str], List[ShortTermMessageORM]] = {}
        for m in msgs:
            key = (int(m.user_id), str(m.companion_id)[:8])
            if key not in pair_set:
                continue
            by_pair.setdefault(key, []).append(m)

        headers = [
            "对话开启时间（UTC）",
            "消息发送时间（UTC）",
            "用户 ID",
            "用户",
            "智能体 ID",
            "智能体",
            "责任运营账号",
            "接管状态",
            "发送方",
            "消息内容",
        ]
        rows: List[list] = []
        for ucs, u, c, opened_at in sessions:
            key = (int(ucs.user_id), str(ucs.companion_id)[:8])
            resolved_ops_id = lookup_companion_ops_map(ops_map, str(ucs.companion_id))
            ops_brief = (
                ops_brief_map.get(int(resolved_ops_id))
                if resolved_ops_id is not None
                else None
            )
            ops_account = ""
            if ops_brief:
                ops_account = (ops_brief.get("username") or ops_brief.get("nickname") or "")
            takeover_label = "接管中" if ucs.takeover_by is not None else "未接管"
            user_label = ((u.nickname if u else None) or (u.username if u else None) or "")
            opened_s = serialize_datetime(opened_at) or ""
            for m in by_pair.get(key, []):
                rows.append(
                    [
                        opened_s,
                        serialize_datetime(m.created_at) or "",
                        ucs.user_id,
                        user_label,
                        ucs.companion_id,
                        (c.name if c else None) or "",
                        ops_account,
                        takeover_label,
                        _export_sender_label(m.role),
                        _export_msg_content(m.content),
                    ]
                )
        if not rows:
            raise HTTPException(status_code=400, detail="该时间区间没有聊天记录")

    fname = (
        f"IM聊天记录_{filename_utc_stamp(t_start)}_{filename_utc_stamp(t_end)}.xlsx"
    )
    return _xlsx_response(headers, rows, fname)


@router.get("/api/admin/im/sessions/messages", summary="IM 会话消息")
async def admin_get_chat_session_messages(
    user_id: int,
    companion_id: str,
    page: int = Query(1, ge=1, description="页码，从 1 开始；第 1 页为最近消息"),
    page_size: int = Query(
        20, ge=1, le=IM_MSG_PAGE_SIZE_MAX, description="每页条数，默认 20，最大 50"
    ),
    translate: bool = Query(
        False,
        description="true 时为本页需要翻译的消息填 translated_content；默认 false 不调翻译",
    ),
    limit: Optional[int] = Query(None, description="兼容旧参数，等同 page_size（最大 50）"),
    offset: Optional[int] = Query(None, description="兼容旧参数，offset=0 视为第 1 页"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    cid = _norm_cid(companion_id, lang)
    if limit is not None:
        page_size = min(IM_MSG_PAGE_SIZE_MAX, max(1, int(limit)))
    else:
        page_size = min(IM_MSG_PAGE_SIZE_MAX, max(1, int(page_size)))
    if offset is not None and int(offset) > 0:
        page = int(offset) // page_size + 1
    else:
        page = max(1, int(page))
    actor = resolve_admin_actor(_token)

    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs:
            has_msgs = (
                db.query(ShortTermMessageORM.id)
                .filter(
                    ShortTermMessageORM.companion_id == cid,
                    ShortTermMessageORM.user_id == user_id,
                )
                .first()
            )
            if not has_msgs:
                raise HTTPException(
                    status_code=404, detail=_get_error_msg("session_not_found", lang)
                )
            now = datetime.now(timezone.utc)
            ucs = UserCompanionStateORM(
                user_id=user_id,
                companion_id=cid,
                affection=0,
                turns=0,
                updated_at=now,
            )
            db.add(ucs)
            db.flush()
        _require_session_access(actor, db, cid, lang)

        u = db.query(UserORM).filter(UserORM.id == user_id).first()
        comp = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
        resolved_ops_id = get_session_ops_admin(db, cid)

        total = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.companion_id == cid,
                ShortTermMessageORM.user_id == user_id,
            )
            .count()
        )
        skip = (page - 1) * page_size
        # 与用户端一致：按发送时间线（created_at），同秒再用 id；勿只按 id
        # （拆条落库时 id 与时间可能反序，只排 id 会和客户端气泡顺序不一致）
        msgs = (
            db.query(ShortTermMessageORM)
            .filter(
                ShortTermMessageORM.companion_id == cid,
                ShortTermMessageORM.user_id == user_id,
            )
            .order_by(
                ShortTermMessageORM.created_at.desc(),
                ShortTermMessageORM.id.desc(),
            )
            .offset(skip)
            .limit(page_size)
            .all()
        )
        msgs = list(reversed(msgs))
        total_n = int(total)
        total_pages = (total_n + page_size - 1) // page_size if total_n else 0
        payload = {
            "user_id": user_id,
            "username": (u.username if u else "") or "",
            "nickname": (u.nickname if u else "") or "",
            "companion_id": cid,
            "companion_name": (comp.name if comp else "") or "",
            "ops_admin_id": resolved_ops_id,
            "ops_admin": admin_user_brief(db, resolved_ops_id),
            "takeover_by": ucs.takeover_by,
            "takeover_at": serialize_datetime(ucs.takeover_at),
            "is_taken_over": ucs.takeover_by is not None,
            "can_assign_ops": can_assign_ops(actor),
            "total": total_n,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_older": page < total_pages,
            "has_newer": page > 1,
            "offset": skip,
            "translate": bool(translate),
            "target_lang": baidu_to_lang_from_accept(accept_language),
            "messages": [
                annotate_chat_message(
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content or "",
                        "admin_id": getattr(m, "admin_id", None),
                        "created_at": serialize_datetime(m.created_at),
                        "translated_content": None,
                        "send_status": (getattr(m, "send_status", None) or "").strip(),
                    }
                )
                for m in msgs
            ],
        }

    # 打开会话（拉最近一页消息）即清未读
    if page == 1:
        try:
            await notify_ops_unread_cleared(
                user_id,
                cid,
                ops_admin_id=payload.get("ops_admin_id"),
            )
        except Exception:
            logger.exception(
                "IM clear unread on messages failed user=%s companion=%s",
                user_id,
                cid,
            )
        payload["ops_unread_count"] = 0
    else:
        from services.im_ops_unread import UNASSIGNED_OPS_ADMIN_ID, get_session_unread

        ops_id = payload.get("ops_admin_id")
        payload["ops_unread_count"] = get_session_unread(
            int(ops_id) if ops_id is not None else UNASSIGNED_OPS_ADMIN_ID,
            cid,
            int(user_id),
        )

    if translate:
        await asyncio.to_thread(
            fill_translated_content, payload["messages"], payload["target_lang"]
        )
    return payload


@router.post("/api/admin/im/sessions/read", summary="标记会话已读（打开即清未读）")
async def admin_im_session_read(
    body: ImSessionKeyBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)
    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == body.user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs:
            raise HTTPException(
                status_code=404, detail=_get_error_msg("session_not_found", lang)
            )
        _require_session_access(actor, db, cid, lang)
        ops_admin_id = get_session_ops_admin(db, cid)

    info = await notify_ops_unread_cleared(
        body.user_id, cid, ops_admin_id=ops_admin_id
    )
    return {
        "ok": True,
        "user_id": body.user_id,
        "companion_id": cid,
        "ops_unread_count": 0,
        "ops_last_read_at": (info or {}).get("ops_last_read_at"),
    }


@router.post(
    "/api/admin/im/sessions/read-companion",
    summary="清除某用户与某智能体之间的未读",
)
async def admin_im_read_companion(
    body: ImSessionKeyBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """主动清除：仅清 (user_id, companion_id) 这一会话的运营未读（与 POST /read 同粒度）。"""
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)
    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == body.user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs:
            raise HTTPException(
                status_code=404, detail=_get_error_msg("session_not_found", lang)
            )
        _require_session_access(actor, db, cid, lang)
        ops_admin_id = get_session_ops_admin(db, cid)

    info = await notify_ops_unread_cleared(
        body.user_id, cid, ops_admin_id=ops_admin_id
    )
    cleared = int((info or {}).get("cleared_from") or 0)
    summary = ops_unread_summary_for_actor(actor)
    return {
        "ok": True,
        "user_id": body.user_id,
        "companion_id": cid,
        "ops_unread_count": 0,
        "cleared_unread": cleared,
        "ops_last_read_at": (info or {}).get("ops_last_read_at"),
        "total_unread": summary.get("total_unread", 0),
        "session_count": summary.get("session_count", 0),
        "by_companion": summary.get("by_companion") or [],
    }


@router.get("/api/admin/im/sessions/unread-summary", summary="IM 未读汇总")
async def admin_im_unread_summary(
    _token: str = Depends(admin_auth_required),
):
    actor = resolve_admin_actor(_token)
    return ops_unread_summary_for_actor(actor)


@router.post("/api/admin/im/sessions/read-all", summary="全部已读")
async def admin_im_read_all(
    _token: str = Depends(admin_auth_required),
):
    """清当前账号可见范围内全部 IM 未读。超管清全部运营；普通运营只清自己。"""
    actor = resolve_admin_actor(_token)
    from services.im_ops_unread import (
        clear_companion_unread,
        clear_companion_unread_for_all_ops,
        get_all_companion_unread_map,
        get_companion_unread_map,
    )

    cleared_unread = 0
    cleared_sessions = 0
    if actor_is_super_admin(actor):
        cmap = get_all_companion_unread_map()
        for cid in list(cmap):
            part = clear_companion_unread_for_all_ops(cid)
            cleared_unread += int(part.get("cleared_unread") or 0)
            cleared_sessions += int(part.get("cleared_sessions") or 0)
    else:
        aid = actor.get("admin_id")
        if aid is None:
            raise HTTPException(status_code=403, detail="无权操作")
        cmap = get_companion_unread_map(int(aid))
        for cid in list(cmap):
            part = clear_companion_unread(int(aid), cid)
            cleared_unread += int(part.get("cleared_unread") or 0)
            cleared_sessions += int(part.get("cleared_sessions") or 0)
    summary = ops_unread_summary_for_actor(actor)
    return {
        "ok": True,
        "cleared_unread": cleared_unread,
        "cleared_sessions": cleared_sessions,
        "total_unread": summary.get("total_unread", 0),
        "session_count": summary.get("session_count", 0),
        "by_companion": summary.get("by_companion") or [],
    }


@router.post("/api/admin/im/translate", summary="点击翻译一条原文")
async def admin_im_translate_one(
    body: ImTranslateBody,
    _token: str = Depends(admin_auth_required),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """运营点击原文时调用。目标语言由 Accept-Language 决定，不改库、不改 content。"""
    target = baidu_to_lang_from_accept(accept_language)
    data = await asyncio.to_thread(translate_one, body.text, target)
    return ok(data=data)


@router.delete("/api/admin/im/sessions", summary="删除 IM 会话")
async def admin_delete_chat_session(
    user_id: int = Query(..., ge=1, description="用户 ID"),
    companion_id: str = Query(..., min_length=1, max_length=32, description="智能体 ID"),
    clear_messages: bool = Query(
        True,
        description="为 true 时清空该用户与该智能体的短期消息并重置本会话亲密度/轮数",
    ),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """删除一条用户–智能体会话；清空仅作用于该 (user_id, companion_id)。"""
    cid = _norm_cid(companion_id, lang)
    actor = resolve_admin_actor(_token)

    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs:
            raise HTTPException(status_code=404, detail=_get_error_msg("session_not_found", lang))
        _require_session_access(actor, db, cid, lang)

        if clear_messages:
            # 先清 Redis/ring，再删 PG（ShortTermMemory.clear 已按 user 隔离）
            pass

    if clear_messages:
        ShortTermMemory(cid, user_id=user_id).clear()
        with get_db() as db:
            db.query(UserCompanionStateORM).filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == cid,
            ).update(
                {
                    UserCompanionStateORM.affection: 0,
                    UserCompanionStateORM.turns: 0,
                    UserCompanionStateORM.takeover_by: None,
                    UserCompanionStateORM.takeover_at: None,
                },
                synchronize_session=False,
            )

    with get_db() as db:
        db.query(UserCompanionStateORM).filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == cid,
        ).delete(synchronize_session=False)

    # 删除会话必须同步清 Redis 未读，否则 summary 幽灵角标无法消除
    try:
        from services.im_ops_unread import clear_session_unread_for_all_ops, redis_available

        if redis_available():
            clear_session_unread_for_all_ops(cid, int(user_id))
    except Exception:
        logger.warning(
            "IM clear unread on delete session failed user=%s companion=%s",
            user_id,
            cid,
            exc_info=True,
        )

    write_im_audit(
        action="clear_session",
        actor=actor,
        user_id=user_id,
        companion_id=cid,
        detail={"clear_messages": clear_messages},
    )
    return {"ok": True, "cleared_messages": clear_messages}


@router.delete("/api/admin/im/sessions/messages", summary="仅清空会话消息")
async def admin_clear_session_messages(
    user_id: int = Query(..., ge=1),
    companion_id: str = Query(..., min_length=1, max_length=32),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(companion_id, lang)
    actor = resolve_admin_actor(_token)
    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs:
            raise HTTPException(status_code=404, detail=_get_error_msg("session_not_found", lang))
        _require_session_access(actor, db, cid, lang)

    ShortTermMemory(cid, user_id=user_id).clear()
    with get_db() as db:
        db.query(UserCompanionStateORM).filter(
            UserCompanionStateORM.user_id == user_id,
            UserCompanionStateORM.companion_id == cid,
        ).update(
            {
                UserCompanionStateORM.affection: 0,
                UserCompanionStateORM.turns: 0,
            },
            synchronize_session=False,
        )

    # 清空消息后若保留未读，会出现「无用户消息但仍有角标」
    try:
        from services.im_ops_unread import clear_session_unread_for_all_ops, redis_available

        if redis_available():
            clear_session_unread_for_all_ops(cid, int(user_id))
    except Exception:
        logger.warning(
            "IM clear unread on clear messages failed user=%s companion=%s",
            user_id,
            cid,
            exc_info=True,
        )

    write_im_audit(
        action="clear_messages",
        actor=actor,
        user_id=user_id,
        companion_id=cid,
    )
    return {"ok": True}


# ===== 指定责任运营 =====


@router.patch("/api/admin/im/sessions/ops", summary="指定/更换责任运营")
@router.put("/api/admin/im/sessions/ops", summary="指定/更换责任运营")
async def admin_assign_ops(
    body: ImAssignOpsBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)
    if not can_assign_ops(actor):
        raise HTTPException(
            status_code=403,
            detail=_get_error_msg("im_assign_ops_forbidden", lang),
        )

    with get_db() as db:
        comp = db.query(CompanionORM).filter(CompanionORM.id == cid).first()
        if not comp:
            raise HTTPException(
                status_code=404, detail=_get_error_msg("companion_not_found", lang)
            )
        _get_ucs(db, body.user_id, cid)
        if body.ops_admin_id is not None and not is_admin_account(db, body.ops_admin_id):
            raise HTTPException(
                status_code=400,
                detail=_get_error_msg("im_ops_not_admin", lang),
            )
        try:
            resolved_ops_id = assign_companion_ops_admin(db, cid, body.ops_admin_id)
        except ValueError as e:
            if str(e) == "not_admin_account":
                raise HTTPException(
                    status_code=400,
                    detail=_get_error_msg("im_ops_not_admin", lang),
                )
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        ops = admin_user_brief(db, resolved_ops_id)
        payload = {
            "user_id": body.user_id,
            "companion_id": cid,
            "ops_admin_id": resolved_ops_id,
            "ops_admin": ops,
            "can_takeover": can_takeover_session(
                actor, resolved_ops_id, db=db, companion_id=cid
            ),
            "can_assign_ops": True,
        }

    write_im_audit(
        action="assign_ops",
        actor=actor,
        user_id=body.user_id,
        companion_id=cid,
        detail={"ops_admin_id": body.ops_admin_id, "scope": "companion"},
    )
    return payload


# ===== 回复模式 =====


@router.get("/api/admin/im/reply-mode", summary="IM 回复模式")
async def admin_get_im_reply_mode(_token: str = Depends(admin_auth_required)):
    from services.im_reply_mode import (
        REPLY_MODE_AI,
        REPLY_MODE_HUMAN_FIRST,
        WAIT_SECONDS_DEFAULT,
        WAIT_SECONDS_MAX,
        WAIT_SECONDS_MIN,
        get_or_create_settings,
    )

    data = get_or_create_settings()
    data["modes"] = [
        {
            "value": REPLY_MODE_HUMAN_FIRST,
            "label_zh": "优先人工",
            "label_en": "Human first",
        },
        {
            "value": REPLY_MODE_AI,
            "label_zh": "AI回复",
            "label_en": "AI reply",
        },
    ]
    data["wait_seconds_min"] = WAIT_SECONDS_MIN
    data["wait_seconds_max"] = WAIT_SECONDS_MAX
    data["wait_seconds_default"] = WAIT_SECONDS_DEFAULT
    return ok(data=data)


@router.put("/api/admin/im/reply-mode", summary="更新 IM 回复模式")
async def admin_put_im_reply_mode(
    body: ImReplyModeBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.im_reply_mode import (
        WAIT_SECONDS_DEFAULT,
        get_or_create_settings,
        normalize_reply_mode,
        save_settings,
    )

    try:
        normalize_reply_mode(body.reply_mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=_get_error_msg("im_invalid_reply_mode", lang),
        )
    actor = resolve_admin_actor(_token)
    current = get_or_create_settings()
    wait = (
        body.wait_seconds
        if body.wait_seconds is not None
        else current.get("wait_seconds") or WAIT_SECONDS_DEFAULT
    )
    data = save_settings(
        reply_mode=body.reply_mode,
        wait_seconds=int(wait),
        updated_by=(actor.get("username") or "")[:64],
    )
    write_im_audit(
        action="reply_mode_update",
        actor=actor,
        user_id=None,
        companion_id=None,
        detail={
            "reply_mode": data.get("reply_mode"),
            "wait_seconds": data.get("wait_seconds"),
        },
    )
    return ok(data=data)


# ===== 接管 / 释放 / 发消息 =====


@router.post("/api/admin/im/takeover", summary="进入人工接管")
async def admin_im_takeover(
    body: ImSessionKeyBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)
    now = _utc_now_naive()

    with get_db() as db:
        ucs = _get_ucs(db, body.user_id, cid)
        if not can_takeover_session(actor, None, db=db, companion_id=cid):
            raise HTTPException(
                status_code=403,
                detail=_get_error_msg("im_takeover_forbidden", lang),
            )
        if ucs.takeover_by is not None:
            if actor.get("admin_id") is not None and int(ucs.takeover_by) == int(
                actor["admin_id"]
            ):
                # 幂等：已是自己接管
                pass
            else:
                raise HTTPException(
                    status_code=409,
                    detail=_get_error_msg("im_already_taken_over", lang),
                )
        else:
            if actor.get("admin_id") is None:
                raise HTTPException(
                    status_code=400,
                    detail=_get_error_msg("im_admin_id_required", lang),
                )
            ucs.takeover_by = int(actor["admin_id"])
            ucs.takeover_at = now

        ucs.takeover_active_at = now
        resolved_ops_id = get_session_ops_admin(db, cid)

        result = {
            "ok": True,
            "user_id": body.user_id,
            "companion_id": cid,
            "takeover_by": ucs.takeover_by,
            "takeover_at": serialize_datetime(ucs.takeover_at),
            "takeover_active_at": serialize_datetime(ucs.takeover_active_at),
            "idle_timeout_seconds": takeover_idle_timeout_seconds(),
            "idle_remaining_seconds": takeover_idle_timeout_seconds(),
            "ops_admin_id": resolved_ops_id,
            "ops_admin": admin_user_brief(db, resolved_ops_id),
            "can_release": True,
        }

    write_im_audit(
        action="takeover_enter",
        actor=actor,
        user_id=body.user_id,
        companion_id=cid,
    )
    try:
        from services.im_reply_mode import cancel_pending

        cancel_pending(body.user_id, cid)
    except Exception:
        logger.exception("cancel human_first pending after takeover failed")
    await broadcast_admin_im(
        body.user_id,
        cid,
        {
            "type": "takeover_state",
            "is_taken_over": True,
            "takeover_by": result["takeover_by"],
            "takeover_at": result["takeover_at"],
            "takeover_active_at": result["takeover_active_at"],
            "reason": "enter",
            "idle_timeout_seconds": takeover_idle_timeout_seconds(),
        },
    )
    return result


@router.post("/api/admin/im/takeover/release", summary="退出人工接管")
async def admin_im_release(
    body: ImSessionKeyBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)

    with get_db() as db:
        ucs = _get_ucs(db, body.user_id, cid)
        if ucs.takeover_by is None:
            return {
                "ok": True,
                "user_id": body.user_id,
                "companion_id": cid,
                "is_taken_over": False,
                "released": False,
                "reason": "manual",
            }
        if not can_release_session(actor, ucs.takeover_by):
            raise HTTPException(
                status_code=403,
                detail=_get_error_msg("im_release_forbidden", lang),
            )

    reason = "force" if actor.get("is_super") else "manual"
    # 非超管只能是自己接管；超管释放他人记 force，释放自己仍 manual
    if actor.get("is_super") and actor.get("admin_id") is not None:
        with get_db() as db:
            ucs = _get_ucs(db, body.user_id, cid)
            if ucs.takeover_by is not None and int(ucs.takeover_by) == int(
                actor["admin_id"]
            ):
                reason = "manual"

    return await release_takeover_session(
        body.user_id,
        cid,
        actor=actor,
        reason=reason,
    )


@router.post("/api/admin/im/messages", summary="运营发送消息")
async def admin_im_send_message(
    body: ImSendMessageBody,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(body.companion_id, lang)
    actor = resolve_admin_actor(_token)
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=_get_error_msg("empty_content", lang))

    with get_db() as db:
        ucs = _get_ucs(db, body.user_id, cid)
        _require_session_access(actor, db, cid, lang)
        if ucs.takeover_by is None:
            raise HTTPException(
                status_code=409,
                detail=_get_error_msg("im_not_taken_over", lang),
            )
        if not actor.get("is_super"):
            if actor.get("admin_id") is None or int(ucs.takeover_by) != int(
                actor["admin_id"]
            ):
                raise HTTPException(
                    status_code=403,
                    detail=_get_error_msg("im_send_forbidden", lang),
                )

    msg = persist_operator_message(
        user_id=body.user_id,
        companion_id=cid,
        content=text,
        admin_id=actor.get("admin_id"),
    )
    touch_takeover_activity(body.user_id, cid)
    # App 侧仍显示为智能体（assistant）
    pushed = push_operator_to_user_app(body.user_id, cid, text)
    write_im_audit(
        action="send_message",
        actor=actor,
        user_id=body.user_id,
        companion_id=cid,
        detail={"message_id": msg.get("id"), "len": len(text)},
    )
    await broadcast_admin_im(
        body.user_id,
        cid,
        {
            "type": "message",
            "role": "operator",
            "text": text,
            "id": msg.get("id"),
            "admin_id": actor.get("admin_id"),
            "admin_username": actor.get("username") or "",
            "created_at": msg.get("created_at"),
            "pushed_to_user": pushed,
        },
    )
    return {**msg, "pushed_to_user": pushed}


# ===== 侧栏资料 / 消耗 =====


@router.get("/api/admin/im/companions/{companion_id}", summary="IM 侧智能体资料")
async def admin_im_companion_profile(
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    cid = _norm_cid(companion_id, lang)
    actor = resolve_admin_actor(_token)
    with get_db() as db:
        if not actor.get("is_super"):
            if not can_access_companion(actor, db, cid):
                raise HTTPException(
                    status_code=403, detail=_get_error_msg("im_session_forbidden", lang)
                )
        data = companion_profile_payload(db, cid)
        if not data:
            raise HTTPException(
                status_code=404, detail=_get_error_msg("companion_not_found", lang)
            )
        return data


@router.get(
    "/api/admin/im/users/{user_id}/consume-stats",
    summary="IM 顶部用户消耗统计",
)
async def admin_im_user_consume_stats(
    user_id: int,
    companion_id: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.credit_service import (
        get_effective_balance,
        get_user_free_turns_snapshot,
        is_vip_active,
    )

    actor = resolve_admin_actor(_token)
    with get_db() as db:
        # 非超管必须带 companion_id，且只能看自己负责的会话对应用户
        if not actor.get("is_super"):
            if not companion_id:
                raise HTTPException(
                    status_code=403, detail=_get_error_msg("im_session_forbidden", lang)
                )
            cid_chk = companion_id.strip()[:8]
            ucs_chk = (
                db.query(UserCompanionStateORM)
                .filter(
                    UserCompanionStateORM.user_id == user_id,
                    UserCompanionStateORM.companion_id == cid_chk,
                )
                .first()
            )
            if not ucs_chk:
                raise HTTPException(
                    status_code=404, detail=_get_error_msg("session_not_found", lang)
                )
            _require_session_access(actor, db, cid_chk, lang)

        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=404, detail=_get_error_msg("user_not_found", lang)
            )
        uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
        ft_snap = get_user_free_turns_snapshot(db, user_id)
        free_limit = int(ft_snap.get("free_turns_limit") or 0)
        free_used = int(ft_snap.get("free_turns_used") or 0)
        session_turns = 0
        if companion_id:
            cid = companion_id.strip()[:8]
            ucs = (
                db.query(UserCompanionStateORM)
                .filter(
                    UserCompanionStateORM.user_id == user_id,
                    UserCompanionStateORM.companion_id == cid,
                )
                .first()
            )
            session_turns = int(ucs.turns or 0) if ucs else 0

        spent = (
            db.query(func.coalesce(func.sum(CreditTransactionORM.amount), 0))
            .filter(
                CreditTransactionORM.user_id == user_id,
                CreditTransactionORM.amount < 0,
            )
            .scalar()
        )
        spent_abs = abs(int(spent or 0))

        return {
            "user_id": user_id,
            "username": user.username or "",
            "nickname": user.nickname or "",
            "is_vip": bool(is_vip_active(uc)) if uc else False,
            "vip_type": (uc.vip_type or "") if uc and is_vip_active(uc) else "",
            "credits": int(get_effective_balance(uc) or 0) if uc else 0,
            "daily_gift_remaining": int(uc.daily_gift_remaining or 0) if uc else 0,
            "free_turns_remaining": max(0, free_limit - free_used),
            "session_turns": session_turns,
            "credits_spent_total": spent_abs,
        }


@router.get("/api/admin/im/audit-logs", summary="IM 操作审计日志")
async def admin_im_audit_logs(
    _token: str = Depends(admin_auth_required),
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[int] = None,
    companion_id: Optional[str] = None,
    action: Optional[str] = None,
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    actor = resolve_admin_actor(_token)
    with get_db() as db:
        q = db.query(AdminImAuditLogORM)
        if not actor.get("is_super"):
            aid = actor.get("admin_id")
            if aid is None:
                return {"total": 0, "page": page, "page_size": page_size, "items": []}
            q = q.filter(AdminImAuditLogORM.admin_id == int(aid))
        if user_id is not None:
            q = q.filter(AdminImAuditLogORM.user_id == user_id)
        if companion_id:
            q = q.filter(AdminImAuditLogORM.companion_id == companion_id.strip()[:8])
        if action:
            q = q.filter(AdminImAuditLogORM.action == action.strip()[:40])
        total = q.count()
        rows = (
            q.order_by(desc(AdminImAuditLogORM.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [
            {
                "id": r.id,
                "admin_id": r.admin_id,
                "admin_username": r.admin_username or "",
                "action": r.action,
                "user_id": r.user_id,
                "companion_id": r.companion_id,
                "detail": r.detail or "",
                "created_at": serialize_datetime(r.created_at),
            }
            for r in rows
        ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


# ===== Admin IM WebSocket =====


@router.websocket("/ws/admin/im/inbox")
async def ws_admin_im_inbox(websocket: WebSocket):
    """运营 IM 收件箱：列表页响铃 / 未读实时更新。"""
    await websocket.accept()
    token = (
        websocket.headers.get("x-token")
        or websocket.headers.get("X-Token")
        or websocket.query_params.get("token")
        or ""
    ).strip()
    if not token or not verify_admin_token(token):
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "text": "unauthorized"}, ensure_ascii=False)
            )
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    actor = resolve_admin_actor(token)
    admin_id = actor.get("admin_id")
    if admin_id is None:
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "text": "admin_id_required",
                        "code": "im_admin_id_required",
                    },
                    ensure_ascii=False,
                )
            )
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    aid = int(admin_id)
    summary = ops_unread_summary_for_actor(actor)
    await register_admin_im_inbox(aid, websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "admin_id": aid,
                    "total_unread": summary.get("total_unread", 0),
                    "session_count": summary.get("session_count", 0),
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        await unregister_admin_im_inbox(aid, websocket)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = (data.get("type") or "").strip()
            if mtype in ("ping", "pong"):
                await websocket.send_text(
                    json.dumps({"type": "pong"}, ensure_ascii=False)
                )
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_admin_im_inbox error admin_id=%s", aid)
    finally:
        await unregister_admin_im_inbox(aid, websocket)


@router.websocket("/ws/admin/im/{user_id}/{companion_id}")
async def ws_admin_im(websocket: WebSocket, user_id: int, companion_id: str):
    """运营实时通道：接收用户消息；可发 operator 消息。"""
    await websocket.accept()
    token = (
        websocket.headers.get("x-token")
        or websocket.headers.get("X-Token")
        or websocket.query_params.get("token")
        or ""
    ).strip()
    if not token or not verify_admin_token(token):
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "text": "unauthorized"}, ensure_ascii=False)
            )
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    cid = (companion_id or "").strip()[:8]
    if not cid or user_id < 1:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    actor = resolve_admin_actor(token)

    with get_db() as db:
        ucs = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == cid,
            )
            .first()
        )
        if not ucs or not can_access_companion(actor, db, cid):
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "text": "forbidden",
                            "code": "im_session_forbidden",
                        },
                        ensure_ascii=False,
                    )
                )
                await websocket.close(code=1008)
            except Exception:
                pass
            return

        resolved_ops_id = get_session_ops_admin(db, cid)
        state = {
            "type": "hello",
            "user_id": user_id,
            "companion_id": cid,
            "is_taken_over": ucs.takeover_by is not None,
            "takeover_by": ucs.takeover_by,
            "takeover_at": serialize_datetime(ucs.takeover_at),
            "takeover_active_at": serialize_datetime(
                getattr(ucs, "takeover_active_at", None)
            ),
            "ops_admin_id": resolved_ops_id,
            "ops_admin": admin_user_brief(db, resolved_ops_id),
            "can_takeover": can_takeover_session(
                actor, resolved_ops_id, db=db, companion_id=cid
            ),
            "can_release": can_release_session(actor, ucs.takeover_by),
            "can_assign_ops": can_assign_ops(actor),
            "idle_timeout_seconds": takeover_idle_timeout_seconds(),
            "idle_remaining_seconds": idle_remaining_seconds(
                getattr(ucs, "takeover_active_at", None),
                ucs.takeover_at,
            )
            if ucs.takeover_by is not None
            else None,
        }

    await register_admin_im_ws(user_id, cid, websocket)
    try:
        # 打开会话窗 / 心跳不续期；仅「接管进入 / 发消息」刷新空闲计时
        # 打开会话 WS 即清未读
        try:
            cleared = await notify_ops_unread_cleared(
                user_id, cid, ops_admin_id=state.get("ops_admin_id")
            )
            state["ops_unread_count"] = 0
            if cleared and cleared.get("ops_last_read_at"):
                state["ops_last_read_at"] = cleared["ops_last_read_at"]
        except Exception:
            logger.exception(
                "IM clear unread on session ws failed user=%s companion=%s",
                user_id,
                cid,
            )
            state["ops_unread_count"] = 0
        await websocket.send_text(json.dumps(state, ensure_ascii=False))
    except Exception:
        await unregister_admin_im_ws(user_id, cid, websocket)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = (data.get("type") or "").strip()
            if mtype in ("ping", "pong"):
                # 心跳保活不续期，回报真实剩余秒数
                rem = None
                with get_db() as db:
                    row = (
                        db.query(UserCompanionStateORM)
                        .filter(
                            UserCompanionStateORM.user_id == user_id,
                            UserCompanionStateORM.companion_id == cid,
                        )
                        .first()
                    )
                    if row and row.takeover_by is not None:
                        rem = idle_remaining_seconds(
                            getattr(row, "takeover_active_at", None),
                            row.takeover_at,
                        )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "pong",
                            "takeover_touched": False,
                            "idle_timeout_seconds": takeover_idle_timeout_seconds(),
                            "idle_remaining_seconds": rem,
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            if mtype == "send":
                text = (data.get("text") or data.get("content") or "").strip()
                if not text:
                    continue
                # 复用 HTTP 发送逻辑约束
                with get_db() as db:
                    ucs = (
                        db.query(UserCompanionStateORM)
                        .filter(
                            UserCompanionStateORM.user_id == user_id,
                            UserCompanionStateORM.companion_id == cid,
                        )
                        .first()
                    )
                    if not ucs or not can_access_companion(actor, db, cid):
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "text": "forbidden",
                                    "code": "im_session_forbidden",
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue
                    if ucs.takeover_by is None:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "text": "not_taken_over",
                                    "code": "im_not_taken_over",
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue
                    if not actor.get("is_super") and (
                        actor.get("admin_id") is None
                        or int(ucs.takeover_by) != int(actor["admin_id"])
                    ):
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "text": "forbidden",
                                    "code": "im_send_forbidden",
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue
                msg = persist_operator_message(
                    user_id=user_id,
                    companion_id=cid,
                    content=text,
                    admin_id=actor.get("admin_id"),
                )
                touch_takeover_activity(user_id, cid)
                pushed = push_operator_to_user_app(user_id, cid, text)
                write_im_audit(
                    action="send_message",
                    actor=actor,
                    user_id=user_id,
                    companion_id=cid,
                    detail={"message_id": msg.get("id"), "via": "ws"},
                )
                event = {
                    "type": "message",
                    "role": "operator",
                    "text": text,
                    "id": msg.get("id"),
                    "admin_id": actor.get("admin_id"),
                    "admin_username": actor.get("username") or "",
                    "created_at": msg.get("created_at"),
                    "pushed_to_user": pushed,
                }
                await broadcast_admin_im(user_id, cid, event)
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_admin_im error user=%s companion=%s", user_id, cid)
    finally:
        await unregister_admin_im_ws(user_id, cid, websocket)
