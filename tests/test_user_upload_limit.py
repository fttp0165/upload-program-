"""T141:後台可設定「每個帳號」的單檔上限。

計畫書:`docs/plans/設計_每個帳號的單檔上限.md`

🔴 本檔釘住的界線,其中兩條是本任務的重點而不是補充:

1. **`NULL` = 沿用全站**(既有帳號行為逐字不變)。
2. **有效上限取 `min(帳號值, 全站值)`** —— 少了這個 `min`,日後把全站上限
   **調小**(磁碟吃緊)這個動作會**靜默地對已被設定過的帳號無效**。
3. **不得存下大於全站上限的值** —— 那個欄位會撒謊:存得下、畫面說可以,
   而上傳撞 **nginx 的 413**(2026-09-08 真的發生過一次:App 已是 500 MB
   而 gateway 還是 128m,使用者只看到「上傳失敗(413)」)。
4. **上傳頁的 `data-max-bytes` 必須是「這個使用者的」有效上限** ——
   拿全站值的話,前端預檢會對被調小的帳號**放行一個必定失敗的上傳**。
"""


from app.limits import effective_artifact_limit
from app.models import User
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200


class _FakeUser:
    def __init__(self, limit):
        self.max_artifact_bytes = limit


class _FakeSettings:
    def __init__(self, limit):
        self.max_artifact_bytes = limit


# --- 純函式:有效上限 --------------------------------------------------------


def test_沒設定的帳號沿用全站上限():
    """既有帳號一律 NULL —— 行為必須逐字不變。"""
    assert effective_artifact_limit(_FakeSettings(500), _FakeUser(None)) == 500


def test_有設定就用帳號的值():
    assert effective_artifact_limit(_FakeSettings(500), _FakeUser(100)) == 100


def test_帳號值大於全站上限時取全站上限():
    """🔴 `min` 的守門:全站上限事後被調小時,舊的個別設定不得繞過它。

    少了這一條,「把全站上限調小」這個動作會**靜默地**對某些帳號無效 ——
    而那正是最需要它生效的時候(磁碟吃緊)。
    """
    assert effective_artifact_limit(_FakeSettings(200), _FakeUser(400)) == 200


# --- 上傳實際被擋 -----------------------------------------------------------


async def _project_and_release(client, token, slug="limit-tool"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "上限測試", "summary": ""},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": ""},
        headers=auth(token),
    )
    assert release.status_code == 201, release.text
    return release.json()["id"]


async def _set_limit(app, sub, value):
    from sqlalchemy import select

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == sub))).scalar_one()
        user.max_artifact_bytes = value
        await session.commit()


async def test_帳號上限比檔案小時上傳被擋且說明是帳號限制(client, app, oidc, storage):
    await make_user(app, "sub-small")
    token = oidc.issue("sub-small")
    release_id = await _project_and_release(client, token)
    await _set_limit(app, "sub-small", 100)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert resp.status_code == 413, resp.text
    # 🔴 訊息要分得出是帳號限制還是全站限制 —— 否則使用者問「為什麼我只能傳這麼小」
    #    時,沒有人分得出他是被單獨設定過、還是全站就這麼小。
    assert "帳號" in resp.json()["detail"]


async def test_沒設定的帳號仍走全站上限(client, app, oidc, settings, storage):
    """守門:本功能不得改變沒被設定過的人的行為。"""
    await make_user(app, "sub-default")
    token = oidc.issue("sub-default")
    release_id = await _project_and_release(client, token, slug="default-tool")

    too_big = ELF + b"\x00" * settings.max_artifact_bytes
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/big.bin?kind=binary",
        content=too_big,
        headers=auth(token),
    )
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "帳號" not in detail, "沒設定過的人不該看到帳號限制的字眼"


# --- 後台設定端點 -----------------------------------------------------------


async def _admin_headers(app, oidc, sub="sub-limit-admin"):
    await make_user(app, sub, admin=True)
    return auth(oidc.issue(sub))


