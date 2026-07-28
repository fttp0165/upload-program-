"""錯誤回應:API 一律 RFC 7807 Problem Details(平台規約,禁自創格式);
瀏覽器導覽則依 `Accept` 協商成 HTML 錯誤頁(T47)。

RFC 7807 的義務只及於 API 回應,不禁止對瀏覽器回 HTML;而 F75 要求
「未開通者看到指引頁而非裸 403 JSON」,沒有這層協商就做不到。
"""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .logging_setup import trace_id_var
from .templating import render

CONTENT_TYPE = "application/problem+json"

# 🔴 這些路徑一律回 problem+json,不管呼叫端的 Accept 說什麼。
# 只看 Accept 的話,`curl -H 'Accept: text/html' /v1/projects` 就能把整個 API 表面的
# 錯誤格式改掉——平台鐵則「錯誤回應一律 RFC 7807」不該被呼叫端的一個標頭鬆動。
#
# 註:gateway 以尾斜線 proxy_pass **剝掉前綴**後轉發,所以到達本服務的路徑就是
# `/v1/...`,不含對外前綴;這裡直接比對 `/v1/` 是正確的,不需要拼 api_prefix。
_API_PREFIXES = ("/v1/",)
_MACHINE_PATHS = ("/health", "/ready")


def wants_html(request: Request) -> bool:
    """這個請求該不該收到 HTML 錯誤頁。

    **兩個條件都要成立**:

    1. 路徑不是機器端點(`/v1/*`、`/health`、`/ready`)
    2. `Accept` **明示**含 `text/html`

    第 2 點的「明示」是關鍵:`*/*`(fetch、XHR、curl 的預設)**不算**。
    瀏覽器導覽送的是 `text/html,application/xhtml+xml,...,*/*;q=0.8` 會命中;
    JS 呼叫送 `*/*` 不會命中——這正是「人在看」與「程式在呼叫」的分界。

    參數:request。回傳:True 表示回 HTML。副作用:無。
    """
    path = request.url.path
    if path.startswith(_API_PREFIXES) or path in _MACHINE_PATHS:
        return False
    return "text/html" in request.headers.get("accept", "")


