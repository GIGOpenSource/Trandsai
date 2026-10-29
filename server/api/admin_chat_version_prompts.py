"""管理端：客户端版本号聊天兜底提示词。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, or_

from api.admin_common import admin_auth_required, router
from core.database import ChatVersionPromptORM, ChatVersionPromptUserORM, UserORM, get_db
from core.response import ok, paginated
from services.version_prompt import (
    DEFAULT_VERSION,
    cohort_users_query,
    ensure_default_version_prompt,
    invalidate_version_prompt_cache,
    normalize_app_version,
    parse_optional_datetime,
    row_to_dict,
)


def _apply_fields(row: ChatVersionPromptORM, data: dict) -> None:
    if "prompt" in data:
        row.prompt = str(data.get("prompt") or "")
    if "is_enabled" in data:
        row.is_enabled = bool(data.get("is_enabled"))
    if "remark" in data:
        row.remark = str(data.get("remark") or "")[:200]
    if "sort_order" in data:
        try:
            row.sort_order = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            row.sort_order = 0
    if "version" in data:
        ver = normalize_app_version(data.get("version"))
        if not ver:
            raise HTTPException(status_code=400, detail="version_required")
        row.version = ver
    for key in ("apply_registered_start_at", "apply_registered_end_at"):
        if key not in data:
            continue
        try:
            setattr(row, key, parse_optional_datetime(data.get(key)))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid_{key}")
    row.updated_at = datetime.now(timezone.utc)


def _iso(dt) -> str:
    if not dt:
        return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _user_brief(user: UserORM, extra: Optional[dict] = None) -> dict:
    data = {
        "id": user.id,
        "username": user.username or "",
        "nickname": user.nickname or "",
        "email": user.email or "",
        "app_version": getattr(user, "app_version", None) or "",
        "created_at": _iso(user.created_at),
        "account_status": user.account_status or "",
        "platform": user.platform or "",
        "language": user.language or "",
        "app_key": getattr(user, "app_key", None) or "",
    }
    if extra:
        data.update(extra)
    return data


def _keyword_filter(q, keyword: Optional[str]):
    text = (keyword or "").strip()
    if not text:
        return q
    like = f"%{text}%"
    clauses = [
        UserORM.username.ilike(like),
        UserORM.nickname.ilike(like),
        UserORM.email.ilike(like),
    ]
    if text.isdigit():
        clauses.append(UserORM.id == int(text))
    return q.filter(or_(*clauses))


def _find_prompt(db, *, item_id: Optional[int] = None, version: Optional[str] = None):
    if item_id:
        row = db.query(ChatVersionPromptORM).filter(ChatVersionPromptORM.id == item_id).first()
    else:
        ver = normalize_app_version(version)
        if not ver:
            raise HTTPException(status_code=400, detail="version_required")
        row = (
            db.query(ChatVersionPromptORM)
            .filter(ChatVersionPromptORM.version == ver)
            .first()
        )
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return row


def _removed_ids_subq(db, prompt_id: int):
    return (
        db.query(ChatVersionPromptUserORM.user_id)
        .filter(ChatVersionPromptUserORM.prompt_id == prompt_id)
    )


def _counts(db, row: ChatVersionPromptORM) -> tuple[int, int]:
    removed = (
        db.query(func.count(ChatVersionPromptUserORM.id))
        .filter(ChatVersionPromptUserORM.prompt_id == row.id)
        .scalar()
        or 0
    )
    applied = (
        cohort_users_query(db, row)
        .filter(~UserORM.id.in_(_removed_ids_subq(db, row.id)))
        .count()
    )
    return int(applied), int(removed)


def _serialize_prompt(db, row: ChatVersionPromptORM) -> dict:
    applied, removed = _counts(db, row)
    return row_to_dict(row, applied_count=applied, removed_count=removed)


@router.get("/api/admin/chat-version-prompts", summary="版本号聊天提示词列表")
async def list_version_prompts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    is_enabled: Optional[bool] = Query(None),
    keyword: Optional[str] = Query(None),
    _token: str = Depends(admin_auth_required),
):
    ensure_default_version_prompt()
    with get_db() as db:
        q = db.query(ChatVersionPromptORM)
        if is_enabled is not None:
            q = q.filter(ChatVersionPromptORM.is_enabled == is_enabled)
        if keyword and keyword.strip():
            like = f"%{keyword.strip()}%"
            from sqlalchemy import or_

            q = q.filter(
                or_(
                    ChatVersionPromptORM.version.ilike(like),
                    ChatVersionPromptORM.prompt.ilike(like),
                    ChatVersionPromptORM.remark.ilike(like),
                )
            )
        total = q.count()
        rows = (
            q.order_by(
                ChatVersionPromptORM.sort_order.asc(),
                ChatVersionPromptORM.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return paginated(
            items=[_serialize_prompt(db, r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get(
    "/api/admin/chat-version-prompts/users",
    summary="按版本号筛选提示词应用用户范围",
)
async def list_version_prompt_users(
    version: str = Query(..., description="版本号，如 1.1.1"),
    scope: str = Query("applied", description="applied=应用列表，removed=已移除列表"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = Query(None, description="用户 id / 用户名 / 昵称 / 邮箱"),
    _token: str = Depends(admin_auth_required),
):
    scope_key = (scope or "applied").strip().lower()
    if scope_key not in ("applied", "removed"):
        raise HTTPException(status_code=400, detail="invalid_scope")
    with get_db() as db:
        row = _find_prompt(db, version=version)
        return _list_scope_users(db, row, scope_key, page, page_size, keyword)


@router.get(
    "/api/admin/chat-version-prompts/{item_id}/users",
    summary="某条版本提示词的应用/已移除用户列表",
)
async def list_version_prompt_users_by_id(
    item_id: int,
    scope: str = Query("applied", description="applied=应用列表，removed=已移除列表"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = Query(None),
    _token: str = Depends(admin_auth_required),
):
    scope_key = (scope or "applied").strip().lower()
    if scope_key not in ("applied", "removed"):
        raise HTTPException(status_code=400, detail="invalid_scope")
    with get_db() as db:
        row = _find_prompt(db, item_id=item_id)
        return _list_scope_users(db, row, scope_key, page, page_size, keyword)


def _list_scope_users(db, row, scope_key: str, page: int, page_size: int, keyword: Optional[str]):
    extra_base = {
        "prompt_id": row.id,
        "version": row.version or "",
        "scope": scope_key,
    }
    if scope_key == "removed":
        q = (
            db.query(UserORM, ChatVersionPromptUserORM)
            .join(
                ChatVersionPromptUserORM,
                ChatVersionPromptUserORM.user_id == UserORM.id,
            )
            .filter(ChatVersionPromptUserORM.prompt_id == row.id)
        )
        q = _keyword_filter(q, keyword)
        total = q.count()
        pairs = (
            q.order_by(ChatVersionPromptUserORM.created_at.desc(), UserORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [
            _user_brief(
                user,
                {
                    **extra_base,
                    "removed_at": _iso(excl.created_at),
                    "removed_by": excl.removed_by or "",
                    "in_scope": False,
                },
            )
            for user, excl in pairs
        ]
    else:
        q = cohort_users_query(db, row).filter(~UserORM.id.in_(_removed_ids_subq(db, row.id)))
        q = _keyword_filter(q, keyword)
        total = q.count()
        users = (
            q.order_by(UserORM.created_at.desc(), UserORM.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [_user_brief(u, {**extra_base, "in_scope": True}) for u in users]
    return paginated(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/api/admin/chat-version-prompts/{item_id}/users/{user_id}/remove",
    summary="将该用户从本版本提示词合规名单永久移除",
)
async def remove_version_prompt_user(
    item_id: int,
    user_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = _find_prompt(db, item_id=item_id)
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="user_not_found")
        exists = (
            db.query(ChatVersionPromptUserORM.id)
            .filter(
                ChatVersionPromptUserORM.prompt_id == row.id,
                ChatVersionPromptUserORM.user_id == user_id,
            )
            .first()
        )
        if exists:
            raise HTTPException(status_code=400, detail="already_removed")
        db.add(
            ChatVersionPromptUserORM(
                prompt_id=row.id,
                user_id=user_id,
                version=row.version or "",
                removed_by="",
            )
        )
        db.commit()
        invalidate_version_prompt_cache()
        return ok(
            data={
                "prompt_id": row.id,
                "version": row.version or "",
                "user_id": user_id,
                "scope": "removed",
            }
        )


@router.post(
    "/api/admin/chat-version-prompts/{item_id}/users/{user_id}/restore",
    summary="将用户重新纳入本版本提示词应用范围",
)
async def restore_version_prompt_user(
    item_id: int,
    user_id: int,
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        row = _find_prompt(db, item_id=item_id)
        rec = (
            db.query(ChatVersionPromptUserORM)
            .filter(
                ChatVersionPromptUserORM.prompt_id == row.id,
                ChatVersionPromptUserORM.user_id == user_id,
            )
            .first()
        )
        if not rec:
            raise HTTPException(status_code=404, detail="not_removed")
        db.delete(rec)
        db.commit()
        invalidate_version_prompt_cache()
        return ok(
            data={
                "prompt_id": row.id,
                "version": row.version or "",
                "user_id": user_id,
                "scope": "applied",
            }
        )


@router.get("/api/admin/chat-version-prompts/{item_id}", summary="版本号聊天提示词详情")
async def get_version_prompt(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(ChatVersionPromptORM).filter(ChatVersionPromptORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        return ok(data=_serialize_prompt(db, row))


@router.post("/api/admin/chat-version-prompts", summary="新建版本号聊天提示词")
async def create_version_prompt(data: dict, _token: str = Depends(admin_auth_required)):
    ver = normalize_app_version(data.get("version"))
    if not ver:
        raise HTTPException(status_code=400, detail="version_required")
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt_required")
    with get_db() as db:
        exists = (
            db.query(ChatVersionPromptORM.id)
            .filter(ChatVersionPromptORM.version == ver)
            .first()
        )
        if exists:
            raise HTTPException(status_code=400, detail="version_exists")
        try:
            start_at = parse_optional_datetime(data.get("apply_registered_start_at"))
            end_at = parse_optional_datetime(data.get("apply_registered_end_at"))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_datetime")
        row = ChatVersionPromptORM(
            version=ver,
            prompt=prompt,
            is_enabled=bool(data.get("is_enabled")),
            remark=str(data.get("remark") or "")[:200],
            sort_order=int(data.get("sort_order") or 0),
            apply_registered_start_at=start_at,
            apply_registered_end_at=end_at,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        invalidate_version_prompt_cache()
        return ok(data=_serialize_prompt(db, row))


@router.put("/api/admin/chat-version-prompts/{item_id}", summary="更新版本号聊天提示词")
async def update_version_prompt(
    item_id: int, data: dict, _token: str = Depends(admin_auth_required)
):
    with get_db() as db:
        row = db.query(ChatVersionPromptORM).filter(ChatVersionPromptORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if "version" in data:
            ver = normalize_app_version(data.get("version"))
            if not ver:
                raise HTTPException(status_code=400, detail="version_required")
            dup = (
                db.query(ChatVersionPromptORM.id)
                .filter(
                    ChatVersionPromptORM.version == ver,
                    ChatVersionPromptORM.id != item_id,
                )
                .first()
            )
            if dup:
                raise HTTPException(status_code=400, detail="version_exists")
        _apply_fields(row, data)
        if not (row.prompt or "").strip():
            raise HTTPException(status_code=400, detail="prompt_required")
        db.commit()
        db.refresh(row)
        invalidate_version_prompt_cache()
        return ok(data=_serialize_prompt(db, row))


@router.patch(
    "/api/admin/chat-version-prompts/{item_id}/enabled",
    summary="启用/关闭版本号聊天提示词",
)
async def toggle_version_prompt(
    item_id: int, data: dict, _token: str = Depends(admin_auth_required)
):
    with get_db() as db:
        row = db.query(ChatVersionPromptORM).filter(ChatVersionPromptORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if "is_enabled" not in data:
            raise HTTPException(status_code=400, detail="is_enabled_required")
        row.is_enabled = bool(data.get("is_enabled"))
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        invalidate_version_prompt_cache()
        return ok(data=_serialize_prompt(db, row))


@router.delete("/api/admin/chat-version-prompts/{item_id}", summary="删除版本号聊天提示词")
async def delete_version_prompt(item_id: int, _token: str = Depends(admin_auth_required)):
    with get_db() as db:
        row = db.query(ChatVersionPromptORM).filter(ChatVersionPromptORM.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        if (row.version or "") == DEFAULT_VERSION:
            raise HTTPException(status_code=400, detail="default_version_protected")
        db.query(ChatVersionPromptUserORM).filter(
            ChatVersionPromptUserORM.prompt_id == item_id
        ).delete(synchronize_session=False)
        db.delete(row)
        db.commit()
        invalidate_version_prompt_cache()
        return ok(data={"id": item_id})
