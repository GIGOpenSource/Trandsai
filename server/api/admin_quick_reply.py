"""【快速回复消息策略】后台 API：策略库 / 策略配置。

用户发来聊天消息后按关键词/兜底立刻回一条并跳过 AI。
与【埋点消息策略】msg-strategy、【单体消息策略】companion_strategies 无关。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query

from api.admin_common import admin_auth_required, router
from core.database import CompanionORM, QuickReplyConfigORM, QuickReplyLibraryORM, get_db
from core.response import ok, paginated
from services.companion_content import SUPPORTED_LANGUAGES
from services.quick_reply import (
    apply_config_fields,
    apply_library_fields,
    config_to_dict,
    enrich_config_rows,
    enrich_library_rows,
    ensure_quick_reply_configs,
    library_to_dict,
)
from services.msg_strategy_crud import load_companion_id_name_map


# ---------- 快回消息策略库（对齐埋点消息策略库 CRUD） ----------


@router.get("/api/admin/quick-reply/library", summary="快回消息策略库列表")
async def list_library(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    language: Optional[str] = Query(None, description="用户语言，如 zh/en"),
    is_active: Optional[bool] = Query(None, description="是否生效"),
    companion_id: Optional[str] = Query(None, description="作用智能体筛选"),
    keyword: Optional[str] = Query(None, description="文案或触发词模糊"),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        q = db.query(QuickReplyLibraryORM)
        if language:
            q = q.filter(QuickReplyLibraryORM.language == language.strip())
        if is_active is not None:
            q = q.filter(QuickReplyLibraryORM.is_active == is_active)
        if companion_id and companion_id.strip():
            q = q.filter(
                QuickReplyLibraryORM.companion_ids.contains([companion_id.strip()[:8]])
            )
        if keyword and keyword.strip():
            like = f"%{keyword.strip()}%"
            # 文案模糊；触发词也用 cast 文本模糊（JSONB）
            from sqlalchemy import cast, String, or_

            q = q.filter(
                or_(
                    QuickReplyLibraryORM.content.ilike(like),
                    cast(QuickReplyLibraryORM.keywords, String).ilike(like),
                )
            )
        total = q.count()
        rows = (
            q.order_by(
                QuickReplyLibraryORM.sort_order.asc(),
                QuickReplyLibraryORM.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=enrich_library_rows(db, rows),
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get("/api/admin/quick-reply/library/{item_id}", summary="快回消息策略库详情")
async def get_library(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        name_map = load_companion_id_name_map(db, row.companion_ids or [])
        return ok(data=library_to_dict(row, name_map=name_map))


@router.post("/api/admin/quick-reply/library", summary="新建快回消息策略")
async def create_library(data: dict, _token: str = Depends(admin_auth_required)):
    content = (data.get("content") or "").strip()
    language = (data.get("language") or "zh").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content_required")
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="unsupported_language")

    with get_db() as db:
        row = QuickReplyLibraryORM(content=content, language=language, keywords=[])
        try:
            apply_library_fields(row, data)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_keywords")
        row.content = content
        row.language = language
        db.add(row)
        ensure_quick_reply_configs(db, row.companion_ids or [])
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, row.companion_ids or [])
        return ok(data=library_to_dict(row, name_map=name_map))


@router.put("/api/admin/quick-reply/library/{item_id}", summary="更新快回消息策略")
async def update_library(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if "language" in data:
            language = (data.get("language") or "").strip()
            if language not in SUPPORTED_LANGUAGES:
                raise HTTPException(status_code=400, detail="unsupported_language")
        if "content" in data and not (data.get("content") or "").strip():
            raise HTTPException(status_code=400, detail="content_required")
        try:
            apply_library_fields(row, data)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_keywords")
        ensure_quick_reply_configs(db, row.companion_ids or [])
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, row.companion_ids or [])
        return ok(data=library_to_dict(row, name_map=name_map))


@router.patch("/api/admin/quick-reply/library/{item_id}/active", summary="快回消息策略开关")
async def patch_library_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": item_id, "is_active": bool(row.is_active)})


@router.delete("/api/admin/quick-reply/library/{item_id}", summary="删除快回消息策略")
async def delete_library(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyLibraryORM).filter(QuickReplyLibraryORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id})


# ---------- 快速回复策略配置（按智能体总开关，库之外另配） ----------


@router.get("/api/admin/quick-reply/configs", summary="快速回复策略配置列表")
async def list_configs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    companion_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = None,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        q = db.query(QuickReplyConfigORM)
        if companion_id and companion_id.strip():
            q = q.filter(QuickReplyConfigORM.companion_id == companion_id.strip()[:8])
        if is_active is not None:
            q = q.filter(QuickReplyConfigORM.is_active == is_active)
        if keyword and keyword.strip():
            q = q.filter(QuickReplyConfigORM.name.ilike(f"%{keyword.strip()}%"))
        total = q.count()
        rows = (
            q.order_by(
                QuickReplyConfigORM.priority.desc(),
                QuickReplyConfigORM.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=enrich_config_rows(db, rows),
            total=total,
            page=page,
            page_size=page_size,
        )


@router.post("/api/admin/quick-reply/configs", summary="新建快速回复配置")
async def create_config(data: dict, _token: str = Depends(admin_auth_required)):
    companion_id = (data.get("companion_id") or "").strip()[:8]
    if not companion_id:
        raise HTTPException(status_code=400, detail="companion_id_required")
    with get_db() as db:
        if not db.query(CompanionORM.id).filter(CompanionORM.id == companion_id).first():
            raise HTTPException(status_code=404, detail="companion_not_found")
        exists = (
            db.query(QuickReplyConfigORM)
            .filter(QuickReplyConfigORM.companion_id == companion_id)
            .first()
        )
        if exists:
            raise HTTPException(status_code=400, detail="config_already_exists")
        row = QuickReplyConfigORM(companion_id=companion_id)
        apply_config_fields(row, data)
        if "is_active" not in data:
            row.is_active = True
        db.add(row)
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, [companion_id])
        return ok(
            data=config_to_dict(row, companion_name=name_map.get(companion_id, ""))
        )


@router.put("/api/admin/quick-reply/configs/{item_id}", summary="更新快速回复配置")
async def update_config(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyConfigORM).filter(QuickReplyConfigORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        # companion_id 唯一，创建后不允许改绑；需要换智能体请删建
        if "companion_id" in data:
            new_cid = (data.get("companion_id") or "").strip()[:8]
            if new_cid and new_cid != row.companion_id:
                raise HTTPException(status_code=400, detail="companion_id_immutable")
        apply_config_fields(row, data)
        db.commit()
        db.refresh(row)
        name_map = load_companion_id_name_map(db, [row.companion_id])
        return ok(
            data=config_to_dict(
                row, companion_name=name_map.get(row.companion_id or "", "")
            )
        )


@router.patch("/api/admin/quick-reply/configs/{item_id}/active", summary="快速回复配置开关")
async def patch_config_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyConfigORM).filter(QuickReplyConfigORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active", True))
        db.commit()
        return ok(data={"id": item_id, "is_active": bool(row.is_active)})


@router.delete("/api/admin/quick-reply/configs/{item_id}", summary="删除快速回复配置")
async def delete_config(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(QuickReplyConfigORM).filter(QuickReplyConfigORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"deleted_id": item_id})
