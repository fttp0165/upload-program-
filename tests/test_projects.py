"""專案可見性與授權——deny-by-default 的邊界在這裡定樁。"""

from tests.conftest import auth, make_user


async def test_建立專案與短名不可重複(client, active_user):
    _, token = active_user
    payload = {"slug": "my-tool", "name": "我的工具"}

    first = await client.post("/v1/projects", json=payload, headers=auth(token))
    assert first.status_code == 201
    assert first.json()["my_role"] == "owner"

    again = await client.post("/v1/projects", json=payload, headers=auth(token))
    assert again.status_code == 409
    assert again.json()["type"].endswith("/conflict")


async def test_短名格式不合法回422(client, active_user):
    _, token = active_user
    resp = await client.post(
        "/v1/projects", json={"slug": "My Tool!", "name": "x"}, headers=auth(token)
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation")


async def test_private專案對非成員等同不存在(client, app, oidc, active_user):
    _, owner_token = active_user
    await client.post(
        "/v1/projects",
        json={"slug": "secret-tool", "name": "機密", "visibility": "private"},
        headers=auth(owner_token),
    )

    await make_user(app, "sub-other")
    other = oidc.issue("sub-other")

    # 不洩漏「存在但沒權限」——private 專案一律回 404
    detail = await client.get("/v1/projects/secret-tool", headers=auth(other))
    assert detail.status_code == 404

    listed = await client.get("/v1/projects", headers=auth(other))
    assert listed.json()["total"] == 0


async def test_internal專案全站可讀但不可寫(client, app, oidc, active_user):
    _, owner_token = active_user
    await client.post(
        "/v1/projects", json={"slug": "open-tool", "name": "公開"}, headers=auth(owner_token)
    )

    await make_user(app, "sub-reader")
    reader = oidc.issue("sub-reader")

    assert (await client.get("/v1/projects/open-tool", headers=auth(reader))).status_code == 200

    denied = await client.patch(
        "/v1/projects/open-tool", json={"name": "亂改"}, headers=auth(reader)
    )
    assert denied.status_code == 403

    denied_release = await client.post(
        "/v1/projects/open-tool/releases", json={"version": "v9"}, headers=auth(reader)
    )
    assert denied_release.status_code == 403


async def test_加入成員後可發版(client, app, oidc, active_user):
    _, owner_token = active_user
    await client.post(
        "/v1/projects", json={"slug": "team-tool", "name": "團隊"}, headers=auth(owner_token)
    )
    member = await make_user(app, "sub-member")
    member_token = oidc.issue("sub-member")

    added = await client.put(
        "/v1/projects/team-tool/members",
        json={"user_id": str(member.id), "role": "maintainer"},
        headers=auth(owner_token),
    )
    assert added.status_code == 200

    created = await client.post(
        "/v1/projects/team-tool/releases", json={"version": "v0.1.0"}, headers=auth(member_token)
    )
    assert created.status_code == 201

    # maintainer 不能刪專案(那是 owner 的權限)
    assert (
        await client.delete("/v1/projects/team-tool", headers=auth(member_token))
    ).status_code == 403


async def test_搜尋只回可見專案(client, app, oidc, active_user):
    _, owner_token = active_user
    await client.post(
        "/v1/projects", json={"slug": "alpha-tool", "name": "Alpha 工具"}, headers=auth(owner_token)
    )
    await client.post(
        "/v1/projects",
        json={"slug": "hidden-tool", "name": "Alpha 機密", "visibility": "private"},
        headers=auth(owner_token),
    )

    await make_user(app, "sub-searcher")
    searcher = oidc.issue("sub-searcher")

    resp = await client.get("/v1/search?q=Alpha", headers=auth(searcher))
    assert resp.status_code == 200
    slugs = {p["slug"] for p in resp.json()["items"]}
    assert slugs == {"alpha-tool"}


async def test_刪除專案會一併清掉物件(client, active_user, storage):
    _, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "temp-tool", "name": "暫時"}, headers=auth(token)
    )
    release = await client.post(
        "/v1/projects/temp-tool/releases", json={"version": "v1"}, headers=auth(token)
    )
    await client.put(
        f"/v1/releases/{release.json()['id']}/artifacts/a.bin?kind=binary",
        content=b"\x7fELF" + b"\x00" * 100,
        headers=auth(token),
    )
    assert len(storage.objects) == 1

    resp = await client.delete("/v1/projects/temp-tool", headers=auth(token))
    assert resp.status_code == 204
    assert storage.objects == {}  # 不留孤兒物件佔空間
