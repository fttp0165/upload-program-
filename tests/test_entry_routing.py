"""T81 首頁入口導流:未登入直上登入頁、未開通回平台入口。

裁示(2026-08-04 Benny):「點擊卡片還是會進入登入頁面,不要進到這一頁。
**沒權限就回入口,沒登入就進登入頁,有權限就進總覽。**」

這推翻了 T53「深層頁 302、**首頁留落地頁**」中關於首頁的那一半:
從 portal 卡片進來的人已經知道這是什麼系統,他要的是進去,不是再讀一次介紹。

🔴 本檔要釘住的三件事,少一件就會出事:

1. **非瀏覽器(`Accept: */*`)維持 200 落地頁**——冒煙與監控拿首頁 200 當服務活著的
   判準(runbook §A.4),把它改成 302 會讓監控在換版當下集體變紅,而那與登入無關。
2. **停用者不得在登入頁與首頁之間無限彈跳**。`optional_identity` 對 disabled 會吞掉
   403 回 None(與匿名無法區分),所以「匿名 → 登入頁」這條規則單獨存在時,
   停用者會 首頁 → IdP → callback → 首頁 → IdP … 永遠繞下去。
   解法是在 callback 就擋下:停用者**不建立 session**,直接送回平台入口。
3. **平台入口若被設成指回本服務**,未開通者會在 portal 與本服務之間彈跳;
   轉址目標落在本服務前綴內時退回 `/pending`(那一頁至少停得下來,且有他的 sub)。
"""

from urllib.parse import parse_qs, urlparse

from fastapi import Response

from app.models import UserStatus
from app.oidc import Discovery, OidcClient
from app.session import LoginState
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
DISABLED_SUB = "sub-disabled-entry"


class _CallbackOidc(OidcClient):
    """真的 OidcClient,只換掉會打網路的部分——測的是 callback 的分流,不是 HTTP。"""

    async def load_discovery(self, force: bool = False) -> Discovery:
        return Discovery(
            issuer="https://idp.example.test",
            authorization_endpoint="https://idp.example.test/auth",
            token_endpoint="https://idp.example.test/token",
            jwks_uri="https://idp.example.test/certs",
        )

    async def exchange_code(self, code: str, verifier: str) -> dict:
        return {"access_token": "a", "refresh_token": "r", "id_token": "i"}

    def verify(self, token: str, *, expected_nonce: str | None = None) -> dict:
        return self.verify_access_token(token)

    def verify_access_token(self, token: str) -> dict:
        return {"sub": DISABLED_SUB, "aud": self._settings.oidc_client_id}


def _bake(setter, data) -> str:
    """把 codec 寫出的 Set-Cookie 取出純值,供測試當成瀏覽器帶回的 cookie 使用。"""
    holder = Response()
    setter(holder, data)
    return holder.headers["set-cookie"].split("=", 1)[1].split(";")[0]


# --- 1. 沒登入就進登入頁 -----------------------------------------------------


async def test_匿名瀏覽器開首頁_302到登入頁(client, app):
    """卡片點進來的人不該看到落地頁,直接送去登入。"""
    resp = await client.get("/", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 302, resp.text
    assert urlparse(resp.headers["location"]).path == f"{PREFIX}/auth/login"


async def test_匿名首頁轉址是互動式登入_不帶silent(client, app):
    """🔴 靜默探測(prompt=none)失敗時會**無聲送回落地頁**——正是要消滅的那一頁。

    互動式登入同樣達成 T64 的目的(IdP 有 session 就直接導回、不顯示畫面),
    差別只在沒有 session 時會顯示登入畫面,而那正是這次要的結果。
    """
    resp = await client.get("/", headers=BROWSER, follow_redirects=False)
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert "silent" not in query, "首頁不應再發起靜默探測"


async def test_非瀏覽器開首頁_維持200落地頁(client, app):
    """🔴 冒煙與監控的視角(`Accept: */*`)必須與從前一模一樣。"""
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200


# --- 2. 沒權限就回入口 -------------------------------------------------------


async def test_待開通者開首頁_302回平台入口(client, app, oidc):
    await make_user(app, "sub-pending-entry", status=UserStatus.pending)
    resp = await client.get(
        "/",
        headers={**BROWSER, **auth(oidc.issue("sub-pending-entry"))},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == app.state.settings.portal_home_url


async def test_待開通者用非瀏覽器開首頁_維持200(client, app, oidc):
    """轉址是給人看的;機器端點的行為不因這次改動而變。"""
    await make_user(app, "sub-pending-curl", status=UserStatus.pending)
    resp = await client.get(
        "/", headers=auth(oidc.issue("sub-pending-curl")), follow_redirects=False
    )
    assert resp.status_code == 200


async def test_停用者登入回呼_不建立session且回平台入口(client, app):
    """🔴 迴圈防線:停用者若拿到 session,首頁仍認不得他(403 被 `optional_identity`
    吞成 None),就會在首頁與 IdP 之間永遠繞下去——而且每一圈都是無聲的,
    使用者只看得到瀏覽器一直轉。停用要在 callback 當場擋掉。
    """
    await make_user(app, DISABLED_SUB, status=UserStatus.disabled)
    app.state.oidc = _CallbackOidc(app.state.settings)
    codec = app.state.cookies
    raw = _bake(
        codec.set_login_state,
        LoginState(state="s-x", verifier="v-x", nonce="n-x", next_path="/"),
    )

    resp = await client.get(
        "/oidc/callback/?code=c&state=s-x",
        cookies={codec.login_cookie_name: raw},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == app.state.settings.portal_home_url
    assert app.state.settings.session_cookie_name not in resp.cookies


# --- 3. 有權限就進總覽 -------------------------------------------------------


async def test_已開通者開首頁_200看得到專案總覽(client, app, oidc):
    await make_user(app, "sub-active-entry")
    resp = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-active-entry"))}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert "請先" not in resp.text, "已開通者不該看到落地頁文案"


# --- 迴圈防線 ---------------------------------------------------------------


async def test_平台入口指回本服務時_未開通者退回pending(client, app, oidc):
    """設定錯了要停得下來,不要在兩個服務之間彈跳。"""
    app.state.settings.portal_home_url = f"{PREFIX}/"
    await make_user(app, "sub-loop-guard", status=UserStatus.pending)
    resp = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-loop-guard"))}, follow_redirects=False
    )
    assert resp.status_code == 302, resp.text
    assert urlparse(resp.headers["location"]).path == f"{PREFIX}/pending"
