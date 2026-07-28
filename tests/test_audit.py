"""T38 稽核紀錄(F54)。

本檔釘住四件事,前兩件是這個任務真正的重點:

1. 🔴 **稽核存快照,不是外鍵。** 東西刪掉之後 `target_id` join 不回任何東西,
   而「誰刪掉了什麼」正是稽核最重要的用途——最需要它的時候最沒用是不能接受的。
2. 🔴 **不留假紀錄。** 稽核與業務同一個 transaction:業務 rollback 時稽核一起消失,
   保證「有紀錄 ⇔ 事情真的發生了」。一旦表裡混進沒發生過的事,整張表就不能用了。
3. 🔴 **只有平台管理員可查。** 這是 T37 那個決定的延續:T37 讓下載統計停在「次數」
   的粒度是刻意的個資決定,若專案 owner 能從稽核看到個別下載者,那個決定就被繞過了。
4. 🔴 **只存 `user_id`**,結構上沒有 email / 姓名欄位。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
PLATFORM_URLS = {"/account", "/login"}


async def _events(app, **filters):
    """直接從 DB 讀稽核列(不經 API,免得測試被查詢端的 bug 掩蓋)。"""
    from sqlalchemy import select

    from app.models import AuditEvent

    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(select(AuditEvent).order_by(AuditEvent.occurred_at))
        ).scalars().all()
    return [
        row
        for row in rows
        if all(getattr(row, key) == value for key, value in filters.items())
    ]


async def _project(client, token, slug="audit-tool", **extra):
    resp = await client.post(
        "/v1/projects", json={"slug": slug, "name": "稽核測試", **extra}, headers=auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _publish(client, token, slug, version="v1.0.0", filename="tool.bin"):
    release = await client.post(
        f"/v1/projects/{slug}/releases", json={"version": version}, headers=auth(token)
    )
    assert release.status_code == 201, release.text
    release_id = release.json()["id"]
    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/{filename}?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text
    done = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert done.status_code == 200, done.text
    return release_id, up.json()["id"]


# --- 建了/刪了什麼 ----------------------------------------------------------


async def test_建立專案留下稽核(client, active_user, app):
    user, token = active_user
    await _project(client, token)

    rows = await _events(app, action="project.create")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].target_type == "project"
    assert rows[0].target_label == "audit-tool"


async def test_刪除專案後仍查得到被刪的是哪一個(client, active_user, app):
    """🔴 本任務的核心:稽核存快照,不是外鍵。

    專案刪掉之後 `target_id` join 不回任何東西。若只存外鍵,稽核頁會顯示
    「某人在某時刪了 9f2c…」——最需要它的時候最沒用。
    """
    _, token = active_user
    await _project(client, token, slug="doomed-tool")
    resp = await client.delete("/v1/projects/doomed-tool", headers=auth(token))
    assert resp.status_code == 204, resp.text

    rows = await _events(app, action="project.delete")
    assert len(rows) == 1
    assert rows[0].target_label == "doomed-tool", "被刪掉的專案 slug 必須留在稽核紀錄裡"


async def test_版本與檔案的建立刪除都有紀錄(client, active_user, app):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "audit-tool")

    assert len(await _events(app, action="release.create")) == 1
    assert len(await _events(app, action="release.publish")) == 1
    uploads = await _events(app, action="artifact.upload")
    assert len(uploads) == 1
    assert uploads[0].target_label == "tool.bin"

    # 已發布的版本不能刪檔(既有規則),所以刪除要在另一個 draft 上做。
    draft = await client.post(
        "/v1/projects/audit-tool/releases", json={"version": "v2.0.0"}, headers=auth(token)
    )
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    up = await client.put(
        f"/v1/releases/{draft_id}/artifacts/doomed.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text

    resp = await client.delete(
        f"/v1/releases/{draft_id}/artifacts/{up.json()['id']}", headers=auth(token)
    )
    assert resp.status_code == 204, resp.text
    deleted = await _events(app, action="artifact.delete")
    assert len(deleted) == 1
    assert deleted[0].target_label == "doomed.bin"

    resp = await client.delete(f"/v1/releases/{draft_id}", headers=auth(token))
    assert resp.status_code == 204, resp.text
    removed = await _events(app, action="release.delete")
    assert len(removed) == 1
    assert removed[0].target_label == "audit-tool:v2.0.0"


# --- 上傳與下載 -------------------------------------------------------------


async def test_下載留下稽核(client, active_user, app):
    """F54 明列「下載了什麼」。這是 T37 刻意不做下載事件表時說好由稽核承擔的部分。"""
    _, token = active_user
    await _project(client, token)
    release_id, artifact_id = await _publish(client, token, "audit-tool")

    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(token)
    )
    assert resp.status_code == 200, resp.text

    rows = await _events(app, action="artifact.download")
    assert len(rows) == 1
    assert rows[0].target_label == "tool.bin"


async def test_無權限的下載不留稽核(client, active_user, app, oidc):
    """擋下來的操作沒有發生,不該進稽核表(與 T37 的「失敗不計數」同一個語意)。"""
    _, owner_token = active_user
    await _project(client, owner_token, slug="secret-tool", visibility="private")
    release_id, artifact_id = await _publish(client, owner_token, "secret-tool")

    await make_user(app, "sub-audit-outsider")
    outsider = oidc.issue("sub-audit-outsider")
    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(outsider)
    )
    assert resp.status_code == 404

    assert await _events(app, action="artifact.download") == []


# --- 開通了誰 ---------------------------------------------------------------


async def test_API開通使用者留下稽核(client, admin_user, app):
    from app.models import UserStatus

    admin, admin_token = admin_user
    target = await make_user(app, "sub-to-activate", status=UserStatus.pending)

    resp = await client.patch(
        f"/v1/admin/users/{target.id}", json={"status": "active"}, headers=auth(admin_token)
    )
    assert resp.status_code == 200, resp.text

    rows = await _events(app, action="user.activate")
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id
    assert rows[0].target_id == target.id
    assert rows[0].target_type == "user"


async def test_網頁開通使用者產生相同的action(client, admin_user, app):
    """⚠️ 開通目前有 API 與網頁兩條實作路徑(既有分岔)。

    T38 不重構它們,但至少把「兩條路的稽核語意相同」釘住——否則稽核紀錄會依
    管理員用哪個介面而不同,而那種不一致查起來最花時間。
    """
    from app.models import UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-web-activate", status=UserStatus.pending)

    resp = await client.post(
        f"/admin/users/{target.id}/activate",
        headers={**BROWSER, **auth(admin_token)},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    rows = await _events(app, action="user.activate")
    assert len(rows) == 1
    assert rows[0].target_id == target.id


async def test_停用使用者留下稽核(client, admin_user, app):
    from app.models import UserStatus

    _, admin_token = admin_user
    target = await make_user(app, "sub-to-disable", status=UserStatus.active)

    resp = await client.patch(
        f"/v1/admin/users/{target.id}", json={"status": "disabled"}, headers=auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    assert len(await _events(app, action="user.disable")) == 1


# --- 🔴 不留假紀錄 ----------------------------------------------------------


async def test_業務失敗時不留下稽核(client, active_user, app):
    """🔴 稽核與業務同一個 transaction:rollback 時稽核一起消失。

    反例是用獨立 session 寫稽核——那會留下「記了但沒發生」的紀錄,
    而一張混進假紀錄的稽核表就不能用了。
    """
    _, token = active_user
    await _project(client, token, slug="dup-tool")

    again = await client.post(
        "/v1/projects", json={"slug": "dup-tool", "name": "重複"}, headers=auth(token)
    )
    assert again.status_code == 409, again.text

    rows = await _events(app, action="project.create")
    assert len(rows) == 1, "失敗的建立不該留下稽核紀錄"


# --- 🔴 個資邊界 ------------------------------------------------------------


async def test_稽核表結構上沒有個資欄位(app):
    """🔴 F54 定案時釘住的邊界:稽核只存 `user_id`,不存 email / 姓名。

    稽核不是繞過個資紅線的後門,所以這件事要**結構上做不到**,不是靠自律。
    """
    from app.models import AuditEvent

    columns = set(AuditEvent.__table__.columns.keys())
    for banned in ("email", "name", "display_name", "username", "actor_email", "actor_name"):
        assert banned not in columns, f"稽核表不得有 {banned} 欄位"
    assert "actor_id" in columns


async def test_稽核不記錄使用者輸入的自由文字(client, active_user, app):
    """`target_label` 只放識別用字串(slug/version/filename),不放摘要或版本說明。"""
    _, token = active_user
    await client.post(
        "/v1/projects",
        json={"slug": "prose-tool", "name": "名稱", "summary": "這段摘要不該進稽核表"},
        headers=auth(token),
    )
    rows = await _events(app, action="project.create")
    assert "這段摘要不該進稽核表" not in (rows[0].target_label or "")


# --- 🔴 查詢權限 ------------------------------------------------------------


async def test_管理員可查稽核(client, admin_user, app):
    _, admin_token = admin_user
    await _project(client, admin_token)

    resp = await client.get("/v1/admin/audit", headers=auth(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["action"] == "project.create" for item in body["items"])


async def test_非管理員查稽核回403(client, active_user):
    """🔴 T37 讓下載統計停在「次數」是刻意的個資決定。

    若專案 owner 能從稽核看到個別下載者,那個決定就被從另一扇門繞過了。
    """
    _, token = active_user
    resp = await client.get("/v1/admin/audit", headers=auth(token))
    assert resp.status_code == 403, resp.text


async def test_未認證查稽核回401(client):
    resp = await client.get("/v1/admin/audit")
    assert resp.status_code == 401


# --- 查詢:篩選、排序、分頁 -------------------------------------------------


async def test_可依action篩選(client, admin_user):
    _, admin_token = admin_user
    await _project(client, admin_token)
    await _publish(client, admin_token, "audit-tool")

    resp = await client.get("/v1/admin/audit?action=artifact.upload", headers=auth(admin_token))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items and all(item["action"] == "artifact.upload" for item in items)


async def test_可依操作者與目標篩選(client, admin_user, app):
    admin, admin_token = admin_user
    project = await _project(client, admin_token)

    by_actor = await client.get(
        f"/v1/admin/audit?actor_id={admin.id}", headers=auth(admin_token)
    )
    assert by_actor.status_code == 200
    assert by_actor.json()["total"] >= 1

    by_target = await client.get(
        f"/v1/admin/audit?target_id={project['id']}", headers=auth(admin_token)
    )
    assert by_target.status_code == 200
    assert all(item["target_id"] == project["id"] for item in by_target.json()["items"])


async def test_依時間倒序且可分頁(client, admin_user):
    _, admin_token = admin_user
    for i in range(25):
        await _project(client, admin_token, slug=f"page-tool-{i:02d}")

    first = await client.get("/v1/admin/audit?limit=20", headers=auth(admin_token))
    assert first.status_code == 200, first.text
    items = first.json()["items"]
    assert len(items) == 20
    stamps = [item["occurred_at"] for item in items]
    assert stamps == sorted(stamps, reverse=True), "最新的在最前面"

    second = await client.get("/v1/admin/audit?limit=20&offset=20", headers=auth(admin_token))
    assert second.status_code == 200
    ids = {item["id"] for item in items}
    assert not (ids & {item["id"] for item in second.json()["items"]})


# --- 字彙一致性 -------------------------------------------------------------


async def test_action字彙集中且格式一致():
    """🔴 `action` 刻意不下 DB CHECK(每加一個動作就要 migration,阻力會讓人選擇不記)。

    守門因此改由這條測試負責:字彙只有一個來源,且命名格式一致。
    """
    from app.audit import AuditAction

    assert len(AuditAction) >= 10
    for action in AuditAction:
        assert re.fullmatch(r"[a-z]+\.[a-z_]+", action.value), action.value


async def test_程式裡不得出現硬寫的action字串():
    """寫入點一律用 `AuditAction.xxx`,不用裸字串——否則打錯字只會安靜地少一筆紀錄。"""
    import pathlib

    from app.audit import AuditAction

    values = {a.value for a in AuditAction}
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        if path.name == "audit.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for value in values:
                if f'"{value}"' in line or f"'{value}'" in line:
                    offenders.append(f"{path}:{number}")
    assert not offenders, f"請改用 AuditAction 常數:{offenders}"


# --- 保留期清理 -------------------------------------------------------------


async def test_清理工具只刪超過保留期的(app, settings):
    """T37 承諾「稽核有自己的保存期限」。這條測試讓那句承諾有落點。"""
    from datetime import UTC, datetime, timedelta

    from app.audit import purge_expired
    from app.models import AuditEvent

    now = datetime.now(UTC)
    async with app.state.sessionmaker() as session:
        session.add_all(
            [
                AuditEvent(
                    action="project.create",
                    target_type="project",
                    target_label="old",
                    occurred_at=now - timedelta(days=settings.audit_retention_days + 1),
                ),
                AuditEvent(
                    action="project.create",
                    target_type="project",
                    target_label="fresh",
                    occurred_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    async with app.state.sessionmaker() as session:
        dry = await purge_expired(session, settings.audit_retention_days, dry_run=True)
    assert dry == 1
    assert len(await _events(app)) == 2, "dry-run 不得真的刪"

    async with app.state.sessionmaker() as session:
        removed = await purge_expired(session, settings.audit_retention_days, dry_run=False)
    assert removed == 1
    remaining = await _events(app)
    assert [row.target_label for row in remaining] == ["fresh"]


# --- 網頁 -------------------------------------------------------------------


async def test_管理員的稽核頁看得到紀錄(client, admin_user):
    _, admin_token = admin_user
    await _project(client, admin_token)

    resp = await client.get("/admin/audit", headers={**BROWSER, **auth(admin_token)})
    assert resp.status_code == 200, resp.text
    assert "project.create" in resp.text
    assert "audit-tool" in resp.text


async def test_非管理員開稽核頁回403(client, active_user):
    _, token = active_user
    resp = await client.get("/admin/audit", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 403


async def test_未登入開稽核頁會302(client):
    resp = await client.get("/admin/audit", headers=BROWSER, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"{PREFIX}/auth/login")


async def test_稽核頁對使用者可控內容逸出(client, admin_user, app):
    """`target_label` 是使用者取的 slug / 檔名,屬使用者可控內容。

    slug 與檔名各自有格式限制,`<` 進不來——但稽核頁顯示的是**歷史快照**,
    而格式規則會隨版本改變。直接把惡意字串塞進資料列,測的才是頁面本身的逸出。
    """
    from app.models import AuditEvent

    _, admin_token = admin_user
    async with app.state.sessionmaker() as session:
        session.add(
            AuditEvent(
                action="project.delete",
                target_type="project",
                target_label='<script>alert(1)</script>',
            )
        )
        await session.commit()

    page = await client.get("/admin/audit", headers={**BROWSER, **auth(admin_token)})
    assert page.status_code == 200, page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


async def test_稽核頁連結帶前綴且無絕對網址(client, admin_user):
    _, admin_token = admin_user
    await _project(client, admin_token)

    resp = await client.get("/admin/audit", headers={**BROWSER, **auth(admin_token)})
    found = [link for link in _LINK_RE.findall(resp.text) if link not in PLATFORM_URLS]
    assert found
    for link in found:
        assert link.startswith(f"{PREFIX}/"), link
        assert not link.startswith(("http://", "https://", "//")), link
