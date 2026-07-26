"""最新版捷徑(F26 / T35)。

為什麼需要:現在的下載網址含 `release_id` 與 `artifact_id`,每發一次新版就換一組 UUID
——寫進 wiki、腳本、批次檔的連結全部作廢。捷徑讓同事可以寫死一條連結永遠指向最新版。

🔴 本檔最重要的一條是 `test_最新版以發布時間判定而非版本號字串`:
版本號是自由字串,字串排序會讓 `v9` 排在 `v10` 前面——發到第十版就會靜默地一直給舊版。
"""

import hashlib

from tests.conftest import auth, make_user

ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 120


async def _project(client, token, slug="cli-tool", visibility="internal"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "指令工具", "visibility": visibility},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _publish(client, token, slug, version, *, filename="tool.bin", body=ELF):
    """建版本 → 上傳一個檔 → 發布,回傳 release dict。"""
    release = await client.post(
        f"/v1/projects/{slug}/releases", json={"version": version}, headers=auth(token)
    )
    assert release.status_code == 201, release.text
    rid = release.json()["id"]

    up = await client.put(
        f"/v1/releases/{rid}/artifacts/{filename}?kind=binary",
        content=body,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text

    pub = await client.post(f"/v1/releases/{rid}/publish", headers=auth(token))
    assert pub.status_code == 200, pub.text
    return pub.json()


async def test_取得最新已發布版本(client, active_user):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "cli-tool", "v1.0.0")

    resp = await client.get("/v1/projects/cli-tool/releases/latest", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == "v1.0.0"


async def test_最新版以發布時間判定而非版本號字串(client, active_user):
    """🔴 三種排序給出不同答案,只有正確實作會通過。

    建立順序:v10 先建、v9 後建   → 若依 created_at 倒序,答案會是 v9(錯)
    版本號字串:"v9" > "v10"      → 若依版本號字串倒序,答案會是 v9(錯)
    發布順序:v9 先發、v10 後發   → 依 published_at 倒序,答案是 v10(對)
    """
    _, token = active_user
    await _project(client, token)

    # v10 先「建立」
    r10 = await client.post(
        "/v1/projects/cli-tool/releases", json={"version": "v10"}, headers=auth(token)
    )
    rid10 = r10.json()["id"]
    await client.put(
        f"/v1/releases/{rid10}/artifacts/tool.bin?kind=binary", content=ELF, headers=auth(token)
    )

    # v9 後「建立」,但先發布
    await _publish(client, token, "cli-tool", "v9")

    # v10 最後才發布 → 它的 published_at 最大
    pub10 = await client.post(f"/v1/releases/{rid10}/publish", headers=auth(token))
    assert pub10.status_code == 200

    resp = await client.get("/v1/projects/cli-tool/releases/latest", headers=auth(token))
    assert resp.json()["version"] == "v10", (
        "latest 給出了 v9 —— 表示用了版本號字串或建立時間排序,而非 published_at"
    )


async def test_只有draft時latest回404(client, active_user):
    """latest 是給使用者抓的,draft 是作者的工作區,不該外流。"""
    _, token = active_user
    await _project(client, token)
    await client.post(
        "/v1/projects/cli-tool/releases", json={"version": "v0.1.0"}, headers=auth(token)
    )

    resp = await client.get("/v1/projects/cli-tool/releases/latest", headers=auth(token))
    assert resp.status_code == 404
    assert "發布" in resp.json()["detail"]


async def test_沒有任何版本時回404(client, active_user):
    _, token = active_user
    await _project(client, token)

    resp = await client.get("/v1/projects/cli-tool/releases/latest", headers=auth(token))
    assert resp.status_code == 404
    # 沒有這行的話,端點不存在時也會 404,測試會因錯誤的理由通過
    assert "尚未發布" in resp.json()["detail"]


async def test_internal專案的非成員也能取得latest(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token)
    await _publish(client, owner_token, "cli-tool", "v1.0.0")

    await make_user(app, "sub-reader")
    resp = await client.get(
        "/v1/projects/cli-tool/releases/latest", headers=auth(oidc.issue("sub-reader"))
    )
    assert resp.status_code == 200


async def test_private專案的非成員回404(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-cli", visibility="private")
    await _publish(client, owner_token, "secret-cli", "v1.0.0")

    await make_user(app, "sub-outsider")
    resp = await client.get(
        "/v1/projects/secret-cli/releases/latest", headers=auth(oidc.issue("sub-outsider"))
    )
    assert resp.status_code == 404
    # 必須是「找不到專案」而非「找不到端點」——private 專案不洩漏存在
    assert "專案" in resp.json()["detail"]


# --- 下載捷徑 ---------------------------------------------------------------


async def test_以檔名下載最新版(client, active_user):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "cli-tool", "v1.0.0")

    resp = await client.get(
        "/v1/projects/cli-tool/releases/latest/artifacts/tool.bin/download",
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == ELF
    # 安全標頭與 by-UUID 的下載路徑一致,不能因為是捷徑就鬆掉
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-artifact-sha256"] == hashlib.sha256(ELF).hexdigest()


async def test_發新版後同一條網址指向新檔案(client, active_user):
    """這正是 F26 的重點:連結寫進文件後不必再改。"""
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "cli-tool", "v1.0.0")

    url = "/v1/projects/cli-tool/releases/latest/artifacts/tool.bin/download"
    first = await client.get(url, headers=auth(token))
    assert first.content == ELF

    new_body = ELF + b"NEWVERSION"
    await _publish(client, token, "cli-tool", "v1.1.0", body=new_body)

    second = await client.get(url, headers=auth(token))
    assert second.status_code == 200
    assert second.content == new_body, "同一條網址仍指向舊版檔案"
    assert second.headers["x-artifact-sha256"] == hashlib.sha256(new_body).hexdigest()


async def test_下載不存在的檔名回404(client, active_user):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "cli-tool", "v1.0.0")

    resp = await client.get(
        "/v1/projects/cli-tool/releases/latest/artifacts/nope.bin/download",
        headers=auth(token),
    )
    assert resp.status_code == 404
    assert "檔案" in resp.json()["detail"]


async def test_private專案的下載捷徑對非成員回404(client, app, oidc, active_user):
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-cli", visibility="private")
    await _publish(client, owner_token, "secret-cli", "v1.0.0")

    await make_user(app, "sub-outsider")
    resp = await client.get(
        "/v1/projects/secret-cli/releases/latest/artifacts/tool.bin/download",
        headers=auth(oidc.issue("sub-outsider")),
    )
    assert resp.status_code == 404
    assert "專案" in resp.json()["detail"]
