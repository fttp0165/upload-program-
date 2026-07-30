"""T65:發布三類齊備——每一版必須有 更新文件(doc)、執行檔(binary)、原始碼包(source)。

背景:使用者 99% 是 Python 開發,每版固定交付三件套;缺一件的發布對下載者
就是資訊不全。規則本體只有一份(`releases.missing_required_kinds`),
API 與網頁表單都走同一條;前端按鈕停用只是提示,伺服器端才是兜底。

釘住的行為:
1. 只有 binary → publish 422,type=release-missing-kinds,detail 列出缺哪幾類
2. 三類齊備 → publish 200
3. 網頁表單發布缺類 → 302 回上傳頁帶 error=missing-kinds,版本仍是 draft
4. 上傳頁缺類 → 顯示缺項提示、發布鈕 disabled;補齊後鈕恢復可按
"""

from tests.conftest import DOC_PDF, SOURCE_ZIP, auth, complete_kinds

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200


async def _project_and_release(client, token, slug="kinds-demo"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "三類測試", "summary": "T65"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": "首版"},
        headers=auth(token),
    )
    assert release.status_code == 201, release.text
    return release.json()["id"]


async def _put(client, token, release_id, name, kind, body):
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/{name}?kind={kind}",
        content=body,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text


async def test_只有執行檔不能發布_並列出缺哪幾類(client, active_user):
    _, token = active_user
    release_id = await _project_and_release(client, token)
    await _put(client, token, release_id, "tool.bin", "binary", ELF)

    resp = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"].endswith("/release-missing-kinds")
    # 缺項要指名道姓,使用者才知道還差什麼
    assert "更新文件(doc)" in body["detail"]
    assert "原始碼包(source)" in body["detail"]
    assert "執行檔(binary)" not in body["detail"].split("目前缺:")[-1]


async def test_三類齊備即可發布(client, active_user):
    _, token = active_user
    release_id = await _project_and_release(client, token)
    await _put(client, token, release_id, "tool.bin", "binary", ELF)
    await complete_kinds(client, token, release_id)

    resp = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


async def test_網頁表單發布缺類_退回上傳頁且仍是draft(client, active_user):
    _, token = active_user
    release_id = await _project_and_release(client, token)
    await _put(client, token, release_id, "tool.bin", "binary", ELF)

    headers = {**BROWSER, **auth(token)}
    resp = await client.post(
        f"/releases/{release_id}/publish", headers=headers, follow_redirects=False
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["location"].endswith(
        f"/releases/{release_id}/upload?error=missing-kinds"
    )

    # 伺服器端有真的擋下:版本仍是 draft,之後補齊仍可上傳
    await _put(client, token, release_id, "src.zip", "source", SOURCE_ZIP)
    await _put(client, token, release_id, "notes.pdf", "doc", DOC_PDF)


async def test_上傳頁缺類_顯示缺項且發布鈕停用(client, active_user):
    _, token = active_user
    release_id = await _project_and_release(client, token)
    await _put(client, token, release_id, "tool.bin", "binary", ELF)

    headers = {**BROWSER, **auth(token)}
    page = await client.get(f"/releases/{release_id}/upload", headers=headers)
    assert page.status_code == 200
    assert "更新文件(doc)" in page.text
    assert "原始碼包(source)" in page.text
    # 發布鈕停用(提示性;真正的兜底在伺服器端)
    assert "disabled" in page.text

    # 補齊三類後:缺項提示消失、發布鈕恢復可按
    await complete_kinds(client, token, release_id)
    page = await client.get(f"/releases/{release_id}/upload", headers=headers)
    assert "目前還缺" not in page.text
    assert ">發布這個版本</button>" in page.text
    assert "disabled" not in page.text
