"""T90:建立版本頁顯示這個專案的歷史版本號。

Benny 實測回報(截圖):版本號欄位只有 placeholder `v1.0.0` 與「不強制 SemVer」,
**要填的人手上沒有前一版是什麼**。

🔴 這不只是方便:版本號有 UNIQUE 約束,撞號要送出之後才看得到錯誤,而正確值
就是「上一版的下一個」——那個資訊本來就該在輸入框旁邊,不該逼人離開這一頁去查。

本檔釘住的界線:
1. 草稿必須標示——不標的話,看到版本號已存在卻找不到它發布在哪。
2. **送出失敗重新渲染時清單仍在**:填錯的那一次最需要它。
3. 🔴 只列本專案的版本;別的專案的版本號不得出現(存在性不得外洩)。
"""

import re

from tests.conftest import auth

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def _project(client, token, slug, name=None):
    # T96:表單不再有短名欄位——短名由**名稱**產生,所以這裡把名稱設成想要的短名。
    await client.post(
        "/projects/new",
        data={"name": name or slug, "summary": "", "visibility": "internal"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )


async def _release(client, token, slug, version):
    resp = await client.post(
        f"/projects/{slug}/releases/new",
        data={"version": version, "notes": ""},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


async def _form(client, token, slug):
    resp = await client.get(f"/projects/{slug}/releases/new", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    return resp.text


def _hint(html: str) -> str:
    """只取歷史版本區塊。

    🔴 不整頁搜尋:版本號欄位的 placeholder 就是 `v1.0.0`,整頁搜尋會讓
    「什麼都沒做」也能通過——那種綠燈比紅燈危險。
    """
    match = re.search(r'<div class="version-hint">(.*?)</div>', html, re.DOTALL)
    return match.group(1) if match else ""


async def test_建立版本頁列出已有的版本號(client, active_user):
    _, token = active_user
    await _project(client, token, "hist-tool")
    await _release(client, token, "hist-tool", "v2.3.4")
    await _release(client, token, "hist-tool", "v2.4.0")

    hint = _hint(await _form(client, token, "hist-tool"))
    assert "v2.3.4" in hint
    assert "v2.4.0" in hint


async def test_草稿版本標示為草稿(client, active_user):
    """未發布的版本也要列——但要看得出它還沒發布,否則會以為「已存在卻找不到」。"""
    _, token = active_user
    await _project(client, token, "draft-tool")
    await _release(client, token, "draft-tool", "v0.9.0")

    hint = _hint(await _form(client, token, "draft-tool"))
    assert "v0.9.0" in hint
    assert "草稿" in hint


async def test_還沒有版本時說這是第一個版本(client, active_user):
    """不留空白區塊——空白會讓人以為清單壞了。"""
    _, token = active_user
    await _project(client, token, "first-tool")

    hint = _hint(await _form(client, token, "first-tool"))
    assert "第一個版本" in hint


async def test_送出失敗重新渲染時清單仍在(client, active_user):
    """🔴 填錯的那一次最需要看到已有哪些版本號。"""
    _, token = active_user
    await _project(client, token, "retry-tool")
    await _release(client, token, "retry-tool", "v5.0.0")

    resp = await client.post(
        "/projects/retry-tool/releases/new",
        data={"version": "", "notes": ""},  # 空版本號 → 回到表單
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.text
    assert "v5.0.0" in _hint(resp.text), "重新渲染的表單應仍列出既有版本號"


async def test_不列出別的專案的版本號(client, active_user):
    """🔴 版本號是專案內的事;跨專案出現等於洩漏別人專案的內容。"""
    _, token = active_user
    await _project(client, token, "mine-tool")
    await _project(client, token, "other-tool")
    await _release(client, token, "other-tool", "v7.7.7")

    body = await _form(client, token, "mine-tool")
    assert "v7.7.7" not in body
