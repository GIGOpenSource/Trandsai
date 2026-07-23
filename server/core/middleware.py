import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth import verify_token

logger = logging.getLogger(__name__)

# 不需要认证的路径
WHITE_LIST = [
    "/api/auth/login",
    "/api/auth/register",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/admin/login",
]


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """全局 Token 认证中间件"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 白名单放行
        if any(path.startswith(wh) for wh in WHITE_LIST):
            request.state.user_id = None
            return await call_next(request)

        # 提取 Token（从 Header 或 Query）
        token = request.headers.get("x-token") or request.query_params.get("token")

        # 验证 Token
        user_id = verify_token(token) if token else None

        # 调试日志
        if token:
            logger.info("[Middleware] path=%s token=%s... user_id=%s", path, token[:8], user_id)

        # 存入 request.state
        request.state.user_id = user_id

        response = await call_next(request)
        return response