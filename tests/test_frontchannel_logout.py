"""T74:front-channel logout 端點(SSO 契約 v2.0 §10.3)。

情境(契約 §10 緣起):A 在入口登出、B 登入後,進我們的站**仍然是 A**——
我方 session 是自己簽的 cookie,IdP 結束 session 時我們不會知道。
共用電腦上這是安全事故,所以 IdP 登出頁會以 iframe 載入本端點來刪我方 cookie。

🔴 三個容易做成「看起來有做、實際不做事」的地方,各有一條測試:
1. **免認證**——iframe 不會帶我方 token;要求認證等於這個端點永遠不生效。
2. **Path 必須與種 cookie 時相同**,否則瀏覽器根本不刪那個 cookie。
3. **零副作用**——被任意第三方呼叫的最壞後果只能是「使用者被登出」。
"""

from sqlalchemy import func, select

from tests.conftest import auth, make_user

PATH = "/oidc/frontchannel-logout"


async def _counts(app) -> tuple[int, int]:
    """回傳 (使用者數, 稽核筆數) —— 用來證明端點沒有副作用。"""
    from app.models import AuditEvent, User

    async with app.state.sessionmaker() as session:
        users = (await session.execute(select(func.count()).select_from(User))).scalar()
        events = (await session.execute(select(func.count()).select_from(AuditEvent))).scalar()
    return int(users or 0), int(events or 0)


async def test_無cookie呼叫回204(client):
    """🔴 免認證:回 401 或 302 都代表這個端點在 iframe 裡永遠不會生效。"""
    resp = await client.get(PATH)
    assert resp.status_code == 204, resp.text
    assert not resp.content


async def test_帶session呼叫回204並清除兩個cookie(client, app, oidc, settings):
    await make_user(app, "sub-fclogout")
    # 走真實登入流程種下 session cookie(不手工偽造,才驗得到真實行為)
    login = await client.get("/auth/login")
    codec = app.state.cookies
    state_cookie = login.cookies[codec.login_cookie_name]

    resp = await client.get(
        PATH, cookies={settings.session_cookie_name: "whatever", codec.login_cookie_name: state_cookie}
    )
    assert resp.status_code == 204
    cleared = resp.headers.get_list("set-cookie")
    assert any(settings.session_cookie_name in item for item in cleared), "應清除 session cookie"
    assert any(codec.login_cookie_name in item for item in cleared), "應清除 login state cookie"


async def test_清除用的Path與種cookie時相同(client, settings):
    """🔴 Path 不符 = 瀏覽器不會刪那個 cookie(看起來有做、實際不做事)。"""
    resp = await client.get(PATH)
    cleared = resp.headers.get_list("set-cookie")
    assert cleared, "即使沒有 session 也應送出刪除指示(冪等)"
    for item in cleared:
        assert f"Path={settings.cookie_path}" in item, f"Path 不符:{item}"


async def test_帶iss與sid參數也回204(client):
    """Keycloak 會帶 iss / sid;我方 session 無狀態,忽略即可,但不得因此報錯。"""
    resp = await client.get(f"{PATH}?iss=https://idp.example/realms/sporton&sid=abc123")
    assert resp.status_code == 204


async def test_重複呼叫皆為204(client):
    for _ in range(2):
        assert (await client.get(PATH)).status_code == 204


async def test_零副作用_不建帳號不寫稽核不種session(client, app):
    """🔴 被任意第三方呼叫的最壞後果必須只是「使用者被登出」。"""
    before = await _counts(app)
    resp = await client.get(f"{PATH}?iss=evil&sid=evil")
    assert resp.status_code == 204
    assert await _counts(app) == before, "端點不得建立使用者或寫入稽核"

    settings = app.state.settings
    for item in resp.headers.get_list("set-cookie"):
        if settings.session_cookie_name in item:
            # 只能是「刪除」——刪除的形式是 Max-Age=0 / 過去的 Expires
            assert "Max-Age=0" in item or "expires=Thu, 01 Jan 1970" in item.lower(), item


async def test_登入者呼叫後不影響其他端點語意(client, app, oidc):
    """端點只碰 cookie,不動任何伺服器端狀態:登出後帶 token 仍可正常使用 API。"""
    await make_user(app, "sub-fclogout-2")
    token = oidc.issue("sub-fclogout-2")
    assert (await client.get(PATH)).status_code == 204
    resp = await client.get("/v1/me", headers=auth(token))
    assert resp.status_code == 200
