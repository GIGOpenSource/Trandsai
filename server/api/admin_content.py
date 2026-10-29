"""内容管理后台 API：智能体列表/资料/策略库/朋友圈。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified

from api.admin_common import _get_error_msg, admin_auth_required, get_admin_lang, router
from core.database import CompanionORM, CompanionStrategyORM, MomentORM, StrategyTypeORM, get_db
from core.response import ok, paginated
from core.state import get_companion_manager
from services.strategy_image import normalize_strategy_image_url
from services.companion_content import (
    I18N_PROFILE_FIELDS,
    SUPPORTED_LANGUAGES,
    apply_companion_filters,
    apply_companion_tag_filter,
    apply_moment_engagement_stats,
    apply_moment_filters,
    companion_credit_stats,
    companion_list_item,
    moment_to_dict,
    profile_payload,
    strategy_to_dict,
    validate_trigger_event,
)
from services.moment_schedule import moment_effective_at

MOMENT_EXPORT_MAX_ROWS = 20000
from services.companion_manager import _normalize_gender
from services.im_admin import (
    apply_companion_ops_admin_scope_filter,
    assert_companion_in_scope,
    batch_admin_user_brief,
    build_companion_ops_map,
    companion_scope_sql_ids,
    lookup_companion_ops_map,
    resolve_admin_actor,
)
from services.moment_urls import normalize_preview_pos, optional_image_url
from services.msg_strategy_crud import load_companion_id_name_map
from services.strategy_trigger import StrategyTrigger
from services.strategy_types import list_strategy_types, strategy_type_to_dict

logger = logging.getLogger(__name__)

# 审核状态：与举报处置 / companions.moderation_status 一致
_ALLOWED_MODERATION_STATUS = frozenset({"published", "under_review", "removed", "restored"})


def _parse_preview_pos_field(data: dict):
    try:
        return normalize_preview_pos(data.get("preview_pos"))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_preview_pos")


def _ops_admin_name_obj(brief: Optional[dict]) -> Optional[dict]:
    """责任运营精简对象：{id, name}（name 优先 nickname，否则 username）。"""
    if not brief:
        return None
    name = (brief.get("nickname") or brief.get("username") or "").strip()
    return {"id": brief.get("id"), "name": name}


def _attach_companion_ops_admin(db, items: list, id_key: str = "id") -> list:
    """按智能体责任人填充 ops_admin_id / ops_admin({id,name})。"""
    if not items:
        return items
    cids = [str(it.get(id_key) or it.get("companion_id") or "") for it in items]
    ops_map = build_companion_ops_map(db, [c for c in cids if c])
    aids = {aid for aid in ops_map.values() if aid is not None}
    brief = batch_admin_user_brief(db, aids)
    for it in items:
        cid = str(it.get(id_key) or it.get("companion_id") or "")
        ops_id = lookup_companion_ops_map(ops_map, cid)
        it["ops_admin_id"] = ops_id
        it["ops_admin"] = (
            _ops_admin_name_obj(brief.get(int(ops_id))) if ops_id is not None else None
        )
    return items


def _attach_moment_ops_admin(db, items: list, companion_ids: list) -> list:
    """按智能体责任人填充 moments 列表的 ops_admin_id / ops_admin。"""
    if not items:
        return items
    ops_map = build_companion_ops_map(db, [str(c) for c in companion_ids if c])
    aids = {aid for aid in ops_map.values() if aid is not None}
    brief = batch_admin_user_brief(db, aids)
    for it in items:
        cid = str(it.get("companion_id") or "")
        ops_id = lookup_companion_ops_map(ops_map, cid)
        it["ops_admin_id"] = ops_id
        it["ops_admin"] = (
            _ops_admin_name_obj(brief.get(int(ops_id))) if ops_id is not None else None
        )
    return items


def _parse_int_id_filter(raw) -> List[int]:
    """解析整数 ID 多选（责任运营、朋友圈 ID）。

    支持：
    - 重复参数：moment_id=1&moment_id=2
    - 逗号分隔：moment_id=1,2
    - 中文逗号 / 分号
    """
    if raw is None:
        return []
    chunks: list = []
    if isinstance(raw, (list, tuple, set)):
        chunks = list(raw)
    else:
        chunks = [raw]
    out: List[int] = []
    seen: set[int] = set()
    for item in chunks:
        text = str(item).strip()
        if not text:
            continue
        for part in text.replace("，", ",").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                n = int(part)
            except (TypeError, ValueError):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get_companion_or_404(db, companion_id: str, lang: str = "zh") -> CompanionORM:
    row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
    return row


def _require_companion_scope(token: str, companion_id: str, lang: str = "zh") -> dict:
    actor = resolve_admin_actor(token)
    assert_companion_in_scope(actor, companion_id, lang)
    return actor


def _apply_actor_companion_scope(q, token: str, column=CompanionORM.id):
    """无权智能体直接从查询结果排除（不展示）。"""
    ids = companion_scope_sql_ids(resolve_admin_actor(token))
    if ids is None:
        return q
    return q.filter(column.in_(ids))


# ---------- 策略类型（= 埋点事件字典，可编辑） ----------


@router.get("/api/admin/content/strategy-types", summary="策略类型/埋点事件列表")
async def admin_content_strategy_types(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    keyword: Optional[str] = Query(None, description="英文/中文模糊"),
    is_active: Optional[bool] = Query(None),
    all: bool = Query(False, description="true 时不分页，返回全部（下拉用）"),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        rows = list_strategy_types(db, keyword=keyword, is_active=is_active)
        items = [strategy_type_to_dict(r) for r in rows]
        if all:
            return ok(data={"items": items, "total": len(items)})
        total = len(items)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        return paginated(items=page_items, total=total, page=page, page_size=page_size)


@router.post("/api/admin/content/strategy-types", summary="新建策略类型/埋点事件")
async def admin_content_create_strategy_type(
    data: dict,
    _token: str = Depends(admin_auth_required),
):
    event_key = (data.get("event_key") or data.get("name_en") or "").strip()
    name_zh = (data.get("name_zh") or "").strip()
    if not event_key or not name_zh:
        raise HTTPException(status_code=400, detail="event_key_and_name_zh_required")
    with get_db() as db:
        if db.query(StrategyTypeORM.id).filter(StrategyTypeORM.event_key == event_key).first():
            raise HTTPException(status_code=400, detail="event_key_exists")
        row = StrategyTypeORM(
            seq=int(data.get("seq") or 0),
            event_key=event_key,
            name_zh=name_zh,
            report_rule=(data.get("report_rule") or "").strip(),
            remark=(data.get("remark") or "").strip(),
            report_type=(data.get("report_type") or "PV & UV").strip()[:64],
            status=str(data.get("status") or "").strip()[:32],
            note=(data.get("note") or "").strip(),
            is_active=bool(data.get("is_active", True)),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        try:
            from services.msg_strategy_meta import invalidate_meta_cache

            invalidate_meta_cache()
        except Exception:
            pass
        return ok(data=strategy_type_to_dict(row))


@router.put("/api/admin/content/strategy-types/{type_id}", summary="更新策略类型/埋点事件")
async def admin_content_update_strategy_type(
    type_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = db.query(StrategyTypeORM).filter(StrategyTypeORM.id == type_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy_type_not_found")
        if "event_key" in data or "name_en" in data:
            new_key = (data.get("event_key") or data.get("name_en") or "").strip()
            if not new_key:
                raise HTTPException(status_code=400, detail="event_key_required")
            clash = (
                db.query(StrategyTypeORM.id)
                .filter(StrategyTypeORM.event_key == new_key, StrategyTypeORM.id != type_id)
                .first()
            )
            if clash:
                raise HTTPException(status_code=400, detail="event_key_exists")
            row.event_key = new_key
        if "name_zh" in data:
            row.name_zh = (data.get("name_zh") or "").strip()
        if "seq" in data:
            row.seq = int(data.get("seq") or 0)
        if "report_rule" in data:
            row.report_rule = (data.get("report_rule") or "").strip()
        if "remark" in data:
            row.remark = (data.get("remark") or "").strip()
        if "report_type" in data:
            row.report_type = (data.get("report_type") or "PV & UV").strip()[:64]
        if "status" in data:
            row.status = str(data.get("status") or "").strip()[:32]
        if "note" in data:
            row.note = (data.get("note") or "").strip()
        if "is_active" in data:
            row.is_active = bool(data.get("is_active"))
        db.commit()
        db.refresh(row)
        try:
            from services.msg_strategy_meta import invalidate_meta_cache

            invalidate_meta_cache()
        except Exception:
            pass
        return ok(data=strategy_type_to_dict(row))


@router.delete("/api/admin/content/strategy-types/{type_id}", summary="删除策略类型/埋点事件")
async def admin_content_delete_strategy_type(
    type_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = db.query(StrategyTypeORM).filter(StrategyTypeORM.id == type_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="strategy_type_not_found")
        in_use = (
            db.query(CompanionStrategyORM.id)
            .filter(
                (CompanionStrategyORM.trigger_event == row.event_key)
                | (CompanionStrategyORM.strategy_type == row.event_key)
            )
            .first()
        )
        if in_use:
            raise HTTPException(status_code=400, detail="strategy_type_in_use")
        db.delete(row)
        db.commit()
        try:
            from services.msg_strategy_meta import invalidate_meta_cache

            invalidate_meta_cache()
        except Exception:
            pass
        return ok(data={"deleted_id": type_id})


@router.get("/api/admin/content/product-events", summary="兼容别名→strategy-types")
async def admin_content_product_events_alias(_token: str = Depends(admin_auth_required)):
    """已合并到 strategy-types；保留别名避免旧前端报错。"""
    with get_db() as db:
        items = [strategy_type_to_dict(r) for r in list_strategy_types(db, active_only=True)]
        return ok(data={"items": items, "deprecated": True, "use": "/api/admin/content/strategy-types"})


@router.get("/api/admin/content/languages", summary="支持的语言列表")
async def admin_content_languages(_token: str = Depends(admin_auth_required)):
    from services.companion_content import ADMIN_LANGUAGES, supported_language_options

    return ok(
        data={
            "items": list(ADMIN_LANGUAGES),
            "options": supported_language_options(),
        }
    )


# ---------- 智能体列表 ----------


@router.get("/api/admin/content/companions", summary="内容管理-智能体列表")
async def admin_content_list_companions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    companion_id: Optional[str] = Query(
        None,
        description="智能体 ID 模糊；纯数字同时匹配创建人 ID。兼容 5 / 5.0",
    ),
    companionId: Optional[str] = Query(None, description="companion_id 别名"),
    id_alias: Optional[str] = Query(
        None, alias="id", description="companion_id 别名，兼容筛选项 id"
    ),
    keyword: Optional[str] = Query(None, description="ID/名字模糊"),
    language: Optional[str] = None,
    tier: Optional[str] = Query(
        None,
        description="分层：s/featured/candidate/ops（ops=已分配责任运营）",
    ),
    gender: Optional[str] = None,
    is_active: Optional[bool] = Query(None, description="是否生效"),
    is_deleted: Optional[bool] = Query(False, description="默认不含软删"),
    created_by: Optional[str] = Query(None, description="创建人 ID"),
    created_from: Optional[str] = Query(None, description="创建日期起 YYYY-MM-DD"),
    created_to: Optional[str] = Query(None, description="创建日期止 YYYY-MM-DD"),
    has_ops_admin: Optional[bool] = Query(
        None,
        description="true=仅已分配责任运营；false=仅未分配；不传=不过滤",
    ),
    ops_admin_id: Optional[List[str]] = Query(
        None,
        description="责任运营 ID 多选：ops_admin_id=107&ops_admin_id=57 或 ops_admin_id=107,57",
    ),
    tags: Optional[List[str]] = Query(
        None,
        description="运营标签多选，匹配 companions.tags：tags=romance&tags=中东24 或 tags=romance,中东24",
    ),
    sort_by: str = Query("created_at", description="created_at|total_credit_revenue|month_credit_revenue|age"),
    sort_order: str = Query("desc"),
    _token: str = Depends(admin_auth_required),
):
    ops_admin_ids = _parse_int_id_filter(ops_admin_id)
    with get_db() as db:
        q = db.query(CompanionORM)
        q = _apply_actor_companion_scope(q, _token, CompanionORM.id)
        q = apply_companion_filters(
            q,
            companion_id=companion_id or companionId or id_alias,
            keyword=keyword,
            language=language,
            tier=tier,
            gender=gender,
            is_active=is_active,
            is_deleted=is_deleted,
            created_by=created_by,
            created_from=_parse_dt(created_from),
            created_to=_parse_dt(created_to),
        )
        q = apply_companion_ops_admin_scope_filter(
            q,
            db,
            has_ops_admin=has_ops_admin,
            ops_admin_ids=ops_admin_ids or None,
        )
        q = apply_companion_tag_filter(q, tags)
        total = q.count()
        sort_key = (sort_by or "created_at").strip().lower()
        ascending = (sort_order or "desc").strip().lower() == "asc"

        if sort_key in ("total_credit_revenue", "month_credit_revenue"):
            rows = q.all()
            credits = companion_credit_stats(db, [r.id for r in rows])
            rows.sort(
                key=lambda r: credits.get(r.id, {}).get(sort_key, 0),
                reverse=not ascending,
            )
            start = (page - 1) * page_size
            rows = rows[start : start + page_size]
        else:
            if sort_key == "age":
                col = func.coalesce(CompanionORM.age, 18)
            else:
                col = CompanionORM.created_at
            order = col.asc().nullslast() if ascending else col.desc().nullslast()
            rows = q.order_by(order, CompanionORM.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
            credits = companion_credit_stats(db, [r.id for r in rows])

        items = [companion_list_item(r, credits.get(r.id, {})) for r in rows]
        # 按智能体责任人回填 ops_admin_id / ops_admin
        try:
            items = _attach_companion_ops_admin(db, items, id_key="id")
        except Exception:
            logger.exception("attach companion ops_admin failed")
            # 保证字段始终存在
            for it in items:
                it.setdefault("ops_admin_id", None)
                it.setdefault("ops_admin", None)
                it.setdefault("sexual_orientation", "")
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/content/companions/{companion_id}", summary="内容管理-智能体详情")
async def admin_content_get_companion(
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    with get_db() as db:
        row = _get_companion_or_404(db, companion_id, lang)
        credits = companion_credit_stats(db, [companion_id]).get(companion_id, {})
        data = profile_payload(row)
        data.update(credits)
        items = _attach_companion_ops_admin(db, [data], id_key="id")
        from services.private_album import list_admin_photos

        album = list_admin_photos(companion_id)
        items[0]["private_album_count"] = len(album)
        items[0]["private_album"] = album
        return ok(data=items[0])


@router.put("/api/admin/content/companions/{companion_id}/profile", summary="内容管理-基本资料")
async def admin_content_update_profile(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    cm = get_companion_manager()
    if not cm.get(companion_id, include_deleted=True):
        with get_db() as db:
            if not db.query(CompanionORM.id).filter(CompanionORM.id == companion_id).first():
                raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))

    payload = dict(data or {})
    if "gender" in payload:
        payload["gender"] = _normalize_gender(payload.get("gender"))
    if "tags" in payload:
        pass  # companion_manager.update handles tags
    if "i18n_profile" in payload and not isinstance(payload["i18n_profile"], dict):
        raise HTTPException(status_code=400, detail="i18n_profile must be object")
    if "profile_backgrounds" in payload:
        from services.companion_manager import normalize_profile_backgrounds

        payload["profile_backgrounds"] = normalize_profile_backgrounds(
            payload.get("profile_backgrounds")
        )
    if "moderation_status" in payload:
        status = str(payload.get("moderation_status") or "").strip()
        if status not in _ALLOWED_MODERATION_STATUS:
            raise HTTPException(
                status_code=400,
                detail=f"moderation_status must be one of: {', '.join(sorted(_ALLOWED_MODERATION_STATUS))}",
            )
        payload["moderation_status"] = status

    # 走 manager 同步内存 + DB 常规字段（软删/审核/开关/i18n 单独落库）
    _skip_core = {"i18n_profile", "is_active", "is_deleted", "moderation_status"}
    core = {k: v for k, v in payload.items() if k not in _skip_core}
    if core:
        updated = cm.update(companion_id, core)
        if not updated:
            # 库里有行但 Manager 加载失败时勿伪装成「不存在」
            with get_db() as db:
                exists = (
                    db.query(CompanionORM.id)
                    .filter(CompanionORM.id == companion_id)
                    .first()
                )
            if exists:
                logger.error(
                    "admin profile update: companion %s exists in DB but cm.update failed",
                    companion_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail="智能体资料加载失败，请检查人设字段是否过短或为空",
                )
            raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))

    with get_db() as db:
        row = _get_companion_or_404(db, companion_id, lang)
        if "is_active" in payload:
            row.is_active = bool(payload.get("is_active"))
        if "moderation_status" in payload:
            row.moderation_status = payload["moderation_status"]
            # 与举报处置对齐：下架关生效，恢复/上架开生效（显式传 is_active 时以显式为准）
            if "is_active" not in payload:
                if payload["moderation_status"] == "removed":
                    row.is_active = False
                elif payload["moderation_status"] in ("published", "restored"):
                    row.is_active = True
        if "i18n_profile" in payload:
            cleaned = {}
            for lk, block in (payload.get("i18n_profile") or {}).items():
                if lk not in SUPPORTED_LANGUAGES:
                    continue
                if not isinstance(block, dict):
                    continue
                cleaned[lk] = {k: str(v) for k, v in block.items() if k in I18N_PROFILE_FIELDS and v is not None}
            row.i18n_profile = cleaned
            flag_modified(row, "i18n_profile")
        db.commit()
        db.refresh(row)
        return ok(data=profile_payload(row))


@router.patch("/api/admin/content/companions/{companion_id}/active", summary="内容管理-生效开关")
async def admin_content_set_active(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    active = bool(data.get("is_active", data.get("active", True)))
    with get_db() as db:
        row = _get_companion_or_404(db, companion_id, lang)
        row.is_active = active
        db.commit()
    return ok(data={"id": companion_id, "is_active": active})


# ---------- 单体消息策略（单智能体嵌套；跨智能体列表见 /api/admin/companion-strategies） ----------


@router.get(
    "/api/admin/content/companions/{companion_id}/strategies",
    summary="单体消息策略列表（指定智能体）",
)
async def admin_content_list_strategies(
    companion_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    language: Optional[str] = None,
    trigger_event: Optional[str] = Query(None, description="埋点事件名筛选"),
    strategy_type: Optional[str] = Query(None, description="兼容旧参数，等同 trigger_event"),
    is_active: Optional[bool] = None,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    event_filter = (trigger_event or strategy_type or "").strip()
    with get_db() as db:
        _get_companion_or_404(db, companion_id, lang)
        q = db.query(CompanionStrategyORM).filter(CompanionStrategyORM.companion_id == companion_id)
        if language:
            q = q.filter(CompanionStrategyORM.language == language.strip())
        if event_filter:
            q = q.filter(
                (CompanionStrategyORM.trigger_event == event_filter)
                | (CompanionStrategyORM.strategy_type == event_filter)
            )
        if is_active is not None:
            q = q.filter(CompanionStrategyORM.is_active == is_active)
        total = q.count()
        rows = (
            q.order_by(CompanionStrategyORM.sort_order.asc(), CompanionStrategyORM.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        name_map = load_companion_id_name_map(db, [companion_id])
        cname = name_map.get(companion_id, "")
        items = [strategy_to_dict(r, companion_name=cname) for r in rows]
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/api/admin/content/companions/{companion_id}/strategies",
    summary="新建单体消息策略（指定智能体）",
)
async def admin_content_create_strategy(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    trigger_event = (data.get("trigger_event") or data.get("strategy_type") or "").strip()
    content = (data.get("content") or "").strip()
    language = (data.get("language") or "zh").strip()
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="unsupported_language")
    if not content:
        raise HTTPException(status_code=400, detail="content_required")

    with get_db() as db:
        try:
            trigger_event = validate_trigger_event(trigger_event, db)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_trigger_event")
        _get_companion_or_404(db, companion_id, lang)
        row = CompanionStrategyORM(
            companion_id=companion_id,
            strategy_type=trigger_event,
            content=content,
            image_url=normalize_strategy_image_url(data.get("image_url")),
            language=language,
            trigger_event=trigger_event,
            source=(data.get("source") or "manual").strip()[:20],
            sort_order=int(data.get("sort_order") or 0),
            is_active=bool(data.get("is_active", True)),
            created_by=(data.get("created_by") or "").strip()[:64],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ok(data=strategy_to_dict(row))


@router.put(
    "/api/admin/content/companions/{companion_id}/strategies/{strategy_id}",
    summary="更新单体消息策略（指定智能体）",
)
async def admin_content_update_strategy(
    companion_id: str,
    strategy_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    with get_db() as db:
        _get_companion_or_404(db, companion_id, lang)
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id, CompanionStrategyORM.companion_id == companion_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        if "trigger_event" in data or "strategy_type" in data:
            raw = (data.get("trigger_event") or data.get("strategy_type") or "").strip()
            try:
                ev = validate_trigger_event(raw, db)
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_trigger_event")
            row.trigger_event = ev
            row.strategy_type = ev
        if "content" in data:
            row.content = (data.get("content") or "").strip()
        if "image_url" in data:
            row.image_url = normalize_strategy_image_url(data.get("image_url"))
        if "language" in data:
            lk = (data.get("language") or "").strip()
            if lk not in SUPPORTED_LANGUAGES:
                raise HTTPException(status_code=400, detail="unsupported_language")
            row.language = lk
        if "sort_order" in data:
            row.sort_order = int(data.get("sort_order") or 0)
        if "is_active" in data:
            row.is_active = bool(data.get("is_active"))
        db.commit()
        db.refresh(row)
        return ok(data=strategy_to_dict(row))


@router.patch(
    "/api/admin/content/companions/{companion_id}/strategies/{strategy_id}/active",
    summary="单体消息策略开关（指定智能体）",
)
async def admin_content_strategy_active(
    companion_id: str,
    strategy_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id, CompanionStrategyORM.companion_id == companion_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": strategy_id, "is_active": bool(row.is_active)})


@router.delete(
    "/api/admin/content/companions/{companion_id}/strategies/{strategy_id}",
    summary="删除单体消息策略（指定智能体）",
)
async def admin_content_delete_strategy(
    companion_id: str,
    strategy_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    with get_db() as db:
        row = (
            db.query(CompanionStrategyORM)
            .filter(CompanionStrategyORM.id == strategy_id, CompanionStrategyORM.companion_id == companion_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="strategy_not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": strategy_id})


@router.post("/api/admin/content/strategies/dispatch-preview", summary="策略伪触发（print，不发消息）")
async def admin_content_strategy_dispatch_preview(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """调试：传入用户 token + trigger_event，匹配策略并 print。"""
    trigger_event = (data.get("trigger_event") or data.get("event") or "").strip()
    user_token = (data.get("user_token") or data.get("token") or "").strip()
    if not trigger_event or not user_token:
        raise HTTPException(status_code=400, detail="trigger_event_and_user_token_required")
    cid = (data.get("companion_id") or "").strip() or None
    if cid:
        _require_companion_scope(_token, cid, lang)
    result = StrategyTrigger.dispatch(
        trigger_event,
        user_token,
        companion_id=cid,
        language=(data.get("language") or "").strip() or None,
        extra=data.get("extra") if isinstance(data.get("extra"), dict) else None,
    )
    return ok(data=result)


# ---------- 朋友圈（智能体维度 + 全局） ----------


def _moment_list_id_tokens(companion_ids: Optional[str]) -> Optional[List[str]]:
    if not companion_ids or not companion_ids.strip():
        return None
    return [
        p.strip()
        for p in companion_ids.replace("，", ",").split(",")
        if p.strip()
    ]


def _query_admin_moments(
    db,
    *,
    token: str,
    companion_id: Optional[str] = None,
    companion_ids: Optional[List[str]] = None,
    keyword: Optional[str] = None,
    language: Optional[str] = None,
    has_image: Optional[bool] = None,
    moment_ids: Optional[List[int]] = None,
    published_from: Optional[datetime] = None,
    published_to: Optional[datetime] = None,
    has_ops_admin: Optional[bool] = None,
    ops_admin_ids: Optional[List[int]] = None,
    force_companion_id: Optional[str] = None,
):
    q = db.query(MomentORM)
    if force_companion_id:
        q = q.filter(MomentORM.companion_id == force_companion_id)
    else:
        q = _apply_actor_companion_scope(q, token, MomentORM.companion_id)
    if moment_ids:
        q = q.filter(MomentORM.id.in_(moment_ids))
    q = apply_moment_filters(
        q,
        companion_id=companion_id,
        companion_ids=companion_ids,
        keyword=keyword,
        language=language,
        has_image=has_image,
        published_from=published_from,
        published_to=published_to,
    )
    if force_companion_id is None:
        q = apply_companion_ops_admin_scope_filter(
            q,
            db,
            has_ops_admin=has_ops_admin,
            ops_admin_ids=ops_admin_ids or None,
            id_column=MomentORM.companion_id,
        )
    return q


def _moments_to_items(db, rows: list) -> list:
    cids = list({r.companion_id for r in rows})
    companions = {
        c.id: c for c in db.query(CompanionORM).filter(CompanionORM.id.in_(cids)).all()
    } if cids else {}
    items = [moment_to_dict(r, companions.get(r.companion_id)) for r in rows]
    items = apply_moment_engagement_stats(db, items)
    try:
        items = _attach_moment_ops_admin(db, items, cids)
    except Exception:
        logger.exception("attach moments ops_admin failed")
        for it in items:
            it.setdefault("ops_admin_id", None)
            it.setdefault("ops_admin", None)
    return items


def _export_moments_xlsx(items: List[dict]) -> StreamingResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "朋友圈导出"
    headers = [
        "动态ID",
        "智能体ID",
        "智能体名称",
        "智能体语言",
        "发布时间",
        "展示次数",
        "展示人数",
        "详情点击次数",
        "详情点击人数",
        "点赞次数",
        "点赞人数",
        "评论次数",
        "评论人数",
        "发消息按钮点击次数",
        "发消息按钮点击人数",
    ]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin
    for r_idx, it in enumerate(items, 2):
        vals = [
            it.get("id"),
            it.get("companion_id") or "",
            it.get("companion_name") or "",
            it.get("language") or "",
            it.get("published_at") or it.get("publish_at") or it.get("created_at") or "",
            it.get("view_count", 0),
            it.get("view_users", 0),
            it.get("detail_click_count", 0),
            it.get("detail_click_users", 0),
            it.get("likes_count", 0),
            it.get("likes_users", 0),
            it.get("comments_count", 0),
            it.get("comments_users", 0),
            it.get("msg_click_count", 0),
            it.get("msg_click_users", 0),
        ]
        for c_idx, v in enumerate(vals, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=v)
            cell.border = thin
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"朋友圈导出_{ts}.xlsx"
    ascii_name = f"moments_export_{ts}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
            )
        },
    )


@router.get("/api/admin/content/companions/{companion_id}/moments", summary="智能体朋友圈列表")
async def admin_content_companion_moments(
    companion_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    language: Optional[str] = Query(
        None, description="文案或智能体语种：ja / 日本語 / ja-JP 等"
    ),
    has_image: Optional[bool] = None,
    moment_id: Optional[List[str]] = Query(
        None,
        description="动态 ID 多选：moment_id=1&moment_id=2 或 moment_id=1,2",
    ),
    published_from: Optional[str] = Query(None, description="发布时间起 YYYY-MM-DD"),
    published_to: Optional[str] = Query(None, description="发布时间止 YYYY-MM-DD"),
    sort_order: str = Query("desc", description="发布时间 asc|desc"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    moment_ids = _parse_int_id_filter(moment_id)
    with get_db() as db:
        companion = _get_companion_or_404(db, companion_id, lang)
        q = _query_admin_moments(
            db,
            token=_token,
            language=language,
            has_image=has_image,
            moment_ids=moment_ids or None,
            published_from=_parse_dt(published_from),
            published_to=_parse_dt(published_to),
            force_companion_id=companion_id,
        )
        total = q.count()
        ascending = sort_order.strip().lower() == "asc"
        pub = moment_effective_at()
        order = pub.asc() if ascending else pub.desc()
        rows = q.order_by(order, MomentORM.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        items = [moment_to_dict(r, companion) for r in rows]
        items = apply_moment_engagement_stats(db, items)
        try:
            items = _attach_moment_ops_admin(db, items, [companion_id])
        except Exception:
            logger.exception("attach companion moments ops_admin failed")
            for it in items:
                it.setdefault("ops_admin_id", None)
                it.setdefault("ops_admin", None)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/content/moments/export", summary="朋友圈导出 Excel")
async def admin_content_export_moments(
    companion_id: Optional[str] = Query(
        None, description="智能体 ID 或名称，模糊匹配；也可与 companion_ids 合并"
    ),
    companion_ids: Optional[str] = Query(
        None,
        description="多个智能体 ID 或名称，逗号分隔",
    ),
    keyword: Optional[str] = Query(
        None, description="智能体 ID/名称模糊，与 companion_id 合并"
    ),
    language: Optional[str] = Query(
        None,
        description="文案或智能体语种",
    ),
    has_image: Optional[bool] = None,
    moment_id: Optional[List[str]] = Query(
        None,
        description="动态 ID 多选：moment_id=1&moment_id=2 或 moment_id=1,2",
    ),
    published_from: Optional[str] = Query(None, description="发布时间起 YYYY-MM-DD"),
    published_to: Optional[str] = Query(None, description="发布时间止 YYYY-MM-DD"),
    has_ops_admin: Optional[bool] = Query(
        None,
        description="true=仅有责任运营；false=仅未分配；不传=不过滤",
    ),
    ops_admin_id: Optional[List[str]] = Query(
        None,
        description="责任运营 ID 多选",
    ),
    sort_order: str = Query("desc"),
    _token: str = Depends(admin_auth_required),
):
    """导出与列表相同筛选条件下的全部匹配记录（最多 20000 条）。"""
    id_list = _moment_list_id_tokens(companion_ids)
    moment_ids = _parse_int_id_filter(moment_id)
    ops_admin_ids = _parse_int_id_filter(ops_admin_id)
    with get_db() as db:
        q = _query_admin_moments(
            db,
            token=_token,
            companion_id=companion_id,
            companion_ids=id_list,
            keyword=keyword,
            language=language,
            has_image=has_image,
            moment_ids=moment_ids or None,
            published_from=_parse_dt(published_from),
            published_to=_parse_dt(published_to),
            has_ops_admin=has_ops_admin,
            ops_admin_ids=ops_admin_ids,
        )
        total = q.count()
        if total > MOMENT_EXPORT_MAX_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"匹配 {total} 条，超过导出上限 {MOMENT_EXPORT_MAX_ROWS}，请缩小发布时间或其它筛选",
            )
        ascending = sort_order.strip().lower() == "asc"
        pub = moment_effective_at()
        order = pub.asc() if ascending else pub.desc()
        rows = q.order_by(order, MomentORM.id.desc()).limit(MOMENT_EXPORT_MAX_ROWS).all()
        items = _moments_to_items(db, rows)
    return _export_moments_xlsx(items)


@router.get("/api/admin/content/moments", summary="全局朋友圈列表")
async def admin_content_list_moments(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    companion_id: Optional[str] = Query(
        None, description="智能体 ID 或名称，模糊匹配；也可与 companion_ids 合并"
    ),
    companion_ids: Optional[str] = Query(
        None,
        description="多个智能体 ID 或名称，逗号分隔；名称/部分 ID 模糊匹配",
    ),
    keyword: Optional[str] = Query(
        None, description="智能体 ID/名称模糊，与 companion_id 合并"
    ),
    language: Optional[str] = Query(
        None,
        description="文案或智能体语种：ja / 日本語 / ja-JP 等，非精确码也会归一",
    ),
    has_image: Optional[bool] = None,
    moment_id: Optional[List[str]] = Query(
        None,
        description="动态 ID 多选：moment_id=1&moment_id=2 或 moment_id=1,2",
    ),
    published_from: Optional[str] = Query(
        None, description="发布时间起 YYYY-MM-DD（coalesce(publish_at, created_at)）"
    ),
    published_to: Optional[str] = Query(
        None, description="发布时间止 YYYY-MM-DD"
    ),
    has_ops_admin: Optional[bool] = Query(
        None,
        description="true=仅有责任运营的智能体朋友圈；false=仅未分配；不传=不过滤",
    ),
    ops_admin_id: Optional[List[str]] = Query(
        None,
        description="责任运营 ID 多选：ops_admin_id=107&ops_admin_id=57 或 ops_admin_id=107,57",
    ),
    sort_order: str = Query("desc"),
    _token: str = Depends(admin_auth_required),
):
    id_list = _moment_list_id_tokens(companion_ids)
    moment_ids = _parse_int_id_filter(moment_id)
    ops_admin_ids = _parse_int_id_filter(ops_admin_id)
    with get_db() as db:
        q = _query_admin_moments(
            db,
            token=_token,
            companion_id=companion_id,
            companion_ids=id_list,
            keyword=keyword,
            language=language,
            has_image=has_image,
            moment_ids=moment_ids or None,
            published_from=_parse_dt(published_from),
            published_to=_parse_dt(published_to),
            has_ops_admin=has_ops_admin,
            ops_admin_ids=ops_admin_ids,
        )
        total = q.count()
        ascending = sort_order.strip().lower() == "asc"
        pub = moment_effective_at()
        order = pub.asc() if ascending else pub.desc()
        rows = (
            q.order_by(order, MomentORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = _moments_to_items(db, rows)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/content/moments/{moment_id}", summary="朋友圈详情")
async def admin_content_get_moment(
    moment_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        _require_companion_scope(_token, row.companion_id, lang)
        companion = db.query(CompanionORM).filter(CompanionORM.id == row.companion_id).first()
        data = moment_to_dict(row, companion)
        apply_moment_engagement_stats(db, [data])
        try:
            items = _attach_moment_ops_admin(db, [data], [row.companion_id])
            data = items[0]
        except Exception:
            logger.exception("attach moment detail ops_admin failed")
            data.setdefault("ops_admin_id", None)
            data.setdefault("ops_admin", None)
        return ok(data=data)


@router.post("/api/admin/content/moments", summary="新建朋友圈")
async def admin_content_create_moment(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """创建时可指定定时发布（schedule_at + schedule_lang）；发布后仅允许改文案/配图。"""
    from services.moment_schedule import resolve_schedule_for_create

    companion_id = (data.get("companion_id") or "").strip()
    caption = (data.get("caption") or "").strip()
    if not companion_id or not caption:
        raise HTTPException(status_code=400, detail=_get_error_msg("companion_id_caption_required", lang))
    _require_companion_scope(_token, companion_id, lang)

    with get_db() as db:
        companion = _get_companion_or_404(db, companion_id, lang)
        caption_lang = (data.get("caption_lang") or companion.language or "zh").strip()
        schedule_lang = None
        schedule_at_local = None
        publish_at = None
        try:
            schedule_lang, schedule_at_local, publish_at = resolve_schedule_for_create(
                data,
                fallback_lang=caption_lang,
            )
        except ValueError as e:
            code = str(e)
            if code == "schedule_lang_required":
                raise HTTPException(status_code=400, detail="schedule_lang_required")
            raise HTTPException(status_code=400, detail="invalid_schedule_at")

        created_at = None
        if not publish_at and data.get("created_at"):
            try:
                raw = str(data["created_at"]).replace("Z", "+00:00")
                created_at = datetime.fromisoformat(raw)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_created_at")

        moment = MomentORM(
            companion_id=companion_id,
            caption=caption,
            image_url=optional_image_url(data.get("image_url")),
            preview_url=optional_image_url(data.get("preview_url")),
            preview_pos=_parse_preview_pos_field(data),
            caption_lang=caption_lang,
            schedule_lang=schedule_lang,
            schedule_at_local=schedule_at_local,
            publish_at=publish_at,
        )
        if created_at:
            moment.created_at = created_at
        elif publish_at:
            moment.created_at = publish_at
        db.add(moment)
        db.commit()
        db.refresh(moment)
        return ok(data=moment_to_dict(moment, companion))


@router.put("/api/admin/content/moments/{moment_id}", summary="编辑朋友圈（仅文案/配图）")
async def admin_content_update_moment(
    moment_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    if "created_at" in data:
        raise HTTPException(status_code=400, detail="moment_created_at_readonly")

    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        _require_companion_scope(_token, row.companion_id, lang)
        if "caption" in data:
            row.caption = (data.get("caption") or "").strip()
        if "image_url" in data:
            row.image_url = optional_image_url(data.get("image_url"))
        if "preview_url" in data:
            row.preview_url = optional_image_url(data.get("preview_url"))
        if "preview_pos" in data:
            row.preview_pos = _parse_preview_pos_field(data)
        db.commit()
        companion = db.query(CompanionORM).filter(CompanionORM.id == row.companion_id).first()
        return ok(data=moment_to_dict(row, companion))


@router.delete("/api/admin/content/moments/{moment_id}", summary="删除朋友圈")
async def admin_content_delete_moment(
    moment_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.moments import delete_moment

    with get_db() as db:
        row = db.query(MomentORM).filter(MomentORM.id == moment_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
        _require_companion_scope(_token, row.companion_id, lang)

    if not delete_moment(moment_id):
        raise HTTPException(status_code=404, detail=_get_error_msg("moment_not_found", lang))
    return ok(data={"deleted_id": moment_id})


# ---------- 帖子（内容管理） ----------


def _admin_reviewer_name(token: str) -> str:
    from services.im_admin import resolve_admin_actor

    actor = resolve_admin_actor(token)
    return str(getattr(actor, "username", None) or getattr(actor, "id", None) or "admin")


@router.get("/api/admin/content/posts", summary="帖子列表")
async def admin_content_list_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    review_status: Optional[str] = Query(
        None, description="pending / approved / rejected / all；空=全部"
    ),
    language: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = Query(
        None,
        description="作者用户 ID 精确匹配。兼容 16、16.0；昵称请用 user_nickname",
    ),
    userId: Optional[str] = Query(None, description="user_id 别名"),
    uid: Optional[str] = Query(None, description="user_id 别名"),
    keyword: Optional[str] = Query(None, description="标题/正文模糊搜"),
    user_nickname: Optional[str] = Query(None, description="作者昵称模糊"),
    has_image: Optional[bool] = None,
    created_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    created_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    sort_by: str = Query(
        "created_at",
        description="created_at / reviewed_at / likes_count / comments_count",
    ),
    sort_order: str = Query("desc", description="asc / desc"),
    _token: str = Depends(admin_auth_required),
):
    from services.posts import list_posts_for_content_admin

    items, total = list_posts_for_content_admin(
        review_status=review_status,
        language=language,
        category=category,
        user_id=user_id or userId or uid,
        keyword=keyword,
        user_nickname=user_nickname,
        has_image=has_image,
        created_from=created_from,
        created_to=created_to,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/content/posts/{post_id}", summary="帖子详情")
async def admin_content_get_post(
    post_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import get_post_for_admin

    item = get_post_for_admin(post_id)
    if not item:
        raise HTTPException(status_code=404, detail=_get_error_msg("post_not_found", lang))
    return ok(data=item)


@router.post("/api/admin/content/posts", summary="新建帖子（运营代发，默认已通过）")
async def admin_content_create_post(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_create_post

    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail=_get_error_msg("title_content_required", lang))

    user_id = data.get("user_id")
    if user_id is not None and user_id != "":
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_user_id")
    else:
        user_id = None

    result = admin_create_post(
        title=title,
        content=content,
        user_id=user_id,
        user_name=(data.get("user_name") or "匿名用户"),
        images=data.get("images"),
        category=(data.get("category") or ""),
        language=(data.get("language") or "zh"),
        reviewer=_admin_reviewer_name(_token),
    )
    if not result.get("ok"):
        err = result.get("error") or "server_error"
        raise HTTPException(status_code=400, detail=err if isinstance(err, str) else _get_error_msg("server_error", lang))
    result.pop("ok", None)
    return ok(data=result)


@router.put("/api/admin/content/posts/{post_id}", summary="编辑帖子（不改变审核状态）")
async def admin_content_update_post(
    post_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_update_post

    kwargs = {}
    if "title" in data:
        kwargs["title"] = data.get("title")
    if "content" in data:
        kwargs["content"] = data.get("content")
    if "images" in data:
        kwargs["images"] = data.get("images")
    if "category" in data:
        kwargs["category"] = data.get("category")
    if "language" in data:
        kwargs["language"] = data.get("language")

    result = admin_update_post(post_id, **kwargs)
    if not result.get("ok"):
        err = result.get("error", "server_error")
        status = 404 if err == "post_not_found" else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    result.pop("ok", None)
    return ok(data=result)


@router.delete("/api/admin/content/posts/{post_id}", summary="删除帖子")
async def admin_content_delete_post(
    post_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_delete_post

    result = admin_delete_post(post_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail=_get_error_msg(result.get("error", "post_not_found"), lang),
        )
    return ok(data={"deleted_id": post_id})


@router.get("/api/admin/content/posts/{post_id}/comments", summary="帖子评论列表")
async def admin_content_list_post_comments(
    post_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    keyword: Optional[str] = None,
    user_id: Optional[int] = None,
    sort_order: str = Query("asc"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import list_comments_for_admin

    items, total = list_comments_for_admin(
        post_id,
        keyword=keyword,
        user_id=user_id,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    if total < 0:
        raise HTTPException(status_code=404, detail=_get_error_msg("post_not_found", lang))
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.post("/api/admin/content/posts/{post_id}/comments", summary="新建评论（运营代发）")
async def admin_content_create_post_comment(
    post_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_add_comment

    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail=_get_error_msg("empty_content", lang))

    user_id = data.get("user_id")
    if user_id is not None and user_id != "":
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_user_id")
    else:
        user_id = None

    result = admin_add_comment(
        post_id,
        content,
        user_id=user_id,
        user_name=(data.get("user_name") or "官方"),
    )
    if not result.get("ok"):
        err = result.get("error", "server_error")
        status = 404 if err == "post_not_found" else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    result.pop("ok", None)
    return ok(data=result)


@router.put(
    "/api/admin/content/posts/{post_id}/comments/{comment_id}",
    summary="编辑评论",
)
async def admin_content_update_post_comment(
    post_id: int,
    comment_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_update_comment

    result = admin_update_comment(post_id, comment_id, (data.get("content") or ""))
    if not result.get("ok"):
        err = result.get("error", "server_error")
        status = 404 if err == "comment_not_found" else 400
        raise HTTPException(status_code=status, detail=_get_error_msg(err, lang))
    result.pop("ok", None)
    return ok(data=result)


@router.delete(
    "/api/admin/content/posts/{post_id}/comments/{comment_id}",
    summary="删除评论",
)
async def admin_content_delete_post_comment(
    post_id: int,
    comment_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.posts import admin_delete_comment

    result = admin_delete_comment(post_id, comment_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail=_get_error_msg(result.get("error", "comment_not_found"), lang),
        )
    return ok(data={"deleted_id": comment_id, "post_id": post_id})


@router.get(
    "/api/admin/content/companions/{companion_id}/private-album",
    summary="内容管理-私密相册列表",
)
async def admin_list_private_album(
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    from services.private_album import list_admin_photos

    with get_db() as db:
        _get_companion_or_404(db, companion_id, lang)
    items = list_admin_photos(companion_id)
    return ok(data={"companion_id": companion_id, "items": items, "total": len(items)})


@router.post(
    "/api/admin/content/companions/{companion_id}/private-album",
    summary="内容管理-新建私密照片",
)
async def admin_create_private_album_photo(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    from services.private_album import create_admin_photo

    with get_db() as db:
        _get_companion_or_404(db, companion_id, lang)
    try:
        return ok(data=create_admin_photo(companion_id, data or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put(
    "/api/admin/content/companions/{companion_id}/private-album/{photo_id}",
    summary="内容管理-编辑私密照片",
)
async def admin_update_private_album_photo(
    companion_id: str,
    photo_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    from services.private_album import update_admin_photo

    try:
        return ok(data=update_admin_photo(companion_id, photo_id, data or {}))
    except KeyError:
        raise HTTPException(status_code=404, detail="not_found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete(
    "/api/admin/content/companions/{companion_id}/private-album/{photo_id}",
    summary="内容管理-删除私密照片",
)
async def admin_delete_private_album_photo(
    companion_id: str,
    photo_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    from services.private_album import delete_admin_photo

    try:
        return ok(data=delete_admin_photo(companion_id, photo_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="not_found")


@router.post(
    "/api/admin/content/companions/{companion_id}/private-album/reorder",
    summary="内容管理-私密相册排序",
)
async def admin_reorder_private_album(
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    _require_companion_scope(_token, companion_id, lang)
    from services.private_album import reorder_admin_photos

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items_required")
    with get_db() as db:
        _get_companion_or_404(db, companion_id, lang)
    photos = reorder_admin_photos(companion_id, items)
    return ok(data={"companion_id": companion_id, "items": photos, "total": len(photos)})
