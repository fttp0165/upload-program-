"""T69:服務顯示名稱「AI 小程式分享平台」。

🔴 **單一來源** `app.branding.SITE_NAME`:這個字串原本散在五個地方
(導航列品牌、側欄副標、首頁大標、教學頁、OpenAPI summary),
改名要五處同步——與 T68 那個停在 `0.1.0` 五個版本的寫死版本號是同一類問題。

🔴 技術識別名 `upload-program` **不隨之改動**:它是平台登記的服務名、gateway
路徑、image 名與 log 的 `service` 欄位。行銷名稱與技術識別是兩件事。
"""

import hashlib
import re
from pathlib import Path

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
_TEMPLATES = Path(__file__).parent.parent / "app" / "templates"


async def test_首頁顯示新名稱(client):
    from app.branding import SITE_NAME

    resp = await client.get("/", headers=BROWSER)
    assert resp.status_code == 200
    assert SITE_NAME == "AI 小程式分享平台"
    assert SITE_NAME in resp.text


async def test_導航列與側欄顯示新名稱(client, app, oidc):
    from app.branding import SITE_NAME

    await make_user(app, "sub-brand-user")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-brand-user"))})
    # 品牌(頂列)+ 側欄副標,兩處都應是同一個名字
    assert resp.text.count(SITE_NAME) >= 2


async def test_教學頁顯示新名稱(client):
    from app.branding import SITE_NAME

    resp = await client.get("/help", headers=BROWSER)
    assert SITE_NAME in resp.text


def test_模板不得再硬編碼站名():
    """🔴 名稱只准有一份。模板裡再出現裸字串,下次改名又會漏掉其中幾處。"""
    offenders = [
        path.name
        for path in _TEMPLATES.glob("*.html")
        if "程式分享平台" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"這些模板硬編碼了站名,應改用 site_name:{offenders}"


async def test_技術識別名不變(client):
    """🔴 `upload-program` 是平台登記的服務名與 gateway 路徑,不隨行銷名稱改。"""
    resp = await client.get("/openapi.json")
    assert resp.json()["info"]["title"] == "upload-program"

    page = await client.get("/", headers=BROWSER)
    assert "upload-program" in page.text  # 分頁標題 / 頁尾版本行


# ── T80:品牌標誌改用入口網站官方 logo ──────────────────────────────
#
# 原本導航列的標記是 app.css 的 `.nav-brand::before` 偽元素(藏青圓 +
# box-shadow 疊出綠圓)。形狀與比例對,但它是**近似**——官方標誌的兩個
# 橢圓之間有交錯的白色鋸齒,還帶 SPORTON LAB. 字樣,偽元素畫不出來。
# 使用者從 portal 首頁點進來,看到的是一個「很像但不一樣」的標記。
#
# 🔴 因此第一條斷言比對的是 **SHA-256**,不是「有一張圖」——
#    「看起來很像」正是現在這個問題本身,用眼睛驗收會把它驗過。
#
# (本節用到的 hashlib / re 已併入檔頭的 import —— ruff E402 不允許
#  module level import 出現在檔案中段,分節寫在這裡會被 CI 擋下。)

_STATIC = Path(__file__).parent.parent / "app" / "static"
_LOGO = _STATIC / "logo.png"

# 入口網站官方標誌的 SHA-256(cats-portal/landing/assets/sporton-logo.png,
# 同時也是 portal landing 與 Keycloak 登入頁所用的那一支)。
_PORTAL_LOGO_SHA256 = "aadf4191d59b4a8ec1a044b2949ca0dd892a19770661cf38153cd51d43561a3f"


def test_logo檔案與入口網站官方檔一致():
    """🔴 位元組相同才算「換成入口網站的 logo」,像不像不是標準。"""
    assert _LOGO.exists(), "app/static/logo.png 不存在"
    digest = hashlib.sha256(_LOGO.read_bytes()).hexdigest()
    assert digest == _PORTAL_LOGO_SHA256, (
        f"logo.png 與入口網站官方檔不符(實得 {digest})——"
        "請自 cats-portal/landing/assets/sporton-logo.png 複製,不要另存或轉檔"
    )


def test_導航列品牌使用官方logo圖檔():
    """品牌區要有真圖,而且路徑必須經過 url()(帶 /upload 前綴)。"""
    base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "url('/static/logo.png')" in base, (
        "base.html 的品牌 logo 未用 url() 包住 —— gateway 剝前綴後轉發,"
        "裸路徑在瀏覽器端會 404(決策文件 §6.2,PLM 出過這個事故)"
    )


def test_有favicon():
    """分頁圖示也是品牌識別的一部分,不能留瀏覽器預設空白。"""
    base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in base, "base.html 沒有 favicon <link>"


def test_偽元素標記已移除():
    """🔴 真圖進來後,近似標記必須撤掉。

    留著的話兩個標記會並排——Keycloak login theme 那次就是兩層 header 各畫
    一個 logo,本機預覽看不出來(預覽 DOM 只有一層),上線才發現。

    🔴 先剝掉 CSS 註解再檢查:註解裡提到 `.nav-brand::before` 是在交代
    「這裡以前是什麼、為什麼換掉」,它不會畫出任何東西。把註解也算進去的話,
    這條斷言會逼人刪掉那段歷史,而那段歷史正是防止有人改回去的東西。
    """
    css = (_STATIC / "app.css").read_text(encoding="utf-8")
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert ".nav-brand::before" not in css_no_comments, (
        "app.css 仍有 .nav-brand::before 偽元素規則,會與官方 logo 並排重複"
    )


async def test_logo可被下載(client):
    resp = await client.get("/static/logo.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
