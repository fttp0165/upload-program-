"""T70:管理後台總覽 `/admin`(KPI + 需要動作的待辦 + 系統資訊)。

🔴 這一檔的重點是**數字要正確**,不是「頁面上有出現數字」——
面板存在的意義就是那些數字可信;斷言若只驗「有沒有出現」,錯的數字照樣過關。

🔴 個資紅線:面板只做聚合。不得出現 email 樣式字串,
也不得有任何「誰下載了什麼」的欄位(設計文件 §2 原則 1、§4.6(b))。
"""

import re
from datetime import UTC, datetime, timedelta

from tests.conftest import auth, make_user, publish_and_approve

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
ZIP = b"PK\x03\x04" + b"\x00" * 60
PDF = b"%PDF-1.4\n" + b"\x00" * 60

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
PLATFORM_URLS = {"/account", "/login", "/"}


def _main(html: str) -> str:
    start = html.index("<main")
    return html[start : html.index("</main>", start)]


async def _admin(app, oidc, sub="sub-dash-admin"):
    await make_user(app, sub, admin=True)
    return auth(oidc.issue(sub))


# --- 權限(沿用既有規則,不另立)---------------------------------------------


async def test_匿名開總覽被送去登入(client):
    resp = await client.get("/admin", headers=BROWSER, follow_redirects=False)
    assert resp.status_code in (302, 303)


async def test_非管理員開總覽403(client, active_user):
    _, token = active_user
    resp = await client.get("/admin", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 403


async def test_管理員可看總覽(client, app, oidc):
    headers = await _admin(app, oidc)
    resp = await client.get("/admin", headers={**BROWSER, **headers})
    assert resp.status_code == 200
    assert "總覽" in resp.text


# --- 空狀態(全新平台不得炸)-------------------------------------------------


async def test_零資料時不炸且有空狀態文字(client, app, oidc):
    headers = await _admin(app, oidc)
    resp = await client.get("/admin", headers={**BROWSER, **headers})
    assert resp.status_code == 200
    body = _main(resp.text)
    assert "0" in body  # KPI 仍然顯示,只是都是 0
    assert "目前沒有需要處理的項目" in body


# --- 🔴 數字要正確 -----------------------------------------------------------


async def test_KPI數字等於實際資料(client, app, oidc, active_user):
    """造出已知資料,斷言頁面數字**等於**它們。"""
    _, token = active_user
    # 2 個專案(1 internal、1 private)
    for slug, visibility in (("dash-a", "internal"), ("dash-b", "private")):
        resp = await client.post(
            "/v1/projects",
            json={"slug": slug, "name": slug, "visibility": visibility},
            headers=auth(token),
        )
        assert resp.status_code == 201, resp.text

    # dash-a:1 個已發布版本(三類齊備 = 3 個檔案)
    release = await client.post(
        "/v1/projects/dash-a/releases", json={"version": "v1.0.0"}, headers=auth(token)
    )
    rid = release.json()["id"]
    for name, kind, body in (
        ("tool.bin", "binary", ELF),
        ("src.zip", "source", ZIP),
        ("notes.pdf", "doc", PDF),
    ):
        up = await client.put(
            f"/v1/releases/{rid}/artifacts/{name}?kind={kind}",
            content=body,
            headers=auth(token),
        )
        assert up.status_code == 201, up.text
    # T123:KPI 的 releases-published 指「真的可下載」的版本,所以要核准到底。
    await publish_and_approve(client, token, rid)

    # dash-b:1 個 draft
    await client.post(
        "/v1/projects/dash-b/releases", json={"version": "v0.1.0"}, headers=auth(token)
    )

    # 1 位待開通
    from app.models import UserStatus

    await make_user(app, "sub-dash-pending", status=UserStatus.pending)

    headers = await _admin(app, oidc)
    resp = await client.get("/admin", headers={**BROWSER, **headers})
    body = _main(resp.text)

    # 直接對「標籤 → 數值」的 data 屬性斷言,避免撞到頁面其他地方的數字
    def metric(name: str) -> str:
        match = re.search(rf'data-metric="{name}"[^>]*>([^<]*)<', body)
        assert match, f"找不到指標 {name}"
        return match.group(1).strip()

    assert metric("projects-total") == "2"
    assert metric("projects-private") == "1"
    assert metric("releases-published") == "1"
    assert metric("releases-draft") == "1"
    assert metric("artifacts-total") == "3"
    assert metric("artifacts-not-scanned") == "3"  # 掃毒未接,一律 not_scanned
    assert metric("users-pending") == "1"
    assert metric("downloads-total") == "0"


async def test_下載次數計入KPI(client, app, oidc, active_user):
    _, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "dl-proj", "name": "下載"}, headers=auth(token)
    )
    release = await client.post(
        "/v1/projects/dl-proj/releases", json={"version": "v1"}, headers=auth(token)
    )
    rid = release.json()["id"]
    put = await client.put(
        f"/v1/releases/{rid}/artifacts/tool.bin?kind=binary", content=ELF, headers=auth(token)
    )
    aid = put.json()["id"]
    for _ in range(3):
        got = await client.get(
            f"/v1/releases/{rid}/artifacts/{aid}/download", headers=auth(token)
        )
        assert got.status_code == 200

    headers = await _admin(app, oidc)
    body = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    match = re.search(r'data-metric="downloads-total"[^>]*>([^<]*)<', body)
    assert match and match.group(1).strip() == "3"


