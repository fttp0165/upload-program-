"""T142:程式碼(source)下載需作者核准。

計畫書:`docs/plans/設計_程式碼下載需作者核准.md`

🔴 本檔兩條最要緊的守門,不是補充驗收:

- **`test_非成員仍可下載說明與執行檔`** —— 漏了會鎖到不該鎖的東西,
  而使用者只會覺得平台壞了。
- **`test_最新版捷徑同樣被擋`** —— F26 那條網址是設計成「能寫進文件而不會
  失效」的,**最容易被傳出去**。鎖了主路徑卻留這條捷徑**等於沒鎖**,
  而所有其他測試都會是綠的。
"""

from sqlalchemy import select

from app.models import ProjectRole, SourceAccessRequest, SourceAccessStatus
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
ZIP = b"PK\x03\x04" + b"\x00" * 60
PDF = b"%PDF-1.7\n" + b"\x00" * 100


async def _published_project(client, token, slug="locked-tool"):
    """建一個 internal 專案 + 一個三類齊備、已發布的版本。"""
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "上鎖測試", "summary": "", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    rel = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": ""},
        headers=auth(token),
    )
    release_id = rel.json()["id"]
    for name, kind, body in (
        ("src.zip", "source", ZIP),
        ("tool.bin", "binary", ELF),
        ("readme.pdf", "doc", PDF),
    ):
        r = await client.put(
            f"/v1/releases/{release_id}/artifacts/{name}?kind={kind}",
            content=body,
            headers=auth(token),
        )
        assert r.status_code == 201, r.text
    # 送審 → 核准(T123 之後 publish 是送審)
    r = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert r.status_code == 200, r.text
    return slug, release_id


async def _artifact_ids(client, token, release_id):
    resp = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    return {a["filename"]: a["id"] for a in resp.json()["artifacts"]}


async def _approve_release(client, app, oidc, release_id):
    await make_user(app, "sub-approver", admin=True)
    token = oidc.issue("sub-approver")
    resp = await client.post(
        f"/v1/releases/{release_id}/approve", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text


async def _setup(client, app, oidc, slug="locked-tool"):
    """作者建好專案並讓版本真的可下載;回傳 (slug, release_id, ids)。"""
    owner = await make_user(app, "sub-owner")
    owner_token = oidc.issue("sub-owner")
    slug, release_id = await _published_project(client, owner_token, slug)
    await _approve_release(client, app, oidc, release_id)
    ids = await _artifact_ids(client, owner_token, release_id)
    return owner, owner_token, slug, release_id, ids


# --- 判準 -------------------------------------------------------------------


async def test_非成員不能下載程式碼(client, app, oidc, storage):
    _, _, _, release_id, ids = await _setup(client, app, oidc)
    await make_user(app, "sub-outsider")
    outsider = oidc.issue("sub-outsider")

    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{ids['src.zip']}/download",
        headers=auth(outsider),
    )
    assert resp.status_code == 403, resp.text
    # 🔴 擋的同時要給路:只說「不行」不說「怎麼才可以」,使用者的下一步是來問人。
    assert "申請" in resp.json()["detail"]


async def test_非成員仍可下載說明與執行檔(client, app, oidc, storage):
    """🔴 守門:只鎖程式碼。漏了會鎖到不該鎖的東西,而使用者只會覺得平台壞了。"""
    _, _, _, release_id, ids = await _setup(client, app, oidc, slug="kinds-tool")
    await make_user(app, "sub-reader")
    reader = oidc.issue("sub-reader")

    for name in ("tool.bin", "readme.pdf"):
        resp = await client.get(
            f"/v1/releases/{release_id}/artifacts/{ids[name]}/download",
            headers=auth(reader),
        )
        assert resp.status_code == 200, f"{name} 不該被鎖:{resp.text}"


async def test_平台管理員可以下載程式碼(client, app, oidc, storage):
    _, _, _, release_id, ids = await _setup(client, app, oidc, slug="admin-tool")
    await make_user(app, "sub-super", admin=True)
    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{ids['src.zip']}/download",
        headers=auth(oidc.issue("sub-super")),
    )
    assert resp.status_code == 200, resp.text


async def test_專案成員即使只是viewer也可以下載程式碼(client, app, oidc, storage):
    """守門:「加入成員」本身就是作者的同意(2026-09-08 裁示)。"""
    _, owner_token, slug, release_id, ids = await _setup(client, app, oidc, slug="member-tool")
    member = await make_user(app, "sub-member")
    resp = await client.put(
        f"/v1/projects/{slug}/members",
        json={"user_id": str(member.id), "role": ProjectRole.viewer.value},
        headers=auth(owner_token),
    )
    assert resp.status_code in (200, 201), resp.text

    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{ids['src.zip']}/download",
        headers=auth(oidc.issue("sub-member")),
    )
    assert resp.status_code == 200, resp.text


async def test_最新版捷徑同樣被擋(client, app, oidc, storage):
    """🔴 守門:F26 的捷徑是最容易漏的後門。

    那條網址是設計成「能寫進文件而不會失效」的,**最容易被傳出去** ——
    鎖了主路徑卻留這條,等於沒鎖,而其他測試全部都會是綠的。
    """
    _, _, slug, _, _ = await _setup(client, app, oidc, slug="shortcut-tool")
    await make_user(app, "sub-sneaky")
    resp = await client.get(
        f"/v1/projects/{slug}/releases/latest/artifacts/src.zip/download",
        headers=auth(oidc.issue("sub-sneaky")),
    )
    assert resp.status_code == 403, resp.text


# --- 申請與決定 -------------------------------------------------------------


