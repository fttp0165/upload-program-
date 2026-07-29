"""T53 深層頁未登入導向 IdP(SSO 契約 §7 冒煙第 1 項)。

裁示(2026-07-28 Benny):**深層頁 302、首頁留落地頁**。

- 深層頁(`/projects/*`)未登入 → 302 到 `/auth/login`,帶 `next` 導回原頁
- 首頁(`/`)保留「請先登入」的落地說明頁——它承擔「這是什麼系統、找誰開通」的說明功能

🔴 302 **不得因此洩漏專案是否存在**:T42 好不容易做到的結構保證
(匿名一律不查詢)不能因為改成轉址就破功。存在的 private 專案與不存在的 slug
必須導到**一模一樣**的地方。
"""

from urllib.parse import parse_qs, urlparse

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"


async def _project(client, token, slug="redir-tool", **extra):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "轉址測試", **extra}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text


# --- 深層頁 302 -------------------------------------------------------------


async def test_未登入開專案頁會302到登入(client, active_user):
    _, token = active_user
    await _project(client, token)

    resp = await client.get("/projects/redir-tool", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 302, resp.text
    assert urlparse(resp.headers["location"]).path == f"{PREFIX}/auth/login"


async def test_未登入開歷史頁會302到登入(client, active_user):
    _, token = active_user
    await _project(client, token)

    resp = await client.get(
        "/projects/redir-tool/releases", headers=BROWSER, follow_redirects=False
    )
    assert resp.status_code == 302
    assert urlparse(resp.headers["location"]).path == f"{PREFIX}/auth/login"


async def test_302帶next導回原頁(client, active_user):
    """登入完把人送回他本來要去的地方,不是丟回首頁。"""
    _, token = active_user
    await _project(client, token)

    resp = await client.get(
        "/projects/redir-tool/releases", headers=BROWSER, follow_redirects=False
    )
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query.get("next") == ["/projects/redir-tool/releases"], query


# --- 首頁保留落地頁 ---------------------------------------------------------


async def test_首頁不轉址而是顯示落地頁(client):
    """裁示:首頁承擔「這是什麼系統、找誰開通」的說明功能,不直接彈到 IdP。"""
    resp = await client.get("/", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 200
    assert "登入" in resp.text


# --- 🔴 轉址不得洩漏專案是否存在 --------------------------------------------


async def test_302不洩漏專案是否存在(client, active_user):
    """🔴 T42 的結構保證不能因為改成轉址就破功。

    若存在的專案 302、不存在的專案 404,那**轉址與否本身就是答案**。
    """
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-redir", visibility="private")

    exists = await client.get(
        "/projects/secret-redir", headers=BROWSER, follow_redirects=False
    )
    missing = await client.get(
        "/projects/no-such-thing-at-all", headers=BROWSER, follow_redirects=False
    )

    assert exists.status_code == missing.status_code == 302
    # 兩者導向同一個端點,只有 next 不同(next 就是使用者自己打的網址,不算洩漏)
    assert urlparse(exists.headers["location"]).path == urlparse(
        missing.headers["location"]
    ).path


# --- 待開通者不轉址(轉了也沒用) -------------------------------------------


async def test_待開通者看到指引而不是被轉走(client, app, oidc):
    """待開通的人已經登入了,再送他去 IdP 只會轉一圈回來——要給的是指引。"""
    from app.models import UserStatus
    from app.problems import pending_activation

    await make_user(app, "sub-pending53", status=UserStatus.pending)
    token = oidc.issue("sub-pending53")

    resp = await client.get(
        "/projects/anything", headers={**BROWSER, **auth(token)}, follow_redirects=False
    )
    assert resp.status_code == 200, "已登入者不該被轉去登入"
    assert pending_activation().detail in resp.text


# --- API 不受影響 -----------------------------------------------------------


async def test_API未認證仍回401而非302(client):
    """🔴 API 表面的行為不因網頁的轉址而改變——呼叫端要的是 401,不是一頁 HTML。"""
    resp = await client.get("/v1/projects", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_next只接受站內路徑(client, active_user):
    """🔴 開放轉址防護:`next` 來自使用者的網址,不能讓它把人導去外站。

    `_safe_next()` 已在 auth.py 擋掉;這裡確認網頁產生的 next 一定是站內相對路徑。
    """
    _, token = active_user
    await _project(client, token)

    resp = await client.get("/projects/redir-tool", headers=BROWSER, follow_redirects=False)
    next_value = parse_qs(urlparse(resp.headers["location"]).query)["next"][0]
    assert next_value.startswith("/")
    assert not next_value.startswith("//")
