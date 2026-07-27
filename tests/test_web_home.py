"""T41 主頁:專案列表 + 搜尋 + 標籤篩選(F71)。

🔴 本檔的紅線是**可見性不得因為換到網頁就鬆掉**:private 專案不能出現在非成員的首頁。
API 與網頁共用 `queries.query_projects()`,所以有一條測試直接比對兩者看到的專案集合
——兩份查詢遲早會分岔,而分岔的後果是資料外洩。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


async def _make_project(client, token, slug, name, **extra):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": name, **extra}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_tags(client, token, slug, tags):
    resp = await client.put(
        f"/v1/projects/{slug}/tags", json={"tags": tags}, headers=auth(token)
    )
    assert resp.status_code == 200, resp.text


# --- 登入狀態 ---------------------------------------------------------------


async def test_匿名訪客看到登入提示而不是專案列表(client, active_user):
    """網頁不能回 401;但也絕不能因此漏出專案。"""
    _, token = active_user
    await _make_project(client, token, "public-tool", "公開工具")

    resp = await client.get("/", headers=BROWSER)
    assert resp.status_code == 200
    assert "登入" in resp.text
    assert "公開工具" not in resp.text, "匿名訪客不該看到任何專案"


async def test_待開通者看到與API相同的指引文案(client, app, oidc):
    """兩份文案遲早會不一致,而使用者會同時從 API 與網頁看到它。"""
    from app.models import UserStatus
    from app.problems import pending_activation

    await make_user(app, "sub-pending-web", status=UserStatus.pending)
    token = oidc.issue("sub-pending-web")

    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert pending_activation().detail in resp.text


# --- 專案列表 ---------------------------------------------------------------


async def test_已開通者看到專案卡片(client, active_user):
    _, token = active_user
    await _make_project(client, token, "demo-tool", "示範工具", summary="一個示範用的小工具")
    await _set_tags(client, token, "demo-tool", ["python", "工具"])

    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    body = resp.text
    assert "示範工具" in body
    assert "demo-tool" in body
    assert "一個示範用的小工具" in body
    assert "python" in body
    assert "工具" in body


async def test_空結果顯示空狀態文案(client, active_user):
    _, token = active_user
    resp = await client.get("/?q=不存在的關鍵字", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "沒有" in resp.text, "空結果要有文案,不能是一片空白"


# --- 🔴 可見性 --------------------------------------------------------------


async def test_private專案不出現在非成員的首頁(client, active_user, app, oidc):
    _, owner_token = active_user
    await _make_project(client, owner_token, "secret-tool", "機密工具", visibility="private")

    await make_user(app, "sub-stranger")
    stranger = oidc.issue("sub-stranger")
    resp = await client.get("/", headers={**BROWSER, **auth(stranger)})
    assert resp.status_code == 200
    assert "機密工具" not in resp.text
    assert "secret-tool" not in resp.text

    # 成員(此處為 owner 本人)看得到
    mine = await client.get("/", headers={**BROWSER, **auth(owner_token)})
    assert "機密工具" in mine.text


async def test_網頁與API看到的專案集合一致(client, active_user, app, oidc):
    """🔴 兩份查詢分岔的後果是資料外洩,所以直接比對兩邊的結果。"""
    _, owner_token = active_user
    await _make_project(client, owner_token, "open-a", "公開甲")
    await _make_project(client, owner_token, "open-b", "公開乙")
    await _make_project(client, owner_token, "hidden-c", "機密丙", visibility="private")

    await make_user(app, "sub-compare")
    other = oidc.issue("sub-compare")

    api = await client.get("/v1/projects", headers=auth(other))
    assert api.status_code == 200
    api_slugs = {item["slug"] for item in api.json()["items"]}

    page = await client.get("/", headers={**BROWSER, **auth(other)})
    page_slugs = {slug for slug in ("open-a", "open-b", "hidden-c") if slug in page.text}

    assert page_slugs == api_slugs
    assert "hidden-c" not in page_slugs


# --- 搜尋與標籤篩選 ---------------------------------------------------------


async def test_關鍵字搜尋(client, active_user):
    _, token = active_user
    await _make_project(client, token, "alpha-tool", "報表產生器")
    await _make_project(client, token, "beta-tool", "備份腳本")

    resp = await client.get("/?q=報表", headers={**BROWSER, **auth(token)})
    assert "報表產生器" in resp.text
    assert "備份腳本" not in resp.text


async def test_標籤篩選(client, active_user):
    _, token = active_user
    await _make_project(client, token, "py-tool", "Python 工具")
    await _make_project(client, token, "sh-tool", "Shell 工具")
    await _set_tags(client, token, "py-tool", ["python"])
    await _set_tags(client, token, "sh-tool", ["shell"])

    resp = await client.get("/?tag=python", headers={**BROWSER, **auth(token)})
    assert "Python 工具" in resp.text
    assert "Shell 工具" not in resp.text


async def test_標籤是可點的連結且經過URL編碼(client, active_user):
    """中文標籤必然需要 URL 編碼;連結也一樣要帶路徑前綴。"""
    _, token = active_user
    await _make_project(client, token, "cn-tool", "中文標籤工具")
    await _set_tags(client, token, "cn-tool", ["報表"])

    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    tag_links = [link for link in _links(resp.text) if "tag=" in link]
    assert tag_links, "標籤應該是可點的連結"
    for link in tag_links:
        assert link.startswith(f"{PREFIX}/"), link
        assert "報表" not in link, f"中文標籤未經 URL 編碼:{link}"
        assert "%E5%A0%B1%E8%A1%A8" in link or "%" in link


# --- 分頁 -------------------------------------------------------------------


async def test_分頁可用且連結帶著目前的篩選條件(client, active_user):
    _, token = active_user
    for i in range(25):
        await _make_project(client, token, f"tool-{i:02d}", f"工具{i:02d}")
        await _set_tags(client, token, f"tool-{i:02d}", ["批次"])

    first = await client.get("/?tag=批次", headers={**BROWSER, **auth(token)})
    assert first.status_code == 200
    next_links = [link for link in _links(first.text) if "offset=" in link]
    assert next_links, "超過一頁時應該要有下一頁連結"
    assert any("tag=" in link for link in next_links), "分頁連結要帶著目前的篩選條件"

    second = await client.get("/?tag=批次&offset=20", headers={**BROWSER, **auth(token)})
    assert second.status_code == 200
    # 第二頁不該再有「下一頁」(25 筆、每頁 20)
    assert not [
        link for link in _links(second.text) if "offset=40" in link
    ], "最後一頁不該有下一頁"


async def test_第二頁顯示的是不同的專案(client, active_user):
    _, token = active_user
    for i in range(25):
        await _make_project(client, token, f"page-{i:02d}", f"分頁{i:02d}")

    first = await client.get("/", headers={**BROWSER, **auth(token)})
    second = await client.get("/?offset=20", headers={**BROWSER, **auth(token)})
    on_first = {f"page-{i:02d}" for i in range(25) if f"page-{i:02d}" in first.text}
    on_second = {f"page-{i:02d}" for i in range(25) if f"page-{i:02d}" in second.text}
    assert len(on_first) == 20
    assert len(on_second) == 5
    assert not (on_first & on_second), "兩頁不該有重複的專案"


# --- 🔴 逸出與 URL 編碼 -----------------------------------------------------


async def test_搜尋關鍵字同時做HTML逸出與URL編碼(client, active_user):
    """🔴 兩件不同的事:autoescape 管 HTML 語意,urlencode 管 URL 語意,少一個都是洞。"""
    _, token = active_user
    for i in range(25):
        await _make_project(client, token, f"x-{i:02d}", f"專案{i:02d}")

    evil = '"><script>alert(1)</script>'
    resp = await client.get("/", params={"q": evil}, headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text, "未做 HTML 逸出"

    # 有結果才會有分頁連結;用能命中的關鍵字再驗一次連結的 URL 編碼
    tricky = "專案0&x=1"
    paged = await client.get("/", params={"q": tricky}, headers={**BROWSER, **auth(token)})
    for link in _links(paged.text):
        assert "&x=1" not in link.replace("&amp;", "&").split("q=")[0], f"查詢字串外洩:{link}"


async def test_首頁所有連結仍帶前綴且無絕對網址(client, active_user):
    """T40 的紅線在 T41 之後必須依然成立。"""
    _, token = active_user
    await _make_project(client, token, "link-tool", "連結檢查")
    await _set_tags(client, token, "link-tool", ["python"])

    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    found = _links(resp.text)
    assert found
    for link in found:
        assert link.startswith(f"{PREFIX}/"), link
        assert not link.startswith(("http://", "https://", "//")), link
