"""T49 專案容量級距與擴充(F17)。

級距(standard 2 GB / extended 10 GB)是**政策**,不是每個專案自帶的數字:
專案列上只存級距代號,對應的位元組數由設定值決定。這批測試釘住三件事——
誰能改級距、級距真的會改變上傳結果、超限的錯誤訊息說得夠清楚。

測試環境的級距數值由 conftest 的 settings 夾具給(標準 4 MB / 擴充 16 MB),
用小數字才跑得快;真實數值由 test_config_and_logging 的預設值測試把關。
"""

from tests.conftest import auth, make_user

ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

STANDARD_BYTES = 4 * 1024 * 1024
EXTENDED_BYTES = 16 * 1024 * 1024


async def _project(client, token, slug="quota-demo"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "配額示範", "summary": "測試用"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _release(client, token, slug):
    resp = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": "首版"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_used_bytes(app, slug: str, used: int) -> None:
    """直接把專案的已用量灌到指定值。

    為什麼不真的傳一堆檔案上去:本檔測的是**級距機制**,不是位元組累加
    (那由 T33 的上傳測試把關)。灌值讓每條測試只需要一次小上傳就能踩到界線。
    """
    from sqlalchemy import select

    from app.models import Project

    async with app.state.sessionmaker() as session:
        project = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one()
        project.total_bytes = used
        await session.commit()


async def _upload(client, token, release_id, name="tool.bin", content=ELF):
    return await client.put(
        f"/v1/releases/{release_id}/artifacts/{name}?kind=binary",
        content=content,
        headers=auth(token),
    )


# --- 級距預設與呈現 ---------------------------------------------------------


async def test_新專案預設為標準級距(client, active_user):
    _, token = active_user
    body = await _project(client, token)
    assert body["quota_tier"] == "standard"
    assert body["quota_bytes"] == STANDARD_BYTES


async def test_列表與詳情與搜尋都帶出級距(client, active_user):
    """三條讀取路徑各自組裝 ProjectOut,漏掉任何一條都是靜默的 null 欄位。"""
    _, token = active_user
    await _project(client, token)

    detail = await client.get("/v1/projects/quota-demo", headers=auth(token))
    listing = await client.get("/v1/projects", headers=auth(token))
    search = await client.get("/v1/search?q=配額", headers=auth(token))

    for resp in (detail, listing, search):
        assert resp.status_code == 200, resp.text
    assert detail.json()["quota_bytes"] == STANDARD_BYTES
    assert listing.json()["items"][0]["quota_tier"] == "standard"
    assert listing.json()["items"][0]["quota_bytes"] == STANDARD_BYTES
    assert search.json()["items"][0]["quota_bytes"] == STANDARD_BYTES


# --- 誰能改級距 -------------------------------------------------------------


async def test_管理員可調為擴充級距(client, active_user, admin_user):
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)

    resp = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quota_tier"] == "extended"
    assert body["quota_bytes"] == EXTENDED_BYTES


async def test_重複調同一級距是冪等的(client, active_user, admin_user):
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)

    for _ in range(2):
        resp = await client.put(
            "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(admin_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["quota_tier"] == "extended"


async def test_專案owner不能自己調級距(client, active_user):
    """🔴 owner 能自調等於沒有上限——F17 明文「由平台管理員核可」。"""
    _, token = active_user
    await _project(client, token)

    resp = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(token)
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["type"].endswith("/forbidden")
    assert "管理員" in body["detail"]


async def test_非成員不能調級距(client, active_user, app, oidc):
    _, token = active_user
    await _project(client, token)
    await make_user(app, "sub-outsider")
    outsider = oidc.issue("sub-outsider")

    resp = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(outsider)
    )
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("/forbidden")


async def test_不存在的專案回404(client, admin_user):
    _, admin_token = admin_user
    resp = await client.put(
        "/v1/projects/no-such-project/quota",
        json={"tier": "extended"},
        headers=auth(admin_token),
    )
    assert resp.status_code == 404
    assert "專案" in resp.json()["detail"]


