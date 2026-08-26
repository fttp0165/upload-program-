"""T43 專案歷史:版本列表(F73)。

🐛 本檔的第一組測試釘住的是一個**既有缺陷**:`GET /v1/projects/{slug}/releases`
原本依 `created_at` 倒序,而 T35 已經確立「建立順序不等於發布順序」——
後果是列表第一筆與 `/latest` 可能指向不同版本,而且兩邊都不會報錯。
F73 的驗收標準正是「依發布時間倒序」,所以一併修正 API,不製造網頁與 API 的分岔。

🔴 draft 的可見性、private 專案不洩漏存在,沿用既有規則與 T42 的結構保證。
"""

import re

from tests.conftest import auth, complete_kinds, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

# 契約 §2.1 的平台層短網址:由 gateway 302 轉址,**刻意不帶各 App 的前綴**
# (加上前綴會變成一條不存在的路徑)。這是下面「所有連結帶前綴」那條紅線的
# **具名例外**——不是把斷言放寬,例外本身在 test_sso_contract.py 有測試保護。
# T67:平台入口(`/`)也是平台層網址,同一類具名例外。
# 🔴 白名單納入 `/` 會讓「漏掉 url() 的首頁連結」逃過這條檢查——
#    補償斷言在 test_portal_link.py(每條 `/` 都必須帶 nav-exit / side-exit 標記)。
PLATFORM_URLS = {"/account", "/login", "/"}



def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


def _main(html: str) -> str:
    """只取 <main> 內容再比對順序。

    🐛 T67 踩到的坑:排序斷言原本在整頁 HTML 上找 `body.index("v9")`,
    而版型骨架的行內 SVG 路徑資料含 `v9`(SVG 的垂直線指令),
    比真正的版本號更早出現 → 測試紅得莫名其妙。**版面的東西不該影響排序斷言**,
    所以先切出 <main>(內容區)再比。
    """
    start = html.index("<main")
    return html[start : html.index("</main>", start)]


async def _project(client, token, slug="cli-tool", **extra):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "命令列工具", **extra}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text


async def _create(client, token, slug, version, notes=""):
    resp = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": version, "notes": notes},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _upload(client, token, release_id, filename="tool.bin"):
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/{filename}?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _publish(client, token, release_id):
    await complete_kinds(client, token, release_id)
    resp = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert resp.status_code == 200, resp.text


# --- 🐛 排序:既有缺陷的修正 ------------------------------------------------


async def _reversed_order_fixture(client, token):
    """造出「建立順序與發布順序相反」的資料(沿用 T35 那組情境)。

    建立:v10 先建、v9 後建   → 依 created_at 倒序會是 v9 在前(錯)
    發布:v9 先發、v10 後發   → 依 published_at 倒序會是 v10 在前(對)
    """
    await _project(client, token)
    ten = await _create(client, token, "cli-tool", "v10")
    nine = await _create(client, token, "cli-tool", "v9")
    await _upload(client, token, ten)
    await _upload(client, token, nine)
    await _publish(client, token, nine)   # v9 先發
    await _publish(client, token, ten)    # v10 後發
    return ten, nine


async def test_API的版本列表依發布時間倒序(client, active_user):
    """🐛 原本依 created_at 倒序,會給出 v9 —— 與 `/latest` 的答案矛盾。"""
    _, token = active_user
    await _reversed_order_fixture(client, token)

    resp = await client.get("/v1/projects/cli-tool/releases", headers=auth(token))
    assert resp.status_code == 200, resp.text
    versions = [item["version"] for item in resp.json()["items"]]
    assert versions[0] == "v10", (
        f"列表第一筆是 {versions[0]},表示用了建立時間排序;"
        "與 /latest 的答案矛盾,而且兩邊都不會報錯"
    )


async def test_列表第一筆與latest一致(client, active_user):
    """兩個端點對「最新是哪一版」不能各說各話。"""
    _, token = active_user
    await _reversed_order_fixture(client, token)

    listed = await client.get("/v1/projects/cli-tool/releases", headers=auth(token))
    latest = await client.get("/v1/projects/cli-tool/releases/latest", headers=auth(token))
    assert listed.json()["items"][0]["version"] == latest.json()["version"]


async def test_歷史頁依發布時間倒序(client, active_user):
    _, token = active_user
    await _reversed_order_fixture(client, token)

    page = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})).text
    body = _main(page)
    assert body.index("v10") < body.index("v9"), "歷史頁第一筆應該是最後發布的 v10"


async def test_draft浮在最上面(client, active_user):
    """draft 是作者正在做的東西,對看得到它的人最相關。"""
    _, token = active_user
    await _project(client, token)
    first = await _create(client, token, "cli-tool", "v1.0.0")
    await _upload(client, token, first)
    await _publish(client, token, first)
    await _create(client, token, "cli-tool", "v2.0.0-wip", notes="還在做")

    body = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})).text
    assert body.index("v2.0.0-wip") < body.index("v1.0.0")


