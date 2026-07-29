"""T59:顯示名稱快取(契約 §4.2a L1,portal 2026-07-30 裁決)。

釘住的行為:
1. 登入時以本人 token 的 `name` claim 寫入快取;**每次登入覆寫**
2. 🔴 claim 不存在 → 快取為 NULL(name 由 firstName+lastName 推導,皆空則無
   ——這是會真的走到的路徑);IdP 拿掉名字後,快取不得留舊值
3. 🔴 來源僅限 `name`:`preferred_username` 不在裁決准許範圍
4. 僅管理後台顯示:/v1/me 不回傳快取欄位(它不是給使用者的 API 資料)
5. 後台清單:有名字 → 名字 + 截斷 sub(title 帶完整值);無名字 → 完整 sub
"""

from sqlalchemy import select

from app.models import User
from tests.conftest import auth, make_user


async def _login(client, oidc, sub, **claims):
    resp = await client.get("/v1/me", headers=auth(oidc.issue(sub, **claims)))
    return resp


async def _cache(app, sub):
    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == sub))).scalar_one()
        return user.display_name_cache


async def test_登入寫入快取且每次覆寫(client, app, oidc):
    await make_user(app, "sub-name-1")
    await _login(client, oidc, "sub-name-1", name="王小明")
    assert await _cache(app, "sub-name-1") == "王小明"
    # 改名後再登入 → 覆寫
    await _login(client, oidc, "sub-name-1", name="王大明")
    assert await _cache(app, "sub-name-1") == "王大明"


async def test_無name_claim時快取為NULL_舊值也要清掉(client, app, oidc):
    await make_user(app, "sub-name-2")
    await _login(client, oidc, "sub-name-2", name="林新人")
    assert await _cache(app, "sub-name-2") == "林新人"
    # IdP 端名字被清空 → 下次登入快取跟著清,不得留舊值
    await _login(client, oidc, "sub-name-2")
    assert await _cache(app, "sub-name-2") is None


async def test_來源僅限name_不採preferred_username(client, app, oidc):
    await make_user(app, "sub-name-3")
    await _login(client, oidc, "sub-name-3", preferred_username="peter.pan")
    assert await _cache(app, "sub-name-3") is None


async def test_me不回傳快取欄位(client, app, oidc):
    """§4.2a:僅管理後台顯示——快取不是一般 API 的輸出。"""
    await make_user(app, "sub-name-4")
    resp = await _login(client, oidc, "sub-name-4", name="陳測試")
    assert "display_name_cache" not in resp.json()


async def test_後台清單_有名字顯示名字與截斷sub_無名字顯示完整sub(client, app, oidc):
    admin_sub = "sub-name-admin"
    await make_user(app, admin_sub, admin=True)
    await make_user(app, "sub-name-noname")
    # 管理員本人登入(帶名字)→ 自己那列有名字 + 截斷 sub
    cookie = None
    resp = await client.get("/admin/users", headers=auth(oidc.issue(admin_sub, name="管理員甲")))
    assert resp.status_code == 200
    html = resp.text
    assert "管理員甲" in html
    assert f"{admin_sub[:8]}…" in html
    assert f'title="{admin_sub}"' in html   # 完整 sub 仍在(截斷值僅供人眼)
    # 沒名字的帳號 → 完整 sub 照舊
    assert "sub-name-noname" in html
    assert cookie is None
