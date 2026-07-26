"""Single logout 與 session cookie 的契約測試(SSO 契約 §4.5、§4.6、接入指南 §6)。

這塊原本**一行都沒被測到**(欠項 A6)。契約寫著「不得只清本地 session 假裝登出」,
但沒有測試的話,日後任何人「順手簡化」成只清 cookie 都不會被發現。

本檔用**真的 `OidcClient`**(只把 discovery 與驗章換成假的),所以 `logout_url()`、
`authorization_url()` 的組法都是真的在被檢驗,不是對假物件斷言。

🔴 契約 §4.8:一律假資料。本檔的 token 都是編出來的字串。
"""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import Response

from app.oidc import Discovery, OidcClient
from app.routers.auth import _safe_next
from app.session import CookieCodec, LoginState, SessionData
from tests.conftest import make_user

ISSUER = "https://auth.example.test/realms/test"
AUTHZ = "https://auth.example.test/protocol/openid-connect/auth"
TOKEN = "https://auth.example.test/protocol/openid-connect/token"
END_SESSION = "https://auth.example.test/protocol/openid-connect/logout"


class _StubbedOidc(OidcClient):
    """真的 OidcClient,只換掉會打網路的部分——組網址的邏輯仍是受測對象。"""

    def __init__(self, settings, *, end_session: str = END_SESSION) -> None:
        super().__init__(settings)
        self._end_session = end_session
        self.exchanged: list[tuple[str, str]] = []

    async def load_discovery(self, force: bool = False) -> Discovery:
        return Discovery(
            issuer=ISSUER,
            authorization_endpoint=AUTHZ,
            token_endpoint=TOKEN,
            jwks_uri="https://auth.example.test/certs",
            end_session_endpoint=self._end_session,
        )

    async def exchange_code(self, code: str, verifier: str) -> dict:
        self.exchanged.append((code, verifier))
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "id_token": "fake-id-token",
        }

    def verify(self, token: str, *, expected_nonce: str | None = None) -> dict:
        return {"sub": "sub-logout-tester", "aud": self._settings.oidc_client_id, "iss": ISSUER}

    def verify_access_token(self, token: str) -> dict:
        from app.problems import unauthorized

        if token != "fake-access-token":
            raise unauthorized("token 無效(測試用)")
        return {"sub": "sub-logout-tester", "aud": self._settings.oidc_client_id, "iss": ISSUER}


@pytest.fixture
def oidc_stub(app):
    stub = _StubbedOidc(app.state.settings)
    app.state.oidc = stub
    return stub


def _bake(setter, data) -> str:
    """把 codec 寫出的 Set-Cookie 取出純值,供測試當成瀏覽器帶回的 cookie 使用。"""
    holder = Response()
    setter(holder, data)
    return holder.headers["set-cookie"].split("=", 1)[1].split(";")[0]


def _cookie_header(resp, name: str) -> str | None:
    for raw in resp.headers.get_list("set-cookie"):
        if raw.split("=", 1)[0].strip() == name:
            return raw
    return None


# --- §4.5 single logout ------------------------------------------------------


async def test_登出必須導向IdP而非只清本地session(client, oidc_stub, settings):
    """🔴 契約 §4.5 的核心:只清 cookie 是「假裝登出」,別的 App 仍然登入著。"""
    resp = await client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302

    location = resp.headers["location"]
    parsed = urlparse(location)
    assert location.startswith(END_SESSION), f"登出未導向 IdP:{location}"
    assert parsed.netloc == "auth.example.test"

    params = parse_qs(parsed.query)
    # 登出後要回得來,且回的是本服務的對外網址(含子路徑前綴)
    assert params["post_logout_redirect_uri"] == [f"{settings.external_base}/"]


async def test_有session時帶id_token_hint(client, app, oidc_stub, settings):
    raw = _bake(
        app.state.cookies.set_session,
        SessionData(access_token="a", refresh_token="r", id_token="the-id-token"),
    )

    resp = await client.get(
        "/auth/logout",
        cookies={settings.session_cookie_name: raw},
        follow_redirects=False,
    )
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["id_token_hint"] == ["the-id-token"]


