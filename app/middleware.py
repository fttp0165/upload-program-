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
        # 🔴 CSP:全站零 inline script / inline style。
        # T40 導入的時機是刻意的——當時全站零 JS、CSS 也正要搬進 static/,
        # 是成本最低的一刻;等寫了上傳 JS 再補就得回頭改一輪。
        # `default-src 'self'` 同時涵蓋 script-src 與 style-src,所以內嵌 <style>/<script>
        # 一律被擋:這正好把樣式逼進外部檔案(T44 的上傳 JS 同理)。
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        return response


class SessionRenewalMiddleware(BaseHTTPMiddleware):
    """把自動續期換到的新 session 寫回 cookie(T52)。

    為什麼需要中介層:續期發生在**相依注入**階段,那時還沒有 response 物件可以設 cookie。
    中介層是唯一同時看得到「請求開始」與「回應完成」的地方。

    不用 `BackgroundTask`——它在回應**送出之後**才跑,設 cookie 已經來不及。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        renewed = getattr(request.state, "renewed_session", None)
        if renewed is not None:
            # 走 CookieCodec 的公開介面,確保 HttpOnly / Secure / SameSite / Path
            # 這些同源義務(契約 §4.10)不會因為換一條路徑就鬆掉。
            request.app.state.cookies.set_session(response, renewed)
        return response
