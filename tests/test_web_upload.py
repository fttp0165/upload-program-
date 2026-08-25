"""T44 上傳介面:建專案、建版本、傳檔、發布(F74)。

驗收標準是**不需 curl 即可完成一輪上傳與發布**——所以第一條測試就是走完整的一輪。

🔴 三條紅線:
1. JS 必須是**外部檔案**(CSP `default-src 'self'` 擋 inline script)
2. JS **不得碰 token**(§4.10:同源之下 localStorage 全平台可讀)——T51 已有守門
3. CSRF 的結論建立在 `SameSite=Lax` 之上,所以那個屬性本身要有測試
"""

import re

from tests.conftest import auth, complete_kinds, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
# T67:平台入口(`/`)也是平台層網址,同一類具名例外。
# 🔴 白名單納入 `/` 會讓「漏掉 url() 的首頁連結」逃過這條檢查——
#    補償斷言在 test_portal_link.py(每條 `/` 都必須帶 nav-exit / side-exit 標記)。
PLATFORM_URLS = {"/account", "/login", "/"}


def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


# --- 完整一輪(驗收標準本身)------------------------------------------------


async def test_不需curl即可完成一輪上傳與發布(client, active_user):
    """🔴 這條就是 M7 的出場條件。每一步都走網頁路由,不碰 /v1/* 的 API。"""
    _, token = active_user
    headers = {**BROWSER, **auth(token)}

    # 1. 建專案(HTML 表單 POST)
    created = await client.post(
        "/projects/new",
        # T96:短名由名稱產生(表單已無短名欄位)。
        data={"name": "round-trip", "summary": "走完整流程", "visibility": "internal"},
        headers=headers,
        follow_redirects=False,
    )
    assert created.status_code in (302, 303), created.text
    assert created.headers["location"].endswith("/projects/round-trip")

    # 2. 建版本(HTML 表單 POST)
    release = await client.post(
        "/projects/round-trip/releases/new",
        data={"version": "v1.0.0", "notes": "首版"},
        headers=headers,
        follow_redirects=False,
    )
    assert release.status_code in (302, 303), release.text
    release_id = release.headers["location"].rstrip("/").split("/")[-2]

    # 3. 傳檔(XHR PUT——測試裡直接打同一個端點,那正是 JS 會打的)
    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text
    await complete_kinds(client, token, release_id)  # T65:發布需三類齊備

    # 4. 發布(HTML 表單 POST)
    published = await client.post(
        f"/releases/{release_id}/publish", headers=headers, follow_redirects=False
    )
    assert published.status_code in (302, 303), published.text

    # 驗:專案頁看得到已發布的版本與下載按鈕
    page = await client.get("/projects/round-trip", headers=headers)
    assert "v1.0.0" in page.text
    assert "tool.bin" in page.text
    assert "下載" in page.text


# --- 導航列入口(T40 遺留)--------------------------------------------------


async def test_已開通者的導航列有建立專案(client, active_user):
    """T40 刻意延後:當時頁面還不存在,放上去就是懸空連結。"""
    _, token = active_user
    body = (await client.get("/", headers={**BROWSER, **auth(token)})).text
    assert "建立專案" in body
    assert f'href="{PREFIX}/projects/new"' in body


async def test_匿名者看不到建立專案(client):
    """看得到也點不了,只會撞牆。

    斷言的是**連結**而不是「建立專案」這四個字——首頁正文本來就有
    「建立專案,上傳原始碼…」這句散文。測連結才是測到行為。
    """
    body = (await client.get("/", headers=BROWSER)).text
    assert f'href="{PREFIX}/projects/new"' not in body


async def test_待開通者看不到建立專案(client, app, oidc):
    from app.models import UserStatus

    await make_user(app, "sub-pending44", status=UserStatus.pending)
    token = oidc.issue("sub-pending44")
    body = (await client.get("/", headers={**BROWSER, **auth(token)})).text
    assert f'href="{PREFIX}/projects/new"' not in body


# --- 表單錯誤要回到表單 -----------------------------------------------------


