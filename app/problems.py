"""錯誤回應:一律 RFC 7807 Problem Details(平台規約,禁自創格式)。"""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .logging_setup import trace_id_var

CONTENT_TYPE = "application/problem+json"


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


def problem_response(
    request: Request,
    status_code: int,
    slug: str,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body = {
        "type": f"{get_settings().problem_type_base}/{slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "trace_id": trace_id_var.get(),
        **extra,
    }
    return JSONResponse(body, status_code=status_code, media_type=CONTENT_TYPE, headers=headers)


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
        "你的帳號已建立但尚未開通。請聯絡 upload-program 平台管理員為你指派角色後再使用。",
    )


def forbidden(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_403_FORBIDDEN, "forbidden", "Forbidden", detail)


def not_found(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_404_NOT_FOUND, "not-found", "Not Found", detail)


def conflict(detail: str) -> ProblemError:
    return ProblemError(status.HTTP_409_CONFLICT, "conflict", "Conflict", detail)


def payload_too_large(detail: str) -> ProblemError:
    return ProblemError(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "payload-too-large", "Payload Too Large", detail
    )


def unprocessable(slug: str, title: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(status.HTTP_422_UNPROCESSABLE_ENTITY, slug, title, detail, **extra)


def bad_request(detail: str, slug: str = "bad-request") -> ProblemError:
    return ProblemError(status.HTTP_400_BAD_REQUEST, slug, "Bad Request", detail)


def service_unavailable(detail: str) -> ProblemError:
    return ProblemError(
        status.HTTP_503_SERVICE_UNAVAILABLE, "service-unavailable", "Service Unavailable", detail
    )


# --- exception handlers -----------------------------------------------------


async def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
    return problem_response(
        request, exc.status_code, exc.slug, exc.title, exc.detail, exc.headers, **exc.extra
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
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
) -> JSONResponse:
    return problem_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation",
        "Validation failed",
        "請求內容不符合規格,詳見 errors 欄位。",
        errors=[
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ],
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 內部錯誤不把 stack trace 吐給呼叫端;細節在 log 裡靠 trace_id 追。
    return problem_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal",
        "Internal Server Error",
        "服務發生非預期錯誤,請帶 trace_id 聯絡管理員。",
    )
