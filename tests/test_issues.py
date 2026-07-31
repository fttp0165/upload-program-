"""T77:問題回報系統(第一期)——建立、清單、詳情、討論串、狀態機、權限。

依施工計畫書 §4.3 的權限矩陣與 §5 的測試計畫。

🔴 三條紅線:
1. **非本人非管理員一律 404**(不是 403)——與 private 專案同一個立場:不洩漏存在。
2. **待開通者可以回報**——他們最可能遇到問題,卻是最沒有管道的人。
3. **狀態只有管理員能改**;每次變更都要有稽核。
"""

import re

from sqlalchemy import func, select

from app.models import UserStatus
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"


def _main(html: str) -> str:
    """只取 <main> 內容區。

    版型自己有 <script src=...>(Bootstrap),整頁比對會撞到它——
    要驗的是「使用者的內容有沒有變成活的標籤」,不是版型有沒有腳本。
    """
    start = html.index("<main")
    return html[start : html.index("</main>", start)]


async def _create(client, token, title="按下上傳沒反應", body="重現步驟:\n\n1. 開專案頁"):
    return await client.post(
        "/issues/new",
        data={"title": title, "body_markdown": body, "page_url": "/upload/projects/x"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )


async def _issue_id(resp) -> str:
    assert resp.status_code in (302, 303), resp.text
    return resp.headers["location"].rstrip("/").split("/")[-1]


async def _audit_count(app, action: str) -> int:
    from app.models import AuditEvent

    async with app.state.sessionmaker() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(AuditEvent).where(AuditEvent.action == action)
                )
            ).scalar()
            or 0
        )


# --- 建立 -------------------------------------------------------------------


async def test_匿名者被送去登入(client):
    resp = await client.get("/issues/new", headers=BROWSER, follow_redirects=False)
    assert resp.status_code in (302, 303)


async def test_待開通者也能回報(client, app, oidc):
    """🔴 他們最可能遇到問題,卻是最沒有管道的人——擋掉等於聽不到最需要的回饋。"""
    await make_user(app, "sub-pending-reporter", status=UserStatus.pending)
    token = oidc.issue("sub-pending-reporter")
    resp = await _create(client, token)
    assert resp.status_code in (302, 303), resp.text


async def test_建立後自動帶入版本與頁面(client, active_user, app):
    from app.version import APP_VERSION

    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert page.status_code == 200
    assert APP_VERSION in page.text, "使用者不會記得自己看到問題時是哪一版,系統要自己記"
    assert "/upload/projects/x" in page.text


async def test_標題為空不建立(client, active_user):
    _, token = active_user
    resp = await client.post(
        "/issues/new",
        data={"title": "", "body_markdown": "內容"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 200, "應回到表單顯示訊息,而不是丟一頁錯誤"
    assert "請填寫" in resp.text


async def test_建立寫稽核(client, active_user, app):
    _, token = active_user
    await _create(client, token)
    assert await _audit_count(app, "issue.create") == 1


# --- 🔴 XSS(端到端,不只單元層)---------------------------------------------


async def test_回報內容的HTML不會被執行(client, active_user):
    _, token = active_user
    evil = "<script>alert(1)</script> 與 <img src=x onerror=alert(2)>"
    issue_id = await _issue_id(await _create(client, token, title="<b>粗</b>", body=evil))

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    body = _main(page.text)
    # 🔴 危險的是**活的標籤**,不是那串字被看到:使用者打了什麼就顯示什麼(已逸出),
    # 但頁面上不得出現任何我方沒產生的標籤。
    assert "<script" not in body
    assert "<img src=x" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body, "應以純文字原樣顯示"
    assert "<b>粗</b>" not in body, "標題也要逸出"


async def test_外部圖片不會被載入(client, active_user):
    _, token = active_user
    issue_id = await _issue_id(
        await _create(client, token, body="![x](https://evil.example/pixel.png)")
    )
    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "evil.example" not in page.text


# --- 權限:看得到誰的 --------------------------------------------------------


async def test_看不到別人的回報且回404(client, app, oidc, active_user):
    _, owner_token = active_user
    issue_id = await _issue_id(await _create(client, owner_token))

    await make_user(app, "sub-other-user")
    other = oidc.issue("sub-other-user")
    resp = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(other)})
    assert resp.status_code == 404, "🔴 用 404 而不是 403——不洩漏這件回報存不存在"


