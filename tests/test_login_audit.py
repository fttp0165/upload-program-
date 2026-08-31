"""T108:登入這件事要進稽核,後台要看得到最後登入時間。

盤點的結果是「缺的不是儲存,是兩件事」:
1. `users.last_login_at` 只留**最後一次**,而稽核表才是回答「誰在何時做了什麼」
   的地方 —— 登入卻不在裡面。
2. 那個欄位存了兩個月,後台**一個畫面都沒顯示過**。

🔴 `user.login_denied`(停用帳號嘗試登入)比成功登入更值得記:契約 §1.1 明載
離職不會自動停用 IdP 帳號,deny-by-default 是第二道防線 —— 那道防線擋下了誰,
目前只留在會滾掉的 stdout log 裡。

🔴 稽核只記 `actor_id`,不記 email / 姓名 / IP(理由見 dev-log)。
"""

import pytest
from fastapi.responses import Response
from sqlalchemy import select

from app.models import AuditEvent, UserStatus
from app.oidc import Discovery, OidcClient
from app.session import LoginState
from tests.conftest import auth, make_user

ISSUER = "https://auth.example.test/realms/test"
BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


class _StubbedOidc(OidcClient):
    """真的 OidcClient,只換掉會打網路的部分(比照 test_logout_session.py)。"""

    def __init__(self, settings, sub: str) -> None:
        super().__init__(settings)
        self._sub = sub

    async def load_discovery(self, force: bool = False) -> Discovery:
        return Discovery(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/auth",
            token_endpoint=f"{ISSUER}/token",
            jwks_uri=f"{ISSUER}/certs",
            end_session_endpoint=f"{ISSUER}/logout",
        )

    async def exchange_code(self, code: str, verifier: str) -> dict:
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "id_token": "fake-id-token",
        }

    def verify(self, token: str, *, expected_nonce: str | None = None) -> dict:
        return {"sub": self._sub, "aud": self._settings.oidc_client_id, "iss": ISSUER}

    def verify_access_token(self, token: str) -> dict:
        return {
            "sub": self._sub,
            "aud": self._settings.oidc_client_id,
            "iss": ISSUER,
            "name": "登入測試員",
            "email": "login-tester@sporton.com.tw",
            "email_verified": True,
        }


def _bake(setter, data) -> str:
    holder = Response()
    setter(holder, data)
    return holder.headers["set-cookie"].split("=", 1)[1].split(";")[0]


async def _login(client, app, sub: str):
    """走完一次真的 callback 往返,回傳回應。"""
    app.state.oidc = _StubbedOidc(app.state.settings, sub)
    raw = _bake(
        app.state.cookies.set_login_state,
        LoginState(state="s-1", verifier="v-1", nonce="n-1", next_path="/"),
    )
    return await client.get(
        "/oidc/callback/?code=abc&state=s-1",
        cookies={app.state.cookies.login_cookie_name: raw},
        follow_redirects=False,
    )


async def _events(app, action: str) -> list[AuditEvent]:
    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(select(AuditEvent).where(AuditEvent.action == action))
        ).scalars().all()
    return list(rows)


async def test_登入成功寫下稽核(client, app):
    user = await make_user(app, "login-ok", status=UserStatus.active)

    resp = await _login(client, app, "login-ok")
    assert resp.status_code == 302, resp.text

    events = await _events(app, "user.login")
    assert len(events) == 1, "登入沒有進稽核 —— last_login_at 只答得出最後一次"
    assert events[0].actor_id == user.id


async def test_停用帳號登入被拒也要留痕(client, app):
    """🔴 這一條比成功登入更值得記(契約 §1.1:離職不會自動停用 IdP 帳號)。"""
    await make_user(app, "login-off", status=UserStatus.disabled)

    resp = await _login(client, app, "login-off")
    assert resp.status_code == 302, resp.text

    denied = await _events(app, "user.login_denied")
    assert len(denied) == 1, "被 deny-by-default 擋下的登入只留在會滾掉的 log 裡"
    assert await _events(app, "user.login") == []


async def test_登入稽核不含email與姓名(client, app):
    """反向驗證紅線:稽核內容不落地個資(§4.2a L1 第 3 條 / L1b)。"""
    await make_user(app, "login-priv", status=UserStatus.active)
    await _login(client, app, "login-priv")

    for event in await _events(app, "user.login"):
        assert "@" not in event.target_label
        assert "登入測試員" not in event.target_label


async def test_後台使用者頁顯示最後登入時間(client, app, oidc, admin_user):
    user = await make_user(app, "login-shown", status=UserStatus.active)
    await _login(client, app, "login-shown")

    _, admin_token = admin_user
    app.state.oidc = oidc  # 還原成 FakeOidc,讓管理員的 token 驗得過
    page = await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})
    assert page.status_code == 200, page.text

    async with app.state.sessionmaker() as session:
        refreshed = await session.get(type(user), user.id)
        stamp = refreshed.last_login_at
    assert stamp is not None
    assert stamp.strftime("%Y-%m-%d") in page.text, "後台看不到最後登入時間"


async def test_從未登入的帳號顯示從未登入(client, app, oidc, admin_user):
    """守門:別印出空白或 None —— 「沒有值」與「沒顯示」在畫面上長得一樣。"""
    await make_user(app, "never-logged-in", status=UserStatus.active)

    _, admin_token = admin_user
    page = await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})
    assert page.status_code == 200
    assert "從未登入" in page.text
    assert "None" not in page.text


@pytest.mark.parametrize("action", ["user.login", "user.login_denied"])
def test_新動作在字彙表裡(action):
    """稽核頁的篩選清單來自 AuditAction,漏加就是「記了但篩不到」。"""
    from app.audit import AuditAction

    assert action in {a.value for a in AuditAction}
