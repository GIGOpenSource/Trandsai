from typing import Optional

from fastapi import Header, HTTPException, Request

from core.auth import verify_token as verify_user_token
from core.database import UserORM, get_db
from core.i18n import (
    auth_required_message,
    companion_forbidden_message,
    companion_not_found_message,
)


async def require_login_user(
    request: Request,
    x_token: Optional[str] = Header(None, alias="x-token"),
) -> int:
    """REST 接口：必须携带有效用户 Token（与 WebSocket IM 一致）。"""
    uid = verify_user_token(x_token) if x_token else None
    if uid is None:
        raise HTTPException(
            status_code=401,
            detail=auth_required_message(request.headers.get("accept-language")),
        )
    return uid


def _assert_companion_readable(companion, user_id: int) -> None:
    """登录用户可查看/聊天任意已存在智能体（业务：可见并互动别人创建的智能体）。"""
    if user_id is None:
        raise HTTPException(status_code=401, detail=auth_required_message())
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())


def _assert_companion_owner(companion, user_id: int) -> None:
    """仅创建者可执行删除/重生头像等写操作。
    归属匹配 user_id 或唯一 username；不用 nickname（可撞车导致误授权）。
    """
    if not companion:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
    cb = (companion.profile.created_by or "").strip()
    if not cb:
        raise HTTPException(status_code=403, detail=companion_forbidden_message())
    if cb == str(user_id):
        return
    with get_db() as db:
        user = db.query(UserORM).filter(UserORM.id == user_id).first()
        if user:
            uname = (user.username or "").strip()
            if uname and cb == uname:
                return
    raise HTTPException(status_code=403, detail=companion_forbidden_message())


# 兼容旧名：读路径语义改为「可读」
def _assert_companion_user_access(companion, user_id: int) -> None:
    _assert_companion_readable(companion, user_id)


def _assert_companion_visible_for_user(user_id: int, companion_id: str) -> None:
    """举报人隐藏 / 全网下架时，对 C 端表现为不存在。"""
    from services.companion_report import assert_companion_visible_to_user

    vis_err = assert_companion_visible_to_user(user_id, companion_id)
    if vis_err:
        raise HTTPException(status_code=404, detail=companion_not_found_message())
