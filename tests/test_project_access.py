"""T100:發布者自行設定查看權限 + ADMIN 都看得到。

Benny:「發布者可以設定使用者查看權限」+「ADMIN 則都可以看到」。
裁示:**輸入名字搜尋**、**沿用現有三種角色**。

🔴 本檔釘住兩組東西:

1. **介面的權限邊界** —— 權限模型與 API 早就齊備(`ProjectMember` / `visibility` /
   `require_project_read`),本任務只是給它介面。介面若比 API 寬鬆,
   等於開了第二套規則,而兩套規則遲早分岔 —— 那是 private 專案外洩的典型起點。
2. **「ADMIN 都看得到」** —— 這條行為施工前**已經成立卻沒有任何測試**
   (只靠 `security.py` 的 `or identity.user.is_admin` 那一行活著)。
   使用者明確要求的行為必須有守門,否則下一次有人「優化」那行條件時不會有東西攔他。
"""

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def _project(client, token, slug, visibility="private"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": slug, "visibility": visibility},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- ADMIN 都看得到(施工前沒有測試的那一塊)---------------------------------


async def test_admin看得到別人的private專案(client, app, oidc):
    owner = await make_user(app, "sub-p-owner")
    assert owner is not None
    await _project(client, oidc.issue("sub-p-owner"), "secret-proj")

    await make_user(app, "sub-p-admin", admin=True)
    resp = await client.get(
        "/projects/secret-proj", headers={**BROWSER, **auth(oidc.issue("sub-p-admin"))}
    )
    assert resp.status_code == 200, "ADMIN 應該都看得到"


async def test_admin的清單含別人的private專案(client, app, oidc):
    await make_user(app, "sub-l-owner")
    await _project(client, oidc.issue("sub-l-owner"), "hidden-proj")

    await make_user(app, "sub-l-admin", admin=True)
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-l-admin"))})
    assert "hidden-proj" in resp.text


async def test_一般使用者對private非成員仍是404而非403(client, app, oidc):
    """🔴 403 等於承認專案存在。這條不因為「admin 看得到」而鬆動。"""
    await make_user(app, "sub-o-owner")
    await _project(client, oidc.issue("sub-o-owner"), "other-secret")

    await make_user(app, "sub-outsider")
    resp = await client.get(
        "/projects/other-secret", headers={**BROWSER, **auth(oidc.issue("sub-outsider"))}
    )
    assert resp.status_code == 404


# --- 可見性切換 -------------------------------------------------------------


async def test_擁有者可以切換可見性(client, app, oidc):
    await make_user(app, "sub-v-owner")
    token = oidc.issue("sub-v-owner")
    await _project(client, token, "vis-proj", visibility="internal")

    resp = await client.post(
        "/projects/vis-proj/visibility",
        data={"visibility": "private"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    detail = await client.get("/v1/projects/vis-proj", headers=auth(token))
    assert detail.json()["visibility"] == "private"


async def test_非成員不能切換可見性(client, app, oidc):
    await make_user(app, "sub-vx-owner")
    await _project(client, oidc.issue("sub-vx-owner"), "vis-guard")
    await make_user(app, "sub-vx-other")

    resp = await client.post(
        "/projects/vis-guard/visibility",
        data={"visibility": "internal"},
        headers={**BROWSER, **auth(oidc.issue("sub-vx-other"))},
        follow_redirects=False,
    )
    assert resp.status_code in (403, 404), resp.status_code


# --- 成員管理 ---------------------------------------------------------------


async def test_擁有者可以加人與移除(client, app, oidc):
    await make_user(app, "sub-m-owner")
    token = oidc.issue("sub-m-owner")
    await _project(client, token, "team-proj")
    guest = await make_user(app, "sub-m-guest")

    add = await client.post(
        "/projects/team-proj/members",
        data={"user_id": str(guest.id), "role": "viewer"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert add.status_code == 303, add.text

    # 加進來之後,對方看得到了(這就是「設定查看權限」的意思)
    seen = await client.get(
        "/projects/team-proj", headers={**BROWSER, **auth(oidc.issue("sub-m-guest"))}
    )
    assert seen.status_code == 200

    remove = await client.post(
        f"/projects/team-proj/members/{guest.id}/remove",
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert remove.status_code == 303

    gone = await client.get(
        "/projects/team-proj", headers={**BROWSER, **auth(oidc.issue("sub-m-guest"))}
    )
    assert gone.status_code == 404, "移除後應回到看不見的狀態"


async def test_成員異動留稽核(client, app, oidc):
    from sqlalchemy import select

    from app.models import AuditEvent

    await make_user(app, "sub-a-owner")
    token = oidc.issue("sub-a-owner")
    await _project(client, token, "audit-proj")
    guest = await make_user(app, "sub-a-guest")
    await client.post(
        "/projects/audit-proj/members",
        data={"user_id": str(guest.id), "role": "viewer"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    async with app.state.sessionmaker() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "member.set" in [a.value if hasattr(a, "value") else a for a in actions]


async def test_maintainer不能改權限(client, app, oidc):
    """🔴 介面不得比 API 寬鬆:成員異動是 owner 的職權(API 就是這樣定的)。"""
    await make_user(app, "sub-mt-owner")
    owner_token = oidc.issue("sub-mt-owner")
    await _project(client, owner_token, "role-proj")
    mt = await make_user(app, "sub-mt-user")
    await client.post(
        "/projects/role-proj/members",
        data={"user_id": str(mt.id), "role": "maintainer"},
        headers={**BROWSER, **auth(owner_token)},
        follow_redirects=False,
    )

    other = await make_user(app, "sub-mt-third")
    resp = await client.post(
        "/projects/role-proj/members",
        data={"user_id": str(other.id), "role": "viewer"},
        headers={**BROWSER, **auth(oidc.issue("sub-mt-user"))},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- 名字搜尋(本任務唯一的新外洩面,所以約束要逐條釘)-----------------------


async def test_搜尋只回已開通者且不含email(client, app, oidc):
    from app.models import UserStatus

    await make_user(app, "sub-s-owner")
    token = oidc.issue("sub-s-owner")
    await _project(client, token, "search-proj")

    # 造一個已開通、有顯示名稱的人,和一個待開通的人
    await client.get(
        "/v1/me",
        headers=auth(
            oidc.issue("sub-s-found", name="王小明", email="ming@example.test", email_verified=True)
        ),
    )
    async with app.state.sessionmaker() as session:
        from sqlalchemy import update

        from app.models import User

        await session.execute(
            update(User).where(User.sub == "sub-s-found").values(status=UserStatus.active)
        )
        await session.commit()
    await client.get("/v1/me", headers=auth(oidc.issue("sub-s-pending", name="待開通的人")))

    resp = await client.get(
        "/projects/search-proj?member_q=小明", headers={**BROWSER, **auth(token)}
    )
    assert resp.status_code == 200
    assert "王小明" in resp.text
    assert "ming@example.test" not in resp.text, "🔴 L1b:email 不得顯示在任何頁面"

    resp2 = await client.get(
        "/projects/search-proj?member_q=待開通", headers={**BROWSER, **auth(token)}
    )
    assert "待開通的人" not in resp2.text, "pending 帳號的存在不該外洩"


async def test_空白搜尋不列出全部(client, app, oidc):
    """🔴 少了這條,它就是一支「整份名單匯出」的 API。"""
    await make_user(app, "sub-e-owner")
    token = oidc.issue("sub-e-owner")
    await _project(client, token, "empty-q-proj")
    await make_user(app, "sub-e-someone")
    await client.get("/v1/me", headers=auth(oidc.issue("sub-e-someone", name="不該被列出")))

    resp = await client.get(
        "/projects/empty-q-proj?member_q=%20", headers={**BROWSER, **auth(token)}
    )
    assert "不該被列出" not in resp.text


async def test_搜尋最多五筆(client, app, oidc):
    import re

    await make_user(app, "sub-f-owner")
    token = oidc.issue("sub-f-owner")
    await _project(client, token, "five-proj")
    for i in range(8):
        await make_user(app, f"sub-many-{i}")
        await client.get("/v1/me", headers=auth(oidc.issue(f"sub-many-{i}", name=f"候選{i}")))

    resp = await client.get("/projects/five-proj?member_q=候選", headers={**BROWSER, **auth(token)})
    hits = re.findall(r'name="user_id" value="', resp.text)
    assert len(hits) <= 5, f"最多 5 筆,實得 {len(hits)}"


async def test_非擁有者看不到權限區塊(client, app, oidc):
    await make_user(app, "sub-g-owner")
    await _project(client, oidc.issue("sub-g-owner"), "guard-proj", visibility="internal")
    await make_user(app, "sub-g-member")

    resp = await client.get(
        "/projects/guard-proj", headers={**BROWSER, **auth(oidc.issue("sub-g-member"))}
    )
    assert resp.status_code == 200
    assert "查看權限" not in resp.text
