"""T146:開通即建帳(SVC-PUSH)接收端點 `POST /v1/provision`。

依 cats-portal 徵詢函 v1.0(2026-09-09 18:00 UTC+8,存於 `docs/plans/inbox/`)。

本檔釘住三組行為:

1. **四層驗證,deny-by-default**(徵詢函 §2.1)—— 每一層都有一條反向測試。
   🔴 第 4 層(呼叫者本人的角色)最要緊:少了它,任何拿得到該 scope 的登入者
   都能在我方庫裡建列。
2. **冪等與「不碰既有列」** —— 重送同一筆結果相同;既有列的名字與信箱
   **不得被抹掉**(`security.upsert_user()` 會覆寫成 NULL,本端點不得重用它)。
3. **只收三個欄位** —— 多一個 `email` 就 422。這是我方這一側對
   「業務庫只存 sub」的結構保證。

🔴 契約 §4.8:本檔所有 sub / token 都是編出來的字串,不含真實個資。
"""

from sqlalchemy import select

from app.models import AuditEvent, PlatformRole, User, UserStatus
from tests.conftest import TEST_CLIENT_ID, auth, make_user

SCOPE = f"account:provision:{TEST_CLIENT_ID}"
SUBJECT = "0b8c1f0e-1111-4c4c-9a9a-000000000001"  # 被開通者(假)


def _admin_token(oidc, **overrides) -> str:
    """portal 平台管理員的推送 token:aud 含本服務、帶 scope、帶 realm 角色。"""
    claims = {
        "aud": [TEST_CLIENT_ID, "account"],
        "azp": "portal-admin",
        "scope": f"openid profile {SCOPE}",
        "realm_access": {"roles": ["portal-admin", "default-roles-sporton"]},
        "groups": ["/org/HY/eng"],
    }
    claims.update(overrides)
    return oidc.issue("sub-portal-admin", **claims)


def _body(**overrides) -> dict:
    body = {"subject": SUBJECT, "service": "upload", "action": "join"}
    body.update(overrides)
    return body


async def _user(app, sub: str) -> User | None:
    async with app.state.sessionmaker() as session:
        return (await session.execute(select(User).where(User.sub == sub))).scalar_one_or_none()


async def _audits(app, action: str) -> list[AuditEvent]:
    async with app.state.sessionmaker() as session:
        rows = await session.execute(select(AuditEvent).where(AuditEvent.action == action))
        return list(rows.scalars())


# --- 1. 正向:join 新人 -------------------------------------------------------


async def test_join新人_建一列pending且無名字(client, app, oidc):
    resp = await client.post("/v1/provision", json=_body(), headers=auth(_admin_token(oidc)))
    assert resp.status_code == 201, resp.text
    assert resp.json()["result"] == "created"

    user = await _user(app, SUBJECT)
    assert user is not None
    # 🔴 pending 而不是 active:開通仍是本服務管理員的職權(契約 §4.4、T63)。
    assert user.status is UserStatus.pending
    assert user.platform_role is PlatformRole.member
    # 推送不帶任何個資,所以這兩欄必然是空的 —— 名字要等本人登入才會有。
    assert user.display_name_cache is None
    assert user.notify_email is None


async def test_join新人_寫一筆稽核(client, app, oidc):
    await client.post("/v1/provision", json=_body(), headers=auth(_admin_token(oidc)))
    events = await _audits(app, "user.provision")
    assert len(events) == 1
    user = await _user(app, SUBJECT)
    assert events[0].target_id == user.id


async def test_呼叫者不會因推送而在本服務被建帳(client, app, oidc):
    """🔴 驗的是 portal 管理員的 token,但**不得**走 `get_identity` 首登自建 ——
    否則每個按過按鈕的 portal 管理員都會憑空變成本服務的 pending 使用者。"""
    await client.post("/v1/provision", json=_body(), headers=auth(_admin_token(oidc)))
    assert await _user(app, "sub-portal-admin") is None


# --- 2. 冪等與不碰既有列 -----------------------------------------------------


async def test_重送同一筆_結果相同不重複建列(client, app, oidc):
    token = _admin_token(oidc)
    r1 = await client.post("/v1/provision", json=_body(), headers=auth(token))
    r2 = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.json()["result"] == "exists"
    async with app.state.sessionmaker() as session:
        rows = (await session.execute(select(User).where(User.sub == SUBJECT))).scalars().all()
    assert len(rows) == 1
    # 第二次什麼都沒發生,所以不留第二筆稽核(稽核記的是「事情真的發生了」)。
    assert len(await _audits(app, "user.provision")) == 1


async def test_join既有人_名字信箱狀態一個都不動(client, app, oidc):
    """🔴 `upsert_user()` 的語意是「每次登入覆寫,含覆寫成 NULL」;
    推送沒有 claims 可給,重用它就會每推一次抹掉一次那個人的名字。"""
    existing = await make_user(app, SUBJECT, status=UserStatus.active)
    async with app.state.sessionmaker() as session:
        row = await session.get(User, existing.id)
        row.display_name_cache = "測試甲"
        row.notify_email = "fake@example.test"
        await session.commit()

    resp = await client.post("/v1/provision", json=_body(), headers=auth(_admin_token(oidc)))
    assert resp.status_code == 200
    user = await _user(app, SUBJECT)
    assert user.display_name_cache == "測試甲"
    assert user.notify_email == "fake@example.test"
    assert user.status is UserStatus.active