async def test_同名專案不再回錯誤而是自動換一個短名(client, active_user):
    """⚠ **這條測試在 T96 換了契約**,不是放水。

    舊行為:短名重複 → 回到表單顯示「已被使用」,讓使用者改那個欄位。
    新行為:表單**已經沒有短名欄位**(T96 由名稱自動產生),所以叫使用者
    「換一個短名」是叫他改一個看不到的東西 —— 改為自動加後綴。

    仍然被釘住的東西一項未少:**不得丟一頁錯誤讓使用者重打**。
    產生規則本身在 `tests/test_slugs.py`。
    """
    _, token = active_user
    headers = {**BROWSER, **auth(token)}
    payload = {"name": "dup-tool", "summary": "", "visibility": "internal"}

    first = await client.post("/projects/new", data=payload, headers=headers, follow_redirects=False)
    second = await client.post("/projects/new", data=payload, headers=headers, follow_redirects=False)

    assert first.status_code in (302, 303)
    assert second.status_code in (302, 303), "第二次同樣要成功,不得回到表單"
    assert first.headers["location"].endswith("/projects/dup-tool")
    assert second.headers["location"].endswith("/projects/dup-tool-2")


async def test_版本號重複時回到表單並顯示訊息(client, active_user):
    _, token = active_user
    headers = {**BROWSER, **auth(token)}
    await client.post(
        "/projects/new",
        data={"name": "ver-tool", "summary": "", "visibility": "internal"},
        headers=headers,
        follow_redirects=False,
    )
    payload = {"version": "v1.0.0", "notes": ""}
    await client.post("/projects/ver-tool/releases/new", data=payload, headers=headers, follow_redirects=False)
    again = await client.post(
        "/projects/ver-tool/releases/new", data=payload, headers=headers, follow_redirects=False
    )
    assert again.status_code == 200
    assert "已存在" in again.text


# --- 🔴 權限 ----------------------------------------------------------------


async def _project_with_release(client, token, slug="perm-tool"):
    headers = {**BROWSER, **auth(token)}
    await client.post(
        "/projects/new",
        data={"name": slug, "summary": "", "visibility": "internal"},
        headers=headers,
        follow_redirects=False,
    )
    resp = await client.post(
        f"/projects/{slug}/releases/new",
        data={"version": "v1.0.0", "notes": ""},
        headers=headers,
        follow_redirects=False,
    )
    return resp.headers["location"].rstrip("/").split("/")[-2]


async def test_非成員不能開建版本頁(client, active_user, app, oidc):
    """internal 專案任何人可讀,但建版本要 maintainer。"""
    _, owner_token = active_user
    await _project_with_release(client, owner_token)

    await make_user(app, "sub-viewer44")
    viewer = oidc.issue("sub-viewer44")
    resp = await client.get(
        "/projects/perm-tool/releases/new", headers={**BROWSER, **auth(viewer)}
    )
    assert resp.status_code == 403


async def test_非成員不能開上傳頁(client, active_user, app, oidc):
    _, owner_token = active_user
    release_id = await _project_with_release(client, owner_token)

    await make_user(app, "sub-viewer44b")
    viewer = oidc.issue("sub-viewer44b")
    resp = await client.get(f"/releases/{release_id}/upload", headers={**BROWSER, **auth(viewer)})
    assert resp.status_code == 403


async def test_未登入開上傳頁會302(client, active_user):
    """沿用 T53 的深層頁轉址。"""
    _, token = active_user
    release_id = await _project_with_release(client, token)
    resp = await client.get(
        f"/releases/{release_id}/upload", headers=BROWSER, follow_redirects=False
    )
    assert resp.status_code == 302


# --- 上傳頁 -----------------------------------------------------------------


async def test_上傳頁有必要的元素(client, active_user):
    _, token = active_user
    release_id = await _project_with_release(client, token)

    body = (await client.get(f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)})).text
    assert 'type="file"' in body, "要有檔案選擇"
    assert "kind" in body, "要能選 source / binary / doc"
    assert "progress" in body.lower(), "要有進度顯示(F74 明列)"