async def test_不合法的級距值回422(client, active_user, admin_user):
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)

    resp = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "unlimited"}, headers=auth(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation")


# --- 級距真的會改變上傳結果(本任務的核心)---------------------------------


async def test_標準級距超限的上傳被擋且訊息說得清楚(client, active_user, app, storage):
    """F17:「不能只丟一句 Payload Too Large」。"""
    _, token = active_user
    await _project(client, token)
    release_id = await _release(client, token, "quota-demo")
    await _set_used_bytes(app, "quota-demo", STANDARD_BYTES - 10)

    resp = await _upload(client, token, release_id)
    assert resp.status_code == 413
    body = resp.json()
    assert body["type"].endswith("/payload-too-large")
    # 級距、上限、已用量、本次大小都要看得到
    assert body["quota_tier"] == "standard"
    assert body["quota_bytes"] == STANDARD_BYTES
    assert body["used_bytes"] == STANDARD_BYTES - 10
    assert body["incoming_bytes"] == len(ELF)
    # 指引:標準級距要告訴人「可以申請擴充」
    assert "申請" in body["detail"]
    assert "擴充" in body["detail"]
    # 擋下來就不該留物件
    assert storage.objects == {}


async def test_同一份上傳改成擴充級距後就成功(client, active_user, admin_user, app):
    """級距若沒真的接上上傳檢查,這條會紅——這是 T49 的核心驗收。"""
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)
    release_id = await _release(client, token, "quota-demo")
    await _set_used_bytes(app, "quota-demo", STANDARD_BYTES - 10)

    blocked = await _upload(client, token, release_id)
    assert blocked.status_code == 413

    upgrade = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(admin_token)
    )
    assert upgrade.status_code == 200, upgrade.text

    ok = await _upload(client, token, release_id)
    assert ok.status_code == 201, ok.text
    assert ok.json()["size_bytes"] == len(ELF)


async def test_擴充級距超限的指引不再說可申請(client, active_user, admin_user, app):
    """🔴 對已是最大級距的專案講「可申請擴充」是錯誤指引,會讓人去申請不存在的東西。"""
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)
    release_id = await _release(client, token, "quota-demo")
    await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(admin_token)
    )
    await _set_used_bytes(app, "quota-demo", EXTENDED_BYTES - 10)

    resp = await _upload(client, token, release_id)
    assert resp.status_code == 413
    body = resp.json()
    assert body["quota_tier"] == "extended"
    assert body["quota_bytes"] == EXTENDED_BYTES
    assert "申請" not in body["detail"]
    assert "刪除" in body["detail"] or "清理" in body["detail"]


async def test_ContentLength預檢與收完後的413訊息一致(client, active_user, app):
    """預檢(省頻寬)與收完才發現(沒有 Content-Length)是兩條路徑,訊息不能各說各話。"""
    _, token = active_user
    await _project(client, token)
    release_id = await _release(client, token, "quota-demo")
    await _set_used_bytes(app, "quota-demo", STANDARD_BYTES - 10)

    # httpx 對 bytes content 會自動加 Content-Length,走預檢路徑
    pre = await _upload(client, token, release_id)

    # 用 async generator 送出 → chunked,沒有 Content-Length,走收完後檢查
    async def _stream():
        yield ELF

    post = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool2.bin?kind=binary",
        content=_stream(),
        headers=auth(token),
    )

    assert pre.status_code == post.status_code == 413
    for body in (pre.json(), post.json()):
        assert body["quota_tier"] == "standard"
        assert body["quota_bytes"] == STANDARD_BYTES
        assert "申請" in body["detail"]


# --- 降級 -------------------------------------------------------------------


async def test_已超標仍可降級但之後上傳被擋(client, active_user, admin_user, app, storage):
    """管理員可能正是要用降級逼專案清理;既有檔案不刪,只擋新上傳。"""
    _, token = active_user
    _, admin_token = admin_user
    await _project(client, token)
    release_id = await _release(client, token, "quota-demo")
    await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "extended"}, headers=auth(admin_token)
    )
    # 先在擴充級距下傳一個檔進去(之後要確認它不會因降級而消失)
    first = await _upload(client, token, release_id)
    assert first.status_code == 201, first.text
    await _set_used_bytes(app, "quota-demo", EXTENDED_BYTES - 10)

    downgrade = await client.put(
        "/v1/projects/quota-demo/quota", json={"tier": "standard"}, headers=auth(admin_token)
    )
    assert downgrade.status_code == 200, downgrade.text
    assert downgrade.json()["quota_tier"] == "standard"
    assert downgrade.json()["total_bytes"] > downgrade.json()["quota_bytes"]  # 已超標

    # 既有物件仍在(降級不刪檔)
    assert len(storage.objects) == 1
    blocked = await _upload(client, token, release_id, name="tool2.bin")
    assert blocked.status_code == 413
    assert blocked.json()["quota_tier"] == "standard"