# --- 🔴 draft 的可見性 ------------------------------------------------------


async def test_非成員看不到draft(client, active_user, app, oidc):
    """draft 是作者的工作區,不是給別人看的。"""
    _, owner_token = active_user
    await _project(client, owner_token)
    published = await _create(client, owner_token, "cli-tool", "v1.0.0")
    await _upload(client, owner_token, published)
    await _publish(client, owner_token, published)
    await _create(client, owner_token, "cli-tool", "v2.0.0-secret", notes="還沒好")

    await make_user(app, "sub-reader43")
    reader = oidc.issue("sub-reader43")
    body = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(reader)})).text
    assert "v1.0.0" in body
    assert "v2.0.0-secret" not in body
    assert "還沒好" not in body


# --- 展開與下載 -------------------------------------------------------------


async def test_可展開看各版檔案並下載(client, active_user):
    _, token = active_user
    await _project(client, token)
    release_id = await _create(client, token, "cli-tool", "v1.0.0", notes="首版")
    artifact_id = await _upload(client, token, release_id)
    await _publish(client, token, release_id)

    body = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})).text
    assert "<details" in body, "展開/收合用原生 <details>,不需要 JS(CSP 禁 inline script)"
    assert "tool.bin" in body
    assert f"{PREFIX}/v1/releases/{release_id}/artifacts/{artifact_id}/download" in body


async def test_最新版預設展開其餘收合(client, active_user):
    _, token = active_user
    await _project(client, token)
    for version in ("v1.0.0", "v2.0.0"):
        rid = await _create(client, token, "cli-tool", version)
        await _upload(client, token, rid)
        await _publish(client, token, rid)

    body = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})).text
    # 用 regex 而不是 `"<details open" in body`:後者綁死屬性順序,
    # 是在測 markup 的寫法而不是測行為。
    opened = re.findall(r"<details[^>]*\bopen\b[^>]*>", body)
    assert len(opened) == 1, "只有最新版預設展開"
    assert body.count("<details") == 2


async def test_掃毒狀態在歷史頁也誠實顯示(client, active_user):
    """🔴 同 T42:任何有下載按鈕的地方都要看得到 not_scanned。"""
    _, token = active_user
    await _project(client, token)
    rid = await _create(client, token, "cli-tool", "v1.0.0")
    await _upload(client, token, rid)
    await _publish(client, token, rid)

    body = (await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})).text
    assert "not_scanned" in body or "未掃描" in body


async def test_無版本時顯示提示(client, active_user):
    _, token = active_user
    await _project(client, token)

    resp = await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "尚未" in resp.text or "沒有" in resp.text


# --- 🔴 可見性與不洩漏存在 --------------------------------------------------


async def test_private專案的歷史對非成員回404(client, active_user, app, oidc):
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-cli", visibility="private")

    await make_user(app, "sub-outsider43")
    outsider = oidc.issue("sub-outsider43")
    resp = await client.get(
        "/projects/secret-cli/releases", headers={**BROWSER, **auth(outsider)}
    )
    assert resp.status_code == 404


async def test_匿名訪客的回應不洩漏專案是否存在(client, active_user):
    """🔴 同 T42 的結構保證:匿名一律不查詢。"""
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-cli", visibility="private")

    exists = await client.get("/projects/secret-cli/releases", headers=BROWSER)
    missing = await client.get("/projects/no-such-thing/releases", headers=BROWSER)
    assert exists.status_code == missing.status_code
    assert exists.text.replace("secret-cli", "X") == missing.text.replace("no-such-thing", "X")


async def test_待開通者看到與API相同的指引文案(client, app, oidc):
    from app.models import UserStatus
    from app.problems import pending_activation

    await make_user(app, "sub-pending43", status=UserStatus.pending)
    token = oidc.issue("sub-pending43")

    resp = await client.get("/projects/anything/releases", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert pending_activation().detail in resp.text


# --- 分頁與銜接 -------------------------------------------------------------


async def test_分頁可用(client, active_user):
    _, token = active_user
    await _project(client, token)
    for i in range(25):
        rid = await _create(client, token, "cli-tool", f"v1.0.{i}")
        await _upload(client, token, rid)
        await _publish(client, token, rid)

    first = await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})
    assert [link for link in _links(first.text) if "offset=" in link], "超過一頁要有下一頁"

    second = await client.get(
        "/projects/cli-tool/releases?offset=20", headers={**BROWSER, **auth(token)}
    )
    assert second.status_code == 200
    assert not [link for link in _links(second.text) if "offset=40" in link]


async def test_專案頁有版本歷史入口(client, active_user):
    """T42 遺留 #1。"""
    _, token = active_user
    await _project(client, token)

    body = (await client.get("/projects/cli-tool", headers={**BROWSER, **auth(token)})).text
    assert f'href="{PREFIX}/projects/cli-tool/releases"' in body


