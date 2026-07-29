"""T52 網頁 session 自動續期(SSO 契約 §3.3:access token 300 秒)。

🐛 缺陷:access token 只有 5 分鐘,但網頁全站零 JS,沒有人會去打
`POST /auth/refresh`;session cookie 卻活 10 小時。結果是登入 5 分鐘後
所有頁面靜默退回「請先登入」,而伺服器沒有任何錯誤。

**240 條測試一條都沒抓到**,因為 `FakeOidc` 的 token 永不過期——
替身與真實行為的落差,正是這個缺陷藏這麼久的原因。本檔連同 conftest
一起把過期與 refresh 補進替身。

🔴 續期不得繞過收權:契約把 access token 壓到 300 秒的目的就是
「管理員收權 / IdP 停用帳號後,既發 token 最長只再活 5 分鐘」。
續期一定要真的去問 IdP,IdP 拒絕就是登出。
"""

from app.session import SessionData
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
COOKIE = "upload_session"


def _session_cookie(app, **kwargs) -> str:
    """把一組 SessionData 簽成 cookie 值(繞過完整登入流程,只測續期)。

    走 `codec.set_session()` 這個公開介面再把值抽出來,而不是碰它的私有序列化器
    ——測試不該依賴實作細節。
    """
    from starlette.responses import Response

    probe = Response()
    app.state.cookies.set_session(probe, SessionData(**kwargs))
    header = next(
        value.decode()
        for key, value in probe.raw_headers
        if key.decode().lower() == "set-cookie"
    )
    return header.split("=", 1)[1].split(";", 1)[0]


async def _login_cookie(app, oidc, sub: str, *, expired: bool, with_refresh: bool = True) -> str:
    await make_user(app, sub)
    return _session_cookie(
        app,
        access_token=oidc.issue(sub, expired=expired),
        refresh_token=oidc.issue_refresh(sub) if with_refresh else "",
        id_token="fake-id-token",
    )


# --- 🐛 自動續期 ------------------------------------------------------------


async def test_access_token過期時自動續期(client, app, oidc):
    """🐛 這條就是缺陷本身:過期後網頁應該照常運作,而不是退回未登入。"""
    cookie = await _login_cookie(app, oidc, "sub-renew", expired=True)

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert "登出" in resp.text, "自動續期後應維持登入狀態,而不是顯示「登入」"
    assert oidc.refresh_calls == 1


async def test_續期後回應帶上新的session_cookie(client, app, oidc):
    """不寫回 cookie 的話,下一個請求還是拿過期 token,等於每次都要 refresh。"""
    cookie = await _login_cookie(app, oidc, "sub-newcookie", expired=True)

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert COOKIE in resp.cookies, "續期後要把新的 session cookie 寫回去"
    assert resp.cookies[COOKIE] != cookie


async def test_續期後的cookie仍符合同源義務(client, app, oidc):
    """🔴 §4.10:HttpOnly + SameSite=Lax + Path 綁自己的前綴,續期不得鬆掉。"""
    cookie = await _login_cookie(app, oidc, "sub-attrs", expired=True)

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    raw = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
    session_header = next(h for h in raw if h.startswith(f"{COOKIE}="))
    assert "HttpOnly" in session_header
    assert "SameSite=lax" in session_header.replace("SameSite=Lax", "SameSite=lax")
    assert "Path=/upload/" in session_header


async def test_token仍有效時不去打IdP(client, app, oidc):
    """每次請求都 refresh 會把 IdP 打爆,也讓 300 秒的設計失去意義。"""
    cookie = await _login_cookie(app, oidc, "sub-valid", expired=False)

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert "登出" in resp.text
    assert oidc.refresh_calls == 0


# --- 🔴 續期不得繞過收權 ----------------------------------------------------


async def test_refresh也失效時視為未登入(client, app, oidc):
    """🔴 IdP 停用帳號 → refresh 失敗 → 使用者立刻被登出。這正是 300 秒的用意。"""
    await make_user(app, "sub-revoked")
    dead = oidc.issue_refresh("sub-revoked")
    oidc.dead_refresh.add(dead)
    cookie = _session_cookie(
        app, access_token=oidc.issue("sub-revoked", expired=True), refresh_token=dead
    )

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert "登出" not in resp.text, "refresh 失敗就該視為未登入,不得自行延長"
    assert "登入" in resp.text


async def test_沒有refresh_token時不崩潰(client, app, oidc):
    cookie = await _login_cookie(app, oidc, "sub-nort", expired=True, with_refresh=False)

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert "登出" not in resp.text
    assert oidc.refresh_calls == 0


async def test_續期不繞過本地停用檢查(client, app, oidc):
    """🔴 兩層都要在:IdP 端的 refresh 失敗,與本地的 status=disabled。"""
    from app.models import UserStatus

    await make_user(app, "sub-disabled52", status=UserStatus.disabled)
    cookie = _session_cookie(
        app,
        access_token=oidc.issue("sub-disabled52", expired=True),
        refresh_token=oidc.issue_refresh("sub-disabled52"),
    )

    resp = await client.get("/", headers=BROWSER, cookies={COOKIE: cookie})
    assert resp.status_code == 200
    assert "登出" not in resp.text, "本地已停用的帳號不該因為續期成功就通行"


# --- API 呼叫端行為不變 -----------------------------------------------------


async def test_Bearer呼叫端不自動續期(client, app, oidc):
    """API 呼叫端本來就該自己管 token;`POST /auth/refresh` 留給他們用。"""
    await make_user(app, "sub-bearer52")
    expired = oidc.issue("sub-bearer52", expired=True)

    resp = await client.get("/v1/projects", headers=auth(expired))
    assert resp.status_code == 401
    assert oidc.refresh_calls == 0


async def test_續期在API路徑上也不生效(client, app, oidc):
    """帶 cookie 打 API 時同樣不自動續期——續期是網頁 session 的機制。

    (若日後決定放寬,要先想清楚 API 呼叫端拿不到新 cookie 的問題。)
    """
    cookie = await _login_cookie(app, oidc, "sub-apicookie", expired=True)

    resp = await client.get("/v1/projects", cookies={COOKIE: cookie})
    assert resp.status_code == 200, "cookie session 在 API 上仍可用(續期後)"
    assert oidc.refresh_calls == 1
