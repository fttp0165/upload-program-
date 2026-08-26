"""T102 發布審核:管理員核准後才可下載。

Benny:「使用者發布後,要管理員審核才會讓其他使用者下載」。

狀態機由兩態變三態:

    draft ──(作者送審)──> pending_review ──(管理員核准)──> published
      ^                         │
      └──(管理員退回 + 理由)────┘

🎯 **`published` 的語意刻意不變**(它一直都是「可下載」)。這是整個設計的樞紐:
既有已發布的版本因此**一列都不用改**,自然滿足裁示「既有已發布視為已核准」,
資料影響從 🔴 UPDATE 降為 🟡 純加欄位。

🔴 三條界線:

1. **待審對非成員隱藏**——沿用草稿既有的 404 規則,不發明新的可見性;
   404 而非 403,否則回應本身就洩漏了「這個版本存在」。
2. **退回必須寫理由**。沒有理由的退回,作者只能猜,然後重傳一模一樣的東西——
   理由欄位不是裝飾,是這個流程能不能運轉的關鍵。
3. **只有平台管理員能審**。專案 maintainer 能發布(送審),但不能核准自己的東西。
"""

from tests.conftest import auth, complete_kinds, make_user

ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"


async def _project(client, token, slug="review-tool"):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "審核測試"}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text


async def _ready_release(client, token, slug, version="v1.0.0"):
    """建一個三類齊備、可以送審的版本。"""
    created = await client.post(
        f"/v1/projects/{slug}/releases", json={"version": version}, headers=auth(token)
    )
    assert created.status_code == 201, created.text
    release_id = created.json()["id"]
    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text
    await complete_kinds(client, token, release_id)
    return release_id


async def _artifact_id(client, token, release_id):
    resp = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["artifacts"][0]["id"]


# --- 送審 -------------------------------------------------------------------


async def test_作者發布後進入待審而不是直接上架(client, app, oidc):
    await make_user(app, "sub-author-t102")
    token = oidc.issue("sub-author-t102")
    await _project(client, token)
    release_id = await _ready_release(client, token, "review-tool")

    resp = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending_review"


async def test_待審版本其他使用者下載不到(client, app, oidc):
    """🔴 404 不是 403——403 等於承認這個版本存在。"""
    await make_user(app, "sub-author-t102b")
    token = oidc.issue("sub-author-t102b")
    await _project(client, token, slug="hidden-tool")
    release_id = await _ready_release(client, token, "hidden-tool")
    artifact_id = await _artifact_id(client, token, release_id)
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    await make_user(app, "sub-outsider-t102")
    outsider = oidc.issue("sub-outsider-t102")
    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(outsider)
    )
    assert resp.status_code == 404, f"待審不得外流,且要 404 不洩漏存在:{resp.status_code}"


async def test_作者自己看得到送審中的版本(client, app, oidc):
    """作者要知道自己的東西卡在哪,否則只會重複送。"""
    await make_user(app, "sub-author-t102c")
    token = oidc.issue("sub-author-t102c")
    await _project(client, token, slug="mine-tool")
    release_id = await _ready_release(client, token, "mine-tool")
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    resp = await client.get(
        f"{PREFIX}/projects/mine-tool/releases", headers={**BROWSER, **auth(token)}
    )
    assert resp.status_code == 200
    assert "待審" in resp.text


# --- 核准 -------------------------------------------------------------------


async def test_管理員核准後大家就下載得到(client, app, oidc, admin_user):
    _, admin_token = admin_user
    await make_user(app, "sub-author-t102d")
    token = oidc.issue("sub-author-t102d")
    await _project(client, token, slug="approved-tool")
    release_id = await _ready_release(client, token, "approved-tool")
    artifact_id = await _artifact_id(client, token, release_id)
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    ok = await client.post(f"/v1/releases/{release_id}/approve", headers=auth(admin_token))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "published"

    await make_user(app, "sub-outsider-t102d")
    outsider = oidc.issue("sub-outsider-t102d")
    got = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(outsider)
    )
    assert got.status_code == 200


