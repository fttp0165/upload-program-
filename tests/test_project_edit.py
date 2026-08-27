"""T101:專案標題與簡介可編輯,以及一條權限不一致的修正。

Benny:「專案標題與內容 發布者都可以編輯」。

🔴 施工前讀 API 時發現:`PATCH /v1/projects/{slug}` 只要 **maintainer**,
而它的 `ProjectUpdate` **包含 `visibility`** —— 而 T100 剛把「誰看得到」定為
**owner 專屬**。兩者相加 = **maintainer 繞過介面打一次 API,就能把 private 專案
改成全公司可見**。那不是排版不一致,是內容外洩的路徑。

T100 的計畫書寫過「介面若比 API 寬鬆等於開第二套規則」;這次是**反過來**——
API 比介面寬鬆,而**寬的那邊才是真正生效的那邊**。
"""

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def _setup(client, app, oidc, slug, visibility="internal"):
    """造一個專案 + 一位 maintainer + 一位 viewer,回傳三組 token。"""
    await make_user(app, f"{slug}-owner")
    owner_token = oidc.issue(f"{slug}-owner")
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "原標題", "summary": "原簡介", "visibility": visibility},
        headers=auth(owner_token),
    )
    assert resp.status_code == 201, resp.text

    mt = await make_user(app, f"{slug}-mt")
    viewer = await make_user(app, f"{slug}-viewer")
    for user, role in ((mt, "maintainer"), (viewer, "viewer")):
        put = await client.put(
            f"/v1/projects/{slug}/members",
            json={"user_id": str(user.id), "role": role},
            headers=auth(owner_token),
        )
        assert put.status_code == 200, put.text
    return owner_token, oidc.issue(f"{slug}-mt"), oidc.issue(f"{slug}-viewer")


# --- 編輯標題與簡介 ---------------------------------------------------------


async def test_maintainer可以改標題與簡介(client, app, oidc):
    owner_token, mt_token, _ = await _setup(client, app, oidc, "edit-proj")

    resp = await client.post(
        "/projects/edit-proj/edit",
        data={"name": "新標題", "summary": "新的簡介內容"},
        headers={**BROWSER, **auth(mt_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    detail = (await client.get("/v1/projects/edit-proj", headers=auth(owner_token))).json()
    assert detail["name"] == "新標題"
    assert detail["summary"] == "新的簡介內容"


async def test_viewer不能改也看不到表單(client, app, oidc):
    _, _, viewer_token = await _setup(client, app, oidc, "guard-edit")

    page = await client.get(
        "/projects/guard-edit", headers={**BROWSER, **auth(viewer_token)}
    )
    assert page.status_code == 200
    assert "編輯專案" not in page.text

    resp = await client.post(
        "/projects/guard-edit/edit",
        data={"name": "偷改", "summary": ""},
        headers={**BROWSER, **auth(viewer_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_空白標題被拒(client, app, oidc):
    """`ProjectUpdate` 的 name 是 min_length=1;介面不得比 schema 寬鬆。"""
    owner_token, mt_token, _ = await _setup(client, app, oidc, "blank-name")

    resp = await client.post(
        "/projects/blank-name/edit",
        data={"name": "   ", "summary": "x"},
        headers={**BROWSER, **auth(mt_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, "應回到專案頁顯示錯誤,不是靜靜接受"
    detail = (await client.get("/v1/projects/blank-name", headers=auth(owner_token))).json()
    assert detail["name"] == "原標題", "被拒的編輯不得寫進資料庫"


async def test_編輯留稽核(client, app, oidc):
    from sqlalchemy import select

    from app.models import AuditEvent

    _, mt_token, _ = await _setup(client, app, oidc, "audit-edit")
    await client.post(
        "/projects/audit-edit/edit",
        data={"name": "改過的名字", "summary": ""},
        headers={**BROWSER, **auth(mt_token)},
        follow_redirects=False,
    )
    async with app.state.sessionmaker() as session:
        actions = [
            a.value if hasattr(a, "value") else a
            for a in (await session.execute(select(AuditEvent.action))).scalars().all()
        ]
    assert "project.update" in actions


async def test_短名不可改(client, app, oidc):
    """🔴 slug 在網址裡,而平台沒有轉址 —— 改它會讓別人貼出去的連結死掉(T96 已載明)。"""
    owner_token, mt_token, _ = await _setup(client, app, oidc, "keep-slug")

    await client.post(
        "/projects/keep-slug/edit",
        data={"name": "沒關係", "summary": "", "slug": "hijacked"},
        headers={**BROWSER, **auth(mt_token)},
        follow_redirects=False,
    )
    assert (await client.get("/v1/projects/keep-slug", headers=auth(owner_token))).status_code == 200
    assert (await client.get("/v1/projects/hijacked", headers=auth(owner_token))).status_code == 404


# --- 🔴 修正:PATCH 的可見性必須是 owner 才能改 ------------------------------


async def test_maintainer不能用API改可見性(client, app, oidc):
    """🔴 本任務的核心修正:這條路徑原本是通的,等於 private 專案的外洩後門。"""
    owner_token, mt_token, _ = await _setup(client, app, oidc, "vis-api", visibility="private")

    resp = await client.patch(
        "/v1/projects/vis-api",
        json={"visibility": "internal"},
        headers=auth(mt_token),
    )
    assert resp.status_code == 403, resp.text

    detail = (await client.get("/v1/projects/vis-api", headers=auth(owner_token))).json()
    assert detail["visibility"] == "private", "被擋下的請求不得留下任何效果"


async def test_owner仍可用API改可見性(client, app, oidc):
    """收窄不等於封死:owner 的權限一字不變。"""
    owner_token, _, _ = await _setup(client, app, oidc, "vis-owner", visibility="private")

    resp = await client.patch(
        "/v1/projects/vis-owner",
        json={"visibility": "internal"},
        headers=auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["visibility"] == "internal"


async def test_maintainer仍可用API改名稱(client, app, oidc):
    """只收窄 visibility 那一項,不連坐其他欄位。"""
    _, mt_token, _ = await _setup(client, app, oidc, "name-api")

    resp = await client.patch(
        "/v1/projects/name-api", json={"name": "API 改的名字"}, headers=auth(mt_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "API 改的名字"
