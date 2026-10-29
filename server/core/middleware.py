import logging
import os
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.api_file_log import client_ip, write_access
from core.auth import is_user_account_blocked, verify_admin_token, verify_token
from core.i18n import account_deleted_message, auth_required_message, bind_request_ui_lang, reset_ui_lang

logger = logging.getLogger(__name__)
_timing_logger = logging.getLogger("api.timing")
SLOW_MS = int(os.getenv("API_SLOW_MS", "500"))
# 设为 0/false 时关闭；未设置或 1 时：非白名单写操作必须登录
_raw_enforce = os.getenv("ENFORCE_AUTH_WRITES", "1").strip().lower()
ENFORCE_AUTH_WRITES = _raw_enforce not in ("0", "false", "no")

# 不需要认证的路径（前缀匹配）
WHITE_LIST = [
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/check-username",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/admin",  # 管理端自建鉴权（x-token / Bearer），不走用户 token 中间件
    "/ws/admin",  # 管理端 IM WebSocket，连接内校验 admin token
    "/api/auth/googlelogin",
    "/api/auth/applelogin",
    "/api/payment/apple-notification",
    "/api/payment/google-notification",
    "/health",
    "/api/health",
]

# 写操作放行（webhook / 公开读旁路）
WRITE_WHITE_LIST = [
    "/api/payment/apple-notification",
    "/api/payment/google-notification",
    "/api/admin",
    "/api/auth/",
    "/api/analytics/",
    "/api/growth/",
    "/knowledge/search",
    "/api/knowledge/search",
    "/api/recommend/audience-sync",
]


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """
    全局 Token 中间件：解析并挂载 request.state.user_id。
    默认对写方法强制登录（ENFORCE_AUTH_WRITES=1，可关）；白名单路径不解析用户 token。
    """

    async def dispatch(self, request: Request, call_next):
        lang_token = bind_request_ui_lang(
            accept_language=request.headers.get("accept-language"),
            lang=request.query_params.get("lang"),
        )
        try:
            path = request.url.path

            # 白名单：不解析 token
            if any(path.startswith(wh) for wh in WHITE_LIST):
                request.state.user_id = None
                # webhook 等仍可能自带 Authorization，但不在此校验用户 token
                if path.startswith("/api/payment/") and request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                    return await call_next(request)
                return await call_next(request)

            x_token = request.headers.get("x-token") or request.query_params.get("token")
            auth = request.headers.get("authorization") or ""
            bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            user_token = x_token or bearer
            user_id = verify_token(user_token) if user_token else None
            if user_id:
                block = is_user_account_blocked(user_id)
                if block == "banned":
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "account_banned", "message": "账号已封禁"},
                    )
                if block == "deleted":
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": "account_deleted",
                            "message": account_deleted_message(
                                request.headers.get("accept-language")
                            ),
                        },
                    )
                if block == "closed":
                    from core.i18n import account_closed_message

                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": "account_closed",
                            "message": account_closed_message(
                                request.headers.get("accept-language")
                            ),
                        },
                    )
            request.state.user_id = user_id
            if user_id:
                try:
                    from services.version_prompt import persist_app_version_from_request

                    persist_app_version_from_request(request, int(user_id))
                except Exception:
                    logger.debug("[Middleware] persist app_version skipped", exc_info=True)

            if ENFORCE_AUTH_WRITES and request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                # 管理端 token：x-token 与 Authorization Bearer 均可
                admin_ok = bool(
                    (bearer and verify_admin_token(bearer))
                    or (x_token and verify_admin_token(x_token))
                )
                if (
                    not any(path.startswith(wh) for wh in WRITE_WHITE_LIST)
                    and not user_id
                    and not admin_ok
                ):
                    return JSONResponse(
                        {
                            "detail": auth_required_message(
                                request.headers.get("accept-language")
                            )
                        },
                        status_code=401,
                    )

            if user_token:
                logger.debug("[Middleware] path=%s user_id=%s", path, user_id)

            return await call_next(request)
        finally:
            reset_ui_lang(lang_token)


class TimingMiddleware(BaseHTTPMiddleware):
    """Request timing middleware for API observability."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(("/api/", "/companions")):
            return await call_next(request)

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            elapsed_ms = (time.perf_counter() - start) * 1000
            response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"

            if elapsed_ms >= SLOW_MS:
                _timing_logger.warning(
                    "Slow request %s %s %.0fms status=%s",
                    request.method,
                    request.url.path,
                    elapsed_ms,
                    response.status_code,
                )
            return response
        finally:
            try:
                write_access(
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    ip=client_ip(request.headers, request.client.host if request.client else None),
                    uid=str(getattr(request.state, "user_id", None) or "-"),
                    query=request.url.query or "",
                )
            except Exception:
                pass