async def test_join已停用者_不得被推送解除停用(client, app, oidc):
    """停權是本服務管理員的刻意決定;推送不是繞過停權的後門。"""
    await make_user(app, SUBJECT, status=UserStatus.disabled)
    resp = await client.post("/v1/provision", json=_body(), headers=auth(_admin_token(oidc)))
    assert resp.status_code == 200
    assert (await _user(app, SUBJECT)).status is UserStatus.disabled


# --- 3. leave = 忽略 ---------------------------------------------------------


async def test_leave_忽略_不建列(client, app, oidc):
    resp = await client.post(
        "/v1/provision", json=_body(action="leave"), headers=auth(_admin_token(oidc))
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == "ignored"
    assert await _user(app, SUBJECT) is None


async def test_leave_忽略_不改既有狀態(client, app, oidc):
    await make_user(app, SUBJECT, status=UserStatus.active)
    resp = await client.post(
        "/v1/provision", json=_body(action="leave"), headers=auth(_admin_token(oidc))
    )
    assert resp.status_code == 200
    assert (await _user(app, SUBJECT)).status is UserStatus.active


# --- 4. 四層驗證(反向)-----------------------------------------------------


async def test_無token_401(client, app):
    resp = await client.post("/v1/provision", json=_body())
    assert resp.status_code == 401
    assert await _user(app, SUBJECT) is None


async def test_token無效_401(client, app):
    resp = await client.post("/v1/provision", json=_body(), headers=auth("tok-not-issued"))
    assert resp.status_code == 401


async def test_aud不含本服務_401(client, app, oidc):
    """🔴 不接受 `verify_access_token` 的 azp 退路:那條是給本服務自己登入的 token 用的,
    而推送 token 的 azp 是 portal-admin —— 必須靠 aud 明確指名本服務。"""
    token = _admin_token(oidc, aud=["portal-admin", "account"])
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 401
    assert await _user(app, SUBJECT) is None


async def test_本服務自己的登入token不能拿來推送(client, app, oidc):
    """一般使用者的登入 token(aud=本服務、無 provision scope)→ 403。"""
    token = oidc.issue("sub-someone", realm_access={"roles": ["portal-admin"]})
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 403
    assert await _user(app, SUBJECT) is None


async def test_缺scope_403(client, app, oidc):
    token = _admin_token(oidc, scope="openid profile email")
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 403
    assert await _user(app, SUBJECT) is None


async def test_別的App的scope不算數_403(client, app, oidc):
    """scope 必須是**本服務**的那一個;拿 survey 的票來打 upload 不行。"""
    token = _admin_token(oidc, scope="openid account:provision:survey")
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 403


async def test_有scope但只是一般使用者_403(client, app, oidc):
    """🔴 徵詢函 §2.1 第 3 條:scope 說「這張票允許走這條路」,角色說「這個人有資格」。"""
    token = _admin_token(oidc, realm_access={"roles": ["default-roles-sporton"]}, groups=[])
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 403
    assert await _user(app, SUBJECT) is None


async def test_部門主管可以推送(client, app, oidc):
    token = _admin_token(
        oidc, realm_access={"roles": []}, groups=["/org/HY/eng", "/mgr/HY/eng"]
    )
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 201


async def test_群組名只是含mgr字樣不算主管_403(client, app, oidc):
    """判準是**前綴** `/mgr/`,不是子字串 —— `/org/mgr-team` 不是主管。"""
    token = _admin_token(oidc, realm_access={"roles": []}, groups=["/org/mgr-team"])
    resp = await client.post("/v1/provision", json=_body(), headers=auth(token))
    assert resp.status_code == 403


# --- 5. payload 只收三個欄位 ---------------------------------------------------


async def test_多送email_422(client, app, oidc):
    """🔴 多送一個 email 不得被默默吃下 —— 那就是個資落地在別人送來的那一刻。"""
    resp = await client.post(
        "/v1/provision",
        json=_body(email="fake@example.test"),
        headers=auth(_admin_token(oidc)),
    )
    assert resp.status_code == 422
    assert await _user(app, SUBJECT) is None


async def test_service不是upload_422(client, app, oidc):
    resp = await client.post(
        "/v1/provision", json=_body(service="survey"), headers=auth(_admin_token(oidc))
    )
    assert resp.status_code == 422
    assert await _user(app, SUBJECT) is None


async def test_action未知_422(client, app, oidc):
    resp = await client.post(
        "/v1/provision", json=_body(action="delete"), headers=auth(_admin_token(oidc))
    )
    assert resp.status_code == 422


async def test_subject不是UUID_422(client, app, oidc):
    """Keycloak 的 sub 是 UUID;不是的話寧可拒收,不建一列沒有人會登入的垃圾。"""
    resp = await client.post(
        "/v1/provision", json=_body(subject="not-a-uuid"), headers=auth(_admin_token(oidc))
    )
    assert resp.status_code == 422