async def test_無session時退而帶client_id(client, oidc_stub, settings):
    """沒有 id_token 就用 client_id,讓 IdP 仍知道是誰要登出。"""
    resp = await client.get("/auth/logout", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["client_id"] == [settings.oidc_client_id]
    assert "id_token_hint" not in params


async def test_登出會清掉兩個cookie(client, oidc_stub, settings, app):
    resp = await client.get("/auth/logout", follow_redirects=False)
    session_cookie = _cookie_header(resp, settings.session_cookie_name)
    login_cookie = _cookie_header(resp, app.state.cookies.login_cookie_name)

    assert session_cookie is not None, "未清除 session cookie"
    assert login_cookie is not None, "未清除登入往返用的 cookie"
    # 刪除 cookie 的作法是設空值 + 立即過期
    for raw in (session_cookie, login_cookie):
        assert 'Max-Age=0' in raw or "expires=Thu, 01 Jan 1970" in raw.lower()


async def test_IdP連不上時仍能登出而不是500(client, app, settings):
    """IdP 掛掉時若回 500,使用者就永遠登不出去了。"""

    class _BrokenOidc(_StubbedOidc):
        async def load_discovery(self, force: bool = False):
            raise RuntimeError("IdP 連不上(測試用)")

    app.state.oidc = _BrokenOidc(settings)
    resp = await client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"{settings.external_base}/"


async def test_IdP沒有登出端點時導回本站(client, app, settings):
    app.state.oidc = _StubbedOidc(settings, end_session="")
    resp = await client.get("/auth/logout", follow_redirects=False)
    assert resp.headers["location"] == f"{settings.external_base}/"


# --- cookie 屬性 -------------------------------------------------------------


async def test_登入cookie屬性符合規約(client, oidc_stub, settings, app):
    resp = await client.get("/auth/login", follow_redirects=False)
    raw = _cookie_header(resp, app.state.cookies.login_cookie_name)
    assert raw is not None

    lowered = raw.lower()
    assert "httponly" in lowered, "cookie 必須 HttpOnly,否則 JS 讀得到"
    assert "samesite=lax" in lowered, "SameSite=Lax 是目前的 CSRF 防線"
    # 接入指南 §6:cookie path 綁自己的前綴,避免與同主機其他 App 互蓋
    assert f"path={settings.cookie_path}".lower() in lowered


async def test_production設定下cookie必須Secure(settings):
    """測試環境刻意關掉 Secure(httpx 走 http),但 production 不得關。"""
    secure_settings = settings.model_copy(update={"session_cookie_secure": True})
    holder = Response()
    CookieCodec(secure_settings).set_session(holder, SessionData(access_token="a"))
    assert "secure" in holder.headers["set-cookie"].lower()


async def test_cookie被竄改就失效(client, oidc_stub, settings, app):
    """cookie 是簽章的,改一個字元就該作廢——否則等於任何人都能偽造身分。"""
    raw = _bake(app.state.cookies.set_session, SessionData(access_token="fake-access-token"))

    tampered = raw[:-6] + ("A" if raw[-6] != "A" else "B") + raw[-5:]
    resp = await client.get(
        "/v1/projects", cookies={settings.session_cookie_name: tampered}
    )
    assert resp.status_code == 401


async def test_session_cookie可作為憑證通行(client, app, oidc_stub, settings):
    """前端是同源網頁,靠 cookie 而不是自己保管 token。"""
    await make_user(app, "sub-logout-tester")
    raw = _bake(app.state.cookies.set_session, SessionData(access_token="fake-access-token"))

    resp = await client.get("/v1/projects", cookies={settings.session_cookie_name: raw})
    assert resp.status_code == 200


# --- 登入往返 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/projects/abc", "/projects/abc"),
        (None, "/"),
        ("", "/"),
        ("//evil.example.com/path", "/"),  # protocol-relative 開放轉址
        ("https://evil.example.com", "/"),  # 絕對網址
        ("javascript:alert(1)", "/"),
    ],
)
def test_next參數擋開放轉址(raw, expected):
    assert _safe_next(raw) == expected


async def test_callback的state不符即拒(client, app, oidc_stub, settings):
    raw = _bake(
        app.state.cookies.set_login_state,
        LoginState(state="the-real-state", verifier="v", nonce="n"),
    )

    resp = await client.get(
        "/oidc/callback/?code=abc&state=attacker-supplied",
        cookies={app.state.cookies.login_cookie_name: raw},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "竄改" in resp.json()["detail"]


async def test_callback缺少login_cookie即拒(client, oidc_stub):
    """登入流程逾時或 cookie 遺失時,不能就這樣放行。"""
    resp = await client.get("/oidc/callback/?code=abc&state=whatever", follow_redirects=False)
    assert resp.status_code == 401


async def test_callback缺code回400(client, oidc_stub):
    resp = await client.get("/oidc/callback/?state=abc", follow_redirects=False)
    assert resp.status_code == 400


async def test_callback成功後建立session並導回站內(client, app, oidc_stub, settings):
    raw = _bake(
        app.state.cookies.set_login_state,
        LoginState(state="s-1", verifier="v-1", nonce="n-1", next_path="/projects/demo"),
    )

    resp = await client.get(
        "/oidc/callback/?code=the-code&state=s-1",
        cookies={app.state.cookies.login_cookie_name: raw},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # 導回的是對外網址(含子路徑前綴),不是容器內的裸路徑
    assert resp.headers["location"] == f"{settings.external_base}/projects/demo"
    # 授權碼交換時必須把 PKCE verifier 帶上去
    assert oidc_stub.exchanged == [("the-code", "v-1")]
    assert _cookie_header(resp, settings.session_cookie_name) is not None


async def test_登入導向帶PKCE與nonce(client, oidc_stub, settings):
    resp = await client.get("/auth/login", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)

    assert params["response_type"] == ["code"]  # 🔴 禁 implicit
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] and params["state"] and params["nonce"]
    assert params["redirect_uri"] == [settings.oidc_redirect_uri]
    assert params["client_id"] == [settings.oidc_client_id]


# --- refresh -----------------------------------------------------------------


async def test_沒有session時refresh回401(client, oidc_stub):
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401
