from typing import Optional, Type
from fastapi import Depends, HTTPException, Request

from core.auth import verify_token
from core.permissions import BasePermission


async def get_current_user(request: Request) -> int:
    """获取当前登录用户 ID，未登录则抛出 401"""
    user_id = request.state.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    return user_id


async def get_optional_user(request: Request) -> Optional[int]:
    """获取当前用户 ID，允许未登录"""
    return request.state.user_id


def require_permissions(*permissions: Type[BasePermission]):
    """
    权限依赖工厂

    使用方式：
        @router.get("/companions")
        async def list_companions(user_id: int = Depends(require_permissions(IsAuthenticated))):
            ...

        @router.delete("/companions/{companion_id}")
        async def delete_companion(user_id: int = Depends(require_permissions(IsOwner))):
            ...
    """

    async def permission_checker(request: Request) -> int:
        user_id = request.state.user_id

        for perm_class in permissions:
            perm = perm_class()
            if not perm.has_permission(request, user_id):
                raise HTTPException(
                    status_code=403,
                    detail=f"权限不足: {perm_class.__name__}"
                )

        if not user_id:
            raise HTTPException(status_code=401, detail="请先登录")

        return user_id

    return permission_checker
