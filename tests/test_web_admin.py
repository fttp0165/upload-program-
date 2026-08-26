"""T45 待開通頁與管理後台(F75、F76)。

🔴 本檔最重要的一條:**待開通頁必須顯示使用者自己的 `sub`**。

契約 §4.2 規定業務庫只存 `sub`,沒有 email、沒有姓名。所以管理後台的清單裡
只有一排 UUID。使用者要怎麼告訴管理員「我是誰」?管理員又要怎麼認出他?
——唯一的答案是把 `sub` 顯示給使用者,讓他複製給管理員。
少了這一步,這兩邊永遠對不上。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
# T67:平台入口(`/`)也是平台層網址,同一類具名例外。
# 🔴 白名單納入 `/` 會讓「漏掉 url() 的首頁連結」逃過這條檢查——
#    補償斷言在 test_portal_link.py(每條 `/` 都必須帶 nav-exit / side-exit 標記)。
PLATFORM_URLS = {"/account", "/login", "/"}


def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


# --- F75 待開通頁 -----------------------------------------------------------


async def test_待開通者看得到指引頁(client, app, oidc):
    from app.models import UserStatus
    from app.problems import pending_activation

    await make_user(app, "sub-pending45", status=UserStatus.pending)
    token = oidc.issue("sub-pending45")

    resp = await client.get("/pending", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    assert pending_activation().detail in resp.text


async def test_待開通頁顯示使用者自己的sub(client, app, oidc):
    """🔴 這是使用者唯一能提供給管理員的識別。

    業務庫只存 sub,管理後台的清單也只有 UUID——不把它顯示出來,
    使用者說「我是王小明」時管理員的畫面上根本沒有那四個字。
    """
    from app.models import UserStatus

    await make_user(app, "sub-identify-me", status=UserStatus.pending)
    token = oidc.issue("sub-identify-me", name="王小明")

    body = (await client.get("/pending", headers={**BROWSER, **auth(token)})).text
    assert "sub-identify-me" in body, "待開通頁必須顯示使用者自己的 sub"


async def test_待開通頁有帳號設定連結(client, app, oidc):
    """契約 §2.1 / §4.8。"""
    from app.models import UserStatus

    await make_user(app, "sub-pending45b", status=UserStatus.pending)
    token = oidc.issue("sub-pending45b")

    body = (await client.get("/pending", headers={**BROWSER, **auth(token)})).text
    assert 'href="/account"' in body


async def test_已開通者開待開通頁會導回首頁(client, active_user):
    """停在一頁「你已經開通了」沒有意義,還會讓人以為出錯。"""
    _, token = active_user
    resp = await client.get(
        "/pending", headers={**BROWSER, **auth(token)}, follow_redirects=False
    )
    assert resp.status_code in (302, 303)


async def test_未登入開待開通頁會302(client):
    resp = await client.get("/pending", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 302


async def test_各頁的待開通提示連到待開通頁(client, app, oidc):
    """完整指引在專屬頁面,其他頁面只要指路。"""
    from app.models import UserStatus

    await make_user(app, "sub-pending45c", status=UserStatus.pending)
    token = oidc.issue("sub-pending45c")

    # T81:待開通者開首頁會被送回平台入口,改用 /help(待開通者仍看得到的頁面)
    body = (await client.get("/help", headers={**BROWSER, **auth(token)})).text
    assert f'href="{PREFIX}/pending"' in body


# --- 🔴 F76 管理後台:權限 -------------------------------------------------


async def test_非管理員開管理後台回403(client, active_user):
    _, token = active_user
    resp = await client.get("/admin/users", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 403


async def test_未登入開管理後台會302(client):
    resp = await client.get("/admin/users", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 302


async def test_管理員看得到待開通清單(client, app, oidc, admin_user):
    from app.models import UserStatus

    _, admin_token = admin_user
    await make_user(app, "sub-waiting-1", status=UserStatus.pending)
    await make_user(app, "sub-waiting-2", status=UserStatus.pending)

    body = (await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})).text
    assert "sub-waiting-1" in body
    assert "sub-waiting-2" in body


async def test_管理後台說明為何只有sub(client, admin_user):
    """第一次用的管理員一定會問「怎麼沒有名字」。

    與其讓人以為壞掉,不如講清楚:業務庫依契約只存 sub,這是紅線不是缺陷。
    """
    _, admin_token = admin_user
    body = (await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})).text
    assert "sub" in body
    assert "不落地" in body or "只存" in body or "個資" in body


async def test_管理後台不顯示個資欄位(client, app, oidc, admin_user):
    """🔴 業務庫結構上就沒有 email 欄位,頁面自然也不該有。

    **T85 修正這條測試的斷言方式。** 原本是整頁搜尋「姓名」二字,那在 T59 之前
    等價於「頁面沒有名字」;T59(契約 §4.2a L1)之後**名字本來就會顯示**,
    這條測試能繼續綠只是因為當時的文案剛好沒用到那兩個字——它守的東西早已不是
    它字面上寫的東西,而任何一句提到「姓名」的說明文字都會誤觸它。

    改成斷言**真正的 invariant**:頁面不得出現 email。並且用 `@` 掃全頁,
    比原本只找「電子郵件」這個標籤更嚴——標籤可以改名,email 本身不會沒有 `@`。

    名字的邊界由 §4.2a L1 的專屬測試把關(`test_display_name_cache.py`、
    `test_pending_list_names.py`),不靠這條的字串比對代管。
    """
    _, admin_token = admin_user
    await make_user(app, "sub-noleak", status=__import__(
        "app.models", fromlist=["UserStatus"]).UserStatus.pending)

    body = (await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})).text
    assert "電子郵件" not in body
    assert "@" not in body, "頁面出現 email——業務庫結構上根本沒有這個欄位"


# --- 一鍵開通 ---------------------------------------------------------------


async def test_一鍵開通(client, app, oidc, admin_user):
    from sqlalchemy import select

    from app.models import User, UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-to-activate", status=UserStatus.pending)

    resp = await client.post(
        f"/admin/users/{target.id}/activate",
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == "sub-to-activate"))).scalar_one()
        assert user.status is UserStatus.active
        assert user.activated_at is not None, "開通時間要留下來"


async def test_開通後對方立刻能用(client, app, oidc, admin_user):
    """契約 §7 冒煙第 3 項:派角色 → 即時通行。"""
    from app.models import UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-instant", status=UserStatus.pending)
    token = oidc.issue("sub-instant")

    before = await client.get("/v1/projects", headers=auth(token))
    assert before.status_code == 403

    await client.post(
        f"/admin/users/{target.id}/activate",
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )

    after = await client.get("/v1/projects", headers=auth(token))
    assert after.status_code == 200


async def test_非管理員不能開通別人(client, active_user, app):
    from app.models import UserStatus

    _, token = active_user
    target = await make_user(app, "sub-victim", status=UserStatus.pending)

    resp = await client.post(
        f"/admin/users/{target.id}/activate",
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- 🔴 停用 ----------------------------------------------------------------


async def test_不得停用自己(client, app, admin_user):
    """🔴 停用自己會讓平台可能一個管理員都不剩。API 已有這條規則,網頁沿用同一條。"""
    admin, admin_token = admin_user

    resp = await client.post(
        f"/admin/users/{admin.id}/disable",
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=True,
    )
    assert "不能停用自己" in resp.text

    from sqlalchemy import select

    from app.models import User, UserStatus

    async with app.state.sessionmaker() as session:
        me = (await session.execute(select(User).where(User.sub == "sub-admin"))).scalar_one()
        assert me.status is UserStatus.active, "自己不該被停用"


async def test_可以停用別人(client, app, admin_user):
    from sqlalchemy import select

    from app.models import User, UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-to-disable", status=UserStatus.active)

    resp = await client.post(
        f"/admin/users/{target.id}/disable",
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == "sub-to-disable"))).scalar_one()
        assert user.status is UserStatus.disabled


# --- 連結與逸出 -------------------------------------------------------------


async def test_新頁面的連結帶前綴且無絕對網址(client, app, oidc, admin_user):
    from app.models import UserStatus

    _, admin_token = admin_user
    await make_user(app, "sub-linkcheck45", status=UserStatus.pending)

    resp = await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})
    assert resp.status_code == 200
    for link in _links(resp.text):
        if link in PLATFORM_URLS:
            continue
        assert link.startswith(f"{PREFIX}/"), link
        assert not link.startswith(("http://", "https://", "//")), link


async def test_sub含特殊字元時逸出(client, app, oidc, admin_user):
    """sub 來自 IdP,理論上是 UUID,但不該假設。"""
    from app.models import UserStatus

    _, admin_token = admin_user
    await make_user(app, '<script>alert(1)</script>', status=UserStatus.pending)

    body = (await client.get("/admin/users", headers={**BROWSER, **auth(admin_token)})).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# --- T101 管理員指派入口 ----------------------------------------------------
#
# Benny:「建立管理員權限」。盤點後發現後端九成已存在(`platform_role` 欄位、
# `PATCH /v1/admin/users/{id}`、後台的管理員徽章、`BOOTSTRAP_ADMIN_SUBS`),
# 缺的只是網頁按鈕——與 T100 同一個形狀:功能在,入口沒有。
# 而**會打 API 的人不需要這個系統的後台**,所以「只能打 API」等於沒有。
#
# 🔴 三條防呆,因為這是給權限不是給顏色:
# 1. 不能取消自己——平台沒有 root 後門,最後一個管理員把自己降級,
#    就沒有人能再指派任何人,只能改 .env 重啟容器才救得回來;
# 2. 不能把未開通者設為管理員——待開通是 deny-by-default,
#    跳過開通直接給管理權等於用後門繞過自己的門禁;
# 3. 稽核與 API 產生**同一個 action**(`user.set_role`),
#    紀錄不該因為用網頁還是 API 而長得不一樣。

async def test_管理員可從網頁指派與取消管理員(client, app, oidc, admin_user):
    from sqlalchemy import select

    from app.models import PlatformRole, User

    _, admin_token = admin_user
    target = await make_user(app, "sub-promote-t101")

    up = await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "admin"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert up.status_code in (302, 303), up.text
    async with app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.sub == "sub-promote-t101"))
        ).scalar_one()
        assert user.platform_role is PlatformRole.admin

    down = await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "member"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert down.status_code in (302, 303), down.text
    async with app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.sub == "sub-promote-t101"))
        ).scalar_one()
        assert user.platform_role is PlatformRole.member


async def test_不能取消自己的管理員身分(client, app, oidc, admin_user):
    """🔴 最後一個管理員把自己降級 = 沒有人能再指派任何人,只剩改 .env 重啟。"""
    from sqlalchemy import select

    from app.models import PlatformRole, User

    me, admin_token = admin_user

    resp = await client.post(
        f"/admin/users/{me.id}/role",
        data={"role": "member"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 409, resp.text

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == "sub-admin"))).scalar_one()
        assert user.platform_role is PlatformRole.admin, "自己降自己必須被擋下"


async def test_不能把未開通者設為管理員(client, app, oidc, admin_user):
    """🔴 待開通是 deny-by-default;跳過開通直接給管理權等於繞過自己的門禁。"""
    from sqlalchemy import select

    from app.models import PlatformRole, User, UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-pending-t101", status=UserStatus.pending)

    resp = await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "admin"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 409, resp.text

    async with app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.sub == "sub-pending-t101"))
        ).scalar_one()
        assert user.platform_role is PlatformRole.member


async def test_網頁改角色與API產生同一個稽核action(client, app, oidc, admin_user):
    """稽核紀錄不該因為管理員用的是網頁還是 API 而長得不一樣。"""
    from sqlalchemy import select

    from app.audit import AuditAction
    from app.models import AuditEvent

    _, admin_token = admin_user
    target = await make_user(app, "sub-audit-t101")

    await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "admin"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )

    async with app.state.sessionmaker() as session:
        events = (
            await session.execute(
                select(AuditEvent).where(AuditEvent.target_id == target.id)
            )
        ).scalars().all()
    actions = [e.action for e in events]
    assert AuditAction.user_set_role in actions, f"應留下 user_set_role,實得:{actions}"


async def test_使用者頁面有指派按鈕(client, app, oidc, admin_user):
    """入口要看得到——否則等於只能打 API,而會打 API 的人不需要後台。"""
    _, admin_token = admin_user
    target = await make_user(app, "sub-button-t101")

    resp = await client.get(f"{PREFIX}/admin/users", headers={**BROWSER, **auth(admin_token)})
    assert resp.status_code == 200
    assert f"/admin/users/{target.id}/role" in resp.text
