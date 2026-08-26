"""T103 專案留言板:使用者對專案的回饋。

Benny 四項需求之三:「專案可以增加 user feedback」,裁示做**專案留言板**。

🔴 **不能沿用問題回報系統**:那是「回報這個網站的問題」,只有本人與平台管理員
看得到、404 不洩漏存在。專案回饋的可見性**完全相反**——它就是要給同專案的人看的。
兩者長得像但方向相反,混用會把其中一邊的保證弄壞。

本檔釘住的四條界線:

1. **可見性跟著專案走**,而且沿用 `require_project_read` 不另寫一套——
   private 專案的留言對非成員是 **404 而非 403**(403 等於承認專案存在)。
2. 🔴 **專案擁有者不能刪別人的留言。** 若能刪,留言板就只會剩下好話,
   而一個只留得住讚美的回饋區**比沒有回饋區更糟**——它讓讀的人誤以為
   「沒有負評」等於「沒有問題」。想回應批評的方式是再留一則言。
3. **能讀就能寫**,viewer 也能留言:只有 maintainer 講得了話的留言板不叫 feedback。
4. 留言是使用者可控字串,**HTML 必須被逸出**。
"""

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"


async def _project(client, token, slug="feedback-tool", **extra):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "回饋測試", **extra}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text


async def _say(client, token, slug, body):
    return await client.post(
        f"/v1/projects/{slug}/comments", json={"body_markdown": body}, headers=auth(token)
    )


# --- 基本 -------------------------------------------------------------------


async def test_留言後在專案頁看得到(client, app, oidc):
    await make_user(app, "sub-owner-t103")
    owner = oidc.issue("sub-owner-t103")
    await _project(client, owner)

    await make_user(app, "sub-user-t103")
    user = oidc.issue("sub-user-t103")
    said = await _say(client, user, "feedback-tool", "在 Win11 上會閃退,v1.2 才修好。")
    assert said.status_code == 201, said.text

    page = await client.get(
        f"{PREFIX}/projects/feedback-tool", headers={**BROWSER, **auth(owner)}
    )
    assert page.status_code == 200
    assert "在 Win11 上會閃退" in page.text


async def test_viewer也能留言(client, app, oidc):
    """🔴 只有 maintainer 講得了話的留言板不叫 user feedback。"""
    await make_user(app, "sub-owner-t103b")
    owner = oidc.issue("sub-owner-t103b")
    await _project(client, owner, slug="viewer-say-tool")

    watcher = await make_user(app, "sub-viewer-t103")
    added = await client.put(
        "/v1/projects/viewer-say-tool/members",
        json={"user_id": str(watcher.id), "role": "viewer"},
        headers=auth(owner),
    )
    assert added.status_code in (200, 201), added.text

    said = await _say(client, oidc.issue("sub-viewer-t103"), "viewer-say-tool", "很好用。")
    assert said.status_code == 201, said.text


# --- 🔴 可見性 --------------------------------------------------------------


async def test_private專案的留言對非成員不可見且回404(client, app, oidc):
    """🔴 404 不是 403——403 等於承認這個專案存在。"""
    await make_user(app, "sub-owner-t103c")
    owner = oidc.issue("sub-owner-t103c")
    await _project(client, owner, slug="secret-tool", visibility="private")
    await _say(client, owner, "secret-tool", "內部備註")

    # 🔴 前提斷言:端點真的存在而且對成員可用。
    # 少了這一條,「非成員拿到 404」在**端點根本不存在**時也會綠——
    # 那是假綠中最陰險的一種:它為了錯的理由而通過,而且永遠不會紅。
    mine = await client.get("/v1/projects/secret-tool/comments", headers=auth(owner))
    assert mine.status_code == 200, f"前提不成立:端點對成員也不通({mine.status_code})"
    assert len(mine.json()) == 1

    await make_user(app, "sub-outsider-t103")
    outsider = oidc.issue("sub-outsider-t103")

    listed = await client.get("/v1/projects/secret-tool/comments", headers=auth(outsider))
    assert listed.status_code == 404, f"private 留言不得外洩,且要 404:{listed.status_code}"

    said = await _say(client, outsider, "secret-tool", "我進得來嗎")
    assert said.status_code == 404


# --- 🔴 刪除權限 ------------------------------------------------------------


async def test_專案擁有者不能刪別人的留言(client, app, oidc):
    """🔴 本任務最重要的一條。

    專案擁有者若能刪掉別人對自己專案的評語,留言板就只會剩下好話——
    而一個只留得住讚美的回饋區**比沒有回饋區更糟**:它讓讀的人誤以為
    「沒有負評」等於「沒有問題」。想回應批評,方式是再留一則言。
    """
    await make_user(app, "sub-owner-t103d")
    owner = oidc.issue("sub-owner-t103d")
    await _project(client, owner, slug="nocensor-tool")

    await make_user(app, "sub-critic-t103")
    critic = oidc.issue("sub-critic-t103")
    said = await _say(client, critic, "nocensor-tool", "這支程式在大檔案上很慢。")
    assert said.status_code == 201
    comment_id = said.json()["id"]

    dropped = await client.delete(
        f"/v1/projects/nocensor-tool/comments/{comment_id}", headers=auth(owner)
    )
    assert dropped.status_code == 403, "擁有者不得刪除他人的批評"

    still = await client.get("/v1/projects/nocensor-tool/comments", headers=auth(owner))
    assert any("很慢" in c["body_markdown"] for c in still.json()), "留言必須還在"


async def test_作者本人可以刪自己的留言(client, app, oidc):
    await make_user(app, "sub-owner-t103e")
    owner = oidc.issue("sub-owner-t103e")
    await _project(client, owner, slug="selfdel-tool")

    await make_user(app, "sub-author-t103")
    author = oidc.issue("sub-author-t103")
    said = await _say(client, author, "selfdel-tool", "打錯字的留言")
    comment_id = said.json()["id"]

    dropped = await client.delete(
        f"/v1/projects/selfdel-tool/comments/{comment_id}", headers=auth(author)
    )
    assert dropped.status_code == 204, dropped.text


async def test_平台管理員可以刪任何留言(client, app, oidc, admin_user):
    """不當內容的管理責任在平台方,不在專案擁有者。"""
    _, admin_token = admin_user
    await make_user(app, "sub-owner-t103f")
    owner = oidc.issue("sub-owner-t103f")
    await _project(client, owner, slug="moderate-tool")

    await make_user(app, "sub-rude-t103")
    said = await _say(client, oidc.issue("sub-rude-t103"), "moderate-tool", "不當內容")
    comment_id = said.json()["id"]

    dropped = await client.delete(
        f"/v1/projects/moderate-tool/comments/{comment_id}", headers=auth(admin_token)
    )
    assert dropped.status_code == 204, dropped.text


# --- 🔴 逸出 ----------------------------------------------------------------


async def test_留言中的HTML被逸出(client, app, oidc):
    """留言是使用者可控字串,autoescape 必須生效。"""
    await make_user(app, "sub-owner-t103g")
    owner = oidc.issue("sub-owner-t103g")
    await _project(client, owner, slug="escape-tool")
    await _say(client, owner, "escape-tool", "<script>alert(1)</script>")

    page = await client.get(f"{PREFIX}/projects/escape-tool", headers={**BROWSER, **auth(owner)})
    body = page.text[page.text.index("<main") : page.text.index("</main>")]
    assert "<script>alert(1)</script>" not in body, "留言中的標籤必須被逸出"
    assert "&lt;script&gt;" in body