# --- 待辦清單:觸發才出現 -----------------------------------------------------


async def test_停滯draft超過門檻才列出(client, app, oidc, active_user):
    from app.dashboard import STALE_DRAFT_DAYS
    from app.models import Release

    _, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "stale-proj", "name": "停滯"}, headers=auth(token)
    )
    resp = await client.post(
        "/v1/projects/stale-proj/releases", json={"version": "v0.9"}, headers=auth(token)
    )
    rid = resp.json()["id"]

    headers = await _admin(app, oidc)
    fresh = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "v0.9" not in fresh, "剛建立的 draft 不該被當成停滯"

    # 把建立時間往前推到超過門檻
    import uuid as _uuid

    async with app.state.sessionmaker() as session:
        release = await session.get(Release, _uuid.UUID(rid))
        release.created_at = datetime.now(UTC) - timedelta(days=STALE_DRAFT_DAYS + 1)
        await session.commit()

    stale = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "v0.9" in stale
    assert "停滯" in stale


async def test_逼近配額的專案才列出(client, app, oidc, active_user, settings):
    from sqlalchemy import select

    from app.dashboard import QUOTA_WARN_RATIO
    from app.models import Project

    _, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "fat-proj", "name": "快滿了"}, headers=auth(token)
    )
    headers = await _admin(app, oidc)
    before = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "fat-proj" not in before

    async with app.state.sessionmaker() as session:
        project = (
            await session.execute(select(Project).where(Project.slug == "fat-proj"))
        ).scalar_one()
        project.total_bytes = int(settings.max_project_bytes * (QUOTA_WARN_RATIO + 0.05))
        await session.commit()

    after = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "fat-proj" in after


async def test_擁有者已停用的專案會被點名(client, app, oidc, active_user):
    from sqlalchemy import select

    from app.models import User, UserStatus

    owner, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "orphan-proj", "name": "孤兒"}, headers=auth(token)
    )
    headers = await _admin(app, oidc)
    before = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "orphan-proj" not in before

    async with app.state.sessionmaker() as session:
        user = (await session.execute(select(User).where(User.id == owner.id))).scalar_one()
        user.status = UserStatus.disabled
        await session.commit()

    after = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "orphan-proj" in after
    assert "轉移" in after, "應提示走轉移擁有權(F16)"


# --- 系統資訊 ---------------------------------------------------------------


async def test_顯示版本號與稽核保留期(client, app, oidc, settings):
    from app.version import APP_VERSION

    headers = await _admin(app, oidc)
    body = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert f"v{APP_VERSION}" in body
    assert str(settings.audit_retention_days) in body


# --- 🔴 紅線:個資、CSP、連結前綴 --------------------------------------------


async def test_面板不得出現個資或誰下載了什麼(client, app, oidc, active_user):
    _, token = active_user
    await client.post(
        "/v1/projects", json={"slug": "priv-proj", "name": "隱私"}, headers=auth(token)
    )
    headers = await _admin(app, oidc)
    body = (await client.get("/admin", headers={**BROWSER, **headers})).text
    assert "@" not in _main(body), "面板不得出現 email 樣式字串"
    for banned in ("下載者", "誰下載"):
        assert banned not in body


