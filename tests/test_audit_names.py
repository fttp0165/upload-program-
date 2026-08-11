"""T84 稽核紀錄顯示使用者名稱。

裁示(2026-08-05 Benny):「稽核紀錄顯示使用者名稱」——原本是兩排 UUID,
實務上認不出「誰把誰開通了」,而**認不出人的稽核紀錄等於沒有稽核紀錄**。

🔴 本檔釘住的邊界,每一條都比「顯示名字」本身重要:

1. **名字只上後台 HTML,不上 JSON API。** 契約 §4.2a L1 給的例外是「僅管理後台顯示」;
   `AuditEventOut` 的 docstring 也早就寫明「稽核不是繞過個資紅線的後門」。
   API 一旦帶名字,任何拿得到 admin token 的程式都能整批匯出姓名對照表。
2. **查詢數不隨列數成長。** 設計文件《管理員後台與數據面板》§2 的紅線;
   每列各查一次名字就是 N+1,而且會隨分頁大小惡化。
3. **查不到名字就顯示 UUID,不得空白。** `name` claim 可能不存在(models.py 註明
   「這是會真的走到的路徑」),目標使用者也可能已被刪除(`target_id` 刻意不是外鍵)。
4. **名字來自 IdP,不是我方可控字串**——必須逸出。
"""

import uuid

from sqlalchemy import event

from app.audit import AuditAction, record
from app.models import UserStatus
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


async def _log(app, *, actor_id=None, target_id=None, target_type="user", label=""):
    """直接寫一筆稽核事件——測的是顯示端,不繞經業務流程。"""
    async with app.state.sessionmaker() as session:
        record(
            session,
            action=AuditAction.user_activate,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            target_label=label,
        )
        await session.commit()


async def _page(client, token):
    resp = await client.get("/admin/audit", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    return resp.text


# --- 1. 顯示名字 -------------------------------------------------------------


async def test_操作者顯示名稱(client, app, oidc):
    """🔴 名字要由**登入當下的 name claim** 寫進去,不能手動塞進資料庫再登入。

    §4.2a 規定每次登入以本人 token 的 `name` 覆寫快取(claim 不存在就覆寫成 NULL)。
    手動塞值再用不帶 name 的 token 登入,值會在讀頁面之前就被清掉——
    測試會紅得莫名其妙,更糟的是**反向的測試會假綠**(見 test_JSON_API不含名稱)。
    """
    admin = await make_user(app, "sub-audit-admin", admin=True)
    await _log(app, actor_id=admin.id, target_id=admin.id)

    body = await _page(client, oidc.issue("sub-audit-admin", name="王小明"))
    assert "王小明" in body


async def test_目標使用者顯示名稱(client, app, oidc):
    admin = await make_user(app, "sub-audit-admin2", admin=True)
    target = await make_user(app, "sub-audit-target", status=UserStatus.pending)
    target.display_name_cache = "李小華"
    async with app.state.sessionmaker() as session:
        await session.merge(target)
        await session.commit()

    await _log(app, actor_id=admin.id, target_id=target.id)
    body = await _page(client, oidc.issue("sub-audit-admin2"))
    assert "李小華" in body


# --- 2. 查不到就退回 UUID(不得空白)-----------------------------------------


async def test_沒有名稱快取時顯示UUID(client, app, oidc):
    """🔴 `name` claim 可能不存在——這是會真的走到的路徑,不是防禦性假設。"""
    admin = await make_user(app, "sub-audit-noname", admin=True)
    await _log(app, actor_id=admin.id, target_id=admin.id)

    body = await _page(client, oidc.issue("sub-audit-noname"))
    assert str(admin.id) in body, "沒有名字時必須顯示 UUID,不能留白"


async def test_目標已被刪除時顯示原本的UUID(client, app, oidc):
    """🔴 `target_id` 刻意不是外鍵(「查不回去也無妨」),查無此人不得炸頁。"""
    admin = await make_user(app, "sub-audit-gone", admin=True)
    ghost = uuid.uuid4()
    await _log(app, actor_id=admin.id, target_id=ghost)

    body = await _page(client, oidc.issue("sub-audit-gone"))
    assert str(ghost) in body


async def test_非使用者目標不受影響(client, app, oidc):
    """target_type 是 project / artifact 時,target_id 不是 user id,不得亂查亂顯示。"""
    admin = await make_user(app, "sub-audit-proj", admin=True)
    other = uuid.uuid4()
    await _log(app, actor_id=admin.id, target_id=other, target_type="project", label="my-tool")

    body = await _page(client, oidc.issue("sub-audit-proj"))
    assert str(other) in body
    assert "my-tool" in body


# --- 3. 🔴 名字不得外洩到 API ------------------------------------------------


async def test_JSON_API不含名稱(client, app, oidc):
    """🔴 §4.2a L1 的例外只涵蓋「管理後台顯示」。

    API 一旦帶名字,任何拿得到 admin token 的程式都能整批匯出姓名對照表——
    那正是「業務庫只存 sub」要防的事。
    """
    admin = await make_user(app, "sub-audit-api", admin=True)
    await _log(app, actor_id=admin.id, target_id=admin.id)

    # 名字必須由登入寫進去,否則快取是空的、這條會**假綠**——
    # 一個「不該出現名字」的測試在根本沒有名字的情況下通過,等於沒有測。
    token = oidc.issue("sub-audit-api", name="陳大文")
    assert "陳大文" in await _page(client, token), "前提:後台頁確實看得到名字"

    resp = await client.get("/v1/admin/audit", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert "陳大文" not in resp.text
    assert "display_name" not in resp.text


# --- 4. 🔴 查詢數不隨列數成長 ------------------------------------------------


async def test_查詢數不隨列數成長(client, app, oidc):
    """🔴 每列各查一次名字就是 N+1,而且會隨分頁大小惡化(設計文件 §2:查詢數固定)。"""
    admin = await make_user(app, "sub-audit-n1", admin=True)
    token = oidc.issue("sub-audit-n1")

    await _log(app, actor_id=admin.id, target_id=admin.id)
    one = await _count_queries(client, app, token)

    for _ in range(9):
        target = await make_user(app, f"sub-audit-n1-{uuid.uuid4().hex[:8]}")
        await _log(app, actor_id=admin.id, target_id=target.id)
    many = await _count_queries(client, app, token)

    assert many == one, f"查詢數隨列數成長({one} → {many}),這是 N+1"


async def _count_queries(client, app, token) -> int:
    """數這一次請求打了幾條 SQL。用 engine 事件,不動受測程式碼。"""
    counter = {"n": 0}

    def _before(conn, cursor, statement, *args):
        counter["n"] += 1

    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", _before)
    try:
        await _page(client, token)
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["n"]


# --- 5. 🔴 名字來自 IdP,必須逸出 --------------------------------------------


async def test_名稱中的HTML被逸出(client, app, oidc):
    """名字是 IdP 給的 claim,不是我方可控字串——autoescape 必須生效。"""
    admin = await make_user(app, "sub-audit-xss", admin=True)
    await _log(app, actor_id=admin.id, target_id=admin.id)

    body = await _page(client, oidc.issue("sub-audit-xss", name="<script>alert(1)</script>"))
    main = body[body.index("<main") : body.index("</main>")]
    assert "<script>alert(1)</script>" not in main, "名字中的標籤必須被逸出"
    assert "&lt;script&gt;" in main