class ProblemError(Exception):
    """業務錯誤;handler 會轉成 RFC 7807 回應。"""

    def __init__(
        self,
        status_code: int,
        slug: str,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.slug = slug
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.extra = extra


def _type_base(request: Request) -> str:
    """取 problem type 的網址前綴。

    🐛 根本原因(T50):原本這裡直接呼叫 `get_settings()`,等於**每產生一個錯誤回應就重讀環境變數**。
    後果是任何沒有完整 .env 的情境(測試、以自訂 Settings 建立的 app)一產生錯誤就先炸在這裡,
    真正的錯誤反而被蓋掉。改為優先取用 app 自己的設定,只有在拿不到時才回退。
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        return settings.problem_type_base
    return get_settings().problem_type_base


def problem_response(
    request: Request,
    status_code: int,
    slug: str,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> Response:
    """產生錯誤回應:API 回 problem+json,瀏覽器導覽回 HTML 錯誤頁。

    協商放在這一個函式裡的理由:四個 exception handler(ProblemError / HTTPException /
    RequestValidationError / 未處理例外)全都經過這裡,所以不可能有某一類錯誤漏掉。
    這與 T35(下載回應)、T37(下載計數)是同一個模式——**同一件事只能有一條路**。

    參數:request、status_code、slug(problem type 的尾段)、title、detail、
    headers(如 401 的 WWW-Authenticate)、extra(RFC 7807 擴充成員)。
    回傳:JSONResponse 或 HTMLResponse。副作用:無。
    """
    trace_id = trace_id_var.get()
    # `Vary: Accept` 一律要帶(含 JSON 回應):同一個 URL 有兩種表述,
    # 不標的話快取可能把 HTML 餵給 API 呼叫端,反之亦然。
    response_headers = {
        **(headers or {}),
        "Vary": "Accept",
        "X-Content-Type-Options": "nosniff",
    }

    if wants_html(request):
        # 用共用的 templating.render():錯誤頁與其他頁面必須是同一套版型與同一個
        # autoescape 設定,兩份 Environment 遲早會長歪。
        page = render(
            request,
            "error.html",
            status=status_code,
            title=title,
            detail=detail,
            instance=request.url.path,
            trace_id=trace_id,
        )
        return HTMLResponse(page, status_code=status_code, headers=response_headers)

    body = {
        "type": f"{_type_base(request)}/{slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "trace_id": trace_id,
        **extra,
    }
    return JSONResponse(
        body, status_code=status_code, media_type=CONTENT_TYPE, headers=response_headers
    )


# --- 常用錯誤 ---------------------------------------------------------------


def unauthorized(detail: str = "Token 無效或已過期") -> ProblemError:
    """401:token 本身有問題。不得與 403 混用(接入契約 §4.7)。"""
    return ProblemError(
        status.HTTP_401_UNAUTHORIZED,
        "unauthorized",
        "Unauthorized",
        detail,
        headers={"WWW-Authenticate": 'Bearer realm="upload-program"'},
    )


def pending_activation() -> ProblemError:
    """403:已認證但未開通。文案要指引去哪開通,不是冷冰冰的 Forbidden(接入契約 §4.3)。"""
    return ProblemError(
        status.HTTP_403_FORBIDDEN,
        "pending-activation",
        "帳號待開通",
        "你的帳號已建立但尚未開通。請聯絡 upload-program 平台管理員為你指派角色後再使用;"
        "你可以在「帳號設定」查看自己的帳號資料。",
    )


def forbidden(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_403_FORBIDDEN, "forbidden", "Forbidden", detail)


def not_found(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_404_NOT_FOUND, "not-found", "Not Found", detail)


def conflict(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_409_CONFLICT, "conflict", "Conflict", detail)


def payload_too_large(detail: str, **extra: Any) -> ProblemError:
    # 用數字字面值而非 status.HTTP_413_*:該常數在新版 Starlette 已改名並發出 DeprecationWarning,
    # 但我們的相依區間也允許舊版(舊版沒有新名字)。狀態碼本身不會變,直接寫數字最不受版本牽動。
    # `extra` 走 RFC 7807 擴充成員(T49 用來帶出級距、上限、已用量),前端不必 parse 中文句子。
    return ProblemError(413, "payload-too-large", "Content Too Large", detail, **extra)


def unprocessable(slug: str, title: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(422, slug, title, detail, **extra)


def bad_request(detail: str, slug: str = "bad-request") -> ProblemError:
    return ProblemError(status.HTTP_400_BAD_REQUEST, slug, "Bad Request", detail)


def service_unavailable(detail: str) -> ProblemError:
    return ProblemError(
        status.HTTP_503_SERVICE_UNAVAILABLE, "service-unavailable", "Service Unavailable", detail
    )


# --- exception handlers -----------------------------------------------------


async def problem_error_handler(request: Request, exc: ProblemError) -> Response:
    return problem_response(
        request, exc.status_code, exc.slug, exc.title, exc.detail, exc.headers, **exc.extra
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    return problem_response(
        request,
        exc.status_code,
        "http-error",
        exc.detail if isinstance(exc.detail, str) else "HTTP error",
        exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        dict(exc.headers or {}),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    return problem_response(
        request,
        422,  # 同上:避免綁定會改名的 Starlette 常數
        "validation",
        "Validation failed",
        "請求內容不符合規格,詳見 errors 欄位。",
        errors=[
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ],
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    # 內部錯誤不把 stack trace 吐給呼叫端;細節在 log 裡靠 trace_id 追。
    return problem_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal",
        "Internal Server Error",
        "服務發生非預期錯誤,請帶 trace_id 聯絡管理員。",
    )
