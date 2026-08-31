"""T132:專案頁 / 版本頁 / 留言顯示真名(契約 v3.4 獲准之後)。

Benny 連續三次回報「使用者還是看不到作者」。盤點是兩件事:
1. 線上是 v0.2.5,T118/T125 的程式碼還沒部署 —— 那件事換版就解決;
2. 🔴 就算部署了也不是名字,是 `sub` 前 8 碼 —— 契約 §4.2a L1 第 3 條擋著,
   而我方 2026-08-12 的申請躺了兩週沒被裁決。

2026-08-31 Benny 裁決「准」,契約升 v3.4:用途擴及「專案/版本/內容頁的擁有者、
作者、上傳者辨識」。**放寬的是「顯示在哪」** —— 仍不進 API、仍不得再散布。

🔴 本檔同時釘住**沒有放寬的那一半**:API 回應不得出現名字。
少了那條,「放寬顯示」很容易被讀成「放寬一切」。
"""

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
EXE = b"MZ\x90\x00" + b"\x00" * 60


async def _project(client, token, slug, name="工具"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": name, "summary": "x", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text


async def _published_release(client, app, oidc, token, slug, version="1.0.0"):
    from tests.conftest import complete_kinds

    resp = await client.post(
        f"/v1/projects/{slug}/releases", json={"version": version, "notes": "n"}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    await client.put(
        f"/v1/releases/{rid}/artifacts/app.exe?kind=binary", content=EXE, headers=auth(token)
    )
    await complete_kinds(client, token, rid)
    assert (await client.post(f"/v1/releases/{rid}/publish", headers=auth(token))).status_code == 200
    await make_user(app, f"{slug}-approver", admin=True)
    admin = oidc.issue(f"{slug}-approver")
    ok = await client.post(f"/v1/releases/{rid}/approve", headers=auth(admin))
    assert ok.status_code == 200, ok.text
    return rid


async def test_專案頁顯示擁有者的名字(client, app, oidc):
    """🔴 由**別人**來看 —— 檢視者自己的名字本來就在導覽列,同一人看自己測不出東西。"""
    await make_user(app, "sub-t132-owner-aaaaaaaa")
    owner = oidc.issue("sub-t132-owner-aaaaaaaa", name="林小明")
    await _project(client, owner, "named-tool")

    await make_user(app, "sub-t132-visitor")
    visitor = oidc.issue("sub-t132-visitor", name="訪客乙")

    page = await client.get("/projects/named-tool", headers={**BROWSER, **auth(visitor)})
    assert page.status_code == 200, page.text
    assert "擁有者" in page.text
    assert "林小明" in page.text, "契約 v3.4 已准:專案頁得顯示擁有者名字"


async def test_版本歷史顯示建立者的名字(client, app, oidc):
    await make_user(app, "sub-t132-jack-bbbbbbbb")
    jack = oidc.issue("sub-t132-jack-bbbbbbbb", name="陳大文")
    await _project(client, jack, "authored-named")
    await _published_release(client, app, oidc, jack, "authored-named")

    await make_user(app, "sub-t132-reader")
    reader = oidc.issue("sub-t132-reader", name="讀者丙")

    page = await client.get(
        "/projects/authored-named/releases", headers={**BROWSER, **auth(reader)}
    )
    assert page.status_code == 200, page.text
    assert "陳大文" in page.text, "每一版要看得出是誰做的 —— 名字,不是 UUID"


async def test_留言顯示作者的名字(client, app, oidc):
    await make_user(app, "sub-t132-cmt-cccccccc")
    author = oidc.issue("sub-t132-cmt-cccccccc", name="留言者丁")
    await _project(client, author, "commented-tool")
    resp = await client.post(
        "/v1/projects/commented-tool/comments",
        json={"body_markdown": "很好用"},
        headers=auth(author),
    )
    assert resp.status_code == 201, resp.text

    await make_user(app, "sub-t132-cmt-reader")
    reader = oidc.issue("sub-t132-cmt-reader", name="讀者戊")
    page = await client.get("/projects/commented-tool", headers={**BROWSER, **auth(reader)})
    assert page.status_code == 200
    assert "留言者丁" in page.text


async def test_沒有名字的人退回識別碼而不是空白(client, app, oidc):
    """守門:契約第 6 項明文「為空時不得阻斷任何功能,顯示退回 `sub`」。

    `name` claim 由 firstName + lastName 推導,兩者皆空即為 NULL —— 這是常態不是例外。
    空白會讓人以為欄位壞了。
    """
    await make_user(app, "sub-t132-noname-dddddddd")
    nameless = oidc.issue("sub-t132-noname-dddddddd")  # 刻意不帶 name
    await _project(client, nameless, "nameless-tool")

    await make_user(app, "sub-t132-nn-reader")
    reader = oidc.issue("sub-t132-nn-reader")
    page = await client.get("/projects/nameless-tool", headers={**BROWSER, **auth(reader)})
    assert page.status_code == 200
    assert "擁有者" in page.text
    assert "sub-t132" in page.text, "沒有名字時要退回 sub 前 8 碼,不得空白"


async def test_API回應仍不得出現名字(client, app, oidc):
    """🔴 契約 v3.4 **沒有**放寬這一項:名字不進 API 回應。

    少了這條測試,「放寬顯示」很容易被讀成「放寬一切」——
    而 API 帶名字等於開一條可以整批匯出姓名的路(§4.2a L1 原本的核心顧慮)。
    """
    await make_user(app, "sub-t132-api-eeeeeeee")
    owner = oidc.issue("sub-t132-api-eeeeeeee", name="不該進API的名字")
    await _project(client, owner, "api-check-tool")
    await _published_release(client, app, oidc, owner, "api-check-tool")

    await make_user(app, "sub-t132-api-reader")
    reader = oidc.issue("sub-t132-api-reader")
    for url in (
        "/v1/projects",
        "/v1/projects/api-check-tool",
        "/v1/projects/api-check-tool/releases",
        "/v1/projects/api-check-tool/comments",
    ):
        resp = await client.get(url, headers=auth(reader))
        assert resp.status_code == 200, f"{url}: {resp.text}"
        assert "不該進API的名字" not in resp.text, f"{url} 的回應含名字 —— 契約未放寬 API"
