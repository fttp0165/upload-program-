"""本機(WSL)驗證用的開發伺服器(T92)。

**用途:** 一條指令把真的 app 跑起來,用瀏覽器看介面改動——**不需要**
PostgreSQL、MinIO,也**不需要** Keycloak。

    DEV_UNSAFE_LOCAL=1 .venv/bin/python tools/devserver.py

為什麼需要它:側欄、建立版本頁這些畫面只在「已開通登入者」的版型出現,
而本專案的紅線是不自建帳號系統 → 本機沒有 IdP 就永遠登不進去,介面改動只能
靠讀 HTML 或等部署到 VM。T85 / T86 都是這樣拖到實測才發現問題的。

做法:用**測試已經在用的替身**(`tests/conftest.py` 的 `FakeOidc`、`FakeStorage`)
搭配 SQLite 檔,並多掛一條 `/dev/login` 直接種 session cookie。

🔴 **這支腳本會偽造 session,所以安全性靠三層結構,不靠自律:**

1. 沒有 `DEV_UNSAFE_LOCAL=1` 就 `SystemExit`——是拒絕啟動,不是印警告然後照跑。
2. 只綁 `127.0.0.1`;對外綁定會讓「本機用的假登入」變成任何人都能用的假登入。
3. **image 裡根本沒有這支腳本**:`Dockerfile` 只 `COPY app/` 與 `alembic/`,
   而本檔 import `tests/`——**兩層都不在 image 內**,正式環境連 import 都會失敗。
   這是結構保證,不是設定選項。

以上三條 + 「`app/` 任何檔案都不得提到 devserver」都由
`tests/test_devserver.py` 反向驗證。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# 讓 `python tools/devserver.py` 直接跑得起來(否則 import app / tests 會找不到)。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEV_SUB = "dev-local-admin"  # 假的 IdP subject;不是任何真實帳號
DEV_DB_DIR = ROOT / ".devdata"
HOST = "127.0.0.1"
PORT = int(os.environ.get("DEV_PORT", "8080"))


def require_dev_flag() -> None:
    """沒有 `DEV_UNSAFE_LOCAL=1` 就拒絕啟動。

    回傳:None。副作用:不合格時 `SystemExit(2)`。

    🔴 刻意做成**拒絕**而不是警告:這支腳本會發出「已登入」的 cookie,
    印一行紅字然後照跑,等於把要求變成建議。
    """
    if os.environ.get("DEV_UNSAFE_LOCAL") != "1":
        raise SystemExit(
            "拒絕啟動:tools/devserver.py 會偽造登入 session,只供本機驗證。\n"
            "確定是在自己的機器上,請明確帶旗標:\n"
            "  DEV_UNSAFE_LOCAL=1 .venv/bin/python tools/devserver.py\n"
        )


def build_settings():
    """本機用設定:SQLite 檔 + 假的 IdP / S3 值。

    回傳:`Settings`。副作用:建立 `.devdata/` 目錄。

    `api_prefix` 保持 `/upload`,網址與正式環境一致(`/upload/help`)——
    路徑前綴出過兩次事(T40 的靜態檔 404、T67 的入口連結),本機驗證要驗到它。
    """
    from app.config import Settings

    DEV_DB_DIR.mkdir(exist_ok=True)
    return Settings(
        environment="dev",
        public_base_url=f"http://{HOST}:{PORT}",
        api_prefix="/upload",
        database_url=f"sqlite+aiosqlite:///{DEV_DB_DIR / 'dev.db'}",
        oidc_issuer="https://auth.invalid/realms/dev",
        oidc_client_id="upload-program-dev",
        oidc_client_secret="not-a-real-secret",
        session_secret="not-a-real-session-secret-devserver",
        s3_endpoint_url="http://minio.invalid:9000",
        s3_bucket="dev-bucket",
        s3_access_key="dev",
        s3_secret_key="dev",
        session_cookie_secure=False,  # 本機是 http,secure cookie 會直接不送出
    )


async def seed(app) -> None:
    """造出「看得到東西」的最小資料:管理員 + 示範專案 + 兩個版本。

    參數:app(已建好的 FastAPI)。回傳:None。
    副作用:建表並寫入假資料(SQLite 檔);已存在時**不重複寫入**。

    兩個版本刻意一個 published 一個 draft:T90 的草稿標示要看得到,
    而「只有草稿」或「只有已發布」都驗不到那個分支。
    """
    from sqlalchemy import select

    from app.db import Base
    from app.models import (
        PlatformRole,
        Project,
        ProjectMember,
        ProjectRole,
        Release,
        ReleaseStatus,
        User,
        UserStatus,
        Visibility,
    )

    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == DEV_SUB))).scalar_one_or_none()
        if user is None:
            user = User(
                sub=DEV_SUB,
                status=UserStatus.active,
                platform_role=PlatformRole.admin,
                display_name_cache="本機驗證用帳號",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        project = (
            await session.execute(select(Project).where(Project.slug == "demo-tool"))
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                slug="demo-tool",
                name="示範工具",
                summary="本機驗證用的假專案,沒有任何真實資料。",
                visibility=Visibility.internal,
                owner_id=user.id,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)
            session.add(
                ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.maintainer)
            )
            session.add_all(
                [
                    Release(
                        project_id=project.id,
                        version="v1.0.0",
                        notes="第一版(已發布)",
                        status=ReleaseStatus.published,
                        created_by_id=user.id,
                        published_at=datetime.now(UTC),
                    ),
                    Release(
                        project_id=project.id,
                        version="v1.1.0",
                        notes="還在做(草稿)",
                        status=ReleaseStatus.draft,
                        created_by_id=user.id,
                    ),
                ]
            )
            await session.commit()


def build_dev_app():
    """把真的 app 建起來,換上測試替身,並掛上 `/dev/login`。

    回傳:FastAPI。副作用:建立 SQLite 檔與假資料。

    🔴 `/dev/login` **只掛在這裡**,不在 `app.main.create_app()`
    ——有一條測試釘住那件事(`test_假登入路由只掛在devserver的app上`)。
    """
    from fastapi import APIRouter
    from starlette.responses import RedirectResponse

    from app.main import create_app
    from app.session import SessionData
    from app.web_urls import web_url
    from tests.conftest import FakeOidc, FakeStorage

    settings = build_settings()
    app = create_app(settings)
    # 替身:本機沒有 Keycloak 也沒有 MinIO。與 pytest 用的是同一份程式碼,
    # 替身與真實行為的落差就只有一處要維護(T52 的教訓)。
    app.state.oidc = FakeOidc()
    app.state.storage = FakeStorage(settings.magic_sniff_bytes)

    dev = APIRouter()

    @dev.get("/dev/login", include_in_schema=False)
    async def dev_login():
        """種一個「已登入」的 session cookie,然後回首頁。

        回傳:302。副作用:設 session cookie。
        本機沒有 IdP,這是唯一能看到登入後版型(側欄、建立版本頁)的方法。
        """
        token = app.state.oidc.issue(DEV_SUB)
        response = RedirectResponse(web_url(settings, "/"), status_code=302)
        app.state.cookies.set_session(
            response,
            SessionData(
                access_token=token,
                refresh_token=app.state.oidc.issue_refresh(DEV_SUB),
                id_token="dev-id-token",
            ),
        )
        return response

    app.include_router(dev)
    return app


def strip_prefix(app, prefix: str = "/upload"):
    """模擬 gateway **剝前綴**的 ASGI 包裝。

    參數:app(ASGI app)、prefix(前綴)。回傳:ASGI app。副作用:無。

    🔴 為什麼一定要有這層:正式環境是 `portal-gateway` 把 `/upload/` 剝掉才轉給我們,
    所以 app 收到的 path 是 `/`、`/admin/users`。少了這層,本機瀏覽器打
    `/upload/` 時 app 看到的是 `/upload/`——側欄的 active 判斷(`current_path == '/'`)
    就永遠不成立,**畫面看起來像壞的,而程式其實是對的**。
    本機驗證要能相信,就得跟正式環境同一個座標系。
    """

    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith(prefix):
            scope = dict(scope)
            scope["path"] = scope["path"][len(prefix) :] or "/"
            scope["root_path"] = prefix
        await app(scope, receive, send)

    return wrapped


def main() -> None:
    require_dev_flag()

    import uvicorn

    app = build_dev_app()
    asyncio.run(seed(app))

    base = f"http://{HOST}:{PORT}/upload"
    print("=" * 72)
    print("本機驗證伺服器(假登入,只綁 127.0.0.1)")
    print(f"  1. 先開這個網址假登入:  {base.replace('/upload', '')}/dev/login")
    print(f"  2. 教學頁(三張圖):      {base}/help")
    print(f"  3. 建立版本頁(版本清單):{base}/projects/demo-tool/releases/new")
    print(f"  4. 專案頁:               {base}/projects/demo-tool")
    print("=" * 72)
    # 🔴 host 寫死 127.0.0.1:對外綁定會讓假登入變成任何人都能用的假登入。
    uvicorn.run(strip_prefix(app), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
