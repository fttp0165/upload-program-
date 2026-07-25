"""追蹤與存取 log。

平台規約:收到 `X-Trace-Id` 就沿用,沒有就產生 UUID v4,並帶進所有 log 與下游呼叫。
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging_setup import trace_id_var, user_id_var

log = logging.getLogger("access")

TRACE_HEADER = "X-Trace-Id"


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get(TRACE_HEADER) or str(uuid.uuid4())
        trace_id_var.set(trace_id)
        user_id_var.set("")
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise

        response.headers[TRACE_HEADER] = trace_id
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,  # 只記路徑,不記 query(可能含 token)
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """本服務散布可執行檔,瀏覽器端的保護要一律加上。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
