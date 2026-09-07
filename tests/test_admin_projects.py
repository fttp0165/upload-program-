"""T134:後台專案管理與重複建立的清除。

Benny:「要建立後台管理機制,重複建立的案件管理員可以刪除」,確認方式裁示 **A 案**
(刪除前要求輸入該專案短名)。

🔴 盤點結果決定了任務形狀:刪除 API、admin 放行、稽核動作**早就都有** ——
缺的是介面與「哪些算重複」的判定。從零做一套刪除路徑會做出第二套規則,
而兩套刪除規則遲早分岔。

本檔最重要的是**兩條反向測試**:`Tool` / `Tool 2` 不得被判成重複、
兩個不同的純中文名稱不得被判成重複。少了它們,這個功能會變成
「把不相干的專案排在一起,然後請管理員刪掉其中一個」。
"""

import re

from sqlalchemy import select

from app.models import AuditEvent
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
EXE = b"MZ\x90\x00" + b"\x00" * 60


def _dup_sections(html: str) -> str:
    """只取「疑似重複」那些分組區段的 HTML。

    🔴 **不用文字切片定位**(例 `html.split("疑似重複")[1]`)——第一版就是這樣寫的,
    而「疑似重複」在頁面上出現**兩次**(說明文字 + 標題),於是切到的是那兩次之間的
    201 個字,斷言等於在檢查一段空白:**永遠綠**。
    改以結構定位(`<section class="dup-group">`),它不隨文案改動而失效。
    """
    return "\n".join(re.findall(r'<section class="dup-group">.*?</section>', html, re.S))


async def _admin(app, oidc, sub="ap-admin"):
    await make_user(app, sub, admin=True)
    return oidc.issue(sub)


async def _proj(client, token, name, slug=None):
    payload = {"name": name, "summary": "x", "visibility": "internal"}
    if slug:
        payload["slug"] = slug
    resp = await client.post(f"{PREFIX}/v1/projects", json=payload, headers=auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


async def _page(client, token):
    resp = await client.get(f"{PREFIX}/admin/projects", headers={**BROWSER, **auth(token)})
    return resp


# --- 列表與重複分組 ---------------------------------------------------------


async def test_後台列出全部專案並把疑似重複置頂(client, app, oidc):
    await make_user(app, "ap-owner")
    owner = oidc.issue("ap-owner")
    first = await _proj(client, owner, "Key_word_serach_V88")
    second = await _proj(client, owner, "Key_word_serach_V88")  # 同名 → T96 自動加後綴
    assert first != second, "前提:同名第二次建立會自動換短名"

    resp = await _page(client, await _admin(app, oidc))
    assert resp.status_code == 200, resp.text
    body = resp.text
    dup = _dup_sections(body)
    assert dup, "同名的兩個專案沒有被分到「疑似重複」"
    for slug in (first, second):
        assert slug in dup, f"{slug} 不在疑似重複區段裡"
    # 最早建立的那一個要標出來 —— 否則管理員不知道哪個是原始的。
    assert "最早建立" in dup


async def test_Tool與Tool2不得被判成重複(client, app, oidc):
    """🔴 反向:`Tool 2` 是**使用者自己取的名字**,不是系統加的後綴。

    這條擋的是「把 slug 的 -N 去掉」那種直覺判準 —— 它會把刻意的第二代
    當成誤建的重複,而刪除是不可逆的。
    """
    await make_user(app, "ap-owner2")
    owner = oidc.issue("ap-owner2")
    await _proj(client, owner, "Tool")
    await _proj(client, owner, "Tool 2")

    dup = _dup_sections((await _page(client, await _admin(app, oidc, "ap-admin2"))).text)
    assert "tool-2" not in dup, "Tool 與 Tool 2 是不同的專案,不該被排在同一組"


async def test_兩個不同的中文名稱不得被判成重複(client, app, oidc):
    """🔴 反向:純中文名稱的 `slugify()` 一律回空字串(T96 已載明)。

    若拿空字串當分組鍵,**全站所有中文名稱的專案會被歸成一大組** ——
    那不是提示,是災難。
    """
    await make_user(app, "ap-owner3")
    owner = oidc.issue("ap-owner3")
    a = await _proj(client, owner, "報價單產生器")
    b = await _proj(client, owner, "測試報告整理工具")

    dup = _dup_sections((await _page(client, await _admin(app, oidc, "ap-admin3"))).text)
    assert a not in dup and b not in dup, "兩個不同的中文名稱被誤判成重複"


# --- 刪除(A 案:輸入短名確認)-----------------------------------------------


async def test_短名打錯不得刪除(client, app, oidc):
    await make_user(app, "ap-owner4")
    owner = oidc.issue("ap-owner4")
    slug = await _proj(client, owner, "要保留的工具")
    admin_token = await _admin(app, oidc, "ap-admin4")

    resp = await client.post(
        f"{PREFIX}/admin/projects/{slug}/delete",
        data={"confirm_slug": "打錯的短名"},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.text
    assert "短名不符" in resp.text

    still = await client.get(f"{PREFIX}/v1/projects/{slug}", headers=auth(owner))
    assert still.status_code == 200, "短名打錯卻把專案刪掉了"


async def test_短名相符即刪除並留下稽核(client, app, oidc, storage):
    await make_user(app, "ap-owner5")
    owner = oidc.issue("ap-owner5")
    keep = await _proj(client, owner, "保留這個")
    doomed = await _proj(client, owner, "刪掉這個")

    # 放一個檔案進去,驗證物件也要一起消失(孤兒物件會永遠佔空間)。
    rel = await client.post(
        f"{PREFIX}/v1/projects/{doomed}/releases",
        json={"version": "1.0.0", "notes": "n"},
        headers=auth(owner),
    )
    rid = rel.json()["id"]
    up = await client.put(
        f"{PREFIX}/v1/releases/{rid}/artifacts/app.exe?kind=binary",
        content=EXE,
        headers=auth(owner),
    )
    assert up.status_code == 201, up.text
    assert any("app.exe" in k for k in storage.objects), "前提:物件真的存進去了"

    admin_token = await _admin(app, oidc, "ap-admin5")
    resp = await client.post(
        f"{PREFIX}/admin/projects/{doomed}/delete",
        data={"confirm_slug": doomed},
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    gone = await client.get(f"{PREFIX}/v1/projects/{doomed}", headers=auth(owner))
    assert gone.status_code == 404
    # 守門:刪一個不得動到別的。
    alive = await client.get(f"{PREFIX}/v1/projects/{keep}", headers=auth(owner))
    assert alive.status_code == 200
    assert not any("app.exe" in k for k in storage.objects), "物件沒刪掉,會變成孤兒"

    async with app.state.sessionmaker() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "project.delete" in actions


async def test_非管理員進不去(client, app, oidc):
    await make_user(app, "ap-plain")
    token = oidc.issue("ap-plain")
    resp = await client.get(
        f"{PREFIX}/admin/projects", headers={**BROWSER, **auth(token)}, follow_redirects=False
    )
    assert resp.status_code == 403
