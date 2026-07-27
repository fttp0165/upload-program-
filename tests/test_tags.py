"""標籤分類(F42 / T36)。

Q4 定案的理由:「專案數量少時搜尋就夠;多了會需要」——標籤是平台長大後的可瀏覽性。

🔴 兩個容易寫錯的地方,本檔特別釘住:
1. **正規化**:`Python` 與 `python` 必須是同一個標籤,否則篩選會漏掉一半
2. **可見性**:private 專案的標籤不得出現在非成員的標籤清單裡——那等於洩漏專案存在
"""

import pytest

from tests.conftest import auth, make_user


async def _project(client, token, slug, *, visibility="internal", tags=None):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": slug, "visibility": visibility},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    if tags is not None:
        put = await client.put(
            f"/v1/projects/{slug}/tags", json={"tags": tags}, headers=auth(token)
        )
        assert put.status_code == 200, put.text
    return resp.json()


# --- 設定標籤 ---------------------------------------------------------------


async def test_設定標籤後出現在專案資料中(client, active_user):
    _, token = active_user
    await _project(client, token, "my-tool")

    resp = await client.put(
        "/v1/projects/my-tool/tags", json={"tags": ["python", "工具"]}, headers=auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["tags"]) == ["python", "工具"]

    detail = await client.get("/v1/projects/my-tool", headers=auth(token))
    assert sorted(detail.json()["tags"]) == ["python", "工具"]


async def test_中文標籤可用(client, active_user):
    """`工具`、`報表` 是實際會用的標籤,不能只支援英數。"""
    _, token = active_user
    await _project(client, token, "report-tool")

    resp = await client.put(
        "/v1/projects/report-tool/tags", json={"tags": ["報表", "自動化"]}, headers=auth(token)
    )
    assert resp.status_code == 200
    assert sorted(resp.json()["tags"]) == sorted(["報表", "自動化"])


async def test_標籤會被正規化(client, active_user):
    """🔴 `Python` 與 `python` 必須是同一個標籤,否則篩選會漏掉一半。"""
    _, token = active_user
    await _project(client, token, "my-tool")

    resp = await client.put(
        "/v1/projects/my-tool/tags",
        json={"tags": ["  Python  ", "PYTHON", "python"]},
        headers=auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["python"], "大小寫與前後空白未正規化,或未去重"


async def test_重複送同一組是冪等的(client, active_user):
    _, token = active_user
    await _project(client, token, "my-tool")
    body = {"tags": ["python", "cli"]}

    first = await client.put("/v1/projects/my-tool/tags", json=body, headers=auth(token))
    second = await client.put("/v1/projects/my-tool/tags", json=body, headers=auth(token))
    assert first.status_code == second.status_code == 200
    assert first.json()["tags"] == second.json()["tags"]


async def test_傳空陣列可清空標籤(client, active_user):
    _, token = active_user
    await _project(client, token, "my-tool", tags=["python"])

    resp = await client.put("/v1/projects/my-tool/tags", json={"tags": []}, headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["tags"] == []


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ([""], "空字串"),
        (["   "], "純空白"),
        (["資料 分析"], "含空白——標籤要能直接放進網址查詢字串"),
        (["a" * 33], "超過 32 字元"),
        ([f"t{i}" for i in range(11)], "超過 10 個"),
    ],
)
async def test_不合法的標籤回422(client, active_user, bad, why):
    _, token = active_user
    await _project(client, token, "my-tool")

    resp = await client.put("/v1/projects/my-tool/tags", json={"tags": bad}, headers=auth(token))
    assert resp.status_code == 422, f"{why} 應被拒絕"
    assert resp.json()["type"].endswith("/validation")


# --- 權限 -------------------------------------------------------------------


async def test_viewer不能改標籤(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token, "my-tool")
    viewer = await make_user(app, "sub-viewer")
    await client.put(
        "/v1/projects/my-tool/members",
        json={"user_id": str(viewer.id), "role": "viewer"},
        headers=auth(owner_token),
    )

    resp = await client.put(
        "/v1/projects/my-tool/tags",
        json={"tags": ["hack"]},
        headers=auth(oidc.issue("sub-viewer")),
    )
    assert resp.status_code == 403


async def test_非成員不能改標籤(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token, "my-tool")
    await make_user(app, "sub-outsider")

    resp = await client.put(
        "/v1/projects/my-tool/tags",
        json={"tags": ["hack"]},
        headers=auth(oidc.issue("sub-outsider")),
    )
    assert resp.status_code == 403


# --- 依標籤篩選 -------------------------------------------------------------


async def test_依標籤篩選專案(client, active_user):
    _, token = active_user
    await _project(client, token, "py-tool", tags=["python", "cli"])
    await _project(client, token, "go-tool", tags=["golang"])

    resp = await client.get("/v1/projects?tag=python", headers=auth(token))
    assert resp.status_code == 200
    assert [p["slug"] for p in resp.json()["items"]] == ["py-tool"]
    assert resp.json()["total"] == 1


async def test_篩選時標籤大小寫不敏感(client, active_user):
    _, token = active_user
    await _project(client, token, "py-tool", tags=["python"])

    resp = await client.get("/v1/projects?tag=PYTHON", headers=auth(token))
    assert [p["slug"] for p in resp.json()["items"]] == ["py-tool"]


async def test_篩選不會回傳看不到的private專案(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token, "secret-tool", visibility="private", tags=["python"])
    await _project(client, owner_token, "open-tool", tags=["python"])

    await make_user(app, "sub-outsider")
    resp = await client.get(
        "/v1/projects?tag=python", headers=auth(oidc.issue("sub-outsider"))
    )
    assert [p["slug"] for p in resp.json()["items"]] == ["open-tool"]


# --- 標籤清單 ---------------------------------------------------------------


async def test_列出標籤與使用計數(client, active_user):
    _, token = active_user
    await _project(client, token, "py-a", tags=["python", "cli"])
    await _project(client, token, "py-b", tags=["python"])

    resp = await client.get("/v1/tags", headers=auth(token))
    assert resp.status_code == 200, resp.text
    counts = {row["tag"]: row["project_count"] for row in resp.json()["items"]}
    assert counts == {"python": 2, "cli": 1}


async def test_標籤清單只算看得到的專案(client, app, oidc, active_user):
    """🔴 private 專案的標籤出現在非成員的清單裡,等於洩漏該專案存在。"""
    _, owner_token = active_user
    await _project(client, owner_token, "secret-tool", visibility="private", tags=["機密專用"])
    await _project(client, owner_token, "open-tool", tags=["python"])

    await make_user(app, "sub-outsider")
    resp = await client.get("/v1/tags", headers=auth(oidc.issue("sub-outsider")))
    tags = {row["tag"] for row in resp.json()["items"]}
    assert tags == {"python"}, "private 專案的標籤外洩了"


async def test_刪除專案會一併清掉標籤(client, active_user):
    _, token = active_user
    await _project(client, token, "temp-tool", tags=["throwaway"])

    assert (await client.delete("/v1/projects/temp-tool", headers=auth(token))).status_code == 204

    resp = await client.get("/v1/tags", headers=auth(token))
    assert [row["tag"] for row in resp.json()["items"]] == []
