"""T40 導航列與版型骨架(F70)。

🔴 本檔的核心是**子路徑正確性**。拓撲:gateway 以尾斜線 proxy_pass 剝掉前綴後轉發,
所以本服務收到 `/static/app.css`,但瀏覽器看到的是 `https://host/upload/static/app.css`。
**頁面裡的連結是給瀏覽器用的,必須帶前綴**;路由註冊則用不帶前綴的路徑。
兩者搞混就是 PLM 出過的 404 事故(決策文件 §6.2)。

所以第 3、4 條規格是「**解析 HTML 抓出所有連結逐一檢查**」而不是抽驗幾個字串——
漏掉一個就是一次事故。
"""

import re

from tests.conftest import auth

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"  # conftest 的 settings 夾具設的 api_prefix

# 抓出 href="…" / src="…" / action="…" 的值
_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

# 契約 §2.1 的平台層短網址:由 gateway 302 轉址,**刻意不帶各 App 的前綴**
# (加上前綴會變成一條不存在的路徑)。這是下面「所有連結帶前綴」那條紅線的
# **具名例外**——不是把斷言放寬,例外本身在 test_sso_contract.py 有測試保護。
PLATFORM_URLS = {"/account", "/login"}



def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


# --- 首頁與導航列 -----------------------------------------------------------


async def test_首頁回HTML且含站名(client):
    resp = await client.get("/", headers=BROWSER)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    assert "upload-program" in resp.text


async def test_導航列含搜尋框與登入入口(client):
    resp = await client.get("/", headers=BROWSER)
    body = resp.text
    assert "<nav" in body
    assert 'name="q"' in body, "導航列要有搜尋框(F70)"
    assert "登入" in body


async def test_匿名訪客開首頁得到200而不是401(client):
    """網頁跟 API 不同:匿名訪客該看到登入按鈕,不是一頁錯誤。"""
    resp = await client.get("/", headers=BROWSER)
    assert resp.status_code == 200
    assert "登出" not in resp.text


async def test_已登入者的導航列顯示名字與登出(client, app, oidc):
    from app.models import UserStatus
    from tests.conftest import make_user

    await make_user(app, "sub-navuser", status=UserStatus.active)
    token = oidc.issue("sub-navuser", name="王小明")

    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "王小明" in resp.text
    assert "登出" in resp.text


async def test_顯示名稱來自IdP而非業務庫(client, app, oidc):
    """🔴 業務庫只存 sub。同一個帳號換一組 claims,畫面就該跟著變——

    如果名字是從業務庫讀的,第二次請求會顯示第一次的舊值(或根本沒有值)。
    """
    from tests.conftest import make_user

    await make_user(app, "sub-renamed")

    first = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-renamed", name="舊名字"))}
    )
    second = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-renamed", name="新名字"))}
    )
    assert "舊名字" in first.text
    assert "新名字" in second.text
    assert "舊名字" not in second.text


# --- 🔴 子路徑正確性 --------------------------------------------------------


async def test_首頁所有連結都帶路徑前綴(client):
    """🔴 一個都不能漏——漏掉一個就是一次 404 事故。"""
    resp = await client.get("/", headers=BROWSER)
    found = [link for link in _links(resp.text) if link not in PLATFORM_URLS]
    assert found, "頁面應該要有連結可檢查"
    for link in found:
        assert link.startswith(f"{PREFIX}/"), f"連結未帶前綴 {PREFIX}:{link!r}"


async def test_錯誤頁所有連結都帶路徑前綴(client):
    resp = await client.get("/no-such-page", headers=BROWSER)
    assert resp.status_code == 404
    found = [link for link in _links(resp.text) if link not in PLATFORM_URLS]
    assert found, "錯誤頁繼承版型後應該要有導航列的連結"
    for link in found:
        assert link.startswith(f"{PREFIX}/"), f"連結未帶前綴 {PREFIX}:{link!r}"


async def test_頁面內不得出現絕對網址(client):
    """🔴 TLS 在 gateway 終結,從 request 推導 scheme 會得到 http://。

    root-relative 連結讓瀏覽器沿用目前的 scheme 與 host,直接繞開這個問題。
    """
    for path in ("/", "/no-such-page"):
        resp = await client.get(path, headers=BROWSER)
        for link in _links(resp.text):
            assert not link.startswith(("http://", "https://", "//")), f"{path}:{link!r}"
        assert "http://" not in resp.text and "https://" not in resp.text


async def test_web_url在無前綴時不產生雙斜線():
    """`api_prefix` 為空(掛在網域根)時,`//static/...` 會被瀏覽器當成別的網域。"""
    from app.web_urls import web_url

    class _S:
        api_prefix = ""

    class _T:
        api_prefix = "/upload"

    assert web_url(_S(), "/static/app.css") == "/static/app.css"
    assert web_url(_S(), "/") == "/"
    assert web_url(_T(), "/static/app.css") == "/upload/static/app.css"
    assert web_url(_T(), "/") == "/upload/"


# --- 靜態檔 -----------------------------------------------------------------


async def test_靜態樣式表可取得(client):
    """路由註冊用**不帶前綴**的路徑——gateway 已經把前綴剝掉了。"""
    resp = await client.get("/static/app.css")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/css")


async def test_靜態檔路徑不得逃出static目錄(client):
    """🔴 靜態檔改用一般路由後(見 web.py 的說明),路徑安全變成我們自己的責任。

    仍然委派給 `StaticFiles.get_response()`——它的 `lookup_path()` 會 realpath 後
    檢查 commonpath,擋掉 `../`。這種安全檢查不該自己重寫,但要有測試證明它真的接上了。
    """
    for attack in (
        "/static/../config.py",
        "/static/../../etc/passwd",
        "/static/..%2f..%2fetc%2fpasswd",
    ):
        resp = await client.get(attack)
        assert resp.status_code in (404, 400), f"{attack} → {resp.status_code}"
        assert "SECRET" not in resp.text and "root:" not in resp.text


async def test_樣式表為light且無外部資源(client):
    """憲法第四條 1–2:一律 light,禁 dark,不用 prefers-color-scheme 自動切深色。"""
    resp = await client.get("/static/app.css")
    body = resp.text
    assert "prefers-color-scheme" not in body
    assert "http://" not in body and "https://" not in body


# --- 錯誤頁繼承版型 ---------------------------------------------------------


async def test_錯誤頁繼承共用版型(client):
    """T47 遺留 #1、#2:錯誤頁要有導航列(才有回首頁的路),CSS 收斂到 static。"""
    resp = await client.get("/no-such-page", headers=BROWSER)
    assert resp.status_code == 404
    body = resp.text
    assert "<nav" in body, "錯誤頁應繼承 base.html 的導航列"
    assert "<style" not in body, "CSS 應收斂到 static/app.css,不再內嵌"
    assert "404" in body


async def test_錯誤頁仍對使用者可控內容逸出(client):
    """🔴 換了版型不代表逸出可以鬆掉——T47 的紅線在 T40 之後必須依然成立。"""
    resp = await client.get("/%3Cscript%3Ealert(1)%3C/script%3E", headers=BROWSER)
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


# --- CSP --------------------------------------------------------------------


async def test_回應帶CSP(client):
    """本服務散布可執行檔;全站零 JS 的現在是導入嚴格 CSP 成本最低的時刻。"""
    resp = await client.get("/", headers=BROWSER)
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


async def test_API回應也帶CSP(client):
    resp = await client.get("/health")
    assert "default-src 'self'" in resp.headers.get("content-security-policy", "")
