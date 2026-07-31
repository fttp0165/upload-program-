"""T69:服務顯示名稱「AI 小程式分享平台」。

🔴 **單一來源** `app.branding.SITE_NAME`:這個字串原本散在五個地方
(導航列品牌、側欄副標、首頁大標、教學頁、OpenAPI summary),
改名要五處同步——與 T68 那個停在 `0.1.0` 五個版本的寫死版本號是同一類問題。

🔴 技術識別名 `upload-program` **不隨之改動**:它是平台登記的服務名、gateway
路徑、image 名與 log 的 `service` 欄位。行銷名稱與技術識別是兩件事。
"""

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
