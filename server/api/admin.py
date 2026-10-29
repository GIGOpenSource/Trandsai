from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from fastapi import Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from api.admin_common import (
    AdminLoginRequest,
    AdminLoginResponse,
    ErrorResponse,
    _get_error_msg,
    admin_auth_required,
    get_admin_lang,
    logger,
    router,
)
from api.auth import create_token
from api.auth import AdminLoginError
from api.auth import _hash_password as _hash
from api.auth import _verify_password as _verify_pwd
from sqlalchemy import cast, func, or_, String
from core.auth import get_admin_token_payload, revoke_admin_tokens_for_user
from core.database import (
    AdminMenuORM,
    AdminRoleMenuORM,
    AdminRoleORM,
    ButtonClickORM,
    CompanionAgentConfigORM,
    CompanionORM,
    CompanionStateORM,
    FeedbackMessageORM,
    FeedbackThreadORM,
    PageViewORM,
    SystemNotificationORM,
    UserCompanionStateORM,
    UserCreditORM,
    UserORM,
    get_db,
    serialize_datetime,
)
from core.gender import canonical_preferred_gender, canonical_user_gender
from core.i18n import _
from core.response import fail, ok, paginated
from core.state import get_companion_manager
from services.async_tasks import start_avatar_generation
from services.companion_manager import _normalize_gender
from services.knowledge_base import knowledge_base
from services.memory import get_embedding_status
from services.msg_strategy_crud import companions_payload, load_companion_id_name_map
from services.recommend_tags import canonical_orientation
from services.admin_rbac import seed_admin_rbac
from services.companion_excel import (
    CompanionImportError,
    apply_companions_import,
    build_companions_template_xlsx,
    build_companions_xlsx,
    orm_row_to_export_dict,
    parse_companions_xlsx,
    summarize_import_result,
)


def _ensure_admin_rbac(db) -> None:
    seed_admin_rbac(db)
    db.commit()

@router.post("/api/admin/login",
             summary="管理员登录",
             tags=["管理后台"],
             description="使用管理员密码登录，返回认证 Token（24小时有效）",
             response_model=AdminLoginResponse,
             responses={
                 200: {"description": "登录成功", "model": AdminLoginResponse},
                 401: {"description": "密码错误", "model": ErrorResponse},
                 500: {"description": "服务器配置错误", "model": ErrorResponse},
             })
async def admin_login(body: AdminLoginRequest, lang: str = Depends(get_admin_lang)):
    """管理员登录接口

    - **username**: 管理员用户名（默认 gigadmin）
    - **password**: 管理员密码
    """
    try:
        with get_db() as db:
            _ensure_admin_rbac(db)
    except Exception:
        logger.exception("admin rbac seed failed")
    try:
        token = create_token(body.password, body.username)
    except AdminLoginError as e:
        raise HTTPException(
            status_code=401,
            detail=_get_error_msg(e.code or "account_inactive", lang),
        )
    except ValueError:
        raise HTTPException(status_code=500, detail=_get_error_msg("server_error", lang))
    if not token:
        raise HTTPException(status_code=401, detail=_get_error_msg("wrong_password", lang))
    return ok(data={"token": token})


def _valid_role_key(raw: str) -> str:
    key = (raw or "").strip().lower()
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", key):
        raise HTTPException(status_code=400, detail="invalid_role_key")
    return key


def _parse_bool_flag(raw) -> Optional[bool]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "active"):
        return True
    if s in ("0", "false", "no", "n", "off", "inactive", "disabled", "banned"):
        return False
    return None


def _admin_user_item(user, *, name_map: Optional[dict] = None) -> dict:
    st = (getattr(user, "account_status", None) or "active").strip() or "active"
    companion_ids = _parse_companion_ids(getattr(user, "admin_companion_ids", None))
    nm = name_map if name_map is not None else {}
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname or "",
        "role_key": (user.role or "user"),
        "account_status": st,
        "is_active": st == "active",
        "email": (getattr(user, "email", None) or "") or "",
        "companion_ids": companion_ids,
        "companions": companions_payload(companion_ids, nm),
        "remark": (getattr(user, "remark", None) or "") or "",
        "created_at": serialize_datetime(user.created_at),
    }


def _enrich_admin_user_items(db, users) -> list:
    """列表一遍 SQL 补 companions[].name。单条失败不影响整表。"""
    row_list = []
    for u in users:
        # 兼容偶发 Row/tuple 包一层
        if not hasattr(u, "id") and isinstance(u, (tuple, list)) and u:
            u = u[0]
        if hasattr(u, "id"):
            row_list.append(u)
    all_ids: list = []
    seen = set()
    for u in row_list:
        for cid in _parse_companion_ids(getattr(u, "admin_companion_ids", None)):
            if cid not in seen:
                seen.add(cid)
                all_ids.append(cid)
    try:
        name_map = load_companion_id_name_map(db, all_ids)
    except Exception:
        logger.exception("load_companion_id_name_map failed; fallback empty names")
        name_map = {}
    return [_admin_user_item(u, name_map=name_map) for u in row_list]


def _parse_companion_ids(raw) -> list:
    from services.im_admin import _normalize_admin_companion_ids

    return _normalize_admin_companion_ids(raw)


def _apply_admin_user_profile(user, data: dict, db=None) -> None:
    """落库前端表单扩展字段：email / remark / companion_ids。"""
    if "email" in data:
        user.email = (str(data.get("email") or "").strip())[:200]
    if "remark" in data:
        # Text 字段，截断防异常超大
        user.remark = (str(data.get("remark") or "").strip())[:2000]
    if "companion_ids" in data or "companions" in data:
        raw = data.get("companion_ids")
        if raw is None:
            raw = data.get("companions")
        new_ids = _parse_companion_ids(raw)
        if db is not None and getattr(user, "id", None):
            from services.im_admin import sync_admin_companion_ids_exclusive

            sync_admin_companion_ids_exclusive(db, int(user.id), new_ids)
        else:
            user.admin_companion_ids = new_ids


def _apply_admin_user_active(user, data: dict) -> None:
    """前端用 is_active；落库用 account_status（active/banned）。"""
    from core.auth import (
        apply_account_status_flags,
        normalize_account_status,
        revoke_admin_tokens_for_user,
    )

    if "is_active" in data:
        flag = _parse_bool_flag(data.get("is_active"))
        if flag is None:
            raise HTTPException(status_code=400, detail="invalid_is_active")
        st = "active" if flag else "banned"
    elif "account_status" in data or "status" in data:
        raw = str(data.get("account_status") or data.get("status") or "").strip()
        mapping = {
            "正常": "active",
            "封禁": "banned",
            "禁用": "banned",
            "暂停": "banned",
            "未生效": "banned",
            "inactive": "banned",
            "disabled": "banned",
            "suspended": "banned",
        }
        st = normalize_account_status(mapping.get(raw, raw))
        if st not in ("active", "banned"):
            # 管理员账号状态仅启用/禁用；注销请走删除
            raise HTTPException(status_code=400, detail="invalid_account_status")
    else:
        return

    if (user.username or "").strip().lower() == "gigadmin" and st != "active":
        raise HTTPException(status_code=400, detail="cannot_disable_default_admin")
    user.account_status = st
    apply_account_status_flags(user.id, st)
    if st != "active":
        revoke_admin_tokens_for_user(user.id)


def _sync_admin_role_menus_id_seq(db) -> None:
    """修复 admin_role_menus.id 序列落后于 max(id)（迁移/手工插入后常见），避免保存角色菜单 500。"""
    from sqlalchemy import text

    try:
        db.execute(
            text(
                "SELECT setval("
                "pg_get_serial_sequence('admin_role_menus', 'id'),"
                "GREATEST(COALESCE((SELECT MAX(id) FROM admin_role_menus), 1), 1)"
                ")"
            )
        )
    except Exception:
        logger.debug("sync admin_role_menus_id_seq skipped", exc_info=True)


def _normalize_menu_key_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = []
    for x in raw:
        if isinstance(x, dict):
            v = x.get("menu_key") or x.get("key") or x.get("path") or x.get("id") or ""
            out.append(str(v).strip().lower())
        else:
            out.append(str(x).strip().lower())
    return [k for k in out if k]


def _apply_role_menus(db, role_key: str, raw_menu_keys) -> list:
    """全量覆盖角色菜单：按 diff 增删，并校正 id 序列，避免 pkey 冲突 500。"""
    from sqlalchemy import or_

    keys = _normalize_menu_key_list(raw_menu_keys)
    valid: set = set()
    if keys:
        ids = [int(k) for k in keys if k.isdigit()]
        clauses = [
            AdminMenuORM.menu_key.in_(keys),
            AdminMenuORM.path.in_(keys),
        ]
        if ids:
            clauses.append(AdminMenuORM.id.in_(ids))
        valid = {m.menu_key for m in db.query(AdminMenuORM).filter(or_(*clauses)).all()}

    existing = {
        r.menu_key
        for r in db.query(AdminRoleMenuORM.menu_key)
        .filter(AdminRoleMenuORM.role_key == role_key)
        .all()
        if r.menu_key
    }
    to_add = sorted(valid - existing)
    to_del = sorted(existing - valid)
    if to_del:
        db.query(AdminRoleMenuORM).filter(
            AdminRoleMenuORM.role_key == role_key,
            AdminRoleMenuORM.menu_key.in_(to_del),
        ).delete(synchronize_session=False)
        db.flush()
    if to_add:
        _sync_admin_role_menus_id_seq(db)
        for mk in to_add:
            db.add(AdminRoleMenuORM(role_key=role_key, menu_key=mk))
        db.flush()
    return sorted(valid)


