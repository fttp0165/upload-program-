"""T63:T2.0 `groups` claim 的防禦性釘子(portal 2026-07-31 通知)。

portal 已在 token 補上 `groups`(字串陣列、full path)。對本服務的裁示:
🔴 **現在只讀不判**——尚無人被指派群組,若把「待開通→開通」改成看
`/svc/upload`,全部使用者(含正在用的人)下次登入會被鎖在外面,
且症狀看起來像 SSO 壞了。啟用時機由 portal 另行通知。

本檔釘住的行為(在 portal 通知啟用之前,這些測試就是「不准提早接」的守門):
1. token 帶 `groups`(含 `/svc/upload`)→ 行為與沒帶完全相同:
   pending 仍是 pending(**不因 /svc/upload 自動開通**)、active 照常通行
2. `groups` 空陣列或整個不存在 → 都能承受(portal §5;兩種都測)
3. 結構上不落地:User 模型沒有 groups 欄位(§4.2a L2 判斷用屬性不得快取)
"""

from app.models import UserStatus
from tests.conftest import auth, make_user

_GROUPS = ["/org/HY/eng/pm", "/svc/upload"]


async def test_pending不因svc_upload而開通(client, app, oidc):
    await make_user(app, "sub-grp-pending", status=UserStatus.pending)
    resp = await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-grp-pending", groups=_GROUPS))
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


async def test_active帶groups照常通行(client, app, oidc):
    await make_user(app, "sub-grp-active")
    resp = await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-grp-active", groups=_GROUPS))
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_groups空陣列與不存在都能承受(client, app, oidc):
    await make_user(app, "sub-grp-empty")
    r1 = await client.get("/v1/me", headers=auth(oidc.issue("sub-grp-empty", groups=[])))
    assert r1.status_code == 200
    r2 = await client.get("/v1/me", headers=auth(oidc.issue("sub-grp-empty")))
    assert r2.status_code == 200


def test_結構上不落地_User無groups欄位():
    """§4.2a L2:判斷用屬性不得快取——用「欄位不存在」從結構上保證。"""
    from app.models import User

    assert not hasattr(User, "groups")
    assert not any("group" in c.name for c in User.__table__.columns)
