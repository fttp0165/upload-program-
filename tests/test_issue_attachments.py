"""T78:問題回報的附件與貼圖(第二期)。

🔴 **本專案第一條「非 attachment」的下載路徑就在這裡。**

所有既有下載都是 `Content-Disposition: attachment` + `nosniff`,為的是不讓上傳內容
在我們的網域被瀏覽器呈現。截圖要能直接看到,這條紅線得開一個口——所以施工計畫書
§4.2 的六條收窄,每一條在這裡都有對應的測試,包括**確認舊路徑沒有被順手改壞**的回歸測試。
"""

import io

from sqlalchemy import func, select

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
GIF = b"GIF8" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!DOCTYPE html><html><body>x</body></html>"
PDF = b"%PDF-1.4\n" + b"\x00" * 64


async def _issue(client, token, title="附件測試") -> str:
    resp = await client.post(
        "/issues/new",
        data={"title": title, "body_markdown": "內容", "page_url": ""},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text
    return resp.headers["location"].rstrip("/").split("/")[-1]


async def _put(client, token, issue_id, name, body):
    return await client.put(
        f"/v1/issues/{issue_id}/attachments/{name}", content=body, headers=auth(token)
    )


# --- 三種圖片都能上傳並顯示 --------------------------------------------------


async def test_三種圖片型別都能上傳(client, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)
    for name, body in (("a.png", PNG), ("b.jpg", JPEG), ("c.gif", GIF)):
        resp = await _put(client, token, issue_id, name, body)
        assert resp.status_code == 201, f"{name}: {resp.text}"


async def test_上傳後詳情頁以img顯示(client, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)
    await _put(client, token, issue_id, "shot.png", PNG)

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "<img" in page.text
    assert f"/upload/v1/issues/{issue_id}/attachments/" in page.text


# --- 🔴 型別:偽裝一律拒收且不留物件 ------------------------------------------


async def test_HTML與SVG與PDF偽裝成png一律拒收(client, active_user, storage):
    _, token = active_user
    issue_id = await _issue(client, token)
    before = len(storage.objects)

    for name, body in (("evil.png", HTML), ("evil2.png", SVG), ("doc.png", PDF)):
        resp = await _put(client, token, issue_id, name, body)
        assert resp.status_code == 422, f"{name} 應被拒收"

    assert len(storage.objects) == before, "🔴 判型不過不得留下任何物件"


async def test_SVG就算宣稱是圖片也不收(client, active_user):
    """SVG 是可執行的 XML——它正是 inline 顯示最危險的型別。"""
    _, token = active_user
    issue_id = await _issue(client, token)
    resp = await _put(client, token, issue_id, "x.svg", SVG)
    assert resp.status_code == 422


# --- 🔴 inline 端點的標頭 -----------------------------------------------------


async def test_inline端點不是attachment且帶nosniff(client, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)
    put = await _put(client, token, issue_id, "shot.png", PNG)
    attachment_id = put.json()["id"]

    resp = await client.get(
        f"/v1/issues/{issue_id}/attachments/{attachment_id}", headers=auth(token)
    )
    assert resp.status_code == 200
    disposition = resp.headers.get("content-disposition", "")
    assert "attachment" not in disposition, "🔴 這條路徑刻意是 inline"
    assert "inline" in disposition
    assert resp.headers["x-content-type-options"] == "nosniff"
    # 🔴 Content-Type 用**判定值**,不是使用者宣稱的
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG


# --- 🔴 回歸:舊的下載路徑不得被改壞 ------------------------------------------


async def test_releases下載仍然是attachment(client, active_user):
    """開 inline 這個口子最危險的不是新端點,是有人順手把共用的下載改壞。"""
    _, token = active_user
    await client.post("/v1/projects", json={"slug": "reg-proj", "name": "回歸"}, headers=auth(token))
    release = await client.post(
        "/v1/projects/reg-proj/releases", json={"version": "v1"}, headers=auth(token)
    )
    rid = release.json()["id"]
    put = await client.put(
        f"/v1/releases/{rid}/artifacts/note.pdf?kind=doc", content=PDF, headers=auth(token)
    )
    aid = put.json()["id"]

    resp = await client.get(f"/v1/releases/{rid}/artifacts/{aid}/download", headers=auth(token))
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"


# --- 權限 -------------------------------------------------------------------


async def test_他人讀附件回404(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)
    attachment_id = (await _put(client, token, issue_id, "shot.png", PNG)).json()["id"]

    await make_user(app, "sub-att-other")
    resp = await client.get(
        f"/v1/issues/{issue_id}/attachments/{attachment_id}",
        headers=auth(oidc.issue("sub-att-other")),
    )
    assert resp.status_code == 404


async def test_管理員讀得到附件(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)
    attachment_id = (await _put(client, token, issue_id, "shot.png", PNG)).json()["id"]

    await make_user(app, "sub-att-admin", admin=True)
    resp = await client.get(
        f"/v1/issues/{issue_id}/attachments/{attachment_id}",
        headers=auth(oidc.issue("sub-att-admin")),
    )
    assert resp.status_code == 200


async def test_他人不能上傳到別人的回報(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue(client, token)

    await make_user(app, "sub-att-intruder")
    resp = await _put(client, oidc.issue("sub-att-intruder"), issue_id, "x.png", PNG)
    assert resp.status_code == 404


# --- 上限 -------------------------------------------------------------------


async def test_超過單張上限回413(client, active_user, app):
    from app.routers.issues import MAX_ATTACHMENT_BYTES

    _, token = active_user
    issue_id = await _issue(client, token)
    huge = PNG + b"\x00" * (MAX_ATTACHMENT_BYTES + 1)
    resp = await _put(client, token, issue_id, "huge.png", huge)
    assert resp.status_code == 413


async def test_超過張數上限回422(client, active_user):
    from app.routers.issues import MAX_ATTACHMENTS

    _, token = active_user
    issue_id = await _issue(client, token)
    for i in range(MAX_ATTACHMENTS):
        assert (await _put(client, token, issue_id, f"a{i}.png", PNG)).status_code == 201
    resp = await _put(client, token, issue_id, "one-too-many.png", PNG)
    assert resp.status_code == 422


# --- 無 JS 也能附圖(漸進增強)-----------------------------------------------


async def test_純表單也能附圖(client, active_user):
    """🔴 網站壞掉時 JS 更可能是壞掉的那一部分——附圖不得只有 JS 一條路。"""
    _, token = active_user
    issue_id = await _issue(client, token)

    resp = await client.post(
        f"/issues/{issue_id}/attachments",
        files={"attachment": ("shot.png", io.BytesIO(PNG), "image/png")},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "<img" in page.text


# --- 稽核 -------------------------------------------------------------------


async def test_上傳寫稽核(client, active_user, app):
    from app.models import AuditEvent

    _, token = active_user
    issue_id = await _issue(client, token)
    await _put(client, token, issue_id, "shot.png", PNG)

    async with app.state.sessionmaker() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "issue.attachment_upload")
            )
        ).scalar()
    assert int(count or 0) == 1
