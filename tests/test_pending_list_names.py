"""T85 待開通清單顯示名稱與申請時間(T59 的漏網之魚)。

Benny 實測回報:「還是沒有申請者名字與身分」。

根因:`admin_users.html` 有兩個迴圈,**T59 只改了「其他帳號」那個**;
「待開通」那個仍只印 `user.sub`。而漏掉的正好是最需要名字的那一半——
「開通」是這一頁唯一有後果的操作,**認不出是誰就不該按下開通**。

🔴 本檔同時釘住三條不得因為「要顯示更多」而鬆動的界線:

1. 沒有名字時**顯示完整 sub**,不得空白(快取為 NULL 是常態:IdP 沒有
   firstName/lastName,或那人最後一次登入早於 T59)。
2. 開通表單一律以**資料庫 id** 為鍵——契約 L1 第 8 條:截斷值僅供人眼辨識。
3. 名字來自 IdP claim,必須逸出。
"""

from app.models import UserStatus
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"


async def _set_name(app, user, name):
    """直接寫快取——模擬「那個人自己登入過、IdP 有給 name」的既成狀態。"""
    async with app.state.sessionmaker() as session:
        user.display_name_cache = name
        await session.merge(user)
        await session.commit()


async def _page(client, token):
    resp = await client.get("/admin/users", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    return resp.text


def _pending_block(body: str) -> str:
    """只取「待開通」那一段——避免拿「其他帳號」那半的名字充數而假綠。"""
    start = body.index("待開通(")
    return body[start : body.index("其他帳號(")]


async def test_待開通者顯示名稱(client, app, oidc):
    await make_user(app, "sub-t85-admin", admin=True)
    applicant = await make_user(app, "sub-t85-applicant", status=UserStatus.pending)
    await _set_name(app, applicant, "張三")

    block = _pending_block(await _page(client, oidc.issue("sub-t85-admin")))
    assert "張三" in block, "待開通清單必須顯示名字——認不出是誰就不該按開通"


async def test_待開通者沒有名稱時顯示完整sub(client, app, oidc):
    """🔴 快取為 NULL 是常態不是例外(IdP 無 firstName/lastName,或登入早於 T59)。"""
    await make_user(app, "sub-t85-admin2", admin=True)
    applicant = await make_user(app, "sub-t85-noname", status=UserStatus.pending)

    block = _pending_block(await _page(client, oidc.issue("sub-t85-admin2")))
    assert applicant.sub in block, "沒有名字時必須顯示完整 sub,不能留白"


async def test_待開通清單顯示申請時間(client, app, oidc):
    """名字空白時,管理員唯一還能對上的線索是「剛剛誰跟我說他申請了」。

    時間不是個資,不動任何紅線,卻是空白名字時的救命稻草。
    """
    await make_user(app, "sub-t85-admin3", admin=True)
    applicant = await make_user(app, "sub-t85-when", status=UserStatus.pending)

    block = _pending_block(await _page(client, oidc.issue("sub-t85-admin3")))
    assert applicant.created_at.date().isoformat() in block


async def test_開通表單仍以資料庫id為鍵(client, app, oidc):
    """🔴 契約 L1 第 8 條:截斷值僅供人眼辨識,操作一律以完整鍵為準。

    顯示改動最容易順手把「畫面上那串」拿去當表單值——這條擋的就是那個。
    """
    await make_user(app, "sub-t85-admin4", admin=True)
    applicant = await make_user(app, "sub-t85-key", status=UserStatus.pending)
    await _set_name(app, applicant, "李四")

    block = _pending_block(await _page(client, oidc.issue("sub-t85-admin4")))
    assert f'action="{PREFIX}/admin/users/{applicant.id}/activate"' in block
    assert applicant.sub[:8] + "/activate" not in block, "不得以截斷值當操作鍵"


async def test_待開通者名稱中的HTML被逸出(client, app, oidc):
    await make_user(app, "sub-t85-admin5", admin=True)
    applicant = await make_user(app, "sub-t85-xss", status=UserStatus.pending)
    await _set_name(app, applicant, "<script>alert(1)</script>")

    block = _pending_block(await _page(client, oidc.issue("sub-t85-admin5")))
    assert "<script>alert(1)</script>" not in block
    assert "&lt;script&gt;" in block


async def test_頁首不再宣稱沒有名字(client, app, oidc):
    """T59 之後那句話就不成立了,文案卻留到現在——畫面因此自相矛盾(第二條 5 回寫)。"""
    await make_user(app, "sub-t85-admin6", admin=True)

    body = await _page(client, oidc.issue("sub-t85-admin6"))
    assert "只有識別碼、沒有名字" not in body