async def test_非管理員不能核准(client, app, oidc):
    """🔴 專案 maintainer 能送審,但不能核准自己的東西。"""
    await make_user(app, "sub-author-t102e")
    token = oidc.issue("sub-author-t102e")
    await _project(client, token, slug="selfapprove-tool")
    release_id = await _ready_release(client, token, "selfapprove-tool")
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    resp = await client.post(f"/v1/releases/{release_id}/approve", headers=auth(token))
    assert resp.status_code == 403, resp.text


# --- 退回 -------------------------------------------------------------------


async def test_退回沒寫理由要被擋下(client, app, oidc, admin_user):
    """🔴 沒有理由的退回,作者只能猜,然後重傳一模一樣的東西。"""
    _, admin_token = admin_user
    await make_user(app, "sub-author-t102f")
    token = oidc.issue("sub-author-t102f")
    await _project(client, token, slug="reject-tool")
    release_id = await _ready_release(client, token, "reject-tool")
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    for body in ({"note": ""}, {"note": "   "}, {}):
        resp = await client.post(
            f"/v1/releases/{release_id}/reject", json=body, headers=auth(admin_token)
        )
        assert resp.status_code == 422, f"{body} 應被擋下,實得 {resp.status_code}"


async def test_退回後回到草稿且理由看得到(client, app, oidc, admin_user):
    _, admin_token = admin_user
    await make_user(app, "sub-author-t102g")
    token = oidc.issue("sub-author-t102g")
    await _project(client, token, slug="reason-tool")
    release_id = await _ready_release(client, token, "reason-tool")
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    reason = "執行檔沒有簽章,請補上再送。"
    resp = await client.post(
        f"/v1/releases/{release_id}/reject", json={"note": reason}, headers=auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "draft", "退回要能改,所以回到 draft"

    page = await client.get(
        f"{PREFIX}/projects/reason-tool/releases", headers={**BROWSER, **auth(token)}
    )
    assert reason in page.text, "作者要看得到為什麼被退"


# --- 回歸:既有行為不得改變 --------------------------------------------------


async def test_既有已發布版本行為完全不變(client, app, oidc, admin_user):
    """🎯 `published` 的語意不變,是這個設計讓 migration 不必改任何一列的原因。

    這條測試釘住那個承諾:一旦有人「順手」把 published 也改成要再審一次,
    既有資料就會在換版當天全部不能下載。
    """
    _, admin_token = admin_user
    await make_user(app, "sub-author-t102h")
    token = oidc.issue("sub-author-t102h")
    await _project(client, token, slug="legacy-tool")
    release_id = await _ready_release(client, token, "legacy-tool")
    artifact_id = await _artifact_id(client, token, release_id)
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    await client.post(f"/v1/releases/{release_id}/approve", headers=auth(admin_token))

    await make_user(app, "sub-anyone-t102")
    anyone = oidc.issue("sub-anyone-t102")

    latest = await client.get("/v1/projects/legacy-tool/releases/latest", headers=auth(anyone))
    assert latest.status_code == 200, "已核准的版本仍是 latest"

    got = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(anyone)
    )
    assert got.status_code == 200

    fixed = await client.get(
        "/v1/projects/legacy-tool/releases/latest/artifacts/tool.bin/download",
        headers=auth(anyone),
    )
    assert fixed.status_code == 200, "F26 固定連結不得因審核制度而失效"


# --- 管理員看得到有東西要審 --------------------------------------------------


async def test_管理總覽待辦區顯示待審數(client, app, oidc, admin_user):
    """🔴 平台沒有 email 也沒有推播——這一格是管理員唯一會知道有東西要審的地方。"""
    _, admin_token = admin_user
    await make_user(app, "sub-author-t102i")
    token = oidc.issue("sub-author-t102i")
    await _project(client, token, slug="todo-tool")
    release_id = await _ready_release(client, token, "todo-tool")
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    resp = await client.get(f"{PREFIX}/admin", headers={**BROWSER, **auth(admin_token)})
    assert resp.status_code == 200
    assert "待審" in resp.text