async def test_面板無inline樣式與腳本且連結帶前綴(client, app, oidc):
    headers = await _admin(app, oidc)
    resp = await client.get("/admin", headers={**BROWSER, **headers})
    assert "style=" not in resp.text, "CSP 擋 inline style"
    assert "<script>" not in resp.text
    for link in _LINK_RE.findall(resp.text):
        if link in PLATFORM_URLS:
            continue
        assert link.startswith(f"{PREFIX}/"), f"連結未帶前綴:{link!r}"


async def test_管理分頁有總覽入口(client, app, oidc):
    headers = await _admin(app, oidc)
    for path in ("/admin", "/admin/users", "/admin/audit"):
        resp = await client.get(path, headers={**BROWSER, **headers})
        assert f'href="{PREFIX}/admin"' in resp.text or "總覽" in resp.text


async def test_管理分頁沒有懸空連結(client, app, oidc):
    """🔴 分頁列上的每一個連結都必須指向真的存在的頁面。

    ⚠ **T134 改寫這一條,依 CI 紅線說明理由(不是放水)。**
    原本寫的是「`/admin/projects` 不得出現在分頁列」——那是 2026-07-31 的現況
    (該頁預留給 T72、還沒做),而 T134 把它做出來了,連結不再懸空。
    照舊斷言改程式,就會為了讓測試綠而把剛做好的頁面從導覽裡藏起來。

    🔴 **改寫的方向是讓它比原本更強**:原本只釘住「那一個路徑」,
    改成**逐一 GET 分頁列上的每個連結並要求 200** —— 下一次有人加分頁而頁面沒做,
    這條一樣會紅,不必回來改測試。
    """
    headers = await _admin(app, oidc)
    dash = await client.get("/admin", headers={**BROWSER, **headers})
    nav = dash.text.split('class="admin-tabs"')[1].split("</nav>")[0]
    links = re.findall(r'href="([^"]+)"', nav)
    assert links, "分頁列一個連結都沒有,這條測試會永遠綠(假綠)"
    for link in links:
        path = link[len(PREFIX) :] if link.startswith(PREFIX) else link
        resp = await client.get(path, headers={**BROWSER, **headers})
        assert resp.status_code == 200, f"分頁列的連結是懸空的:{link} → {resp.status_code}"


# --- T135 稽核保留期未生效的偵測 --------------------------------------------
#
# 🔴 起因不是「要掛 cron」,是**為什麼它躺了兩個月沒有人發現**。
# 稽核頁上那句「保留期 365 天」是**靜態文字** —— 它不管 cron 有沒有掛、
# 不管最舊那一筆是多久以前,永遠長一樣。
#
# 本服務看不到 crontab(它在容器外,而容器不該有主機的視野),所以偵測的是
# **同一件事的可觀測結果**:最舊一筆超過保留期。不論原因是 cron 沒掛、
# 旗標打錯、還是容器沒起來,症狀都會出現在這裡。
# **偵測結果而不是偵測原因** —— 原因有很多種,結果只有一個。


async def _seed_audit(app, *, days_ago: int):
    """塞一筆 days_ago 天前的稽核事件。"""
    import uuid as _uuid

    from app.models import AuditEvent

    async with app.state.sessionmaker() as session:
        session.add(
            AuditEvent(
                id=_uuid.uuid4(),
                occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
                action="user.login",
                actor_id=None,
                target_type="user",
                target_label="",
            )
        )
        await session.commit()


async def test_稽核最舊一筆超過保留期就列進待辦(client, app, oidc, settings):
    await _seed_audit(app, days_ago=settings.audit_retention_days + 45)

    headers = await _admin(app, oidc, "sub-retention-a")
    body = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "保留期" in body and "未生效" in body, "清理沒在跑,而畫面上看不出來"
    # 天數要寫出來 —— 只說「未生效」的話,看的人不知道差多少。
    assert str(settings.audit_retention_days + 45) in body


async def test_保留期內不列進待辦(client, app, oidc, settings):
    """🔴 守門:別做成常駐紅字。**常駐的紅字等於沒有紅字。**"""
    await _seed_audit(app, days_ago=max(settings.audit_retention_days - 30, 1))

    headers = await _admin(app, oidc, "sub-retention-b")
    body = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "未生效" not in body


async def test_稽核表為空時不列進待辦(client, app, oidc):
    """守門:新平台不該一開機就看到一個警告。"""
    headers = await _admin(app, oidc, "sub-retention-c")
    body = _main((await client.get("/admin", headers={**BROWSER, **headers})).text)
    assert "未生效" not in body