async def _request(client, app, oidc, slug, sub="sub-asker", reason="要接手維護"):
    # ⚠ 這個 helper 會被同一個測試呼叫兩次(驗「重複申請只有一筆」),
    # 所以帳號要**存在才建**,否則撞 users.sub 的唯一約束 —— 那會讓測試在
    # 「還沒測到重複申請」之前就死掉,而錯誤訊息指向的是 users 不是本功能。
    from sqlalchemy import select

    async with app.state.sessionmaker() as session:
        from app.models import User as _U

        exists = (
            await session.execute(select(_U).where(_U.sub == sub))
        ).scalar_one_or_none()
    if exists is None:
        await make_user(app, sub)
    return await client.post(
        f"/projects/{slug}/access/request",
        data={"reason": reason},
        headers={**BROWSER, **auth(oidc.issue(sub))},
        follow_redirects=False,
    )


async def test_核准後可下載而撤銷後又不行(client, app, oidc, storage):
    _, owner_token, slug, release_id, ids = await _setup(client, app, oidc, slug="grant-tool")
    resp = await _request(client, app, oidc, slug)
    assert resp.status_code in (302, 303), resp.text

    url = f"/v1/releases/{release_id}/artifacts/{ids['src.zip']}/download"
    asker = auth(oidc.issue("sub-asker"))
    assert (await client.get(url, headers=asker)).status_code == 403

    async with app.state.sessionmaker() as session:
        req = (await session.execute(select(SourceAccessRequest))).scalars().first()
        req_id = req.id

    owner_headers = {**BROWSER, **auth(owner_token)}
    resp = await client.post(
        f"/projects/{slug}/access/{req_id}/approve",
        data={},
        headers=owner_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text
    assert (await client.get(url, headers=asker)).status_code == 200

    resp = await client.post(
        f"/projects/{slug}/access/{req_id}/revoke",
        data={},
        headers=owner_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text
    assert (await client.get(url, headers=asker)).status_code == 403


async def test_拒絕必須寫理由(client, app, oidc, storage):
    """🔴 比照 T123 退回:沒有理由,申請人只能猜,然後重送一模一樣的東西。"""
    _, owner_token, slug, _, _ = await _setup(client, app, oidc, slug="reject-tool")
    await _request(client, app, oidc, slug)
    async with app.state.sessionmaker() as session:
        req_id = (await session.execute(select(SourceAccessRequest))).scalars().first().id

    resp = await client.post(
        f"/projects/{slug}/access/{req_id}/reject",
        data={"decided_reason": "   "},
        headers={**BROWSER, **auth(owner_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 422, resp.text


async def test_重複申請只會有一筆(client, app, oidc, storage):
    """🔴 唯一約束:沒有它,被拒絕的人可以連按十次,而作者的待辦變成垃圾場。"""
    _, _, slug, _, _ = await _setup(client, app, oidc, slug="dup-tool")
    await _request(client, app, oidc, slug)
    await _request(client, app, oidc, slug, reason="再說一次")

    async with app.state.sessionmaker() as session:
        rows = (await session.execute(select(SourceAccessRequest))).scalars().all()
    assert len(rows) == 1, "同一人對同一專案不得長出第二筆"
    assert rows[0].reason == "再說一次", "重複申請應更新那一筆"


async def test_不是作者也不是管理員不得核准(client, app, oidc, storage):
    _, _, slug, _, _ = await _setup(client, app, oidc, slug="acl-tool")
    await _request(client, app, oidc, slug)
    async with app.state.sessionmaker() as session:
        req_id = (await session.execute(select(SourceAccessRequest))).scalars().first().id

    await make_user(app, "sub-stranger")
    resp = await client.post(
        f"/projects/{slug}/access/{req_id}/approve",
        data={},
        headers={**BROWSER, **auth(oidc.issue("sub-stranger"))},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 403, 404)
    async with app.state.sessionmaker() as session:
        row = (await session.execute(select(SourceAccessRequest))).scalars().first()
    assert row.status is SourceAccessStatus.pending, "旁人不得改變申請狀態"


async def test_四個稽核動作都有記(client, app, oidc, storage):
    from app.models import AuditEvent

    _, owner_token, slug, _, _ = await _setup(client, app, oidc, slug="audit-tool")
    await _request(client, app, oidc, slug)
    async with app.state.sessionmaker() as session:
        req_id = (await session.execute(select(SourceAccessRequest))).scalars().first().id

    owner_headers = {**BROWSER, **auth(owner_token)}
    for action in ("approve", "revoke"):
        await client.post(
            f"/projects/{slug}/access/{req_id}/{action}",
            data={},
            headers=owner_headers,
            follow_redirects=False,
        )
    await client.post(
        f"/projects/{slug}/access/{req_id}/reject",
        data={"decided_reason": "不同意"},
        headers=owner_headers,
        follow_redirects=False,
    )

    async with app.state.sessionmaker() as session:
        actions = {e.action for e in (await session.execute(select(AuditEvent))).scalars().all()}
    for expected in (
        "project.source_request",
        "project.source_approve",
        "project.source_revoke",
        "project.source_reject",
    ):
        assert expected in actions, f"缺稽核動作 {expected}"


async def test_作者在專案頁看得到待處理申請數(client, app, oidc, storage):
    """🔴 平台沒有 email 也沒有推播 —— 少了這個數字,申請會安靜地躺在資料庫裡。"""
    _, owner_token, slug, _, _ = await _setup(client, app, oidc, slug="notice-tool")
    await _request(client, app, oidc, slug)

    resp = await client.get(
        f"/projects/{slug}", headers={**BROWSER, **auth(owner_token)}
    )
    assert resp.status_code == 200
    assert "下載申請" in resp.text
