"""智能体消息策略中心：以智能体为主的三类策略应用列表 + 单行 CRUD。

与文案库视角接口并存，勿混用：
- 本模块 /api/admin/strategy-center/* ：一行 = 一个智能体 × 一条策略（精准）
- /api/admin/companion-strategies ：单体策略（已是单智能体一行，字段更少）
- /api/admin/msg-strategy/library ：埋点库全局文案（companion_ids 空，作用全部智能体）
- /api/admin/quick-reply/library ：快回库（一行多个 companion_ids）
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, HTTPException, Query

from api.admin_common import _get_error_msg, admin_auth_required, get_admin_lang, router
from core.database import CompanionStrategyORM, get_db
from core.response import ok, paginated
from services.companion_content import SUPPORTED_LANGUAGES, validate_trigger_event
from services.im_admin import assert_companion_in_scope, resolve_admin_actor
from services.msg_strategy_meta import known_library_type_keys
from services.strategy_image import normalize_strategy_image_url
from services.strategy_center import (
    attach_ops_admin,
    create_independent_rows,
    create_library_row,
    create_quick_reply_row,
    duplicate_names,
    find_independent_duplicate,
    find_library_duplicate,
    find_quick_reply_duplicate,
    get_independent,
    get_library_app,
    get_quick_reply_app,
    independent_config_maps,
    library_config_maps,
    list_independent,
    list_library_apps,
    list_quick_reply_apps,
    load_companions_map,
    parse_companion_ids_from_body,
    parse_ops_admin_ids,
    parse_trigger_keyword,
    serialize_independent,
    serialize_library_app,
    serialize_quick_reply_app,
    split_or_update_library,
    split_or_update_quick_reply,
)


def _actor(token: str) -> dict:
    return resolve_admin_actor(token)


def _dup_message(db, companion_ids: List[str], lang: str) -> str:
    names = duplicate_names(db, companion_ids)
    shown = "、".join(names) if (lang or "zh").startswith("zh") else ", ".join(names)
    if (lang or "zh").startswith("zh"):
        return f"该策略已存在，不能重复创建：{shown}"
    return f"Strategy already exists, cannot create duplicate: {shown}"


def _require_companions(db, actor: dict, companion_ids: List[str], lang: str) -> dict:
    if not companion_ids:
        raise HTTPException(
            status_code=400,
            detail=_get_error_msg("companion_ids_required", lang),
        )
    cmap = load_companions_map(db, companion_ids)
    missing = [cid for cid in companion_ids if cid not in cmap]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=_get_error_msg("companion_not_found", lang),
        )
    for cid in companion_ids:
        assert_companion_in_scope(actor, cid, lang)
    return cmap


def _require_language(language: str, lang: str) -> str:
    from services.companion_content import canonical_content_language

    lk = canonical_content_language(language or "zh")
    if not lk:
        raise HTTPException(
            status_code=400,
            detail=_get_error_msg("unsupported_language", lang),
        )
    return lk


def _require_content(content: str, lang: str) -> str:
    text = (content or "").strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail=_get_error_msg("content_required", lang),
        )
    return text


def _type_key_from_data(data: dict) -> str:
    return (
        data.get("type_key")
        or data.get("trigger_event")
        or data.get("strategy_type")
        or ""
    ).strip()


def _qr_keywords_from_data(data: dict, *, default: Optional[list] = None) -> Optional[list]:
    if "trigger_keyword" in data:
        return parse_trigger_keyword(data.get("trigger_keyword"))
    if "keywords" in data:
        return parse_trigger_keyword(data.get("keywords"))
    if "trigger_keywords" in data:
        return parse_trigger_keyword(data.get("trigger_keywords"))
    return default


def _enrich_independent(db, row: CompanionStrategyORM, companion: CompanionORM) -> dict:
    item = serialize_independent(row, companion)
    count_map, types_map = independent_config_maps(db, [companion.id])
    item["config_count"] = int(count_map.get(companion.id, 0))
    item["config_types"] = list(types_map.get(companion.id, []))
    attach_ops_admin(db, [item])
    return item


def _enrich_library(db, row, companion) -> dict:
    item = serialize_library_app(row, companion)
    count_map, types_map = library_config_maps(db, [companion.id])
    item["config_count"] = int(count_map.get(companion.id, 0))
    item["config_types"] = list(types_map.get(companion.id, []))
    attach_ops_admin(db, [item])
    return item


def _enrich_qr(db, row, companion) -> dict:
    item = serialize_quick_reply_app(row, companion)
    attach_ops_admin(db, [item])
    return item


def _list_params(
    page: int,
    page_size: int,
    companion: Optional[str],
    companion_id: Optional[str],
    companion_name: Optional[str],
    companion_language: Optional[str],
    language: Optional[str],
    ops_admin_id: Optional[List[str]],
    type_key: Optional[str],
    trigger_event: Optional[str],
    gender: Optional[str],
    tier: Optional[str],
    is_active: Optional[bool],
    keyword: Optional[str],
) -> dict:
    return {
        "page": page,
        "page_size": page_size,
        "companion": companion,
        "companion_id": companion_id,
        "companion_name": companion_name,
        "companion_language": companion_language,
        "language": language,
        "ops_admin_ids": parse_ops_admin_ids(ops_admin_id),
        "type_key": (type_key or trigger_event or "").strip() or None,
        "gender": gender,
        "tier": tier,
        "is_active": is_active,
        "keyword": keyword,
    }


# ---------- 共用筛选 Query 说明见文档；三个 Tab 参数一致 ----------


# ---------- 1. 智能体独立策略 ----------


@router.get(
    "/api/admin/strategy-center/independent",
    summary="策略中心-智能体独立策略应用列表",
)
async def list_independent_apps(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    companion: Optional[str] = Query(None, description="智能体 ID 或名称（模糊）"),
    companion_id: Optional[str] = Query(None, description="智能体 ID"),
    companion_name: Optional[str] = Query(None, description="智能体名称模糊"),
    companion_language: Optional[str] = Query(None, description="智能体默认语言"),
    language: Optional[str] = Query(None, description="用户语言（策略语言）"),
    ops_admin_id: Optional[List[str]] = Query(None, description="责任运营 ID"),
    type_key: Optional[str] = Query(None, description="类型/埋点事件"),
    trigger_event: Optional[str] = Query(None, description="type_key 别名"),
    gender: Optional[str] = Query(None, description="智能体性别"),
    tier: Optional[str] = Query(None, description="智能体类型 s/featured/candidate/ops"),
    is_active: Optional[bool] = Query(None, description="策略生效状态"),
    keyword: Optional[str] = Query(None, description="文案模糊"),
    _token: str = Depends(admin_auth_required),
):
    filters = _list_params(
        page, page_size, companion, companion_id, companion_name,
        companion_language, language, ops_admin_id, type_key, trigger_event,
        gender, tier, is_active, keyword,
    )
    with get_db() as db:
        items, total = list_independent(db, _actor(_token), **filters)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/api/admin/strategy-center/independent/{item_id}",
    summary="策略中心-智能体独立策略应用详情",
)
async def get_independent_app(
    item_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    with get_db() as db:
        found = get_independent(db, item_id)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        assert_companion_in_scope(actor, row.companion_id, lang)
        return ok(data=_enrich_independent(db, row, companion))


@router.post(
    "/api/admin/strategy-center/independent",
    summary="策略中心-新建智能体独立策略（可多选智能体）",
)
async def create_independent_app(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    content = _require_content(data.get("content") or "", lang)
    language = _require_language(data.get("language") or "zh", lang)
    type_key = _type_key_from_data(data)
    sort_order = int(data.get("sort_order") or 0)
    is_active = bool(data.get("is_active", True))
    image_url = normalize_strategy_image_url(data.get("image_url"))
    with get_db() as db:
        companion_ids = parse_companion_ids_from_body(data)
        cmap = _require_companions(db, actor, companion_ids, lang)
        try:
            type_key = validate_trigger_event(type_key, db)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_trigger_event")
        dups = [
            cid
            for cid in companion_ids
            if find_independent_duplicate(
                db,
                companion_id=cid,
                content=content,
                language=language,
                type_key=type_key,
                sort_order=sort_order,
                is_active=is_active,
            )
        ]
        if dups:
            raise HTTPException(status_code=400, detail=_dup_message(db, dups, lang))
        rows = create_independent_rows(
            db,
            companion_ids=companion_ids,
            content=content,
            language=language,
            type_key=type_key,
            sort_order=sort_order,
            is_active=is_active,
            created_by=actor.get("username") or "",
            image_url=image_url,
        )
        db.commit()
        for row in rows:
            db.refresh(row)
        items = [
            _enrich_independent(db, row, cmap[row.companion_id])
            for row in rows
        ]
        return ok(data={"created_count": len(items), "items": items})


@router.put(
    "/api/admin/strategy-center/independent/{item_id}",
    summary="策略中心-编辑智能体独立策略（智能体只读）",
)
async def update_independent_app(
    item_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    with get_db() as db:
        found = get_independent(db, item_id)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        assert_companion_in_scope(actor, row.companion_id, lang)
        if "companion_id" in data or "companion_ids" in data:
            incoming = parse_companion_ids_from_body(data)
            if incoming and (len(incoming) != 1 or incoming[0] != row.companion_id):
                raise HTTPException(
                    status_code=400,
                    detail=_get_error_msg("companion_id_immutable", lang),
                )
        content = row.content or ""
        language = row.language or "zh"
        type_key = (row.trigger_event or row.strategy_type or "").strip()
        sort_order = int(row.sort_order or 0)
        is_active = bool(row.is_active)
        if "content" in data:
            content = _require_content(data.get("content") or "", lang)
            row.content = content
        if "language" in data:
            language = _require_language(data.get("language") or "", lang)
            row.language = language
        if "type_key" in data or "trigger_event" in data or "strategy_type" in data:
            raw = _type_key_from_data(data)
            try:
                type_key = validate_trigger_event(raw, db)
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid_trigger_event")
            row.trigger_event = type_key
            row.strategy_type = type_key
        if "sort_order" in data:
            sort_order = int(data.get("sort_order") or 0)
            row.sort_order = sort_order
        if "is_active" in data:
            is_active = bool(data.get("is_active"))
            row.is_active = is_active
        if "image_url" in data:
            row.image_url = normalize_strategy_image_url(data.get("image_url"))
        if find_independent_duplicate(
            db,
            companion_id=row.companion_id,
            content=content,
            language=language,
            type_key=type_key,
            sort_order=sort_order,
            is_active=is_active,
            exclude_id=row.id,
        ):
            raise HTTPException(
                status_code=400,
                detail=_dup_message(db, [row.companion_id], lang),
            )
        db.commit()
        db.refresh(row)
        return ok(data=_enrich_independent(db, row, companion))


@router.patch(
    "/api/admin/strategy-center/independent/{item_id}/active",
    summary="策略中心-智能体独立策略开关",
)
async def patch_independent_active(
    item_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    with get_db() as db:
        found = get_independent(db, item_id)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        assert_companion_in_scope(actor, row.companion_id, lang)
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        db.refresh(row)
        return ok(data=_enrich_independent(db, row, companion))


@router.delete(
    "/api/admin/strategy-center/independent/{item_id}",
    summary="策略中心-删除智能体独立策略",
)
async def delete_independent_app(
    item_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    with get_db() as db:
        found = get_independent(db, item_id)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, _companion = found
        assert_companion_in_scope(actor, row.companion_id, lang)
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id, "companion_id": row.companion_id})


# ---------- 2. 埋点消息策略库应用 ----------


@router.get(
    "/api/admin/strategy-center/msg-library",
    summary="策略中心-埋点消息策略应用列表（按智能体拆行）",
)
async def list_msg_library_apps(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    companion: Optional[str] = Query(None, description="智能体 ID 或名称（模糊）"),
    companion_id: Optional[str] = Query(None, description="智能体 ID"),
    companion_name: Optional[str] = Query(None, description="智能体名称模糊"),
    companion_language: Optional[str] = Query(None, description="智能体默认语言"),
    language: Optional[str] = Query(None, description="用户语言（策略语言）"),
    ops_admin_id: Optional[List[str]] = Query(None, description="责任运营 ID"),
    type_key: Optional[str] = Query(None, description="类型/埋点事件"),
    trigger_event: Optional[str] = Query(None, description="type_key 别名"),
    gender: Optional[str] = Query(None, description="智能体性别"),
    tier: Optional[str] = Query(None, description="智能体类型 s/featured/candidate/ops"),
    is_active: Optional[bool] = Query(None, description="策略生效状态"),
    keyword: Optional[str] = Query(None, description="文案模糊"),
    _token: str = Depends(admin_auth_required),
):
    filters = _list_params(
        page, page_size, companion, companion_id, companion_name,
        companion_language, language, ops_admin_id, type_key, trigger_event,
        gender, tier, is_active, keyword,
    )
    with get_db() as db:
        items, total = list_library_apps(db, _actor(_token), **filters)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/api/admin/strategy-center/msg-library/{item_id}/companions/{companion_id}",
    summary="策略中心-埋点策略应用详情",
)
async def get_msg_library_app(
    item_id: int,
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_library_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        return ok(data=_enrich_library(db, row, companion))


@router.post(
    "/api/admin/strategy-center/msg-library",
    summary="策略中心-新建埋点策略（可多选智能体，列表拆成 N 行）",
)
async def create_msg_library_app(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    content = _require_content(data.get("content") or "", lang)
    language = _require_language(data.get("language") or "zh", lang)
    type_key = _type_key_from_data(data)
    sort_order = int(data.get("sort_order") or 0)
    is_active = bool(data.get("is_active", True))
    image_url = normalize_strategy_image_url(data.get("image_url"))
    with get_db() as db:
        companion_ids = parse_companion_ids_from_body(data)
        cmap = _require_companions(db, actor, companion_ids, lang)
        if type_key not in known_library_type_keys(db):
            raise HTTPException(status_code=400, detail="invalid_type_key")
        dups = [
            cid
            for cid in companion_ids
            if find_library_duplicate(
                db,
                companion_id=cid,
                content=content,
                language=language,
                type_key=type_key,
                sort_order=sort_order,
                is_active=is_active,
            )
        ]
        if dups:
            raise HTTPException(status_code=400, detail=_dup_message(db, dups, lang))
        row = create_library_row(
            db,
            companion_ids=companion_ids,
            content=content,
            language=language,
            type_key=type_key,
            sort_order=sort_order,
            is_active=is_active,
            created_by=actor.get("username") or "",
            image_url=image_url,
        )
        db.commit()
        db.refresh(row)
        items = [_enrich_library(db, row, cmap[cid]) for cid in companion_ids]
        return ok(data={"created_count": len(items), "items": items, "id": row.id})


@router.put(
    "/api/admin/strategy-center/msg-library/{item_id}/companions/{companion_id}",
    summary="策略中心-编辑埋点策略（只影响当前智能体）",
)
async def update_msg_library_app(
    item_id: int,
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_library_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        content = row.content or ""
        language = row.language or "zh"
        type_key = (row.type_key or "").strip()
        sort_order = int(row.sort_order or 0)
        is_active = bool(row.is_active)
        fields: dict = {}
        if "content" in data:
            content = _require_content(data.get("content") or "", lang)
            fields["content"] = content
        if "language" in data:
            language = _require_language(data.get("language") or "", lang)
            fields["language"] = language
        if "type_key" in data or "trigger_event" in data or "strategy_type" in data:
            type_key = _type_key_from_data(data)
            if type_key not in known_library_type_keys(db):
                raise HTTPException(status_code=400, detail="invalid_type_key")
            fields["type_key"] = type_key
        if "sort_order" in data:
            sort_order = int(data.get("sort_order") or 0)
            fields["sort_order"] = sort_order
        if "is_active" in data:
            is_active = bool(data.get("is_active"))
            fields["is_active"] = is_active
        if "image_url" in data:
            fields["image_url"] = normalize_strategy_image_url(data.get("image_url"))
        if find_library_duplicate(
            db,
            companion_id=cid,
            content=content,
            language=language,
            type_key=type_key,
            sort_order=sort_order,
            is_active=is_active,
            exclude=(row.id, cid),
        ):
            raise HTTPException(
                status_code=400,
                detail=_dup_message(db, [cid], lang),
            )
        updated = split_or_update_library(db, row, cid, **fields)
        db.commit()
        if updated is not None:
            db.refresh(updated)
        companion = load_companions_map(db, [cid]).get(cid) or companion
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        return ok(data=_enrich_library(db, updated, companion))


@router.patch(
    "/api/admin/strategy-center/msg-library/{item_id}/companions/{companion_id}/active",
    summary="策略中心-埋点策略开关（只影响当前智能体）",
)
async def patch_msg_library_active(
    item_id: int,
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_library_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        updated = split_or_update_library(
            db, row, cid, is_active=bool(data.get("is_active", True))
        )
        db.commit()
        if updated is not None:
            db.refresh(updated)
        companion = load_companions_map(db, [cid]).get(cid) or companion
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        return ok(data=_enrich_library(db, updated, companion))


@router.delete(
    "/api/admin/strategy-center/msg-library/{item_id}/companions/{companion_id}",
    summary="策略中心-删除埋点策略应用（只解绑当前智能体）",
)
async def delete_msg_library_app(
    item_id: int,
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_library_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, _companion = found
        split_or_update_library(db, row, cid, delete=True)
        db.commit()
        return ok(data={"deleted_id": item_id, "companion_id": cid})


# ---------- 3. 快速回复应用 ----------


@router.get(
    "/api/admin/strategy-center/quick-reply",
    summary="策略中心-快速回复应用列表（按智能体拆行）",
)
async def list_quick_reply_apps_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    companion: Optional[str] = Query(None, description="智能体 ID 或名称（模糊）"),
    companion_id: Optional[str] = Query(None, description="智能体 ID"),
    companion_name: Optional[str] = Query(None, description="智能体名称模糊"),
    companion_language: Optional[str] = Query(None, description="智能体默认语言"),
    language: Optional[str] = Query(None, description="用户语言（策略语言）"),
    ops_admin_id: Optional[List[str]] = Query(None, description="责任运营 ID"),
    type_key: Optional[str] = Query(None, description="快速回复无类型，传入忽略"),
    trigger_event: Optional[str] = Query(None, description="快速回复无类型，传入忽略"),
    gender: Optional[str] = Query(None, description="智能体性别"),
    tier: Optional[str] = Query(None, description="智能体类型 s/featured/candidate/ops"),
    is_active: Optional[bool] = Query(None, description="策略生效状态"),
    keyword: Optional[str] = Query(None, description="文案或触发词模糊"),
    _token: str = Depends(admin_auth_required),
):
    _ = (type_key, trigger_event)
    with get_db() as db:
        items, total = list_quick_reply_apps(
            db,
            _actor(_token),
            page=page,
            page_size=page_size,
            companion=companion,
            companion_id=companion_id,
            companion_name=companion_name,
            companion_language=companion_language,
            language=language,
            ops_admin_ids=parse_ops_admin_ids(ops_admin_id),
            gender=gender,
            tier=tier,
            is_active=is_active,
            keyword=keyword,
        )
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/api/admin/strategy-center/quick-reply/{item_id}/companions/{companion_id}",
    summary="策略中心-快速回复应用详情",
)
async def get_quick_reply_app_api(
    item_id: int,
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_quick_reply_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        return ok(data=_enrich_qr(db, row, companion))


@router.post(
    "/api/admin/strategy-center/quick-reply",
    summary="策略中心-新建快速回复（可多选智能体，列表拆成 N 行）",
)
async def create_quick_reply_app(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    content = _require_content(data.get("content") or "", lang)
    language = _require_language(data.get("language") or "zh", lang)
    keywords = _qr_keywords_from_data(data, default=[]) or []
    sort_order = int(data.get("sort_order") or 0)
    is_active = bool(data.get("is_active", True))
    with get_db() as db:
        companion_ids = parse_companion_ids_from_body(data)
        cmap = _require_companions(db, actor, companion_ids, lang)
        dups = [
            cid
            for cid in companion_ids
            if find_quick_reply_duplicate(
                db,
                companion_id=cid,
                content=content,
                language=language,
                keywords=keywords,
                sort_order=sort_order,
            )
        ]
        if dups:
            raise HTTPException(status_code=400, detail=_dup_message(db, dups, lang))
        row = create_quick_reply_row(
            db,
            companion_ids=companion_ids,
            content=content,
            language=language,
            keywords=keywords,
            sort_order=sort_order,
            is_active=is_active,
            created_by=actor.get("username") or "",
        )
        db.commit()
        db.refresh(row)
        items = [_enrich_qr(db, row, cmap[cid]) for cid in companion_ids]
        return ok(data={"created_count": len(items), "items": items, "id": row.id})


@router.put(
    "/api/admin/strategy-center/quick-reply/{item_id}/companions/{companion_id}",
    summary="策略中心-编辑快速回复（只影响当前智能体）",
)
async def update_quick_reply_app(
    item_id: int,
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_quick_reply_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        content = row.content or ""
        language = row.language or "zh"
        have = (
            [str(k).strip() for k in (row.keywords or []) if str(k).strip()]
            if isinstance(row.keywords, list)
            else []
        )
        keywords = have
        sort_order = int(row.sort_order or 0)
        fields: dict = {}
        if "content" in data:
            content = _require_content(data.get("content") or "", lang)
            fields["content"] = content
        if "language" in data:
            language = _require_language(data.get("language") or "", lang)
            fields["language"] = language
        parsed = _qr_keywords_from_data(data)
        if parsed is not None:
            keywords = parsed
            fields["keywords"] = keywords
        if "sort_order" in data:
            sort_order = int(data.get("sort_order") or 0)
            fields["sort_order"] = sort_order
        if "is_active" in data:
            fields["is_active"] = bool(data.get("is_active"))
        if find_quick_reply_duplicate(
            db,
            companion_id=cid,
            content=content,
            language=language,
            keywords=keywords,
            sort_order=sort_order,
            exclude=(row.id, cid),
        ):
            raise HTTPException(
                status_code=400,
                detail=_dup_message(db, [cid], lang),
            )
        updated = split_or_update_quick_reply(db, row, cid, **fields)
        db.commit()
        if updated is not None:
            db.refresh(updated)
        companion = load_companions_map(db, [cid]).get(cid) or companion
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        return ok(data=_enrich_qr(db, updated, companion))


@router.patch(
    "/api/admin/strategy-center/quick-reply/{item_id}/companions/{companion_id}/active",
    summary="策略中心-快速回复开关（只影响当前智能体）",
)
async def patch_quick_reply_active(
    item_id: int,
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_quick_reply_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, companion = found
        updated = split_or_update_quick_reply(
            db, row, cid, is_active=bool(data.get("is_active", True))
        )
        db.commit()
        if updated is not None:
            db.refresh(updated)
        companion = load_companions_map(db, [cid]).get(cid) or companion
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        return ok(data=_enrich_qr(db, updated, companion))


@router.delete(
    "/api/admin/strategy-center/quick-reply/{item_id}/companions/{companion_id}",
    summary="策略中心-删除快速回复应用（只解绑当前智能体）",
)
async def delete_quick_reply_app(
    item_id: int,
    companion_id: str,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    actor = _actor(_token)
    cid = (companion_id or "").strip()[:8]
    assert_companion_in_scope(actor, cid, lang)
    with get_db() as db:
        found = get_quick_reply_app(db, item_id, cid)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=_get_error_msg("application_not_found", lang),
            )
        row, _companion = found
        split_or_update_quick_reply(db, row, cid, delete=True)
        db.commit()
        return ok(data={"deleted_id": item_id, "companion_id": cid})
