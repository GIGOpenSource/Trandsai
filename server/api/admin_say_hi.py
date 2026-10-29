"""管理后台：Say Hi 打招呼文案 CRUD。"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query

from api.admin_common import admin_auth_required, router
from core.database import SayHiCopyORM, get_db
from core.response import ok, paginated
from services.companion_content import SUPPORTED_LANGUAGES
from services.say_hi_copy import (
    copy_to_dict,
    create_say_hi_copy,
    filter_options,
    list_say_hi_copies,
    normalize_say_hi_language,
    update_say_hi_copy,
)


@router.get("/api/admin/say-hi/options", summary="Say Hi 文案筛选项")
async def say_hi_options(_token: str = Depends(admin_auth_required)):
    return ok(data=filter_options())


@router.get("/api/admin/say-hi/copies", summary="Say Hi 文案列表")
async def list_copies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    language: Optional[str] = Query(None, description="语种短码，如 zh/en"),
    is_active: Optional[bool] = Query(None, description="是否生效"),
    keyword: Optional[str] = Query(None, description="文案模糊搜索"),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        items, total = list_say_hi_copies(
            db,
            page=page,
            page_size=page_size,
            language=language,
            is_active=is_active,
            keyword=keyword,
        )
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/say-hi/copies/{item_id}", summary="Say Hi 文案详情")
async def get_copy(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(SayHiCopyORM).filter(SayHiCopyORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        return ok(data=copy_to_dict(row))


@router.post("/api/admin/say-hi/copies", summary="新建 Say Hi 文案")
async def create_copy(data: dict, _token: str = Depends(admin_auth_required)):
    content = (data.get("content") or "").strip()
    language = normalize_say_hi_language(data.get("language") or "en")
    if not content:
        raise HTTPException(status_code=400, detail="content_required")
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="unsupported_language")

    with get_db() as db:
        row = create_say_hi_copy(
            db,
            content=content,
            language=language,
            sort_order=int(data.get("sort_order") or 0),
            is_active=bool(data.get("is_active", True)),
            remark=str(data.get("remark") or ""),
        )
        db.commit()
        db.refresh(row)
        return ok(data=copy_to_dict(row))


@router.put("/api/admin/say-hi/copies/{item_id}", summary="更新 Say Hi 文案")
async def update_copy(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(SayHiCopyORM).filter(SayHiCopyORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if "content" in data:
            content = (data.get("content") or "").strip()
            if not content:
                raise HTTPException(status_code=400, detail="content_required")
        if "language" in data and data.get("language") is not None:
            language = normalize_say_hi_language(str(data.get("language") or ""))
            if language not in SUPPORTED_LANGUAGES:
                raise HTTPException(status_code=400, detail="unsupported_language")
        update_say_hi_copy(row, data)
        db.commit()
        db.refresh(row)
        return ok(data=copy_to_dict(row))


@router.patch("/api/admin/say-hi/copies/{item_id}/active", summary="开关 Say Hi 文案")
async def patch_copy_active(item_id: int, data: dict, _token: str = Depends(admin_auth_required)):
    if "is_active" not in data:
        raise HTTPException(status_code=400, detail="is_active_required")
    with get_db() as db:
        row = db.query(SayHiCopyORM).filter(SayHiCopyORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        row.is_active = bool(data.get("is_active"))
        db.commit()
        db.refresh(row)
        return ok(data=copy_to_dict(row))


@router.delete("/api/admin/say-hi/copies/{item_id}", summary="删除 Say Hi 文案")
async def delete_copy(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(SayHiCopyORM).filter(SayHiCopyORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        db.delete(row)
        db.commit()
        return ok(data={"id": item_id, "deleted": True})
