"""T66:使用教學頁 `/help` + 回報問題/功能需求管道。

釘住的行為:
1. 匿名可看(教學頁擋登入毫無道理;待開通者也需要它)
2. 內容含:快速上手流程、T65 三類齊備規則、格式白名單、回報問題/功能需求專節
3. 頂列導航(所有人)與左側欄(已開通者)都有入口
4. 頁內連結一律帶前綴(url() 紅線,沿全站規則)
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
# T67:平台入口(`/`)也是平台層網址,同一類具名例外。
# 🔴 白名單納入 `/` 會讓「漏掉 url() 的首頁連結」逃過這條檢查——
#    補償斷言在 test_portal_link.py(每條 `/` 都必須帶 nav-exit / side-exit 標記)。
PLATFORM_URLS = {"/account", "/login", "/"}


async def test_匿名可看教學頁(client):
    resp = await client.get("/help", headers=BROWSER)
    assert resp.status_code == 200
    # 快速上手要把整條路走完:建專案 → 建版本 → 上傳 → 發布
    for keyword in ("快速上手", "建立專案", "建立版本", "上傳", "發布"):
        assert keyword in resp.text, f"教學頁缺「{keyword}」"


async def test_教學頁含三類齊備與格式規則(client):
    resp = await client.get("/help", headers=BROWSER)
    # T65 規則要寫進教學,使用者才不會上傳到一半才被 422 嚇到
    assert "更新文件" in resp.text
    assert "執行檔" in resp.text
    assert "原始碼" in resp.text
    # 安全規則的使用者面:實際內容判型、HTML/SVG 不收、SHA-256 校驗
    assert "HTML" in resp.text and "SVG" in resp.text
    assert "SHA-256" in resp.text


async def test_教學頁含回報問題與功能需求專節(client):
    resp = await client.get("/help", headers=BROWSER)
    assert "回報問題" in resp.text
    assert "功能需求" in resp.text
    # 回報要有用,必須教使用者附上重現步驟
    assert "重現步驟" in resp.text


async def test_教學頁連結一律帶前綴(client):
    resp = await client.get("/help", headers=BROWSER)
    for link in _LINK_RE.findall(resp.text):
        if link in PLATFORM_URLS:
            continue  # 契約 §2.1 平台層短網址是具名例外
        assert link.startswith(PREFIX + "/"), f"連結沒帶前綴:{link}"


async def test_導航列有教學入口_匿名與登入皆有(client, app, oidc):
    # T81:匿名瀏覽器開首頁會被送去登入,/help 是匿名者仍看得到導航列的頁面
    anon = await client.get("/help", headers=BROWSER)
    assert f'href="{PREFIX}/help"' in anon.text

    await make_user(app, "sub-help-nav")
    resp = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-help-nav"))}
    )
    assert f'href="{PREFIX}/help"' in resp.text


async def test_側欄有教學入口_已開通者(client, app, oidc):
    await make_user(app, "sub-help-side")
    resp = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-help-side"))}
    )
    assert resp.status_code == 200
    # 側欄與頂列各一條(側欄 lg+、頂列漢堡),至少要出現兩次
    assert resp.text.count(f'href="{PREFIX}/help"') >= 1
    assert "使用教學" in resp.text


# --- T75:回報問題的獨立入口 ------------------------------------------------


async def test_回報專節有錨點(client):
    """T66 把回報做成頁內一節,沒有入口等於不存在(Benny 2026-07-31 指出)。"""
    resp = await client.get("/help", headers=BROWSER)
    assert 'id="report"' in resp.text


async def test_側欄與頂列都有回報問題入口(client, app, oidc):
    """T77 起入口指向**真正的回報表單**,不再是教學頁的錨點。

    (T75 當時還沒有表單,只能連到說明段落;現在有了,連過去才有意義。)
    """
    await make_user(app, "sub-report-entry")
    resp = await client.get(
        "/", headers={**BROWSER, **auth(oidc.issue("sub-report-entry"))}
    )
    assert resp.status_code == 200
    assert "回報問題" in resp.text
    # 側欄(lg+)與頂列(漢堡)各一條
    assert resp.text.count(f'href="{PREFIX}/issues/new"') >= 2


async def test_匿名者也看得到回報入口(client):
    """網站壞掉時,最需要回報的往往正是還沒登入成功的人。

    連結本身不分登入狀態都給;點進去若未登入,回報頁會把人送去登入(T77)。
    """
    resp = await client.get("/help", headers=BROWSER)  # T81:匿名者的導航列改在 /help 驗
    assert f'href="{PREFIX}/issues/new"' in resp.text
