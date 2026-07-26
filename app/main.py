"""應用組裝。

⚠️ 子路徑:gateway 以 `proxy_pass http://<alias>:8080/;`(尾斜線)**剝掉** `/«PREFIX»/`
後轉發,所以路由一律註冊在根路徑,只把 `root_path` 設成前綴讓 OpenAPI / docs 的網址正確。
對外絕對網址一律用 settings.external_base 組,不從 request 推導。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import problems
from .config import Settings, get_settings
from .db import create_engine, create_sessionmaker
from .logging_setup import setup_logging
from .middleware import SecurityHeadersMiddleware, TraceMiddleware
from .oidc import OidcClient
from .routers import admin, artifacts, auth, health, me, projects, releases, search
from .session import CookieCodec
from .storage import ObjectStorage

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    log.info(
        "服務啟動",
        extra={"environment": settings.environment, "api_prefix": settings.api_prefix or "/"},
    )

    # discovery 失敗不擋啟動:/health 仍要在 60 秒內綠,IdP 沒起來時由 /ready 反映。
    try:
        await app.state.oidc.load_discovery()
    except Exception as exc:
        log.error("啟動時取不到 OIDC discovery,稍後由 /ready 重試", extra={"error": type(exc).__name__})

    try:
        await app.state.storage.ensure_bucket()
    except Exception as exc:
        log.error("啟動時無法確認物件儲存 bucket", extra={"error": type(exc).__name__})

    yield

    await app.state.engine.dispose()
    log.info("服務關閉")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.service_name, settings.log_level)

    app = FastAPI(
        title="upload-program",
        summary="公司內部開發者程式分享平台",
        version="0.1.0",
        root_path=settings.api_prefix,  # 掛在子路徑下,但收到的是被剝過前綴的路徑
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    engine = create_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.oidc = OidcClient(settings)
    app.state.storage = ObjectStorage(settings)
    app.state.cookies = CookieCodec(settings)

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TraceMiddleware)

    # 錯誤一律 RFC 7807,不自創格式。
    app.add_exception_handler(problems.ProblemError, problems.problem_error_handler)
    app.add_exception_handler(StarletteHTTPException, problems.http_exception_handler)
    app.add_exception_handler(RequestValidationError, problems.validation_exception_handler)
    app.add_exception_handler(Exception, problems.unhandled_exception_handler)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(projects.router)
    app.include_router(releases.router)
    app.include_router(artifacts.router)
    app.include_router(search.router)
    app.include_router(admin.router)
    return app


# 🐛 根本原因(T50):原本這裡有 `app = create_app()`,使得 **import 本模組就會讀環境變數**。
# 後果是沒有 .env 的情境(測試、alembic、任何想 import 設定的工具)一律 ImportError。
# 正式進入點改放在 app/asgi.py,本模組只提供工廠函式,不在 import 時產生副作用。