async def test_管理員看得到所有回報(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    await make_user(app, "sub-issue-admin", admin=True)
    admin = oidc.issue("sub-issue-admin")
    resp = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(admin)})
    assert resp.status_code == 200


async def test_清單只有自己的_管理員看全部(client, app, oidc, active_user):
    _, token = active_user
    await _create(client, token, title="我的問題")

    await make_user(app, "sub-list-other")
    other = oidc.issue("sub-list-other")
    mine = await client.get("/issues", headers={**BROWSER, **auth(other)})
    assert "我的問題" not in mine.text

    await make_user(app, "sub-list-admin", admin=True)
    admin = oidc.issue("sub-list-admin")
    all_issues = await client.get("/issues", headers={**BROWSER, **auth(admin)})
    assert "我的問題" in all_issues.text


async def test_清單空狀態不炸(client, app, oidc):
    await make_user(app, "sub-empty-list")
    resp = await client.get("/issues", headers={**BROWSER, **auth(oidc.issue("sub-empty-list"))})
    assert resp.status_code == 200
    assert "還沒有" in resp.text


# --- 討論串 -----------------------------------------------------------------


async def test_回報者可以補充說明(client, active_user, app):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    resp = await client.post(
        f"/issues/{issue_id}/comments",
        data={"body_markdown": "補充:換 Chrome 也一樣"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "換 Chrome 也一樣" in page.text
    assert await _audit_count(app, "issue.comment") == 1


async def test_管理員回覆標為官方(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    await make_user(app, "sub-reply-admin", admin=True)
    admin = oidc.issue("sub-reply-admin")
    await client.post(
        f"/issues/{issue_id}/comments",
        data={"body_markdown": "已修正,請再試一次"},
        headers={**BROWSER, **auth(admin)},
        follow_redirects=False,
    )
    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "平台回覆" in page.text, "使用者要一眼看出哪一則是官方回應"


async def test_無關的人不能回覆(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    await make_user(app, "sub-nosy")
    resp = await client.post(
        f"/issues/{issue_id}/comments",
        data={"body_markdown": "路過"},
        headers={**BROWSER, **auth(oidc.issue("sub-nosy"))},
        follow_redirects=False,
    )
    assert resp.status_code == 404


# --- 狀態機 -----------------------------------------------------------------


async def test_只有管理員能改狀態(client, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    resp = await client.post(
        f"/issues/{issue_id}/status",
        data={"status": "closed"},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )
    assert resp.status_code == 403, "回報者不能自己把問題關掉"


async def test_管理員改狀態並寫稽核(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))

    await make_user(app, "sub-status-admin", admin=True)
    admin = oidc.issue("sub-status-admin")
    resp = await client.post(
        f"/issues/{issue_id}/status",
        data={"status": "in_progress"},
        headers={**BROWSER, **auth(admin)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert await _audit_count(app, "issue.status_change") == 1

    page = await client.get(f"/issues/{issue_id}", headers={**BROWSER, **auth(token)})
    assert "處理中" in page.text


async def test_非法狀態值被拒(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))
    await make_user(app, "sub-badstatus-admin", admin=True)

    resp = await client.post(
        f"/issues/{issue_id}/status",
        data={"status": "deleted-everything"},
        headers={**BROWSER, **auth(oidc.issue("sub-badstatus-admin"))},
        follow_redirects=False,
    )
    assert resp.status_code in (400, 422)


async def test_關閉時記錄關閉者與時間(client, app, oidc, active_user):
    import uuid as _uuid

    from app.models import Issue

    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))
    await make_user(app, "sub-closer", admin=True)

    await client.post(
        f"/issues/{issue_id}/status",
        data={"status": "closed"},
        headers={**BROWSER, **auth(oidc.issue("sub-closer"))},
        follow_redirects=False,
    )
    async with app.state.sessionmaker() as session:
        issue = await session.get(Issue, _uuid.UUID(issue_id))
        assert issue.closed_at is not None
        assert issue.closed_by_id is not None


# --- 入口與版型 --------------------------------------------------------------


async def test_回報入口指向回報表單(client, app, oidc):
    """T66/T75 的教訓:沒有入口的功能等於不存在。"""
    await make_user(app, "sub-entry-check")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-entry-check"))})
    assert f'href="{PREFIX}/issues/new"' in resp.text


async def test_回報頁連結都帶前綴且無inline樣式(client, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))
    for path in ("/issues/new", "/issues", f"/issues/{issue_id}"):
        resp = await client.get(path, headers={**BROWSER, **auth(token)})
        assert "style=" not in resp.text, f"{path} 不得有 inline style(CSP)"
        for link in re.findall(r'\b(?:href|action)\s*=\s*"([^"]*)"', resp.text):
            if link in {"/account", "/login", "/"}:
                continue
            assert link.startswith(f"{PREFIX}/"), f"{path} 的連結未帶前綴:{link}"


# --- T79:總覽待辦與清除工具 --------------------------------------------------


async def test_總覽顯示未處理回報數(client, app, oidc, active_user):
    """本平台沒有 email 也沒有排程器——這個數字是管理員唯一的提醒機制。"""
    _, token = active_user
    await _create(client, token, title="待處理的問題")

    await make_user(app, "sub-todo-admin", admin=True)
    admin = oidc.issue("sub-todo-admin")
    page = await client.get("/admin", headers={**BROWSER, **auth(admin)})
    assert "未處理的回報" in page.text
    match = re.search(r'data-metric="issues-open"[^>]*>([^<]*)<', page.text)
    assert match and match.group(1).strip() == "1"


async def test_沒有未處理回報時總覽不顯示該項(client, app, oidc):
    await make_user(app, "sub-todo-admin2", admin=True)
    page = await client.get(
        "/admin", headers={**BROWSER, **auth(oidc.issue("sub-todo-admin2"))}
    )
    assert "未處理的回報" not in page.text


async def test_已關閉的回報不計入未處理(client, app, oidc, active_user):
    _, token = active_user
    issue_id = await _issue_id(await _create(client, token))
    await make_user(app, "sub-close-admin", admin=True)
    admin = oidc.issue("sub-close-admin")
    await client.post(
        f"/issues/{issue_id}/status",
        data={"status": "closed"},
        headers={**BROWSER, **auth(admin)},
        follow_redirects=False,
    )
    page = await client.get("/admin", headers={**BROWSER, **auth(admin)})
    assert "未處理的回報" not in page.text


async def test_清除工具只刪已關閉滿保存期者(app, active_user, client):
    """🔴 刪資料的工具:兩側都要驗——該刪的刪、不該刪的一個都不能碰。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from app.models import Issue
    from tools.purge_issues import purge_closed_issues

    _, token = active_user
    old_closed = await _issue_id(await _create(client, token, title="很久以前關掉的"))
    recent_closed = await _issue_id(await _create(client, token, title="最近關掉的"))
    still_open = await _issue_id(await _create(client, token, title="還開著的"))

    async with app.state.sessionmaker() as session:
        from app.models import IssueStatus

        now = datetime.now(UTC)
        for issue_id, closed_at in (
            (old_closed, now - timedelta(days=400)),
            (recent_closed, now - timedelta(days=10)),
        ):
            issue = await session.get(Issue, _uuid.UUID(issue_id))
            issue.status = IssueStatus.closed
            issue.closed_at = closed_at
        await session.commit()

    async with app.state.sessionmaker() as session:
        removed = await purge_closed_issues(session, app.state.storage, retention_days=365)
        await session.commit()
    assert removed == 1, "只有超過保存期且已關閉的那一件該被刪"

    async with app.state.sessionmaker() as session:
        assert await session.get(Issue, _uuid.UUID(old_closed)) is None
        assert await session.get(Issue, _uuid.UUID(recent_closed)) is not None
        assert await session.get(Issue, _uuid.UUID(still_open)) is not None


async def test_教學頁說明回報流程與不寄信(client):
    resp = await client.get("/help", headers=BROWSER)
    assert "回報問題" in resp.text
    assert "不會寄信" in resp.text or "不寄信" in resp.text
