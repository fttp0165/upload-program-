"""轉移擁有權(F16 / T34)。

為什麼需要這個功能:owner 離職後,專案沒人能改設定、加成員、刪除——變成孤兒。
`PUT /v1/projects/{slug}/owner` 是唯一的救援路徑,平台管理員也能代為執行。
"""

from app.models import ProjectRole, UserStatus
from tests.conftest import auth, make_user


async def _project(client, token, slug="owned-tool"):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "有主人的工具"}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_owner可把擁有權轉給他人(client, app, oidc, active_user):
    owner, owner_token = active_user
    await _project(client, owner_token)
    successor = await make_user(app, "sub-successor")

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(successor.id)},
        headers=auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == str(successor.id)


async def test_原owner自動降為maintainer而非被踢出(client, app, oidc, active_user):
    """他通常還要繼續維護,只是不再是負責人。"""
    owner, owner_token = active_user
    await _project(client, owner_token)
    successor = await make_user(app, "sub-successor")

    await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(successor.id)},
        headers=auth(owner_token),
    )

    members = await client.get(
        "/v1/projects/owned-tool/members", headers=auth(oidc.issue("sub-successor"))
    )
    by_user = {m["user_id"]: m["role"] for m in members.json()}
    assert by_user[str(successor.id)] == ProjectRole.owner.value
    assert by_user[str(owner.id)] == ProjectRole.maintainer.value

    # 交出去之後就不能再轉了
    again = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(owner.id)},
        headers=auth(owner_token),
    )
    assert again.status_code == 403


async def test_新owner原本是成員時不留重複的成員列(client, app, oidc, active_user):
    owner, owner_token = active_user
    await _project(client, owner_token)
    successor = await make_user(app, "sub-successor")

    await client.put(
        "/v1/projects/owned-tool/members",
        json={"user_id": str(successor.id), "role": "viewer"},
        headers=auth(owner_token),
    )
    await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(successor.id)},
        headers=auth(owner_token),
    )

    members = await client.get(
        "/v1/projects/owned-tool/members", headers=auth(oidc.issue("sub-successor"))
    )
    rows = members.json()
    # 每個人只該出現一次;owner 由 projects.owner_id 表示,不再重複記在成員表
    assert len({m["user_id"] for m in rows}) == len(rows)
    assert [m["role"] for m in rows if m["user_id"] == str(successor.id)] == [
        ProjectRole.owner.value
    ]


async def test_重複轉給現任owner是冪等的(client, active_user):
    owner, owner_token = active_user
    await _project(client, owner_token)

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(owner.id)},
        headers=auth(owner_token),
    )
    assert resp.status_code == 200
    assert resp.json()["owner_id"] == str(owner.id)


async def test_maintainer不能轉移擁有權(client, app, oidc, active_user):
    """只有 owner 能決定繼任者。"""
    owner, owner_token = active_user
    await _project(client, owner_token)
    helper = await make_user(app, "sub-helper")
    await client.put(
        "/v1/projects/owned-tool/members",
        json={"user_id": str(helper.id), "role": "maintainer"},
        headers=auth(owner_token),
    )

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(helper.id)},
        headers=auth(oidc.issue("sub-helper")),
    )
    assert resp.status_code == 403


async def test_非成員不能轉移擁有權(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token)
    outsider = await make_user(app, "sub-outsider")

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(outsider.id)},
        headers=auth(oidc.issue("sub-outsider")),
    )
    assert resp.status_code == 403


async def test_轉給不存在的使用者回404(client, active_user):
    _, owner_token = active_user
    await _project(client, owner_token)

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth(owner_token),
    )
    assert resp.status_code == 404
    # 沒有這一行的話,端點不存在時也會回 404,測試會因為錯誤的理由通過
    assert "使用者" in resp.json()["detail"]


async def test_不得轉給未開通的使用者(client, app, active_user):
    """待開通的人連業務 API 都進不來,讓他當 owner 等於製造新的孤兒。"""
    _, owner_token = active_user
    await _project(client, owner_token)
    pending = await make_user(app, "sub-pending-heir", status=UserStatus.pending)

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(pending.id)},
        headers=auth(owner_token),
    )
    assert resp.status_code == 409
    assert "開通" in resp.json()["detail"]


async def test_平台管理員可代為轉移(client, app, oidc, active_user, admin_user):
    """owner 已離職時的救援路徑——這正是 F16 存在的理由。"""
    _, owner_token = active_user
    await _project(client, owner_token)
    _, admin_token = admin_user
    successor = await make_user(app, "sub-successor")

    resp = await client.put(
        "/v1/projects/owned-tool/owner",
        json={"user_id": str(successor.id)},
        headers=auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["owner_id"] == str(successor.id)
