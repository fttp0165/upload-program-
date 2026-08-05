"""T64:靜默 SSO(prompt=none)——portal 登入過的人免再按登入。

**T81(2026-08-04)改寫前兩條**:首頁不再發起靜默探測,匿名瀏覽器一律送去
互動式登入(裁示「不要進到這一頁」)。`/auth/login?silent=1` 端點與 callback 的
`login_required` 處理**保留**——那段是把 IdP 的正常回應擋在 401 之外的保險。

釘住的行為:
1. 瀏覽器(Accept 含 text/html)匿名首訪首頁 → 302,且**不帶 silent=1**
2. 帶探測 cookie 再訪 → 一樣 302(探測 cookie 對首頁不再有作用)
3. curl / 冒煙(Accept: */*)→ 200 落地頁,行為與從前完全相同
4. silent 登入 → 授權 URL 帶 prompt=none,並種下探測 cookie
5. 🔴 靜默失敗(login_required)→ 無聲 302 回原頁、不設 session、不是 401
6. 🔴 非靜默流程的 IdP 錯誤 → 維持 401(既有行為不動)
"""

from tests.conftest import make_user

_HTML = {"accept": "text/html,application/xhtml+xml"}


async def _probe_cookie_name(app):
    return app.state.cookies.sso_probe_cookie_name


async def _silent_login_cookies(client, app):
    """發起一次 silent 登入,回傳(login_state cookie 值, 探測 cookie 名)。"""
    resp = await client.get("/auth/login?silent=1")
    assert resp.status_code == 302
    codec = app.state.cookies
    return resp.cookies[codec.login_cookie_name], codec.sso_probe_cookie_name


async def test_瀏覽器匿名首訪_改為互動式登入(client, app):
    """**T81 改寫**:首頁不再發起靜默探測,改直接送去互動式登入。

    靜默探測的目的(portal 登入過的人免再按登入)由互動式登入同樣達成——
    IdP 有 session 就直接導回、不顯示畫面;沒有 session 時才顯示登入畫面,
    而那正是 T81 要的結果。原本的「無聲送回落地頁」是要消滅的行為。
    """
    client.cookies.delete(app.state.cookies.sso_probe_cookie_name)  # 模擬真首訪
    resp = await client.get("/", headers=_HTML)
    assert resp.status_code == 302
    assert "silent=1" not in resp.headers["location"]


async def test_帶探測cookie也一樣轉址_探測cookie不再影響首頁(client, app):
    """探測 cookie 曾經是首頁的防迴圈開關;首頁不再探測之後它對首頁不再有作用。"""
    resp = await client.get("/", headers=_HTML)  # client 預設已帶探測 cookie
    assert resp.status_code == 302


async def test_curl冒煙不觸發探測_行為不變(client, app):
    client.cookies.delete(app.state.cookies.sso_probe_cookie_name)  # 沒探測過也一樣
    resp = await client.get("/")  # httpx 預設 Accept: */*
    assert resp.status_code == 200


async def test_silent登入_授權URL帶prompt_none_並種探測cookie(client, app, oidc):
    resp = await client.get("/auth/login?silent=1")
    assert resp.status_code == 302
    assert oidc.last_prompt == "none"
    assert app.state.cookies.sso_probe_cookie_name in resp.cookies


async def test_一般登入_不帶prompt(client, app, oidc):
    resp = await client.get("/auth/login")
    assert resp.status_code == 302
    assert oidc.last_prompt is None
    assert app.state.cookies.sso_probe_cookie_name not in resp.cookies


async def test_靜默失敗_無聲返回且無session(client, app, oidc):
    """🔴 login_required = 「沒有 IdP session」,是預期結果不是錯誤。"""
    login_cookie, _ = await _silent_login_cookies(client, app)
    codec = app.state.cookies
    resp = await client.get(
        "/oidc/callback/?error=login_required",
        cookies={codec.login_cookie_name: login_cookie},
    )
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/upload/")  # 回原頁(落地頁)
    settings = app.state.settings
    assert settings.session_cookie_name not in resp.cookies  # 不得憑空生出 session


async def test_非靜默流程的IdP錯誤_維持401(client, app):
    resp = await client.get("/auth/login")  # silent=False
    codec = app.state.cookies
    login_cookie = resp.cookies[codec.login_cookie_name]
    resp2 = await client.get(
        "/oidc/callback/?error=login_required",
        cookies={codec.login_cookie_name: login_cookie},
    )
    assert resp2.status_code == 401


async def test_已登入者訪首頁_不探測照常顯示(client, app, oidc):
    await make_user(app, "sub-silent-active")
    from tests.conftest import auth

    resp = await client.get(
        "/", headers={**_HTML, **auth(oidc.issue("sub-silent-active"))}
    )
    assert resp.status_code == 200
