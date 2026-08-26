"""T102:發布審核(draft → in_review → published)。

Benny 2026-08-26 裁示釘住的行為:
1. **所有版本都要審**:maintainer 只能「送審」,發布(published)只能由平台管理員核准產生
2. **待審對非成員隱藏**:in_review 與 draft 同一待遇——非成員 404
3. **退回必須寫理由**:空理由不受理;理由回到作者眼前(存 `review_note`)
4. 「最新版」(F26)只認 published;`published_at` 的語意變成「核准時刻」
5. 審核中凍結:in_review 不可上傳/刪檔/改說明;作者可撤回
6. 送審通知走 email(契約 §4.2b):快取僅自本人 token、`email_verified=true` 才落地、
   預設不訂閱、寄失敗不得阻斷送審
"""

from sqlalchemy import select

from app.models import User
from tests.conftest import (
    ELF,
    auth,
    complete_kinds,
    make_user,
    submit_release,
)


async def _project(client, token, slug="review-me"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "待審專案", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _draft_with_kinds(client, token, slug="review-me", version="v1"):
    """建 draft 並補齊三類檔案(可送審狀態),回傳 release id。"""
    release = await client.post(
        f"/v1/projects/{slug}/releases", json={"version": version}, headers=auth(token)
    )
    assert release.status_code == 201, release.text
    rid = release.json()["id"]
    up = await client.put(
        f"/v1/releases/{rid}/artifacts/tool.bin?kind=binary", content=ELF, headers=auth(token)
    )
    assert up.status_code == 201, up.text
    await complete_kinds(client, token, rid)
    return rid


async def _reviewer(app, oidc, sub="sub-reviewer"):
    """造一個平台管理員當審核者,回傳 token。"""
    await make_user(app, sub, admin=True)
    return oidc.issue(sub)


# --- 送審 --------------------------------------------------------------------


async def test_送審後狀態為in_review且記錄送審時間(client, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)

    resp = await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "in_review"
    assert body["submitted_at"] is not None
    assert body["published_at"] is None  # 送審不是發布


async def test_送審仍要求三類齊備(client, active_user):
    """T65 的規則不變,只是提前到送審時把關——管理員審的必須是完整交付。"""
    _, token = active_user
    await _project(client, token)
    release = await client.post(
        "/v1/projects/review-me/releases", json={"version": "v1"}, headers=auth(token)
    )
    rid = release.json()["id"]
    await client.put(
        f"/v1/releases/{rid}/artifacts/tool.bin?kind=binary", content=ELF, headers=auth(token)
    )
    resp = await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert resp.status_code == 422
    assert "缺" in resp.json()["detail"]


async def test_publish路徑是submit的別名_不再直接發布(client, active_user):
    """既有腳本打 /publish 不斷線,但拿到的是送審,不是發布。"""
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    resp = await client.post(f"/v1/releases/{rid}/publish", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "in_review"


async def test_maintainer不能自行核准(client, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    resp = await client.post(f"/v1/releases/{rid}/approve", headers=auth(token))
    assert resp.status_code == 403


# --- 核准 / 退回 -------------------------------------------------------------


async def test_核准後才是published且published_at為核准時刻(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    reviewer = await _reviewer(app, oidc)
    resp = await client.post(f"/v1/releases/{rid}/approve", headers=auth(reviewer))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None
    assert body["reviewed_at"] is not None


async def test_退回必須寫理由(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    reviewer = await _reviewer(app, oidc)

    # 沒理由、空白理由都不受理
    resp = await client.post(f"/v1/releases/{rid}/reject", json={}, headers=auth(reviewer))
    assert resp.status_code == 422
    resp = await client.post(
        f"/v1/releases/{rid}/reject", json={"note": "   "}, headers=auth(reviewer)
    )
    assert resp.status_code == 422


async def test_退回後回到draft且作者看得到理由(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    reviewer = await _reviewer(app, oidc)

    resp = await client.post(
        f"/v1/releases/{rid}/reject",
        json={"note": "執行檔缺 SHA-256 說明,請補上再送"},
        headers=auth(reviewer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "draft"

    # 作者(成員)查得到理由
    detail = await client.get(f"/v1/releases/{rid}", headers=auth(token))
    assert detail.json()["review_note"] == "執行檔缺 SHA-256 說明,請補上再送"

    # 上傳頁把理由擺到作者眼前
    page = await client.get(f"/releases/{rid}/upload", headers=auth(token))
    assert "執行檔缺 SHA-256 說明" in page.text


async def test_重送清掉上一輪退回理由(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    reviewer = await _reviewer(app, oidc)
    await client.post(
        f"/v1/releases/{rid}/reject", json={"note": "先退"}, headers=auth(reviewer)
    )

    resp = await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["review_note"] == ""  # 舊理由是上一輪的事,留著會誤導審核者


async def test_對draft核准或退回回409(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    reviewer = await _reviewer(app, oidc)

    assert (
        await client.post(f"/v1/releases/{rid}/approve", headers=auth(reviewer))
    ).status_code == 409
    assert (
        await client.post(
            f"/v1/releases/{rid}/reject", json={"note": "x"}, headers=auth(reviewer)
        )
    ).status_code == 409


async def test_已發布版本重複核准為冪等(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    reviewer = await _reviewer(app, oidc)
    await client.post(f"/v1/releases/{rid}/approve", headers=auth(reviewer))

    resp = await client.post(f"/v1/releases/{rid}/approve", headers=auth(reviewer))
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


# --- 撤回與凍結 --------------------------------------------------------------


async def test_作者可撤回送審(client, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    resp = await client.post(f"/v1/releases/{rid}/withdraw", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"


async def test_審核中不可上傳刪檔或改說明(client, active_user):
    """審核對象不得中途變動——要改就先撤回。"""
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    up = await client.put(
        f"/v1/releases/{rid}/artifacts/extra.bin?kind=binary", content=ELF, headers=auth(token)
    )
    assert up.status_code == 409

    detail = await client.get(f"/v1/releases/{rid}", headers=auth(token))
    artifact_id = detail.json()["artifacts"][0]["id"]
    rm = await client.delete(f"/v1/releases/{rid}/artifacts/{artifact_id}", headers=auth(token))
    assert rm.status_code == 409

    patch = await client.patch(
        f"/v1/releases/{rid}", json={"notes": "偷偷改"}, headers=auth(token)
    )
    assert patch.status_code == 409


# --- 可見性(裁示 3)---------------------------------------------------------


async def test_待審版本對非成員隱藏(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    await make_user(app, "sub-outsider")
    outsider = oidc.issue("sub-outsider")

    # 詳情、下載、列表三條路都不得洩漏
    assert (
        await client.get(f"/v1/releases/{rid}", headers=auth(outsider))
    ).status_code == 404
    listing = await client.get("/v1/projects/review-me/releases", headers=auth(outsider))
    assert listing.json()["total"] == 0

    detail = await client.get(f"/v1/releases/{rid}", headers=auth(token))
    artifact_id = detail.json()["artifacts"][0]["id"]
    dl = await client.get(
        f"/v1/releases/{rid}/artifacts/{artifact_id}/download", headers=auth(outsider)
    )
    assert dl.status_code == 404


async def test_latest不含待審版本(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)

    rid1 = await _draft_with_kinds(client, token, version="v1")
    await submit_release(client, token, rid1, approve=True)

    rid2 = await _draft_with_kinds(client, token, version="v2")
    await client.post(f"/v1/releases/{rid2}/submit", headers=auth(token))

    resp = await client.get("/v1/projects/review-me/releases/latest", headers=auth(token))
    assert resp.json()["version"] == "v1"  # v2 還在審,不得成為最新版


# --- 後台佇列與待辦 -----------------------------------------------------------


async def test_審核佇列列出待審版本(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    reviewer = await _reviewer(app, oidc)
    page = await client.get("/admin/reviews", headers=auth(reviewer))
    assert page.status_code == 200
    assert "review-me" in page.text
    assert "v1" in page.text


async def test_審核佇列頁非管理員回403(client, active_user):
    _, token = active_user
    resp = await client.get("/admin/reviews", headers=auth(token))
    assert resp.status_code == 403


async def test_管理總覽待辦區出現待審版本(client, app, oidc, active_user):
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))

    reviewer = await _reviewer(app, oidc)
    page = await client.get("/admin", headers=auth(reviewer))
    assert page.status_code == 200
    assert "待審" in page.text


async def test_網頁核准與退回(client, app, oidc, active_user):
    """後台表單那條路要跟 API 同一套規則:退回理由必填。"""
    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    reviewer = await _reviewer(app, oidc)

    # 空理由退回 → 帶錯誤參數回佇列頁,狀態不變
    resp = await client.post(
        f"/admin/reviews/{rid}/reject", data={"note": "  "}, headers=auth(reviewer)
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]
    assert (await client.get(f"/v1/releases/{rid}", headers=auth(token))).json()[
        "status"
    ] == "in_review"

    resp = await client.post(
        f"/admin/reviews/{rid}/approve", headers=auth(reviewer)
    )
    assert resp.status_code == 303
    assert (await client.get(f"/v1/releases/{rid}", headers=auth(token))).json()[
        "status"
    ] == "published"


# --- 稽核 --------------------------------------------------------------------


async def test_送審核准退回撤回都留稽核(client, app, oidc, active_user):
    from app.models import AuditEvent

    _, token = active_user
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    reviewer = await _reviewer(app, oidc)

    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    await client.post(f"/v1/releases/{rid}/withdraw", headers=auth(token))
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    await client.post(
        f"/v1/releases/{rid}/reject", json={"note": "補文件"}, headers=auth(reviewer)
    )
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    await client.post(f"/v1/releases/{rid}/approve", headers=auth(reviewer))

    async with app.state.sessionmaker() as session:
        actions = {
            row.action
            for row in (await session.execute(select(AuditEvent))).scalars().all()
        }
    assert {"release.submit", "release.withdraw", "release.reject", "release.approve"} <= actions
    # 退回理由是自由文字,不得進稽核(AuditEvent 紅線)
    async with app.state.sessionmaker() as session:
        labels = [
            row.target_label
            for row in (await session.execute(select(AuditEvent))).scalars().all()
        ]
    assert all("補文件" not in label for label in labels)


# --- email 快取(契約 §4.2b)-------------------------------------------------


async def _cached_email(app, sub):
    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.sub == sub))).scalar_one()
        return user.notify_email_cache


async def test_登入落地已驗證的email且每次覆寫(client, app, oidc):
    await make_user(app, "sub-mail-1")
    await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-mail-1", email="a@sporton.test", email_verified=True))
    )
    assert await _cached_email(app, "sub-mail-1") == "a@sporton.test"
    await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-mail-1", email="b@sporton.test", email_verified=True))
    )
    assert await _cached_email(app, "sub-mail-1") == "b@sporton.test"


async def test_未驗證或缺email視同沒有(client, app, oidc):
    """§4.2b 第 2 條:email_verified=false 或缺 claim → 落 NULL,舊值也要清。"""
    await make_user(app, "sub-mail-2")
    await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-mail-2", email="a@sporton.test", email_verified=True))
    )
    await client.get(
        "/v1/me",
        headers=auth(oidc.issue("sub-mail-2", email="hacker@evil.test", email_verified=False)),
    )
    assert await _cached_email(app, "sub-mail-2") is None
    await client.get("/v1/me", headers=auth(oidc.issue("sub-mail-2")))
    assert await _cached_email(app, "sub-mail-2") is None


async def test_email快取不進API也不上任何頁面(client, app, oidc):
    """§4.2b 第 4 條:快取不顯示、不進 API 回應。

    ⚠ `/v1/me` 回**本人 token 裡的** email 是既有行為(記憶體傳遞,契約允許)——
    這裡釘的是**快取**:管理後台清單不得露出「別人的」快取信箱
    (L1 的名字是拿來辨識的所以可顯示;email 是拿來投遞的,顯示只多一份外洩面)。
    """
    await make_user(app, "sub-mail-3")
    resp = await client.get(
        "/v1/me", headers=auth(oidc.issue("sub-mail-3", email="a@sporton.test", email_verified=True))
    )
    assert "notify_email_cache" not in resp.text  # 快取欄位不是 API 資料

    admin_sub = "sub-mail-admin"
    await make_user(app, admin_sub, admin=True)
    page = await client.get("/admin/users", headers=auth(oidc.issue(admin_sub)))
    assert page.status_code == 200
    assert "a@sporton.test" not in page.text  # 別人的快取信箱不得出現在後台清單


# --- 送審通知 -----------------------------------------------------------------


async def _opted_in_admin(client, app, oidc, sub="sub-notify-admin", email="admin@sporton.test"):
    """造一個已開通、已登入(email 進快取)、已訂閱的管理員。"""
    await make_user(app, sub, admin=True)
    token = oidc.issue(sub, email=email, email_verified=True)
    await client.get("/v1/me", headers=auth(token))  # 登入 → email 落地
    resp = await client.post("/admin/reviews/notify", headers=auth(token))
    assert resp.status_code == 303
    return token


async def test_送審寄出通知給已訂閱管理員(client, app, oidc, active_user, mailer):
    _, token = active_user
    await _opted_in_admin(client, app, oidc)
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)

    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert len(mailer.sent) == 1
    recipients, subject, body = mailer.sent[0]
    assert recipients == ("admin@sporton.test",)
    assert "review-me" in subject or "review-me" in body
    # §4.2b:信只含 slug/版本/後台連結,不含使用者資料
    assert "sub-active" not in body


async def test_未訂閱或無快取的管理員不收信且送審照常(client, app, oidc, active_user, mailer):
    _, token = active_user
    # 管理員存在但沒訂閱、也沒登入過(無快取)
    await make_user(app, "sub-silent-admin", admin=True)
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)

    resp = await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert resp.status_code == 200
    assert mailer.sent == []


async def test_寄信炸掉不得阻斷送審(client, app, oidc, active_user, mailer):
    """§4.2b 第 6 條精神:通知是加分,不是流程的一部分。"""
    _, token = active_user
    await _opted_in_admin(client, app, oidc)
    mailer.fail = True
    await _project(client, token)
    rid = await _draft_with_kinds(client, token)

    resp = await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_review"


async def test_退訂後不再收信(client, app, oidc, active_user, mailer):
    """§4.2b 第 8 條:訂閱的開關在管理員自己手上。"""
    _, token = active_user
    admin_token = await _opted_in_admin(client, app, oidc)
    # 再按一次=退訂
    await client.post("/admin/reviews/notify", headers=auth(admin_token))

    await _project(client, token)
    rid = await _draft_with_kinds(client, token)
    await client.post(f"/v1/releases/{rid}/submit", headers=auth(token))
    assert mailer.sent == []


async def test_純清除工具可整批與逐人清除email快取(app, client, oidc):
    """§4.2b 第 7 條:整批清除 + 個別即時清除(整批清空必然涵蓋孤兒)。"""
    from tools.purge_notify_email import purge

    await make_user(app, "sub-purge-1")
    await make_user(app, "sub-purge-2")
    for sub in ("sub-purge-1", "sub-purge-2"):
        await client.get(
            "/v1/me", headers=auth(oidc.issue(sub, email=f"{sub}@x.test", email_verified=True))
        )

    async with app.state.sessionmaker() as session:
        cleared = await purge(session, sub="sub-purge-1")
        assert cleared == 1
    assert await _cached_email(app, "sub-purge-1") is None
    assert await _cached_email(app, "sub-purge-2") == "sub-purge-2@x.test"

    async with app.state.sessionmaker() as session:
        cleared = await purge(session)  # 整批
        assert cleared == 1
    assert await _cached_email(app, "sub-purge-2") is None