async def test_已發布的版本不給上傳(client, active_user):
    _, token = active_user
    release_id = await _project_with_release(client, token)
    await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    await complete_kinds(client, token, release_id)  # T65:發布需三類齊備
    await client.post(
        f"/releases/{release_id}/publish",
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )

    body = (await client.get(f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)})).text
    assert "已發布" in body
    assert 'type="file"' not in body, "已發布的版本不該還給人選檔案"


# --- 🔴 JS 的三條紅線 -------------------------------------------------------


async def test_上傳頁沒有inline_script(client, active_user):
    """🔴 CSP `default-src 'self'` 會擋掉 inline script——有的話功能直接壞掉。"""
    _, token = active_user
    release_id = await _project_with_release(client, token)

    body = (await client.get(f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)})).text
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", body)
    assert not inline, f"頁面有 inline script(會被 CSP 擋掉):{inline}"
    assert re.search(r"<script[^>]*\bsrc=", body), "應以外部檔案載入 upload.js"


async def test_upload_js可取得(client):
    resp = await client.get("/static/upload.js")
    assert resp.status_code == 200, resp.text
    assert "javascript" in resp.headers["content-type"]


async def test_upload_js不碰瀏覽器儲存空間(client):
    """🔴 §4.10:同源之下 localStorage 全平台可讀。上傳靠 HttpOnly cookie,JS 不需要碰 token。

    T51 已經放了原始碼守門;本任務是第一次真的寫 JS,那條守門從今天起開始擋人。
    """
    body = (await client.get("/static/upload.js")).text
    assert "localStorage" not in body
    assert "sessionStorage" not in body


async def test_upload_js有進度事件與錯誤處理(client):
    """F74 明列:XHR PUT 並顯示上傳進度與失敗提示。"""
    body = (await client.get("/static/upload.js")).text
    assert "upload.onprogress" in body or "upload.addEventListener" in body
    assert "PUT" in body
    assert "encodeURIComponent" in body, "檔名要編碼才能放進網址"


# --- 🔴 CSRF 的前提 ---------------------------------------------------------


async def test_session_cookie為SameSite_Lax(client, app, oidc):
    """🔴 我們**不加 CSRF token**,結論完全建立在這個屬性上。

    `SameSite=Lax` 只在頂層 GET 導覽時送 cookie,跨站 `<form method="post">`
    不會帶上 → 被當成未登入 → 擋下。哪天有人改成 `None`,防護就沒了而且沒人會發現
    ——所以這個前提本身要有測試。
    """
    from starlette.responses import Response

    from app.session import SessionData

    probe = Response()
    app.state.cookies.set_session(probe, SessionData(access_token=oidc.issue("sub-csrf")))
    header = next(v.decode() for k, v in probe.raw_headers if k.decode().lower() == "set-cookie")
    assert "samesite=lax" in header.lower(), "CSRF 的結論建立在 SameSite=Lax 之上"
    assert "HttpOnly" in header


# --- 連結與逸出 -------------------------------------------------------------


async def test_新頁面的連結帶前綴且無絕對網址(client, active_user):
    _, token = active_user
    release_id = await _project_with_release(client, token)
    headers = {**BROWSER, **auth(token)}

    for path in ("/projects/new", "/projects/perm-tool/releases/new", f"/releases/{release_id}/upload"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200, path
        for link in _links(resp.text):
            if link in PLATFORM_URLS:
                continue
            assert link.startswith(f"{PREFIX}/"), f"{path}:{link!r}"
            assert not link.startswith(("http://", "https://", "//")), f"{path}:{link!r}"


async def test_表單回填的值有逸出(client, active_user):
    """使用者填過的值要留著,但留著不等於原樣輸出。

    ⚠ T96 之後**觸發錯誤的方式改了**:原本靠一個非法的短名(`"BAD SLUG"`),
    但表單已經沒有短名欄位了 —— 那條路徑不再存在。改用空白名稱觸發同一條錯誤路徑,
    驗的東西一字未減:回填的值必須逸出。
    """
    _, token = active_user
    resp = await client.post(
        "/projects/new",
        data={"name": "", "summary": "<script>alert(1)</script>", "visibility": "internal"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
