"""健康檢查。

平台規約:`/health` 是 **liveness,不查相依**(查了 DB 一抖容器就被判死重啟);
`/ready` 才查 DB、物件儲存與 IdP 公鑰。兩者用途不同,不得合成一個。
"""

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/health", summary="Liveness——程序活著就回 200,不查任何相依")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness——DB / 物件儲存 / IdP 公鑰皆就緒才回 200")
async def ready(request: Request, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("readiness: database 未就緒", extra={"error": type(exc).__name__})
        checks["database"] = "error"

    try:
        await request.app.state.storage.check_ready()
        checks["object_storage"] = "ok"
    except Exception as exc:
        log.warning("readiness: 物件儲存未就緒", extra={"error": type(exc).__name__})
        checks["object_storage"] = "error"

    oidc = request.app.state.oidc
    if not oidc.ready:
        try:
            await oidc.load_discovery()
        except Exception as exc:
            log.warning("readiness: OIDC discovery 失敗", extra={"error": type(exc).__name__})
    checks["idp"] = "ok" if oidc.ready else "error"

    ok = all(v == "ok" for v in checks.values())
    response.status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ok else "not_ready", "checks": checks}
