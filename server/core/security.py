from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
security = HTTPBearer(auto_error=False)

async def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[int]:
    """
    从请求头获取 Token 并验证

    返回 user_id 或 None（未认证）
    """
    if not credentials:
        return None

    token = credentials.credentials
    from core.auth import verify_token
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


async def require_auth(
        user_id: Optional[int] = Depends(get_current_user)
) -> int:
    """要求必须认证"""
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


#️⃣在路由中使用认证

from core.security import get_current_user, require_auth


@router.get("/me", summary="获取当前用户信息")
async def get_me(user_id: int = Depends(require_auth)):
    """需要登录才能访问"""
    pass


@router.get("/public", summary="公开接口")
async def public_endpoint(user_id: Optional[int] = Depends(get_current_user)):
    """可选认证，未登录也能访问"""
    pass