# --- T40 的紅線維持 ---------------------------------------------------------


async def test_歷史頁連結帶前綴且無絕對網址(client, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _create(client, token, "cli-tool", "v1.0.0")
    await _upload(client, token, rid)
    await _publish(client, token, rid)

    resp = await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})
    found = _links(resp.text)
    assert found
    for link in found:
        if link in PLATFORM_URLS:
            continue
        assert link.startswith(f"{PREFIX}/"), link
        assert not link.startswith(("http://", "https://", "//")), link


async def test_版本說明對使用者可控內容逸出(client, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _create(client, token, "cli-tool", "v1.0.0", notes='<script>alert(1)</script>')
    await _upload(client, token, rid)
    await _publish(client, token, rid)

    resp = await client.get("/projects/cli-tool/releases", headers={**BROWSER, **auth(token)})
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


# --- T100 🐛 草稿版本回不去 ------------------------------------------------
#
# Benny 實測:「草稿版本就不能再發布與編輯了」。他的專案裡躺著 4 個 0 檔案的草稿,
# 每一個都只有一枚「草稿」徽章,旁邊什麼都沒有。
#
# 根因不是功能沒做——上傳與發布都在,而且有測試。缺的是**入口**:
# 上傳頁 `/releases/{id}/upload` 只在「建立版本」送出後被自動導向一次
# (web.py 的 `_redirect(... f"/releases/{release.id}/upload")`),
# 使用者一旦離開就再也回不去;版本歷史頁只印徽章不給連結,
# 專案頁又只顯示最新**已發布**版本。草稿於是變成孤兒:看得見、點不進去。
#
# 🔴 兩條護欄和功能本身一樣重要:
# 1. viewer **看得見草稿但改不動**(發布要 maintainer 以上),
#    給他一個按下去必然 403 的連結,比不給更糟;
# 2. **已發布的版本不得出現這個入口**——已發布不可再變更檔案(API 回 409),
#    給連結等於誘導使用者去撞一堵牆。

EDIT_HINT = "繼續編輯"


async def test_草稿在版本歷史頁要有繼續編輯的入口(client, app, oidc):
    await make_user(app, "sub-owner-t100")
    token = oidc.issue("sub-owner-t100")
    await _project(client, token, slug="draft-tool")
    release_id = await _create(client, token, "draft-tool", "v0.9.0")

    resp = await client.get(
        f"{PREFIX}/projects/draft-tool/releases", headers={**BROWSER, **auth(token)}
    )
    assert resp.status_code == 200
    body = _main(resp.text)
    assert EDIT_HINT in body, "草稿必須有回得去的入口,否則就是孤兒"
    assert f"/releases/{release_id}/upload" in body, "入口要指向該草稿的上傳頁"


async def test_viewer看得到草稿但不得看到編輯入口(client, app, oidc):
    """🔴 給一個按下去必然 403 的連結,比不給更糟。"""
    await make_user(app, "sub-owner-t100b")
    owner = oidc.issue("sub-owner-t100b")
    await _project(client, token=owner, slug="viewer-tool")
    await _create(client, owner, "viewer-tool", "v0.9.0")

    watcher = await make_user(app, "sub-viewer-t100")
    added = await client.put(
        "/v1/projects/viewer-tool/members",
        json={"user_id": str(watcher.id), "role": "viewer"},
        headers=auth(owner),
    )
    assert added.status_code in (200, 201), added.text

    resp = await client.get(
        f"{PREFIX}/projects/viewer-tool/releases",
        headers={**BROWSER, **auth(oidc.issue("sub-viewer-t100"))},
    )
    assert resp.status_code == 200
    body = _main(resp.text)
    # 前提斷言:viewer 確實看得到這個草稿,否則下面的反向斷言只是在測「什麼都沒有」
    assert "草稿" in body, "前提不成立:viewer 看不到草稿,反向斷言會假綠"
    assert EDIT_HINT not in body, "🔴 viewer 改不動,不該給編輯入口"


async def test_已發布的版本不得出現編輯入口(client, app, oidc):
    """已發布不可再變更檔案(API 回 409),給連結等於誘導使用者去撞牆。"""
    await make_user(app, "sub-owner-t100c")
    token = oidc.issue("sub-owner-t100c")
    await _project(client, token, slug="published-tool")
    release_id = await _create(client, token, "published-tool", "v1.0.0")
    await _upload(client, token, release_id)          # binary(complete_kinds 不含)
    await complete_kinds(client, token, release_id)   # source + doc
    published = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert published.status_code == 200, published.text

    resp = await client.get(
        f"{PREFIX}/projects/published-tool/releases", headers={**BROWSER, **auth(token)}
    )
    assert resp.status_code == 200
    assert EDIT_HINT not in _main(resp.text)