async def test_管理員可以設定與清除帳號上限(client, app, oidc):
    target = await make_user(app, "sub-target")
    headers = await _admin_headers(app, oidc)

    resp = await client.post(
        f"/admin/users/{target.id}/upload-limit",
        # ⚠ 測試環境的全站上限是 1 MB(conftest 刻意用小數字讓測試跑得快),
        # 所以這裡只能填 1 —— 填更大會被「不得超過全站上限」那條規則擋掉,
        # 而那正是下一條測試要驗的東西。
        data={"limit_mb": "1"},
        headers={**BROWSER, **headers},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text

    from sqlalchemy import select

    async with app.state.sessionmaker() as session:
        row = (await session.execute(select(User).where(User.sub == "sub-target"))).scalar_one()
        assert row.max_artifact_bytes == 1024 * 1024

    # 空白 = 清除 = 回到沿用全站
    resp = await client.post(
        f"/admin/users/{target.id}/upload-limit",
        data={"limit_mb": ""},
        headers={**BROWSER, **headers},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    async with app.state.sessionmaker() as session:
        row = (await session.execute(select(User).where(User.sub == "sub-target"))).scalar_one()
        assert row.max_artifact_bytes is None


async def test_不得存下大於全站上限的值(client, app, oidc, settings):
    """🔴 那個欄位會撒謊,除非把它夾住。

    存得下 → 畫面說可以 → 上傳撞 nginx 的 413,而那一頁不說上限也不說用量。
    """
    target = await make_user(app, "sub-toobig")
    headers = await _admin_headers(app, oidc, "sub-limit-admin2")

    over_mb = settings.max_artifact_bytes // (1024 * 1024) + 999
    resp = await client.post(
        f"/admin/users/{target.id}/upload-limit",
        data={"limit_mb": str(over_mb)},
        headers={**BROWSER, **headers},
        follow_redirects=False,
    )
    assert resp.status_code == 422, resp.text
    # 錯誤訊息要寫出全站上限是多少,否則管理員只知道「不行」不知道「能填多少」
    assert str(settings.max_artifact_bytes) in resp.text

    from sqlalchemy import select

    async with app.state.sessionmaker() as session:
        row = (await session.execute(select(User).where(User.sub == "sub-toobig"))).scalar_one()
        assert row.max_artifact_bytes is None, "被拒絕的值不得落地"


async def test_非管理員不得設定帳號上限(client, app, oidc):
    target = await make_user(app, "sub-victim")
    await make_user(app, "sub-plain")
    resp = await client.post(
        f"/admin/users/{target.id}/upload-limit",
        data={"limit_mb": "1"},
        headers={**BROWSER, **auth(oidc.issue("sub-plain"))},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 403)
    from sqlalchemy import select

    async with app.state.sessionmaker() as session:
        row = (await session.execute(select(User).where(User.sub == "sub-victim"))).scalar_one()
        assert row.max_artifact_bytes is None


async def test_設定會留稽核(client, app, oidc):
    from sqlalchemy import select

    from app.models import AuditEvent

    target = await make_user(app, "sub-audited")
    headers = await _admin_headers(app, oidc, "sub-limit-admin3")
    await client.post(
        f"/admin/users/{target.id}/upload-limit",
        data={"limit_mb": "1"},
        headers={**BROWSER, **headers},
        follow_redirects=False,
    )
    async with app.state.sessionmaker() as session:
        actions = [
            e.action
            for e in (await session.execute(select(AuditEvent))).scalars().all()
        ]
    assert "user.set_upload_limit" in actions


# --- 前端預檢拿到的是誰的上限 ------------------------------------------------


async def test_上傳頁的預檢值是該使用者的有效上限(client, app, oidc):
    """🔴 漏掉這一處,前端會用**全站**的值做預檢 ——
    對被調小的帳號,它會放行一個**必定失敗**的上傳。"""
    await make_user(app, "sub-page")
    token = oidc.issue("sub-page")
    release_id = await _project_and_release(client, token, slug="page-tool")
    await _set_limit(app, "sub-page", 700_000)

    resp = await client.get(
        f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)}
    )
    assert resp.status_code == 200
    assert 'data-max-bytes="700000"' in resp.text
