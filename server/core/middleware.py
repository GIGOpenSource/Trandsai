"""Request timing middleware for API observability."""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api.timing")

SLOW_MS = int(__import__("os").getenv("API_SLOW_MS", "500"))


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(("/api/", "/companions")):
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"

        if elapsed_ms >= SLOW_MS:
            logger.warning(
                "Slow request %s %s %.0fms status=%s",
                request.method,
                request.url.path,
                elapsed_ms,
                response.status_code,
            )
        return response