@router.get("/api/admin/admin-users", summary="管理员账号列表")
async def admin_list_admin_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    username: Optional[str] = Query(
        None, description="兼容旧参数：与 keyword 相同，按 ID/昵称/用户名模糊"
    ),
    keyword: Optional[str] = Query(None, description="ID/昵称/用户名模糊筛选"),
    role_key: Optional[str] = Query(None, description="按角色筛选"),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        _ensure_admin_rbac(db)
        base_q = db.query(UserORM).filter(UserORM.role.isnot(None), UserORM.role != "", UserORM.role != "user")
        # 前端目前传 username；正式参数为 keyword。统一为通用关键词模糊。
        qtext = (keyword or username or "").strip()
        if qtext:
            like = f"%{qtext}%"
            base_q = base_q.filter(
                or_(
                    cast(UserORM.id, String).ilike(like),
                    UserORM.nickname.ilike(like),
                    UserORM.username.ilike(like),
                )
            )
        rk = (role_key or "").strip().lower()
        if rk:
            base_q = base_q.filter(UserORM.role == rk)
        # 勿对同一 Query 先 with_entities(count) 再取实体：会污染 SELECT，列表可能空/报错
        total = int(base_q.count() or 0)
        rows = (
            base_q.order_by(UserORM.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = _enrich_admin_user_items(db, rows)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/admin-users/{user_id}", summary="管理员账号详情")
async def admin_get_admin_user(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user or (user.role or "").strip().lower() in ("", "user"):
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        name_map = load_companion_id_name_map(db, getattr(user, "admin_companion_ids", None) or [])
        return ok(data=_admin_user_item(user, name_map=name_map))


@router.post("/api/admin/admin-users", summary="创建管理员账号")
async def admin_create_admin_user(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    nickname = (data.get("nickname") or username).strip()[:32]
    role_key = _valid_role_key(data.get("role_key") or "")
    if len(username) < 3:
        raise HTTPException(status_code=400, detail=_get_error_msg("username_too_short", lang))
    if len(password) < 6:
        raise HTTPException(status_code=400, detail=_get_error_msg("password_too_short", lang))
    with get_db() as db:
        if db.query(UserORM).filter(UserORM.username == username).first():
            raise HTTPException(status_code=400, detail=_get_error_msg("username_exists", lang))
        if not db.query(AdminRoleORM).filter(AdminRoleORM.role_key == role_key, AdminRoleORM.is_active == True).first():  # noqa: E712
            raise HTTPException(status_code=400, detail="role_not_found")
        flag = _parse_bool_flag(data.get("is_active"))
        account_status = "active" if flag is not False else "banned"
        user = UserORM(
            username=username,
            nickname=nickname,
            password_hash=_hash(password),
            role=role_key,
            account_status=account_status,
            gender="",
            sexual_orientation="",
            email=(str(data.get("email") or "").strip())[:200],
            remark=(str(data.get("remark") or "").strip())[:2000],
            admin_companion_ids=[],
        )
        db.add(user)
        db.flush()
        profile_data = dict(data)
        if "companion_ids" in data or "companions" in data:
            _apply_admin_user_profile(user, profile_data, db=db)
        db.commit()
        db.refresh(user)
        if account_status != "active":
            from core.auth import apply_account_status_flags, revoke_admin_tokens_for_user

            apply_account_status_flags(user.id, account_status)
            revoke_admin_tokens_for_user(user.id)
        name_map = load_companion_id_name_map(db, user.admin_companion_ids or [])
        return ok(data=_admin_user_item(user, name_map=name_map))


@router.put("/api/admin/admin-users/{user_id}", summary="更新管理员账号")
async def admin_update_admin_user(user_id: int, data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        if (user.role or "").strip().lower() in ("", "user"):
            raise HTTPException(status_code=400, detail="not_an_admin_user")
        if "nickname" in data:
            user.nickname = (data.get("nickname") or "").strip()[:32]
        if "role_key" in data:
            role_key = _valid_role_key(data.get("role_key") or "")
            if not db.query(AdminRoleORM).filter(AdminRoleORM.role_key == role_key, AdminRoleORM.is_active == True).first():  # noqa: E712
                raise HTTPException(status_code=400, detail="role_not_found")
            user.role = role_key
        if "password" in data and data.get("password"):
            pwd = str(data.get("password"))
            if len(pwd) < 6:
                raise HTTPException(status_code=400, detail=_get_error_msg("password_too_short", lang))
            user.password_hash = _hash(pwd)
        _apply_admin_user_profile(user, data, db=db)
        _apply_admin_user_active(user, data)
        db.commit()
        db.refresh(user)
        name_map = load_companion_id_name_map(db, user.admin_companion_ids or [])
        return ok(data=_admin_user_item(user, name_map=name_map))


@router.delete("/api/admin/admin-users/{user_id}", summary="删除管理员账号")
async def admin_delete_admin_user(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        if (user.username or "").strip().lower() == "gigadmin":
            raise HTTPException(status_code=400, detail="cannot_delete_default_admin")
        from core.auth import revoke_admin_tokens_for_user

        revoke_admin_tokens_for_user(user.id)
        db.delete(user)
        db.commit()
        return ok(data={"deleted_id": user_id})


@router.get("/api/admin/roles", summary="角色列表")
async def admin_list_roles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    with get_db() as db:
        _ensure_admin_rbac(db)
        mapping = {}
        for rm in db.query(AdminRoleMenuORM).order_by(AdminRoleMenuORM.id.asc()).all():
            mapping.setdefault(rm.role_key, []).append(rm.menu_key)
        base_q = db.query(AdminRoleORM)
        total = base_q.with_entities(func.count(AdminRoleORM.id)).scalar() or 0
        rows = base_q.order_by(AdminRoleORM.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
        items = [
            {
                "id": r.id,
                "role_key": r.role_key,
                "role_name": r.role_name,
                "description": r.description,
                "is_active": bool(r.is_active),
                "menu_keys": mapping.get(r.role_key, []),
                "created_at": serialize_datetime(r.created_at),
            }
            for r in rows
        ]
        return paginated(items=items, total=int(total), page=page, page_size=page_size)


@router.post("/api/admin/roles", summary="新建角色")
async def admin_create_role(data: dict, _token: str = Depends(admin_auth_required)):
    role_key = _valid_role_key(data.get("role_key") or "")
    role_name = (data.get("role_name") or role_key).strip()[:64]
    description = (data.get("description") or "").strip()[:200]
    with get_db() as db:
        if db.query(AdminRoleORM).filter(AdminRoleORM.role_key == role_key).first():
            raise HTTPException(status_code=400, detail="role_exists")
        row = AdminRoleORM(role_key=role_key, role_name=role_name, description=description, is_active=True)
        db.add(row)
        db.flush()
        saved_keys = None
        if "menu_keys" in data or "menus" in data:
            raw = data.get("menu_keys") if "menu_keys" in data else data.get("menus")
            saved_keys = _apply_role_menus(db, role_key, raw)
        db.commit()
        db.refresh(row)
        payload = {"id": row.id, "role_key": row.role_key, "role_name": row.role_name}
        if saved_keys is not None:
            payload["menu_keys"] = saved_keys
        return ok(data=payload)


@router.put("/api/admin/roles/{role_key}", summary="更新角色")
async def admin_update_role(role_key: str, data: dict, _token: str = Depends(admin_auth_required)):
    rk = _valid_role_key(role_key)
    with get_db() as db:
        row = db.query(AdminRoleORM).filter(AdminRoleORM.role_key == rk).first()
        if not row:
            raise HTTPException(status_code=404, detail="role_not_found")
        if "role_name" in data:
            row.role_name = (data.get("role_name") or row.role_name).strip()[:64]
        if "description" in data:
            row.description = (data.get("description") or "").strip()[:200]
        if "is_active" in data:
            row.is_active = bool(data.get("is_active"))
        saved_keys = None
        if "menu_keys" in data or "menus" in data:
            raw = data.get("menu_keys") if "menu_keys" in data else data.get("menus")
            saved_keys = _apply_role_menus(db, rk, raw)
        db.commit()
        db.refresh(row)
        payload = {"role_key": row.role_key, "role_name": row.role_name, "is_active": bool(row.is_active)}
        if saved_keys is not None:
            payload["menu_keys"] = saved_keys
        return ok(data=payload)


@router.delete("/api/admin/roles/{role_key}", summary="删除角色")
async def admin_delete_role(role_key: str, _token: str = Depends(admin_auth_required)):
    rk = _valid_role_key(role_key)
    if rk == "admin":
        raise HTTPException(status_code=400, detail="cannot_delete_admin_role")
    with get_db() as db:
        role = db.query(AdminRoleORM).filter(AdminRoleORM.role_key == rk).first()
        if not role:
            raise HTTPException(status_code=404, detail="role_not_found")
        db.query(AdminRoleMenuORM).filter(AdminRoleMenuORM.role_key == rk).delete(synchronize_session=False)
        db.query(UserORM).filter(UserORM.role == rk).update({"role": "user"}, synchronize_session=False)
        db.delete(role)
        db.commit()
        return ok(data={"deleted_role_key": rk})


def _menu_lang_is_en(lang: Optional[str]) -> bool:
    return (lang or "zh").strip().lower().startswith("en")


def _menu_display_name(m, lang: Optional[str] = "zh") -> str:
    """按 Accept-Language 取展示名；英文缺省回退中文。响应字段仍用 menu_name。"""
    if _menu_lang_is_en(lang):
        en = (getattr(m, "menu_name_en", None) or "").strip()
        if en:
            return en
    return (m.menu_name or "").strip()


def _write_menu_names(row, data: dict, lang: Optional[str], *, is_create: bool = False) -> None:
    """写入中/英名称。menu_name 按当前语言写入对应列；也可显式传 menu_name_en / menu_name_zh。"""
    is_en = _menu_lang_is_en(lang)
    if "menu_name_zh" in data:
        row.menu_name = (str(data.get("menu_name_zh") or "").strip() or row.menu_name or row.menu_key)[:100]
    if "menu_name_en" in data:
        row.menu_name_en = (str(data.get("menu_name_en") or "").strip())[:100]
    if "menu_name" in data or is_create:
        raw = data.get("menu_name")
        if raw is None and is_create:
            raw = data.get("menu_key") or getattr(row, "menu_key", "") or ""
        if raw is not None:
            name = str(raw).strip()[:100]
            if is_en:
                row.menu_name_en = name
                if is_create and not (row.menu_name or "").strip():
                    row.menu_name = (str(data.get("menu_name_zh") or name).strip() or name)[:100]
            else:
                row.menu_name = name or (getattr(row, "menu_key", "") or "")[:100]


def _menu_item(m, lang: Optional[str] = "zh") -> dict:
    return {
        "id": m.id,
        "menu_key": m.menu_key,
        "menu_name": _menu_display_name(m, lang),
        "path": m.path or "",
        "parent_key": (m.parent_key or "").strip(),
        "sort_order": int(m.sort_order or 0),
        "is_active": bool(m.is_active),
    }


def _normalize_menu_path(menu_key: str, path: str) -> str:
    """目录型一级菜单可不填 path，自动写成 #{menu_key}（唯一、前端不路由）。"""
    p = (path or "").strip()
    if p:
        return p[:200]
    return f"#{(menu_key or '').strip().lower()}"[:200]


def _validate_parent_key(db, *, self_key: str, parent_key: str) -> str:
    """校验 parent_key：空=一级；否则父菜单必须存在且为一级；仅支持两级。"""
    pk = (parent_key or "").strip().lower()
    sk = (self_key or "").strip().lower()
    if not pk:
        return ""
    if pk == sk:
        raise HTTPException(status_code=400, detail="parent_cannot_be_self")
    parent = db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == pk).first()
    if not parent:
        raise HTTPException(status_code=400, detail="parent_not_found")
    if (parent.parent_key or "").strip():
        raise HTTPException(status_code=400, detail="parent_must_be_top_level")
    # 自身若已有子菜单，不可再挂到别人下面（避免三级）
    has_child = (
        db.query(AdminMenuORM.id)
        .filter(AdminMenuORM.parent_key == sk)
        .first()
    )
    if has_child:
        raise HTTPException(status_code=400, detail="menu_has_children_cannot_nest")
    return pk


def _build_menu_tree(rows, lang: Optional[str] = "zh") -> list:
    """扁平菜单 → 一级 + children 二级树。孤立 parent_key 当作一级。"""
    flat = [_menu_item(m, lang) for m in rows]
    by_key = {x["menu_key"]: {**x, "children": []} for x in flat}
    roots = []
    for x in flat:
        node = by_key[x["menu_key"]]
        pk = x["parent_key"]
        if pk and pk in by_key and pk != x["menu_key"]:
            by_key[pk]["children"].append(node)
        else:
            if pk and pk not in by_key:
                node["parent_key"] = ""  # 父已删：降级为一级展示
            roots.append(node)
    for node in by_key.values():
        node["children"].sort(key=lambda c: (c["sort_order"], c.get("id") or 0))
    roots.sort(key=lambda c: (c["sort_order"], c.get("id") or 0))
    return roots


def _resolve_admin_role_key(db, payload: dict) -> str:
    """角色以 users.role 为准（改角色后无需重新登录也立即生效）；无 user_id 时用 token。"""
    user_id = payload.get("user_id")
    if user_id is not None and str(user_id).strip() != "":
        try:
            user = db.query(UserORM).filter(UserORM.id == int(user_id)).first()
        except (TypeError, ValueError):
            user = None
        if user and (user.role or "").strip():
            return (user.role or "").strip().lower()
    return (payload.get("role_key") or "").strip().lower()


def _role_allowed_menu_keys(db, role_key: str):
    """角色可见 menu_key（只认仍存在于 admin_menus 且启用的 key）。

    - 有 admin_role_menus 记录 → 严格按配置（含 admin）
    - 无记录且 role=admin → None（不限制，兼容旧超级管理员）
    - 其它角色无配置 → 空集合（无菜单）
    """
    rk = (role_key or "").strip().lower()
    if not rk:
        return set()
    rows = (
        db.query(AdminRoleMenuORM.menu_key)
        .filter(AdminRoleMenuORM.role_key == rk)
        .all()
    )
    raw_keys = {str(r[0]).strip() for r in rows if r and str(r[0]).strip()}
    if not raw_keys:
        if rk == "admin":
            return None
        return set()
    # 过滤已删 / 停用菜单，避免幽灵 key 把侧栏撑乱
    alive = {
        m.menu_key
        for m in db.query(AdminMenuORM.menu_key)
        .filter(
            AdminMenuORM.menu_key.in_(raw_keys),
            AdminMenuORM.is_active.is_(True),
        )
        .all()
    }
    return alive


def _expand_menu_keys_with_parents(db, allowed: set) -> set:
    """勾了二级时自动带上父级，便于组树。"""
    if not allowed:
        return set()
    pairs = db.query(AdminMenuORM.menu_key, AdminMenuORM.parent_key).all()
    parent_of = {str(k): (p or "").strip() for k, p in pairs if k}
    out = set(allowed)
    for mk in list(allowed):
        pk = parent_of.get(mk, "")
        seen = set()
        while pk and pk not in seen:
            seen.add(pk)
            out.add(pk)
            pk = parent_of.get(pk, "")
    return out


def _prune_menu_tree(tree: list, allowed: set) -> list:
    """树裁剪：节点在 allowed，或带子节点时保留（父级作目录）。"""
    result = []
    for node in tree:
        kids = _prune_menu_tree(list(node.get("children") or []), allowed)
        if node.get("menu_key") in allowed or kids:
            result.append({**node, "children": kids})
    return result


@router.get("/api/admin/menus", summary="菜单列表")
async def admin_list_menus(
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    as_tree: bool = Query(True, description="true=一级+children 树；false=扁平列表"),
    parent_key: Optional[str] = Query(None, description="扁平模式：按父 key 筛选；空串=仅一级"),
    is_active: Optional[bool] = Query(None),
    keyword: Optional[str] = Query(None, description="menu_key/menu_name 模糊"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        _ensure_admin_rbac(db)
        base_q = db.query(AdminMenuORM)
        if is_active is not None:
            base_q = base_q.filter(AdminMenuORM.is_active == is_active)
        kw = (keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            base_q = base_q.filter(
                AdminMenuORM.menu_key.ilike(like)
                | AdminMenuORM.menu_name.ilike(like)
                | AdminMenuORM.menu_name_en.ilike(like)
            )
        rows = base_q.order_by(AdminMenuORM.sort_order.asc(), AdminMenuORM.id.asc()).all()

        if as_tree:
            tree = _build_menu_tree(rows, lang)
            # parent_key 筛选：只返回该父下的二级（包一层）或某根
            pk_filter = parent_key
            if pk_filter is not None:
                want = (pk_filter or "").strip().lower()
                if want == "":
                    tree = [{**n, "children": n.get("children") or []} for n in tree]
                else:
                    tree = [n for n in tree if n["menu_key"] == want]
            total = len(tree)
            start = (page - 1) * page_size
            end = start + page_size
            return paginated(items=tree[start:end], total=total, page=page, page_size=page_size)

        # 扁平
        if parent_key is not None:
            want = (parent_key or "").strip().lower()
            rows = [m for m in rows if (m.parent_key or "").strip().lower() == want]
        items = [_menu_item(m, lang) for m in rows]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return paginated(items=items[start:end], total=total, page=page, page_size=page_size)


@router.post("/api/admin/menus", summary="新增菜单（可指定 parent_key 挂到一级下）")
async def admin_create_menu(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    menu_key = (data.get("menu_key") or "").strip().lower()
    if not menu_key:
        raise HTTPException(status_code=400, detail="menu_key_required")
    path = _normalize_menu_path(menu_key, data.get("path") or "")
    with get_db() as db:
        if db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == menu_key).first():
            raise HTTPException(status_code=400, detail="menu_exists")
        if db.query(AdminMenuORM).filter(AdminMenuORM.path == path).first():
            raise HTTPException(status_code=400, detail="path_exists")
        parent_key = _validate_parent_key(
            db, self_key=menu_key, parent_key=(data.get("parent_key") or "")
        )
        row = AdminMenuORM(
            menu_key=menu_key,
            menu_name=menu_key,
            menu_name_en="",
            path=path,
            parent_key=parent_key,
            sort_order=int(data.get("sort_order") or 0),
            is_active=bool(data.get("is_active", True)),
        )
        _write_menu_names(row, data, lang, is_create=True)
        if not (row.menu_name or "").strip():
            row.menu_name = menu_key
        db.add(row)
        db.commit()
        db.refresh(row)
        return ok(data=_menu_item(row, lang))


@router.put("/api/admin/menus/{menu_key}", summary="更新菜单（可改 parent_key 实现移动）")
async def admin_update_menu(
    menu_key: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    mk = (menu_key or "").strip().lower()
    with get_db() as db:
        row = db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == mk).first()
        if not row:
            raise HTTPException(status_code=404, detail="menu_not_found")
        if "menu_name" in data or "menu_name_en" in data or "menu_name_zh" in data:
            _write_menu_names(row, data, lang, is_create=False)
        if "path" in data:
            new_path = _normalize_menu_path(mk, data.get("path") if data.get("path") is not None else row.path)
            clash = (
                db.query(AdminMenuORM)
                .filter(AdminMenuORM.path == new_path, AdminMenuORM.menu_key != mk)
                .first()
            )
            if clash:
                raise HTTPException(status_code=400, detail="path_exists")
            row.path = new_path
        if "parent_key" in data:
            row.parent_key = _validate_parent_key(
                db, self_key=mk, parent_key=(data.get("parent_key") or "")
            )
        if "sort_order" in data:
            row.sort_order = int(data.get("sort_order") or 0)
        if "is_active" in data:
            row.is_active = bool(data.get("is_active"))
        db.commit()
        db.refresh(row)
        return ok(data=_menu_item(row, lang))


@router.post("/api/admin/menus/{menu_key}/move", summary="移动菜单到父级（快捷）")
async def admin_move_menu(
    menu_key: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """body: { parent_key: \"system\" | \"\", sort_order?: number }"""
    mk = (menu_key or "").strip().lower()
    with get_db() as db:
        row = db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == mk).first()
        if not row:
            raise HTTPException(status_code=404, detail="menu_not_found")
        if "parent_key" not in data:
            raise HTTPException(status_code=400, detail="parent_key_required")
        row.parent_key = _validate_parent_key(
            db, self_key=mk, parent_key=(data.get("parent_key") or "")
        )
        if "sort_order" in data:
            row.sort_order = int(data.get("sort_order") or 0)
        db.commit()
        db.refresh(row)
        return ok(data=_menu_item(row, lang))


@router.delete("/api/admin/menus/{menu_key}", summary="删除菜单")
async def admin_delete_menu(
    menu_key: str,
    reparent_children: bool = Query(
        False, description="true=子菜单升为一级后删除；false=有子则拒绝"
    ),
    _token: str = Depends(admin_auth_required),
):
    mk = (menu_key or "").strip().lower()
    with get_db() as db:
        row = db.query(AdminMenuORM).filter(AdminMenuORM.menu_key == mk).first()
        if not row:
            raise HTTPException(status_code=404, detail="menu_not_found")
        children = (
            db.query(AdminMenuORM).filter(AdminMenuORM.parent_key == mk).all()
        )
        if children and not reparent_children:
            raise HTTPException(status_code=400, detail="menu_has_children")
        for ch in children:
            ch.parent_key = ""
        db.query(AdminRoleMenuORM).filter(AdminRoleMenuORM.menu_key == mk).delete(
            synchronize_session=False
        )
        db.delete(row)
        db.commit()
        return ok(
            data={
                "deleted_menu_key": mk,
                "reparented": [c.menu_key for c in children],
            }
        )


@router.put("/api/admin/roles/{role_key}/menus", summary="角色菜单可见配置")
async def admin_set_role_menus(role_key: str, data: dict, _token: str = Depends(admin_auth_required)):
    rk = _valid_role_key(role_key)
    with get_db() as db:
        role = db.query(AdminRoleORM).filter(AdminRoleORM.role_key == rk).first()
        if not role:
            raise HTTPException(status_code=404, detail="role_not_found")
        raw = data.get("menu_keys") if data.get("menu_keys") is not None else data.get("menus")
        try:
            saved = _apply_role_menus(db, rk, raw)
        except Exception:
            logger.exception("apply role menus failed role=%s", rk)
            raise HTTPException(status_code=500, detail="role_menus_save_failed")
        # get_db 退出时 commit
        return ok(data={"role_key": rk, "menu_keys": saved})


@router.get("/api/admin/roles/{role_key}/menus", summary="查询角色菜单可见配置")
async def admin_get_role_menus(
    role_key: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    _token: str = Depends(admin_auth_required),
):
    rk = _valid_role_key(role_key)
    with get_db() as db:
        all_menu_keys = [
            x.menu_key
            for x in db.query(AdminRoleMenuORM)
            .filter(AdminRoleMenuORM.role_key == rk)
            .order_by(AdminRoleMenuORM.id.asc())
            .all()
        ]
        total = len(all_menu_keys)
        start = (page - 1) * page_size
        end = start + page_size
        items = [{"menu_key": k} for k in all_menu_keys[start:end]]
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/me/menus", summary="当前管理员可见菜单（按角色菜单配置筛选）")
async def admin_my_menus(
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    as_tree: bool = Query(True, description="侧栏树形：一级+children"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    with get_db() as db:
        _ensure_admin_rbac(db)
        payload = get_admin_token_payload(_token) or {}
        role_key = _resolve_admin_role_key(db, payload)
        allowed = _role_allowed_menu_keys(db, role_key)

        q = db.query(AdminMenuORM).filter(AdminMenuORM.is_active == True)  # noqa: E712
        if allowed is not None:
            visible = _expand_menu_keys_with_parents(db, allowed)
            q = q.filter(AdminMenuORM.menu_key.in_(visible or ["__none__"]))
        rows = q.order_by(AdminMenuORM.sort_order.asc(), AdminMenuORM.id.asc()).all()

        if as_tree:
            tree = _build_menu_tree(rows, lang)
            if allowed is not None:
                tree = _prune_menu_tree(tree, allowed)
            total = len(tree)
            start = (page - 1) * page_size
            end = start + page_size
            return paginated(items=tree[start:end], total=total, page=page, page_size=page_size)

        # 扁平：只返回角色真正勾选的 key（不含仅为组树补上的父级）
        if allowed is not None:
            items = [_menu_item(m, lang) for m in rows if m.menu_key in allowed]
        else:
            items = [_menu_item(m, lang) for m in rows]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return paginated(items=items[start:end], total=total, page=page, page_size=page_size)


@router.put("/api/admin/me/password", summary="当前管理员修改密码")
async def admin_change_my_password(
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """前端个人中心改密：校验原密码后更新。错误 message 需含「原密码」以便前端展示 wrongOld。"""
    old_password = str(data.get("old_password") or "")
    new_password = str(data.get("new_password") or "")
    if not old_password:
        return fail(message=_get_error_msg("password_old_wrong", lang), code=400)
    if len(new_password) < 6:
        return fail(message=_get_error_msg("password_too_short", lang), code=400)

    payload = get_admin_token_payload(_token) or {}
    uid = payload.get("user_id")
    try:
        admin_id = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        admin_id = None
    if admin_id is None:
        # 环境变量密码登录无 user_id，无法改库内密码
        return fail(message=_get_error_msg("password_change_unsupported", lang), code=400)

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == admin_id).first()
        if not user or (user.role or "").strip().lower() in ("", "user"):
            return fail(message=_get_error_msg("user_not_found", lang), code=404)
        if not _verify_pwd(old_password, user.password_hash or ""):
            return fail(message=_get_error_msg("password_old_wrong", lang), code=400)
        user.password_hash = _hash(new_password)
        db.commit()

    try:
        revoke_admin_tokens_for_user(admin_id, keep_token=_token)
    except Exception:
        logger.exception("revoke other admin tokens after password change failed")

    return ok(data={"ok": True}, message="success")


@router.get("/api/admin/stats",summary="获取系统统计")
async def admin_stats(_token: str = Depends(admin_auth_required)):
    from core.database import UserCompanionStateORM

    companions = get_companion_manager().list_all()

    # 排除已删除的伴侣进行统计
    active_companions = [c for c in companions if not c.get("is_deleted")]

    # 从用户特定状态表中聚合数据（排除已删除伴侣的状态）
    active_companion_ids = {c["profile"]["id"] for c in active_companions}

    with get_db() as db:
        if active_companion_ids:
            user_states = db.query(UserCompanionStateORM).filter(
                UserCompanionStateORM.companion_id.in_(active_companion_ids)
            ).all()
        else:
            user_states = []
        total_turns = sum(s.turns or 0 for s in user_states)
        total_affection = sum(s.affection or 0 for s in user_states)
        avg_affection = total_affection / max(len(user_states), 1) if user_states else 0

    return {
        "companion_count": len(active_companions),
        "total_turns": total_turns,
        "avg_affection": round(avg_affection, 1),
        "knowledge_stats": knowledge_base.get_stats(),
    }


@router.get("/api/admin/companions", summary="获取伴侣列表")
async def admin_list_companions(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=500, description="每页条数"),
    language: Optional[str] = Query(None, description="按 language 筛选"),
    tier: Optional[str] = Query(None, description="按 tier 筛选"),
    gender: Optional[str] = Query(None, description="按性别筛选：男/女 或 male/female"),
    is_deleted: Optional[str] = Query(
        None,
        description="软删筛选：true/false；不传则默认包含全部（含软删）",
    ),
    sort_by: str = Query("created_at", description="排序字段：age | created_at"),
    sort_order: str = Query("desc", description="排序方向：asc | desc"),
    _token: str = Depends(admin_auth_required),
):
    """获取伴侣列表（管理后台）：默认包含软删；可分页/筛选/排序。"""
    sort_key = (sort_by or "created_at").strip().lower()
    if sort_key not in ("age", "created_at"):
        sort_key = "created_at"
    ascending = (sort_order or "desc").strip().lower() == "asc"

    with get_db() as db:
        q = db.query(CompanionORM)
        # 运营账号：仅展示白名单内智能体
        from services.im_admin import companion_scope_sql_ids, resolve_admin_actor

        scope_ids = companion_scope_sql_ids(resolve_admin_actor(_token))
        if scope_ids is not None:
            q = q.filter(CompanionORM.id.in_(scope_ids))
        # 默认包含软删；仅当显式传 is_deleted 时过滤
        if is_deleted is not None and str(is_deleted).strip() != "":
            flag = str(is_deleted).strip().lower() in ("1", "true", "yes", "y")
            q = q.filter(CompanionORM.is_deleted == flag)
        if language and language.strip():
            q = q.filter(CompanionORM.language == language.strip())
        if tier and tier.strip():
            q = q.filter(CompanionORM.tier == tier.strip())
        if gender and gender.strip():
            q = q.filter(CompanionORM.gender == _normalize_gender(gender))

        total = q.count()
        # 与前端展示一致：空年龄按 18 参与排序，避免 null 沉底导致“排序错乱”
        if sort_key == "age":
            sort_col = func.coalesce(CompanionORM.age, 18)
        else:
            sort_col = CompanionORM.created_at
        order_expr = sort_col.asc().nullslast() if ascending else sort_col.desc().nullslast()
        rows = (
            q.order_by(order_expr, CompanionORM.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        ids = [r.id for r in rows]
        state_map = {}
        if ids:
            for st in db.query(CompanionStateORM).filter(CompanionStateORM.companion_id.in_(ids)).all():
                state_map[st.companion_id] = st

        items = []
        for row in rows:
            avatar = row.avatar_url or ""
            st = state_map.get(row.id)
            lang_val = row.language or "zh"
            tier_val = row.tier or "featured"
            deleted = bool(getattr(row, "is_deleted", False))
            age_val = row.age if row.age is not None else 18
            created_val = serialize_datetime(row.created_at)
            items.append(
                {
                    "profile": {
                        "id": row.id,
                        "name": row.name,
                        "age": age_val,
                        "gender": row.gender or "女",
                        "city": row.city or "",
                        "personality": row.personality or "",
                        "background": row.background or "",
                        "speech_style": row.speech_style or "",
                        "hobbies": row.hobbies or "",
                        "values": row.values or "",
                        "fears": row.fears or "",
                        "love_view": row.love_view or "",
                        "daily_routine": row.daily_routine or "",
                        "favorite_things": row.favorite_things or "",
                        "language": lang_val,
                        "tier": tier_val,
                        "is_deleted": deleted,
                        "created_at": created_val,
                    },
                    "state": {
                        "affection": float(st.affection) if st and st.affection is not None else 0.0,
                        "turns": int(st.turns) if st and st.turns is not None else 0,
                    },
                    # 顶层字段供 ProTable dataIndex/sorter 使用（避免嵌套 dataIndex 排序键错乱）
                    "age": age_val,
                    "created_at": created_val,
                    "avatar": avatar,
                    "avatar_generating": avatar == "__GENERATING__",
                    "language": lang_val,
                    "tier": tier_val,
                    "is_deleted": deleted,
                }
            )

    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/companions/export", summary="批量导出智能体资料卡 Excel")
async def admin_export_companions(
    ids: Optional[str] = Query(None, description="逗号分隔的 companion id；空则按筛选导出"),
    language: Optional[str] = Query(None, description="按 language 筛选"),
    tier: Optional[str] = Query(None, description="按 tier 筛选"),
    gender: Optional[str] = Query(None, description="按性别筛选"),
    include_deleted: bool = Query(False, description="是否包含已软删"),
    limit: int = Query(5000, ge=1, le=20000, description="最大导出行数"),
    sort_by: str = Query("created_at", description="排序字段：age | created_at"),
    sort_order: str = Query("desc", description="排序方向：asc | desc"),
    _token: str = Depends(admin_auth_required),
):
    """导出 companions 表字段为 xlsx（列与库表一致，可再导入）。"""
    id_list = [x.strip() for x in (ids or "").split(",") if x.strip()]
    sort_key = (sort_by or "created_at").strip().lower()
    if sort_key not in ("age", "created_at"):
        sort_key = "created_at"
    ascending = (sort_order or "desc").strip().lower() == "asc"

    with get_db() as db:
        q = db.query(CompanionORM)
        from services.im_admin import companion_scope_sql_ids, resolve_admin_actor

        scope_ids = companion_scope_sql_ids(resolve_admin_actor(_token))
        if scope_ids is not None:
            q = q.filter(CompanionORM.id.in_(scope_ids))
        if id_list:
            q = q.filter(CompanionORM.id.in_(id_list))
        else:
            if not include_deleted:
                q = q.filter(CompanionORM.is_deleted == False)  # noqa: E712
            if language:
                q = q.filter(CompanionORM.language == language.strip())
            if tier:
                q = q.filter(CompanionORM.tier == tier.strip())
            if gender and gender.strip():
                q = q.filter(CompanionORM.gender == _normalize_gender(gender))
        if sort_key == "age":
            sort_col = func.coalesce(CompanionORM.age, 18)
        else:
            sort_col = CompanionORM.created_at
        order_expr = sort_col.asc().nullslast() if ascending else sort_col.desc().nullslast()
        rows = q.order_by(order_expr, CompanionORM.id.asc()).limit(limit).all()
        export_rows = [orm_row_to_export_dict(r) for r in rows]

    content = build_companions_xlsx(export_rows)
    filename = f"companions_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/admin/companions/template", summary="下载资料卡导入模板 Excel")
async def admin_companions_template(_token: str = Depends(admin_auth_required)):
    """下载导入模板：完整表头 + 两条示例数据（id 为空，导入前请按需修改或删除示例行）。"""
    content = build_companions_template_xlsx()
    filename = "companions_import_template.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/admin/companions/import", summary="批量导入智能体资料卡 Excel")
async def admin_import_companions(
    file: UploadFile = File(..., description="与导出列一致的 .xlsx"),
    _token: str = Depends(admin_auth_required),
):
    """
    批量导入：有 id 且库中存在则按非空字段更新；否则新建。
    created_at 忽略；空单元格不覆盖已有值。
    """
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xlsm")):
        return fail(
            message="请上传 .xlsx 文件（与「导出」或「下载模板」相同格式，不支持 .xls/.csv）",
            code=400,
            data={"total": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1, "errors": [
                {"row": 1, "id": "", "name": "", "field": "file", "reason": "文件格式必须是 .xlsx"},
            ]},
        )

    raw = await file.read()
    if not raw:
        return fail(
            message="上传的文件是空的，请使用导出的 Excel 或下载模板后再导入",
            code=400,
            data={"total": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1, "errors": [
                {"row": 1, "id": "", "name": "", "field": "file", "reason": "文件为空"},
            ]},
        )

    rows, header_errors = parse_companions_xlsx(raw)
    if header_errors:
        reasons = [e.get("reason") for e in header_errors if e.get("reason")]
        return fail(
            message=reasons[0] if reasons else "Excel 表头不正确，请使用导出文件或下载模板",
            code=400,
            data={
                "total": 0,
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "failed": len(header_errors),
                "errors": header_errors,
            },
        )

    cm = get_companion_manager()

    def _exists(companion_id: str) -> bool:
        with get_db() as db:
            return (
                db.query(CompanionORM.id)
                .filter(CompanionORM.id == companion_id)
                .first()
                is not None
            )

    def _create(payload: dict) -> str:
        from services.companion_manager import clamp_companion_profile_dict

        companion = cm.create(clamp_companion_profile_dict(payload))
        cid = companion.profile.id
        if getattr(companion.profile, "avatar_url", "") == "__GENERATING__":
            start_avatar_generation(cid, companion.profile.model_dump())
        return cid

    def _update(companion_id: str, payload: dict) -> None:
        from services.companion_excel import CompanionImportError

        if not cm.get(companion_id, include_deleted=True):
            raise CompanionImportError(
                f"找不到 id={companion_id} 的智能体，无法更新（可能未加载到缓存）",
                field="id",
            )
        updated = cm.update(companion_id, payload)
        if not updated:
            raise CompanionImportError(f"更新失败，id={companion_id}", field="id")

    def _set_deleted(companion_id: str, is_deleted: bool) -> None:
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if not row:
                raise CompanionImportError(f"找不到 id={companion_id} 的智能体", field="id")
            row.is_deleted = bool(is_deleted)
        c = cm.get(companion_id, include_deleted=True)
        if c:
            c.profile.is_deleted = bool(is_deleted)

    result = apply_companions_import(
        rows,
        create_fn=_create,
        update_fn=_update,
        exists_fn=_exists,
        set_deleted_fn=_set_deleted,
    )
    # 去掉内部 ok 标记，统一走 response.ok / fail
    result.pop("ok", None)
    message = summarize_import_result(result)
    if result.get("failed"):
        # 已写入的行不会回滚；message 说明失败原因，errors 含逐行 reason
        return fail(message=message, code=400, data=result)
    return ok(data=result, message=message)


@router.delete("/api/admin/companions/{companion_id}", summary="删除伴侣")
async def admin_delete_companion(companion_id: str, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    ok = get_companion_manager().delete(companion_id)
    if not ok:
        raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
    return {"ok": True}


@router.post("/api/admin/companions/reload-cache", summary="刷新智能体内存缓存")
async def admin_reload_companion_cache(_token: str = Depends(admin_auth_required)):
    """手动刷新智能体内存缓存（用于数据库直接修改后同步到内存）"""
    cm = get_companion_manager()
    if not cm:
        raise HTTPException(status_code=500, detail=_("CompanionManager 未初始化"))
    old_count = len(cm._companions)
    cm._companions.clear()
    cm._load_all()
    new_count = len(cm._companions)
    return {"ok": True, "old_count": old_count, "new_count": new_count}


@router.post("/api/admin/companions/{companion_id}/restore", summary="恢复已删除的伴侣")
async def admin_restore_companion(companion_id: str, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """恢复被用户软删除的伴侣"""
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    cm = get_companion_manager()
    # get() 会过滤 is_deleted=True，所以先从 DB 检查是否存在且已删除
    with get_db() as db:
        row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
        if not row.is_deleted:
            raise HTTPException(status_code=400, detail=_("该伴侣未被删除"))
    ok = cm.restore(companion_id)
    if not ok:
        raise HTTPException(status_code=500, detail=_("恢复失败"))
    return {"ok": True}


@router.put("/api/admin/companions/{companion_id}", summary="更新伴侣信息")
async def admin_update_companion(companion_id: str, data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    companion = get_companion_manager().update(companion_id, data)
    if not companion:
        raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
    return companion.to_dict(lang=lang)


@router.get("/api/admin/companions/{companion_id}", summary="获取伴侣详情")
async def admin_get_companion(companion_id: str, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """获取单个伴侣详情（供编辑表单使用），合并 profile 和 agent-config 中的 system_prompt"""
    from services.im_admin import assert_companion_in_scope, resolve_admin_actor

    assert_companion_in_scope(resolve_admin_actor(_token), companion_id, lang)
    cm = get_companion_manager()
    companion = cm.get(companion_id)
    if not companion:
        # 尝试从 DB 加载（内存中可能未加载）
        with get_db() as db:
            row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
            if not row:
                raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
            # 触发加载到内存
            companion = cm.get_or_create(companion_id)
            if not companion:
                raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))

    data = companion.to_dict() if hasattr(companion, 'to_dict') else {}
    with get_db() as db:
        row = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
        if row is not None:
            data["tags"] = list(getattr(row, "tags", None) or [])
            if isinstance(data.get("profile"), dict):
                data["profile"]["tags"] = data["tags"]

    # 合并 agent-config 中的 system_prompt_*
    with get_db() as db:
        config_row = db.query(CompanionAgentConfigORM).filter(
            CompanionAgentConfigORM.companion_id == companion_id
        ).first()
        if config_row and config_row.config_json:
            cfg = config_row.config_json
            for k, v in cfg.items():
                if k.startswith("system_prompt_") or k in ("temperature", "max_tokens"):
                    data[k] = v
            # 也把 profile 显式添加（前端期望）
            if "profile" not in data:
                data["profile"] = {
                    k: v for k, v in data.items() if not k.startswith("system_prompt_")
                }

    return data


@router.get("/api/admin/knowledge", summary="获取知识库列表")
async def admin_list_knowledge(
    category: str = None,
    language: str = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    _token: str = Depends(admin_auth_required),
):
    items, total = knowledge_base.list_entries_page(
        category=category,
        language=language,
        page=page,
        page_size=page_size,
    )
    return paginated(items=items, total=total, page=page, page_size=page_size)


@router.post("/api/admin/knowledge", summary="添加知识条目")
async def admin_add_knowledge(data: dict, _token: str = Depends(admin_auth_required)):
    entry = knowledge_base.add_entry(data)
    return entry.model_dump()


@router.delete("/api/admin/knowledge/{entry_id}", summary="删除知识条目")
async def admin_delete_knowledge(entry_id: str, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    ok = knowledge_base.delete_entry(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail=_get_error_msg("entry_not_found", lang))
    return {"ok": True}


@router.post("/api/admin/reset-all", summary="重置所有数据")
async def admin_reset_all(data: Optional[dict] = None, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """一键清除全部数据（所有智能体、朋友圈、知识库、记忆文件等）。
    必须提供 confirm=yes_clear_all_data 才能执行，避免误操作。
    可选参数 regenerate=true 会在清空后尝试重新生成朋友圈（需先通过 batch-generate 创建智能体）。
    """
    if not data or data.get("confirm") != "yes_clear_all_data":
        raise HTTPException(
            status_code=400, 
            detail=_get_error_msg("reset_confirm_required", lang)
        )

    cm = get_companion_manager()
    deleted = cm.clear_all()

    # 清空其他非核心数据（用户、反馈、统计等，可根据需要保留配置）
    with get_db() as db:
        db.query(UserORM).delete(synchronize_session=False)
        db.query(FeedbackThreadORM).delete(synchronize_session=False)
        db.query(FeedbackMessageORM).delete(synchronize_session=False)
        db.query(PageViewORM).delete(synchronize_session=False)
        db.query(ButtonClickORM).delete(synchronize_session=False)
        db.query(SystemNotificationORM).delete(synchronize_session=False)
        db.commit()

    regenerate = data.get("regenerate", False)
    moments_created = 0
    if regenerate:
        try:
            from services.moments import regenerate_moments_for_all
            import asyncio
            loop = asyncio.get_event_loop()
            # 注意：清空后需先创建智能体，否则 moments_created=0
            moments_created = await loop.run_in_executor(
                None, regenerate_moments_for_all, cm, data.get("moments_per_companion", 3)
            )
        except Exception as e:
            logger.warning("[Reset] 重新生成朋友圈失败: %s", e)

    return {
        "ok": True,
        "deleted_companions": deleted,
        "deleted_moments_related": "all",
        "moments_regenerated": moments_created,
        "regenerate": regenerate,
        "message": "全部数据已成功清除" + ("并尝试重新生成朋友圈" if regenerate else "。请使用批量生成接口重新创建智能体"),
        "next_step": "推荐运行 python server/scripts/batch_create_all_langs.py 重新生成智能体，然后调用 /moments/regenerate"
    }


@router.get("/api/admin/users", summary="获取用户列表")
async def admin_list_users(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    id: Optional[int] = Query(None, description="按用户 ID 精确筛选"),
    user_id: Optional[int] = Query(None, description="同 id，兼容别名"),
    q: Optional[str] = Query(None, description="综合搜索：数字=ID 精确，否则昵称/用户名模糊"),
    nickname: Optional[str] = Query(None, description="昵称模糊筛选（管理端表格字段）"),
    username: Optional[str] = Query(None, description="用户名模糊筛选"),
    gender: Optional[str] = Query(None, description="性别筛选"),
    sexual_orientation: Optional[str] = Query(None, description="性取向筛选"),
    age: Optional[int] = Query(None, ge=0, le=150, description="年龄精确筛选（管理端表格字段）"),
    age_min: Optional[int] = Query(None, ge=0, le=150, description="年龄下限"),
    age_max: Optional[int] = Query(None, ge=0, le=150, description="年龄上限"),
    region: Optional[str] = Query(None, description="地区（模糊）"),
    is_vip: Optional[str] = Query(None, description="VIP：true/false；不传=全部"),
    vip: Optional[str] = Query(None, description="同 is_vip，兼容别名"),
    status: Optional[str] = Query(None, description="账号状态：active/banned/deleted"),
    is_ads: Optional[str] = Query(None, description="广告户：true/false；不传=全部"),
    acquisition: Optional[str] = Query(
        None, description="获客标识：none / ads / attributed（无 / 广告户 / 带广告）"
    ),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """用户列表：联查 user_credits，支持分页与多条件 AND 组合筛选。"""
    from datetime import datetime, timezone

    from services.credit_service import get_cost_config, get_effective_balance, is_vip_active

    with get_db() as db:
        query = db.query(UserORM)

        # —— 条件均 AND 组合 ——
        exact_id = id if id is not None else user_id
        if exact_id is not None:
            query = query.filter(UserORM.id == int(exact_id))

        nick = (nickname or "").strip()
        if nick:
            query = query.filter(UserORM.nickname.ilike(f"%{nick}%"))

        uname = (username or "").strip()
        if uname:
            query = query.filter(UserORM.username.ilike(f"%{uname}%"))

        # q：综合搜索；与 id/nickname 可并存（AND）
        keyword = (q or "").strip()
        if keyword:
            if keyword.isdigit():
                query = query.filter(
                    (UserORM.id == int(keyword))
                    | UserORM.nickname.ilike(f"%{keyword}%")
                    | UserORM.username.ilike(f"%{keyword}%")
                )
            else:
                like = f"%{keyword}%"
                query = query.filter(
                    UserORM.nickname.ilike(like) | UserORM.username.ilike(like)
                )

        if gender and gender.strip():
            g_vals = _user_gender_filter_values(gender)
            if g_vals:
                query = query.filter(UserORM.gender.in_(g_vals))

        if sexual_orientation and sexual_orientation.strip():
            so_vals = _orientation_filter_values(sexual_orientation)
            if so_vals:
                query = query.filter(UserORM.sexual_orientation.in_(so_vals))

        # age 精确；可与 age_min/age_max 并存（取交集）
        lo = age_min
        hi = age_max
        if age is not None:
            lo = int(age) if lo is None else max(int(lo), int(age))
            hi = int(age) if hi is None else min(int(hi), int(age))
        if lo is not None:
            query = query.filter(UserORM.age.isnot(None), UserORM.age >= int(lo))
        if hi is not None:
            query = query.filter(UserORM.age.isnot(None), UserORM.age <= int(hi))

        if region and region.strip():
            query = query.filter(UserORM.region.ilike(f"%{region.strip()}%"))

        st = (status or "").strip().lower()
        if st in ("ban", "banned"):
            st = "banned"
        elif st in ("cancelled", "canceled", "deactivated", "注销"):
            st = "deleted"
        elif st in ("正常",):
            st = "active"
        if st in ("active", "banned", "deleted"):
            query = query.filter(UserORM.account_status == st)

        vip_flag = ((is_vip if is_vip is not None else vip) or "").strip().lower()
        now = datetime.now(timezone.utc)
        if vip_flag in ("1", "true", "yes", "y"):
            query = query.join(UserCreditORM, UserCreditORM.user_id == UserORM.id).filter(
                UserCreditORM.vip_type != "",
                (UserCreditORM.vip_type == "lifetime")
                | (
                    (UserCreditORM.vip_expire_at.isnot(None))
                    & (UserCreditORM.vip_expire_at > now)
                ),
            )
        elif vip_flag in ("0", "false", "no", "n"):
            sub_vip = (
                db.query(UserCreditORM.user_id)
                .filter(
                    UserCreditORM.vip_type != "",
                    (UserCreditORM.vip_type == "lifetime")
                    | (
                        (UserCreditORM.vip_expire_at.isnot(None))
                        & (UserCreditORM.vip_expire_at > now)
                    ),
                )
                .subquery()
            )
            query = query.filter(~UserORM.id.in_(sub_vip))

        ua_text = cast(UserORM.ua_tags, String)
        ads_like = ua_text.ilike("%ua:source:ads%")
        attr_like = ua_text.ilike("%ua:campaign:%") | ua_text.ilike("%ua:gclid:%") | ua_text.ilike("%ua:adgroup:%")
        acq = (acquisition or "").strip().lower()
        if acq in ("attributed", "tracked", "带广告"):
            query = query.filter(attr_like)
        elif acq in ("ads", "广告户"):
            query = query.filter(ads_like, ~attr_like)
        elif acq in ("none", "empty", "无"):
            query = query.filter(~ads_like, ~attr_like)

        ads_flag = (is_ads or "").strip().lower()
        if ads_flag in ("1", "true", "yes", "y"):
            query = query.filter(ads_like | attr_like)
        elif ads_flag in ("0", "false", "no", "n"):
            query = query.filter(~ads_like, ~attr_like)

        total = (
            query.order_by(None)
            .with_entities(func.count(func.distinct(UserORM.id)))
            .scalar()
            or 0
        )
        # with_entities 会改写 SELECT；取列表前恢复为 UserORM 实体
        users = (
            query.with_entities(UserORM)
            .order_by(UserORM.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        uid_list = [u.id for u in users]
        credit_map = {}
        if uid_list:
            for uc in db.query(UserCreditORM).filter(UserCreditORM.user_id.in_(uid_list)).all():
                credit_map[uc.user_id] = uc

        free_limit = int(get_cost_config().get("free_turns_limit") or 3)
        items = [
            _admin_user_payload(
                u,
                credits=credit_map.get(u.id),
                free_turns_limit=free_limit,
                effective_balance_fn=get_effective_balance,
                is_vip_active_fn=is_vip_active,
            )
            for u in users
        ]
        return paginated(items=items, total=int(total), page=page, page_size=page_size)


@router.post("/api/admin/knowledge/search", summary="搜索知识库")
async def admin_search_knowledge(data: dict, _token: str = Depends(admin_auth_required)):
    query = data.get("query", "")
    top_k = data.get("top_k", 10)
    if not query:
        return {"results": []}
    results = knowledge_base.search_entries(query, top_k=top_k)
    return {"results": results}


@router.post("/api/admin/knowledge/reindex", summary="重建知识库向量索引")
async def admin_reindex_knowledge(_token: str = Depends(admin_auth_required)):
    removed = knowledge_base.dedupe_by_title()
    stats = knowledge_base.reindex_vectors()
    stats["deduped"] = removed
    return ok(data=stats)


@router.get("/api/admin/embedding-status", summary="获取嵌入状态")
async def admin_embedding_status(_token: str = Depends(admin_auth_required)):
    return get_embedding_status()



@router.get("/api/admin/feedback", summary="获取反馈列表")
async def admin_list_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    keyword: Optional[str] = Query(None, description="用户昵称/用户名/展示名模糊"),
    status: Optional[str] = Query(None, description="open / replied / closed"),
    user_id: Optional[int] = Query(None),
    has_unread: Optional[bool] = Query(None, description="true=仅有后台未读"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from core.i18n import localize_feedback_auto_reply
    from services.feedback import admin_list_threads

    with get_db() as db:
        items, total = admin_list_threads(
            db,
            page=page,
            page_size=page_size,
            keyword=keyword,
            status=status,
            user_id=user_id,
            has_unread=has_unread,
        )
        for it in items:
            it["last_message"] = localize_feedback_auto_reply(it.get("last_message") or "", lang=lang)
        return paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/api/admin/feedback/{thread_id}/messages", summary="获取反馈详情")
async def admin_get_feedback_messages(
    thread_id: int,
    since_id: Optional[int] = Query(None),
    mark_read: bool = Query(True, description="打开会话时清后台未读"),
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.feedback import (
        admin_unread_count,
        list_messages,
        mark_admin_read,
    )

    with get_db() as db:
        thread = db.query(FeedbackThreadORM).filter(
            FeedbackThreadORM.id == thread_id
        ).first()
        if not thread:
            raise HTTPException(status_code=404, detail=_get_error_msg("feedback_not_found", lang))
        if mark_read:
            mark_admin_read(thread)
        user = db.query(UserORM).filter(UserORM.id == thread.user_id).first() if thread.user_id else None
        return {
            "thread": {
                "id": thread.id,
                "user_id": thread.user_id,
                "user_name": thread.user_name,
                "username": (user.username if user else "") or "",
                "nickname": (user.nickname if user else "") or "",
                "language": (user.language if user else "") or "",
                "status": thread.status,
                "unread_count": 0 if mark_read else admin_unread_count(db, thread),
            },
            "messages": list_messages(db, thread, lang=lang, since_id=since_id),
        }


@router.post("/api/admin/feedback/{thread_id}/reply", summary="回复反馈")
async def admin_reply_feedback(
    thread_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.feedback import send_admin_reply, serialize_message

    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail=_get_error_msg("empty_content", lang))

    with get_db() as db:
        thread = db.query(FeedbackThreadORM).filter(
            FeedbackThreadORM.id == thread_id
        ).first()
        if not thread:
            raise HTTPException(status_code=404, detail=_get_error_msg("feedback_not_found", lang))
        msg = send_admin_reply(db, thread, content)
        return {"ok": True, "message": serialize_message(msg, lang=lang)}


@router.post("/api/admin/feedback/{thread_id}/read", summary="清零后台未读")
async def admin_read_feedback(
    thread_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from services.feedback import mark_admin_read

    with get_db() as db:
        thread = db.query(FeedbackThreadORM).filter(
            FeedbackThreadORM.id == thread_id
        ).first()
        if not thread:
            raise HTTPException(status_code=404, detail=_get_error_msg("feedback_not_found", lang))
        mark_admin_read(thread)
        return {"ok": True, "unread_count": 0}


# ===== Companions: Create =====

@router.post("/api/admin/companions", summary="创建伴侣")
async def admin_create_companion(data: dict, _token: str = Depends(admin_auth_required)):
    from pydantic import ValidationError
    from services.companion_manager import clamp_companion_profile_dict

    try:
        # 空人设字段先补默认占位，再进 Pydantic
        payload = clamp_companion_profile_dict(dict(data or {}))
        companion = get_companion_manager().create(payload)
        # 启动后台异步生成头像
        if companion.profile.avatar_url == "__GENERATING__":
            start_avatar_generation(companion.profile.id, companion.profile.model_dump())
        return companion.to_dict()
    except ValidationError as e:
        field_labels = {
            "name": "姓名/name",
            "age": "年龄/age",
            "city": "城市/city",
            "personality": "性格/personality",
            "background": "背景/background",
            "speech_style": "说话风格/speech_style",
            "gender": "性别/gender",
        }
        parts = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            label = field_labels.get(loc, loc) or "field"
            msg = err.get("msg", "invalid")
            parts.append(f"{label}: {msg}")
        detail = "; ".join(parts) if parts else str(e)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===== Users: Create / Update / Delete =====

_ALLOWED_USER_GENDER = {"male", "female", "non-binary", "confidential"}
_ALLOWED_PREFERRED_GENDER = {"male", "female", "non-binary", "confidential"}
_ALLOWED_CHAT_PURPOSE = {"chatting", "companionship", "romance", "communication"}


def _opt_enum(value, allowed: set) -> str:
    v = (value or "").strip()
    return v if v in allowed else ""


def _user_gender_value(raw) -> str:
    g = canonical_user_gender(raw)
    return g if g in _ALLOWED_USER_GENDER else ""


def _user_gender_filter_values(raw) -> list:
    """性别筛选：兼容库内 male/女 等别名，避免精确等值漏筛。"""
    g = _user_gender_value(raw)
    if not g:
        return []
    aliases = {
        "male": ("male", "男", "m", "man"),
        "female": ("female", "女", "f", "woman"),
        "non-binary": ("non-binary", "nonbinary", "非二元"),
        "confidential": ("confidential", "secret", "other", "unknown", "保密"),
    }
    return list(dict.fromkeys(aliases.get(g, (g,))))


def _orientation_filter_values(raw) -> list:
    """性取向筛选：兼容中英文落库值。"""
    so = canonical_orientation(raw)
    if not so:
        return []
    zh = {
        "heterosexual": "异性恋",
        "homosexual": "同性恋",
        "bisexual": "双性恋",
        "pansexual": "泛性恋",
        "asexual": "无性恋",
        "secret": "保密",
    }
    vals = [so]
    if so in zh:
        vals.append(zh[so])
    if so == "secret":
        vals.append("confidential")
    return list(dict.fromkeys(vals))


def _user_pref_gender_value(raw) -> str:
    g = canonical_preferred_gender(raw)
    return g if g in _ALLOWED_PREFERRED_GENDER else ""


def _load_user_credits(db, user_id: int, *, create_if_missing: bool = False):
    """读取 user_credits；可选创建空记录，保证管理端字段始终有值。"""
    uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
    if uc is None and create_if_missing:
        from services.credit_service import get_or_create_user_credits

        uc = get_or_create_user_credits(db, user_id)
    return uc


def _admin_user_acquisition(ua_tags) -> dict:
    from services.ad_attribution import acquisition_from_ua_tags

    return acquisition_from_ua_tags(ua_tags)


def _apply_admin_user_ua_tags(user: UserORM, raw) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    from services.ad_attribution import normalize_admin_ua_tags

    user.ua_tags = normalize_admin_ua_tags(raw)
    flag_modified(user, "ua_tags")


def _admin_user_payload(
    user: UserORM,
    credits=None,
    *,
    free_turns_limit: Optional[int] = None,
    effective_balance_fn=None,
    is_vip_active_fn=None,
) -> dict:
    """管理端用户载荷：原有字段保留，并补充 VIP / 积分 / free 消息 / 状态。

    credits=None（无积分行）时：按非 VIP、积分为 0、free 消息=额度上限返回，避免列表缺字段。
    """
    from services.credit_service import get_cost_config, get_effective_balance, is_vip_active

    status = (getattr(user, "account_status", None) or "active").strip() or "active"
    if status not in ("active", "banned", "deleted"):
        status = "active"

    if free_turns_limit is None:
        free_turns_limit = int(get_cost_config().get("free_turns_limit") or 3)
    is_vip_active_fn = is_vip_active_fn or is_vip_active
    effective_balance_fn = effective_balance_fn or get_effective_balance

    is_vip = False
    vip_type = ""
    vip_expire_at = ""
    credits_balance = 0
    free_turns_remaining = int(free_turns_limit)
    daily_gift_remaining = 0

    if credits is not None:
        is_vip = bool(is_vip_active_fn(credits))
        vip_type = (credits.vip_type or "") if is_vip else ""
        if is_vip and vip_type != "lifetime" and credits.vip_expire_at:
            vip_expire_at = serialize_datetime(credits.vip_expire_at) or ""
        credits_balance = int(effective_balance_fn(credits) or 0)
        daily_gift_remaining = int(credits.daily_gift_remaining or 0)
        used = max(0, int(getattr(credits, "free_turns_used", 0) or 0))
        free_turns_remaining = max(0, int(free_turns_limit) - used)

    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "gender": _user_gender_value(user.gender),
        "sexual_orientation": canonical_orientation(user.sexual_orientation),
        "age": getattr(user, "age", None),
        "region": str(getattr(user, "region", None) or ""),
        "occupation": str(getattr(user, "occupation", None) or ""),
        "profile_backgrounds": list(getattr(user, "profile_backgrounds", None) or []),
        "preferred_gender": _user_pref_gender_value(user.preferred_gender),
        "chat_purpose": (user.chat_purpose or "") if getattr(user, "chat_purpose", None) is not None else "",
        "tags": list(getattr(user, "tags", None) or []),
        "ua_tags": list(getattr(user, "ua_tags", None) or []),
        **_admin_user_acquisition(getattr(user, "ua_tags", None)),
        "created_at": serialize_datetime(user.created_at),
        # 需求文档字段（只增不改；管理端列表/详情直接用）
        "is_vip": is_vip,
        "vip": is_vip,
        "vip_type": vip_type,
        "vip_expire_at": vip_expire_at,  # 非 VIP / 终身：空字符串，前端可展示 ——
        "credits": credits_balance,
        "daily_gift_remaining": daily_gift_remaining,
        "free_turns_remaining": free_turns_remaining,
        "free_messages_remaining": free_turns_remaining,
        "account_status": status,
        "status": status,
        "status_label": {
            "active": "正常",
            "banned": "封禁",
            "deleted": "注销",
            "closed": "已销户",
        }.get(status, status),
        "banned": status == "banned",
        "deleted": status == "deleted",
        "closed": status == "closed",
        "deleted_at": serialize_datetime(getattr(user, "deleted_at", None)),
    }


@router.get("/api/admin/users/{user_id}/ban-status", summary="查询用户封禁状态")
async def admin_get_ban_status(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        status = (getattr(user, "account_status", None) or "active").strip() or "active"
        if status not in ("active", "banned", "deleted", "closed"):
            status = "active"
        return {
            "user_id": user.id,
            "account_status": status,
            "status": status,
            "status_label": {
                "active": "正常",
                "banned": "封禁",
                "deleted": "注销",
                "closed": "已销户",
            }.get(status, status),
            "banned": status == "banned",
            "deleted": status == "deleted",
            "closed": status == "closed",
            "deleted_at": serialize_datetime(getattr(user, "deleted_at", None)),
        }


@router.put("/api/admin/users/{user_id}/ban-status", summary="封禁/解封用户")
async def admin_set_ban_status(
    user_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """封禁开关。body：{"banned": true|false} 或 {"status":"banned"|"active"}。
    已注销用户不可通过本接口解封；完整三态请用 PUT .../account-status。
    """
    banned: Optional[bool] = None
    if "banned" in data:
        banned = bool(data.get("banned"))
    else:
        st = str(data.get("status") or data.get("account_status") or "").strip().lower()
        if st in ("banned", "ban"):
            banned = True
        elif st == "active":
            banned = False
        elif st in ("deleted", "cancelled", "canceled"):
            raise HTTPException(
                status_code=400,
                detail="use PUT /api/admin/users/{id}/account-status for deleted",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="banned(bool) or status=active|banned required",
            )
    return await _set_user_ban_status(user_id, banned=banned, lang=lang)


@router.put("/api/admin/users/{user_id}/account-status", summary="设置用户账号状态")
async def admin_set_account_status(
    user_id: int,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """四态：active=正常 / banned=封禁 / deleted=注销冷静期 / closed=已销户。
    active 可用于解除注销（冷静期内）；closed 为最终销户。
    """
    from core.auth import apply_account_status_flags, normalize_account_status
    from services.account_lifecycle import (
        finalize_account_closure,
        restore_account_from_deletion,
        schedule_account_deletion,
    )

    raw = str(data.get("status") or data.get("account_status") or "").strip()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="status=active|banned|deleted|closed required",
        )
    mapping = {
        "正常": "active",
        "封禁": "banned",
        "注销": "deleted",
        "已销户": "closed",
        "ban": "banned",
    }
    st = normalize_account_status(mapping.get(raw, raw))
    if st not in ("active", "banned", "deleted", "closed"):
        raise HTTPException(
            status_code=400,
            detail="status=active|banned|deleted|closed required",
        )

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        cur = normalize_account_status(getattr(user, "account_status", None))
        if cur == "closed" and st != "closed":
            raise HTTPException(status_code=400, detail="cannot_reopen_closed_account")

        if st == "active":
            if cur == "deleted":
                restore_account_from_deletion(db, user)
            else:
                user.account_status = "active"
                user.deleted_at = None
        elif st == "deleted":
            schedule_account_deletion(db, user)
        elif st == "closed":
            finalize_account_closure(db, user)
        else:  # banned
            user.account_status = "banned"
            user.token = ""
            user.token_expire = None

        db.commit()
        db.refresh(user)
        apply_account_status_flags(user_id, st)
        uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
        return _admin_user_payload(user, credits=uc)


@router.post(
    "/api/admin/users/{user_id}/restore-account",
    summary="解除注销",
    description="冷静期内将 deleted 恢复为 active（类似解封）",
)
async def admin_restore_account(
    user_id: int,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    from core.auth import apply_account_status_flags, normalize_account_status
    from services.account_lifecycle import is_grace_expired, restore_account_from_deletion

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        st = normalize_account_status(getattr(user, "account_status", None))
        if st == "closed":
            raise HTTPException(status_code=400, detail="account_already_closed")
        if st != "deleted":
            raise HTTPException(status_code=400, detail="account_not_in_deletion")
        if is_grace_expired(user):
            raise HTTPException(status_code=400, detail="deletion_grace_expired")
        restore_account_from_deletion(db, user)
        db.commit()
        db.refresh(user)
        apply_account_status_flags(user_id, "active")
        uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
        return _admin_user_payload(user, credits=uc)


async def _set_user_ban_status(user_id: int, *, banned: bool, lang: str):
    from core.auth import apply_account_status_flags, normalize_account_status

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        cur = normalize_account_status(getattr(user, "account_status", None))
        if cur in ("deleted", "closed"):
            raise HTTPException(status_code=400, detail="cannot_change_deleted_user")
        if banned:
            user.account_status = "banned"
            user.token = ""
            db.commit()
            db.refresh(user)
            apply_account_status_flags(user_id, "banned")
        else:
            user.account_status = "active"
            db.commit()
            db.refresh(user)
            apply_account_status_flags(user_id, "active")
        uc = db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).first()
        payload = _admin_user_payload(user, credits=uc)
        return payload


@router.post("/api/admin/users", summary="创建用户")
async def admin_create_user(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    nickname = (data.get("nickname") or "").strip()[:32]
    gender = data.get("gender") or ""
    sexual_orientation = data.get("sexual_orientation") or ""
    age_raw = data.get("age")
    age = None
    if age_raw is not None and age_raw != "":
        try:
            ai = int(age_raw)
            if 0 <= ai <= 150:
                age = ai
        except (TypeError, ValueError):
            pass
    region = (data.get("region") or "").strip()[:120] if isinstance(data.get("region"), str) else ""
    occupation = (data.get("occupation") or "").strip()[:100] if isinstance(data.get("occupation"), str) else ""
    preferred_gender = _user_pref_gender_value(data.get("preferred_gender"))
    chat_purpose = _opt_enum(data.get("chat_purpose"), _ALLOWED_CHAT_PURPOSE)

    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail=_get_error_msg("username_too_short", lang))
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail=_get_error_msg("password_too_short", lang))

    with get_db() as db:
        existing = db.query(UserORM).filter(UserORM.username == username).first()
        if existing:
            raise HTTPException(status_code=400, detail=_get_error_msg("username_exists", lang))

        user = UserORM(
            username=username,
            nickname=(nickname or username)[:32],
            password_hash=_hash(password),
            gender=_user_gender_value(gender),
            sexual_orientation=canonical_orientation(sexual_orientation),
            age=age,
            region=region,
            occupation=occupation,
            preferred_gender=preferred_gender,
            chat_purpose=chat_purpose,
            language="en",
        )
        if "ua_tags" in data:
            try:
                _apply_admin_user_ua_tags(user, data.get("ua_tags"))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        db.add(user)
        db.commit()
        db.refresh(user)
        uc = _load_user_credits(db, user.id, create_if_missing=True)
        return _admin_user_payload(user, credits=uc)


@router.get("/api/admin/users/{user_id}", summary="获取用户详情")
async def admin_get_user(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        uc = _load_user_credits(db, user_id, create_if_missing=False)
        return _admin_user_payload(user, credits=uc)


@router.put("/api/admin/users/{user_id}", summary="更新用户信息")
async def admin_update_user(user_id: int, data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))

        if "nickname" in data:
            user.nickname = (data["nickname"] or "").strip()[:32]
        if "gender" in data:
            user.gender = _user_gender_value(data.get("gender"))
        if "sexual_orientation" in data:
            user.sexual_orientation = canonical_orientation(data.get("sexual_orientation"))
        if "age" in data:
            a = data.get("age")
            if a is None or a == "":
                user.age = None
            else:
                try:
                    ai = int(a)
                    user.age = ai if 0 <= ai <= 150 else None
                except (TypeError, ValueError):
                    pass
        if "region" in data and isinstance(data["region"], str):
            user.region = data["region"].strip()[:120]
        if "occupation" in data and isinstance(data["occupation"], str):
            user.occupation = data["occupation"].strip()[:100]
        if "preferred_gender" in data:
            user.preferred_gender = _user_pref_gender_value(data.get("preferred_gender"))
        if "chat_purpose" in data:
            user.chat_purpose = _opt_enum(data.get("chat_purpose"), _ALLOWED_CHAT_PURPOSE)
        if "tags" in data:
            from sqlalchemy.orm.attributes import flag_modified
            from services.recommend_tags import normalize_tag_list
            from services.recommend import load_tag_defs
            tags = normalize_tag_list(data.get("tags"))
            defs = load_tag_defs()
            for key in tags:
                if (key, "user") not in defs:
                    raise HTTPException(status_code=400, detail=f"unknown_user_tag:{key}")
            user.tags = tags
            flag_modified(user, "tags")
        if "ua_tags" in data:
            try:
                _apply_admin_user_ua_tags(user, data.get("ua_tags"))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        if "password" in data and data["password"]:
            user.password_hash = _hash(data["password"])

        uc = _load_user_credits(db, user_id, create_if_missing=False)
        return _admin_user_payload(user, credits=uc)


@router.get("/api/admin/users/{user_id}/companion-stats", summary="获取用户伴侣统计")
async def admin_user_companion_stats(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """列出该用户与各智能体的亲密度记录（含管理员手动写入）。"""
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        rows = (
            db.query(UserCompanionStateORM)
            .filter(UserCompanionStateORM.user_id == user_id)
            .order_by(UserCompanionStateORM.updated_at.desc())
            .all()
        )
        out = []
        for uc in rows:
            c = db.query(CompanionORM).filter(CompanionORM.id == uc.companion_id).first()
            avatar = ""
            name = uc.companion_id
            if c:
                name = c.name or uc.companion_id
                avatar = (c.avatar_url or "").strip()
            out.append(
                {
                    "companion_id": uc.companion_id,
                    "companion_name": name,
                    "avatar_url": avatar,
                    "affection": uc.affection if uc.affection is not None else 0,
                    "turns": uc.turns if uc.turns is not None else 0,
                    "updated_at": serialize_datetime(uc.updated_at),
                }
            )
        return {"items": out}


@router.put("/api/admin/users/{user_id}/companion-stats/{companion_id}", summary="更新用户伴侣统计")
async def admin_put_user_companion_stat(
    user_id: int,
    companion_id: str,
    data: dict,
    _token: str = Depends(admin_auth_required),
    lang: str = Depends(get_admin_lang),
):
    """新建或更新某用户与指定智能体的亲密度（便于运营手动修正）。"""
    affection = data.get("affection")
    turns = data.get("turns")
    if affection is None:
        raise HTTPException(status_code=400, detail=_get_error_msg("affection_required", lang))
    try:
        af = float(affection)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=_get_error_msg("invalid_affection", lang))
    if af < 0 or af > 100:
        raise HTTPException(status_code=400, detail=_get_error_msg("invalid_affection", lang))

    turns_val = None
    if turns is not None:
        try:
            turns_val = int(turns)
        except (TypeError, ValueError):
            turns_val = 0
        if turns_val < 0:
            turns_val = 0

    companion_id = (companion_id or "").strip()
    if len(companion_id) > 8:
        companion_id = companion_id[:8]

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))

        now = datetime.now(timezone.utc)
        row = (
            db.query(UserCompanionStateORM)
            .filter(
                UserCompanionStateORM.user_id == user_id,
                UserCompanionStateORM.companion_id == companion_id,
            )
            .first()
        )
        c = db.query(CompanionORM).filter(CompanionORM.id == companion_id).first()
        # 新建关联必须在 companions 中存在；已有 user_companion_states 行时允许修正孤儿数据（智能体已从库删除但统计行仍在）
        if not row and not c:
            raise HTTPException(status_code=404, detail=_get_error_msg("companion_not_found", lang))
        if row:
            row.affection = af
            if turns_val is not None:
                row.turns = turns_val
            row.updated_at = now
        else:
            db.add(
                UserCompanionStateORM(
                    user_id=user_id,
                    companion_id=companion_id,
                    affection=af,
                    turns=turns_val if turns_val is not None else 0,
                    updated_at=now,
                )
            )

    return {"ok": True, "companion_id": companion_id, "affection": af}


@router.delete("/api/admin/users/{user_id}", summary="删除用户")
async def admin_delete_user(user_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    from core.auth import revoke_user_tokens, set_user_banned_flag

    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=_get_error_msg("user_not_found", lang))
        db.query(UserCompanionStateORM).filter(UserCompanionStateORM.user_id == user_id).delete(
            synchronize_session=False
        )
        db.query(UserCreditORM).filter(UserCreditORM.user_id == user_id).delete(
            synchronize_session=False
        )
        db.delete(user)
    set_user_banned_flag(user_id, False)
    revoke_user_tokens(user_id)
    return {"ok": True}


# ===== Feedback: Create & Delete =====

@router.post("/api/admin/feedback", summary="创建反馈会话")
async def admin_create_feedback(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """管理员创建反馈线程（修复前端新建功能）"""
    user_name = (data.get("user_name") or "管理员录入").strip()
    content = (data.get("content") or data.get("initial_message", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail=_get_error_msg("empty_content", lang))

    with get_db() as db:
        thread = FeedbackThreadORM(
            user_id=data.get("user_id", 0),
            user_name=user_name,
            status="open"
        )
        db.add(thread)
        db.flush()  # 获取 ID

        msg = FeedbackMessageORM(
            thread_id=thread.id,
            sender="admin",  # 或 "system"
            content=content,
        )
        db.add(msg)

    return {"ok": True, "id": thread.id, "thread": {"id": thread.id, "user_name": user_name, "status": "open"}}


@router.delete("/api/admin/feedback/{thread_id}", summary="删除反馈会话")
async def admin_delete_feedback(thread_id: int, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    with get_db() as db:
        thread = db.query(FeedbackThreadORM).filter(FeedbackThreadORM.id == thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail=_get_error_msg("feedback_not_found", lang))
        # 先删除关联消息
        db.query(FeedbackMessageORM).filter(FeedbackMessageORM.thread_id == thread_id).delete(synchronize_session=False)
        db.delete(thread)
    return {"ok": True}


# ===== Knowledge: Update =====

@router.put("/api/admin/knowledge/{entry_id}", summary="更新知识条目")
async def admin_update_knowledge(entry_id: str, data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    entry = knowledge_base.update_entry(entry_id, data)
    if not entry:
        raise HTTPException(status_code=404, detail=_get_error_msg("entry_not_found", lang))
    return entry.model_dump()


# ===== Image Generation =====

@router.post("/api/admin/generate-image", summary="生成图片")
async def admin_generate_image(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """管理端生图：前端传 aspect_ratio + image_size，后端原样交给 Gemini。"""
    from services.image_generation import (
        GEMINI_ASPECT_RATIOS,
        GEMINI_IMAGE_SIZES,
        ImageGenerationError,
        generate_image,
    )

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail=_get_error_msg("prompt_required", lang))

    aspect_ratio = (data.get("aspect_ratio") or "").strip()
    image_size = (data.get("image_size") or "").strip().upper()
    if aspect_ratio not in GEMINI_ASPECT_RATIOS:
        raise HTTPException(status_code=400, detail=_get_error_msg("invalid_aspect_ratio", lang))
    if image_size not in GEMINI_IMAGE_SIZES:
        raise HTTPException(status_code=400, detail=_get_error_msg("invalid_image_size", lang))

    style = data.get("style") or ""
    full_prompt = f"{prompt}. {style}" if style else prompt
    try:
        image_url = generate_image(
            prompt=full_prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            seed=data.get("seed"),
            nologo=data.get("nologo", True),
            model=data.get("model") or "",
        )
    except ImageGenerationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{_get_error_msg('image_generation_failed', lang)}: {e}",
        )
    if not image_url:
        raise HTTPException(status_code=502, detail=_get_error_msg("image_generation_failed", lang))
    return {
        "ok": True,
        "image_url": image_url,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
    }

@router.post("/api/admin/users/batch-delete", summary="批量删除用户")
async def admin_batch_delete_users(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """批量删除用户"""
    from core.auth import revoke_user_tokens, set_user_banned_flag

    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail=_("ids 必须是非空整数列表"))
    with get_db() as db:
        int_ids = [int(i) for i in ids if str(i).isdigit()]
        if not int_ids:
            raise HTTPException(status_code=400, detail=_("无效的ID列表"))
        db.query(UserCompanionStateORM).filter(UserCompanionStateORM.user_id.in_(int_ids)).delete(
            synchronize_session=False
        )
        db.query(UserCreditORM).filter(UserCreditORM.user_id.in_(int_ids)).delete(
            synchronize_session=False
        )
        deleted = db.query(UserORM).filter(UserORM.id.in_(int_ids)).delete(synchronize_session=False)
        db.commit()
    for uid in int_ids:
        set_user_banned_flag(uid, False)
        revoke_user_tokens(uid)
    return {"ok": True, "deleted": deleted, "total": len(int_ids)}


@router.post("/api/admin/knowledge/batch-delete", summary="批量删除知识条目")
async def admin_batch_delete_knowledge(data: dict, _token: str = Depends(admin_auth_required), lang: str = Depends(get_admin_lang)):
    """批量删除知识条目"""
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail=_("ids 必须是非空列表"))
    from services.knowledge_base import knowledge_base
    deleted = 0
    for entry_id in ids:
        if knowledge_base.delete_entry(str(entry_id)):
            deleted += 1
    return {"ok": True, "deleted": deleted, "total": len(ids)}

import api.admin_ops  # register extra routes on shared router
import api.admin_config  # register config-group routes on shared router
import api.admin_sessions  # register chat-session routes on shared router
import api.admin_moments  # register moments routes on shared router
import api.admin_media  # register media proxy (GCS/COS crop) on shared router
import api.admin_batch  # register batch-generate routes on shared router
import api.admin_recommend  # register recommend strategy routes on shared router
import api.admin_attribution  # register attribution event routes on shared router
import api.admin_data_stats  # register data-stats routes on shared router
import api.admin_content  # register content-management routes on shared router
import api.admin_msg_strategy  # 【埋点消息策略】globals/rules/library
import api.admin_companion_strategies  # 【单体消息策略】companion_strategies 跨智能体 CRUD
import api.admin_quick_reply  # 【快速回复消息策略】library/configs
import api.admin_chat_version_prompts  # 客户端版本号聊天兜底提示词
import api.admin_say_hi  # Say Hi 打招呼文案库
import api.admin_strategy_center  # 【智能体消息策略中心】按智能体拆行 CRUD
import api.admin_pending_orders  # register pending-orders CRUD routes on shared router
import api.admin_api_logs  # register api access log list/download routes on shared router
from api.admin_batch import _batch_generate_all_langs_core  # noqa: F401  scripts/batch_create_all_langs.py
