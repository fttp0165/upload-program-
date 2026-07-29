"""T55:BOOTSTRAP_ADMIN_SUBS 的完整行為(首次上線實測抓到的洞)。

🐛 原本 bootstrap 只在**帳號首次建立**時生效。但 T45 的「先部署、後拿 sub」
自助流程在結構上保證了:管理員的帳號會**先以 pending 存在**(要看到自己的
sub 就得先登入),然後才有辦法把 sub 填進 BOOTSTRAP_ADMIN_SUBS——
於是清單永遠升不了級,第一個管理員只能手打 SQL 解鎖(2026-07-29 實際發生)。

本檔釘住三條線:
1. 既有 pending 帳號補進清單後,登入即升級(修正的主體)
2. 🔴 disabled **不升級**——bootstrap 不是繞過停權的後門
3. 既有 active 一般成員不被動升管理員——要升走管理後台,不走清單
"""

from tests.conftest import auth, make_user


async def _me(client, token):
    resp = await client.get("/v1/me", headers=auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_首登在清單內直接是管理員(client, app, oidc):
    """既有行為(建號時生效)——原本沒有測試釘住,一併補上。"""
    app.state.settings.bootstrap_admin_subs = "sub-boot-new"
    body = await _me(client, oidc.issue("sub-boot-new"))
    assert body["status"] == "active"
    assert body["platform_role"] == "admin"


async def test_既有pending帳號補進清單後登入即升級(client, app, oidc):
    """🐛 修正的主體:T45 自助流程的必然順序是「先登入(pending)、後填清單」。"""
    from app.models import UserStatus

    await make_user(app, "sub-boot-late", status=UserStatus.pending)
    app.state.settings.bootstrap_admin_subs = "sub-boot-late"

    body = await _me(client, oidc.issue("sub-boot-late"))
    assert body["status"] == "active", "pending 帳號在清單內,登入當下就該升級"
    assert body["platform_role"] == "admin"

    # activated_at 不在 /v1/me 的回應裡(它是後台欄位),直接從 DB 驗
    from sqlalchemy import select

    from app.models import User

    async with app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.sub == "sub-boot-late"))
        ).scalar_one()
    assert user.activated_at is not None


async def test_disabled帳號在清單內也不升級(client, app, oidc):
    """🔴 disabled 是被刻意停權的帳號;清單若能復活它,停權就形同虛設。"""
    from app.models import UserStatus

    await make_user(app, "sub-boot-disabled", status=UserStatus.disabled)
    app.state.settings.bootstrap_admin_subs = "sub-boot-disabled"

    resp = await client.get("/v1/me", headers=auth(oidc.issue("sub-boot-disabled")))
    assert resp.status_code == 403, "停用帳號必須維持 403,不因清單而復活"


async def test_既有active成員不被清單升成管理員(client, app, oidc):
    """升級既有成員的角色是管理後台的職權;清單只解「第一個管理員」的死結。

    讓清單能改既有 active 帳號的角色,等於多出第二條派角色的路——
    而且是一條藏在環境變數裡、稽核看不見操作者的路。
    """
    from app.models import UserStatus

    await make_user(app, "sub-boot-member", status=UserStatus.active)
    app.state.settings.bootstrap_admin_subs = "sub-boot-member"

    body = await _me(client, oidc.issue("sub-boot-member"))
    assert body["status"] == "active"
    assert body["platform_role"] == "member", "active 成員不該被清單暗中升權"


async def test_bootstrap升級留下稽核(client, app, oidc):
    """開通就是開通——不因為操作者是系統(而非管理員)就不留紀錄。"""
    from sqlalchemy import select

    from app.models import AuditEvent, UserStatus

    await make_user(app, "sub-boot-audit", status=UserStatus.pending)
    app.state.settings.bootstrap_admin_subs = "sub-boot-audit"
    await _me(client, oidc.issue("sub-boot-audit"))

    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(AuditEvent).where(AuditEvent.action == "user.activate")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_id is None, "bootstrap 升級的操作者是系統,不是任何使用者"


async def test_升級只發生一次(client, app, oidc):
    """第二次登入不該再寫第二筆稽核(status 已是 active,不再走升級分支)。"""
    from sqlalchemy import select

    from app.models import AuditEvent, UserStatus

    await make_user(app, "sub-boot-once", status=UserStatus.pending)
    app.state.settings.bootstrap_admin_subs = "sub-boot-once"
    await _me(client, oidc.issue("sub-boot-once"))
    await _me(client, oidc.issue("sub-boot-once"))

    async with app.state.sessionmaker() as session:
        count = len(
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.action == "user.activate")
                )
            ).scalars().all()
        )
    assert count == 1


# --- 🐛 ensure_bucket 冪等(同一批上線修正,附掛於此檔)------------------------


async def test_ensure_bucket遇bucket已存在不拋錯():
    """🐛 首次上線實測:head_bucket 在某些情況回 404 後,create_bucket 拋
    BucketAlreadyOwnedByYou——目標狀態(bucket 存在)其實已達成,
    卻在每次啟動留一筆嚇人的 error log。冪等化:已存在 = 成功。
    """
    from contextlib import asynccontextmanager

    from botocore.exceptions import ClientError

    from app.storage import ObjectStorage

    class _StubS3:
        async def head_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        async def create_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "BucketAlreadyOwnedByYou"}}, "CreateBucket")

    storage = ObjectStorage.__new__(ObjectStorage)  # 不跑 __init__(它要連線設定)
    storage._bucket = "test-bucket"

    @asynccontextmanager
    async def _stub_client():
        yield _StubS3()

    storage._client = _stub_client
    await storage.ensure_bucket()  # 不該拋——已存在就是成功


async def test_ensure_bucket真的失敗仍要拋():
    """守門:冪等化不能變成「什麼錯都吞」——權限錯誤等仍必須浮上來。"""
    from contextlib import asynccontextmanager

    import pytest
    from botocore.exceptions import ClientError

    from app.storage import ObjectStorage

    class _StubS3:
        async def head_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        async def create_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "CreateBucket")

    storage = ObjectStorage.__new__(ObjectStorage)
    storage._bucket = "test-bucket"

    @asynccontextmanager
    async def _stub_client():
        yield _StubS3()

    storage._client = _stub_client
    with pytest.raises(ClientError):
        await storage.ensure_bucket()
