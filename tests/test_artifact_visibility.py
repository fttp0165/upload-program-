"""T106:沒有上傳成功的檔案就不要顯示。

Benny 截圖回報:專案頁出現 `下載 dashboard.html  binary · 0 bytes · 0 次下載`,
按下去會 404。

🔴 值得記的是**這不是新的判斷**:下載端點、三類齊備、配額計算早就只認
`upload_status is ready`。缺的只有網頁清單那一層 —— 於是**畫面在承諾一件
伺服器已經拒絕的事**,而使用者會以為是自己的網路壞掉。

⚠ 上傳頁刻意**不**過濾:那是擁有者的作業面,看不到失敗的檔就不知道要重傳。
本檔案第三條測試釘的就是「不要過濾過頭」。
"""

import uuid

from sqlalchemy import select

from app.models import Artifact, UploadStatus
from tests.conftest import DOC_PDF, SOURCE_ZIP, auth, complete_kinds, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
EXE = b"MZ\x90\x00" + b"\x00" * 60


async def _project_with_broken_artifact(client, app, oidc, slug):
    """造一個已發布版本:三類齊備(ready)+ 一個卡在 pending 的檔案。

    回傳 (token, release_id)。pending 那個檔名是 `broken.zip`。
    """
    await make_user(app, f"{slug}-owner")
    token = oidc.issue(f"{slug}-owner")
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "測試專案", "summary": "x", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "1.0.0", "notes": "n"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release_id = resp.json()["id"]

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/app.exe?kind=binary",
        content=EXE,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    await complete_kinds(client, token, release_id)

    # 再傳一個檔,然後把它打回 pending —— 模擬「傳到一半中斷」的殘留列。
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/broken.zip?kind=source",
        content=SOURCE_ZIP,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    broken_id = resp.json()["id"]

    async with app.state.sessionmaker() as session:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == uuid.UUID(broken_id)))
        ).scalar_one()
        artifact.upload_status = UploadStatus.pending
        artifact.size_bytes = 0
        await session.commit()

    publish = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert publish.status_code == 200, publish.text
    return token, release_id


async def test_專案頁不顯示未上傳完成的檔案(client, app, oidc):
    token, _ = await _project_with_broken_artifact(client, app, oidc, "vis-a")

    page = await client.get("/projects/vis-a", headers={**BROWSER, **auth(token)})
    assert page.status_code == 200, page.text

    assert "broken.zip" not in page.text, "按了會 404 的下載按鈕不該出現在檢視頁"
    # 反向:ready 的仍在,證明不是整段被過濾掉。
    assert "app.exe" in page.text


async def test_版本歷史頁不顯示也不計入未上傳完成的檔案(client, app, oidc):
    token, _ = await _project_with_broken_artifact(client, app, oidc, "vis-b")

    page = await client.get("/projects/vis-b/releases", headers={**BROWSER, **auth(token)})
    assert page.status_code == 200, page.text

    assert "broken.zip" not in page.text
    # 三類齊備 = app.exe / src.zip / notes.pdf,共 3 個 ready。
    # 🔴 計數也要過濾:寫「4 個檔案」卻只列出 3 個,讀的人會以為頁面壞了。
    assert "3 個檔案" in page.text
    assert "4 個檔案" not in page.text


async def test_上傳頁仍然看得到未完成的檔案(client, app, oidc):
    """⚠ 反向驗證:別過濾過頭。

    上傳頁是**擁有者的作業面**。把失敗的檔藏起來,他不會知道要重傳,
    那一列就變成沒有任何入口的幽靈。
    """
    token, release_id = await _project_with_broken_artifact(client, app, oidc, "vis-c")

    page = await client.get(
        f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)}
    )
    assert page.status_code == 200, page.text
    assert "broken.zip" in page.text
    assert "pending" in page.text


async def test_全部ready時三頁顯示不變(client, app, oidc):
    """沒有壞檔的情況下,本次改動不得改變任何既有行為。"""
    await make_user(app, "vis-d-owner")
    token = oidc.issue("vis-d-owner")
    await client.post(
        "/v1/projects",
        json={"slug": "vis-d", "name": "全好", "summary": "x", "visibility": "internal"},
        headers=auth(token),
    )
    resp = await client.post(
        "/v1/projects/vis-d/releases",
        json={"version": "1.0.0", "notes": "n"},
        headers=auth(token),
    )
    release_id = resp.json()["id"]
    await client.put(
        f"/v1/releases/{release_id}/artifacts/app.exe?kind=binary",
        content=EXE,
        headers=auth(token),
    )
    await complete_kinds(client, token, release_id)
    await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))

    project_page = await client.get("/projects/vis-d", headers={**BROWSER, **auth(token)})
    history = await client.get("/projects/vis-d/releases", headers={**BROWSER, **auth(token)})
    for page in (project_page, history):
        assert page.status_code == 200
        for name in ("app.exe", "src.zip", "notes.pdf"):
            assert name in page.text
    assert "3 個檔案" in history.text


# DOC_PDF 由 conftest 匯入供三類齊備使用;此處引用以免 lint 誤判未使用。
assert DOC_PDF
