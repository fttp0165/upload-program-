"""SSO 契約 v1.7 的義務逐條測試化(T51)。

本檔的用途不是「證明現在有做」,而是**讓這些義務不會被日後的變更無意間破壞**。
尤其 §4.10 的「不得把 token 存進 localStorage」:現在全站零 JS,行為上測不出來,
所以改用**原始碼層級的守門**——T44 導入外部 JS 之後,這條就會真的擋人。

🔴 §4.10 的背景:MIS 只核可單一 hostname,IdP 與各 App **共用 origin**,
瀏覽器的同源政策不再提供隔離。我們一旦出現 XSS,等同全平台帳號淪陷。
"""

import pathlib
import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# 契約 §2.1 的平台層短網址:由 gateway 302 轉址,**刻意不帶各 App 的前綴**。
# 這是「所有連結都要帶前綴」那條紅線的具名例外,例外本身也有測試(見下方)。
PLATFORM_URLS = {"/account", "/login"}


# --- §2 端點形式 ------------------------------------------------------------


def test_env_example的issuer為D2子路徑形式():
    """🔴 D2″ 之後 IdP 掛在 `catsapp.sporton.com.tw/auth/...`,不是獨立網域。

    照舊的 `auth.sporton.com.tw` 部署會直接連不上 IdP,而且要到登入當下才發現。
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    issuer = re.search(r"^OIDC_ISSUER=(\S+)", text, flags=re.MULTILINE)
    assert issuer, ".env.example 應該要有 OIDC_ISSUER 的示意值"
    value = issuer.group(1)
    assert value == "https://catsapp.sporton.com.tw/auth/realms/sporton", value


# --- §2.1 / §4.8 帳號設定連結 -----------------------------------------------


async def test_導航列有帳號設定連結(client):
    """§4.8:App 禁自建註冊/改密碼頁,只需在 UI 放連結指向 Account Console。"""
    resp = await client.get("/", headers=BROWSER)
    assert "帳號設定" in resp.text


async def test_帳號設定連結不帶前綴(client):
    """🔴 這是「所有連結帶前綴」那條紅線的**具名例外**,所以它自己也要有測試。

    `/account` 是 gateway 層的 302 轉址(契約 §2.1);加上我們的前綴會變成
    `/upload/account`——一條不存在的路徑。
    """
    resp = await client.get("/", headers=BROWSER)
    assert 'href="/account"' in resp.text, "帳號設定應正好指向 /account"
    assert f'href="{PREFIX}/account"' not in resp.text, "平台短網址不得加上本服務的前綴"


# --- §4.3 / §4.8 待開通文案 -------------------------------------------------


def test_待開通文案能指引使用者找對人():
    """§4.3:文案須指引「找 <你的 App> 管理員開通」,不是冷冰冰的 Forbidden。

    §4.8(v1.6 自助註冊開放後)特別提醒:各 App 會開始看到陌生 sub 首登,
    請確認待開通頁文案能指引使用者找對人。
    """
    from app.problems import pending_activation

    detail = pending_activation().detail
    assert "管理員" in detail, "要說清楚去找誰"
    assert "upload-program" in detail, "要說清楚是哪個系統的管理員"


# --- 🔴 §4.10 同源環境的額外義務(逐條) ------------------------------------


async def test_同源義務_session_cookie屬性(client, app, oidc):
    """§4.10:`Path=/<子路徑>/` + HttpOnly + SameSite=Lax。

    Path 綁自己的前綴,避免與同主機其他 App 的 cookie 互蓋——同源之下這是必要的隔離。
    """
    from starlette.responses import Response

    from app.session import SessionData

    await make_user(app, "sub-cookie51")
    probe = Response()
    app.state.cookies.set_session(probe, SessionData(access_token=oidc.issue("sub-cookie51")))
    header = next(
        v.decode() for k, v in probe.raw_headers if k.decode().lower() == "set-cookie"
    )
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    assert f"Path={PREFIX}/" in header


async def test_同源義務_CSP禁inline(client):
    """§4.10:至少 `default-src 'self'`,**禁 unsafe-inline 的 inline script**。"""
    csp = (await client.get("/", headers=BROWSER)).headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp, "🔴 同源之下 unsafe-inline 等於把 XSS 的門打開"
    assert "unsafe-eval" not in csp


async def test_同源義務_前端base_path為子路徑(client, active_user):
    """§4.10:前端 base path 必須是自己的子路徑,否則靜態資源會打到別人的路徑上。

    平台短網址(§2.1)是具名例外,其餘一律要帶前綴。
    """
    _, token = active_user
    resp = await client.get("/", headers={**BROWSER, **auth(token)})
    links = re.findall(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", resp.text)
    assert links
    for link in links:
        if link in PLATFORM_URLS:
            continue
        assert link.startswith(f"{PREFIX}/"), f"連結未帶前綴:{link!r}"


def test_同源義務_模板不得使用safe過濾器():
    """§4.10:使用者輸入的輸出一律消毒。`|safe` 會直接關掉 autoescape。

    這是原始碼層級的守門——一旦有人為了「讓某段 HTML 生效」而加上 `|safe`,
    就會在這裡當場紅燈,而不是等到有人利用它。
    """
    # 只比對**真的會關掉逸出**的位置:輸出運算式 `{{ x|safe }}` 與 `{% filter safe %}`。
    # 註解裡寫「禁用 |safe」是無害的,不該被當成違規——這條測試要測的是語意,不是字串。
    dangerous = re.compile(r"\{\{[^}]*\|\s*safe\b|\{%\s*filter\s+safe\b")
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "app" / "templates").rglob("*.html")
        if dangerous.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"模板在輸出運算式中使用了 |safe:{offenders}"


def test_同源義務_不得把token存進localStorage():
    """🔴 §4.10:同源之下 localStorage 全平台可讀,token 進去等於公開。

    現在全站零 JS,所以這條**行為上測不出來**——改用原始碼掃描。
    T44 要導入外部 JS 檔(XHR 上傳),屆時這條會真的擋人,那正是它存在的理由。
    """
    scanned = [
        path
        for folder in ("static", "templates")
        for path in (ROOT / "app" / folder).rglob("*")
        if path.is_file() and path.suffix in {".js", ".html", ".css"}
    ]
    assert scanned, "應該要有檔案可掃(掃不到就是這條測試失效了)"

    offenders = [
        path.relative_to(ROOT)
        for path in scanned
        if re.search(r"\b(localStorage|sessionStorage)\b", path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"🔴 不得把 token 存進瀏覽器儲存空間:{offenders}"


# --- §7 冒煙第 6 項:repo 紅線 ----------------------------------------------


def test_repo無Authentik與HS256():
    """契約 §7 第 6 項。CI 已有此檢查,這裡再釘一次以便本機就抓得到。"""
    suspicious = []
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / ".env.example"]:
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bauthentik\b", text, flags=re.IGNORECASE):
            suspicious.append((path.relative_to(ROOT), "Authentik"))
        # 註釋裡說明「禁 HS256」是允許的,只擋把它當成可接受演算法的寫法
        if re.search(r"""["']HS256["']""", text):
            suspicious.append((path.relative_to(ROOT), "HS256 字串"))
    assert not suspicious, suspicious
