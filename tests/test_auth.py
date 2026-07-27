"""身分驗證與開通流程——契約 §4.3 / §4.7 的行為在這裡定樁。"""

from app.models import UserStatus
from tests.conftest import auth, make_user


async def test_health_不查相依就回200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_無憑證是401不是403(client):
    resp = await client.get("/v1/projects")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"].endswith("/unauthorized")


async def test_token無效回401(client, oidc):
    # 假 token 必須是 ASCII:HTTP 標頭值不允許非 ASCII,寫中文會炸在客戶端而根本送不出請求
    # (原本寫成 "tok-偽造的",測試自己壞掉,不是服務的問題)。
    resp = await client.get("/v1/projects", headers=auth("tok-forged-never-issued"))
    assert resp.status_code == 401


async def test_首登自動建零角色帳號且業務API回403待開通(client, oidc, app):
    token = oidc.issue("sub-first-login")

    # 首登:業務 API 一律 403 待開通,且文案要指引去哪開通。
    resp = await client.get("/v1/projects", headers=auth(token))
    assert resp.status_code == 403
    body = resp.json()
    assert body["type"].endswith("/pending-activation")
    assert "管理員" in body["detail"]

    # 但 /v1/me 看得到自己的狀態,否則使用者不知道自己是誰、卡在哪。
    me = await client.get("/v1/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["status"] == "pending"

    from sqlalchemy import select

    from app.models import User

    async with app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.sub == "sub-first-login"))
        ).scalar_one()
    assert user.status is UserStatus.pending
    # 🔴 業務庫不得有個資欄位
    columns = {c.name for c in User.__table__.columns}
    assert not columns & {"email", "name", "first_name", "last_name", "password"}


async def test_me的email來自IdP而非資料庫(client, oidc):
    token = oidc.issue("sub-claims", email="dev@example.test", name="測試人員")
    resp = await client.get("/v1/me", headers=auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "dev@example.test"
    assert body["name"] == "測試人員"


async def test_停用帳號被擋(client, oidc, app):
    await make_user(app, "sub-disabled", status=UserStatus.disabled)
    resp = await client.get("/v1/projects", headers=auth(oidc.issue("sub-disabled")))
    assert resp.status_code == 403


async def test_開通後即可通行(client, active_user):
    _, token = active_user
    resp = await client.get("/v1/projects", headers=auth(token))
    assert resp.status_code == 200


async def test_admin開通待開通帳號(client, oidc, app, admin_user):
    _, admin_token = admin_user
    pending = await make_user(app, "sub-waiting", status=UserStatus.pending)

    listed = await client.get("/v1/admin/users?status=pending", headers=auth(admin_token))
    assert listed.status_code == 200
    assert any(u["sub"] == "sub-waiting" for u in listed.json()["items"])

    patched = await client.patch(
        f"/v1/admin/users/{pending.id}",
        json={"status": "active"},
        headers=auth(admin_token),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "active"
    assert patched.json()["activated_at"] is not None

    # 開通後立刻通行
    resp = await client.get("/v1/projects", headers=auth(oidc.issue("sub-waiting")))
    assert resp.status_code == 200


async def test_一般使用者不能進管理後台(client, active_user):
    _, token = active_user
    resp = await client.get("/v1/admin/users", headers=auth(token))
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("/forbidden")


async def test_登入導向IdP時帶PKCE挑戰(client, app):
    """禁 implicit、必須 PKCE(S256),redirect URI 要含子路徑前綴。"""
    from app.oidc import Discovery, OidcClient

    real = OidcClient(app.state.settings)

    async def fake_discovery(force: bool = False):
        return Discovery(
            issuer="https://auth.example.test/realms/test",
            authorization_endpoint="https://auth.example.test/auth",
            token_endpoint="https://auth.example.test/token",
            jwks_uri="https://auth.example.test/certs",
        )

    real.load_discovery = fake_discovery
    app.state.oidc = real

    resp = await client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "response_type=code" in location  # 🔴 禁 implicit
    assert "%2Fupload%2Foidc%2Fcallback%2F" in location or "/upload/oidc/callback/" in location
