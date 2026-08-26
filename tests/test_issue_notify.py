"""T99:回報通知信(寄給管理員)+ L1b 通知信箱快取。

Benny:「有人回報問題會寄信給 ADMIN,然後 ADMIN 要有後台介面」;
裁示三題:寄給**每一位平台管理員**、SMTP 走**公司內部 relay**、
**寄信失敗只寫 log,回報照樣送出**。

🔴 本檔釘住的是 SSO 契約 §4.2a **L1b 通知用信箱**的每一條約束 ——
把 email 放進業務庫是本專案紅線的例外,而例外的價值全在那些條件上:

1. 只從**本人** ID token 取得,**只信 `email_verified=true`**(未驗證是「聲稱」不是「事實」)
2. 每次登入覆寫(含覆寫成 NULL)
3. 🔴 **不得顯示在任何頁面**(含管理後台清單)—— email 是拿來投遞的,不是拿來辨識的
4. 🔴 **不進 log**
5. 🔴 不得作 join key、不得用於授權
6. **寄信失敗不得阻斷業務流程**
"""

import logging

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ADDR = "admin-notify@example.test"


async def _sub_login(client, oidc, sub, **claims):
    """讓某個 sub 走一次認證(順帶觸發 upsert_user 的快取寫入)。"""
    return await client.get("/v1/me", headers=auth(oidc.issue(sub, **claims)))


async def _read_notify_email(app, sub):
    from sqlalchemy import select

    from app.models import User

    async with app.state.sessionmaker() as session:
        return (
            await session.execute(select(User.notify_email).where(User.sub == sub))
        ).scalar_one()


# --- L1b:快取本身 -----------------------------------------------------------


async def test_已驗證的信箱才寫入(client, app, oidc):
    await make_user(app, "sub-verified")
    await _sub_login(client, oidc, "sub-verified", email=ADDR, email_verified=True)
    assert await _read_notify_email(app, "sub-verified") == ADDR


async def test_未驗證的信箱視同沒有(client, app, oidc):
    """🔴 L1b 第 13 條:未驗證的信箱可以是任何人打上去的字串。"""
    await make_user(app, "sub-unverified")
    await _sub_login(client, oidc, "sub-unverified", email=ADDR, email_verified=False)
    assert await _read_notify_email(app, "sub-unverified") is None

    # 連 email_verified 都沒帶的 token 也一樣視同沒有(缺席不等於通過)
    await make_user(app, "sub-noclaim")
    await _sub_login(client, oidc, "sub-noclaim", email=ADDR)
    assert await _read_notify_email(app, "sub-noclaim") is None


async def test_每次登入覆寫含覆寫成NULL(client, app, oidc):
    """IdP 那邊拿掉或改成未驗證,快取不得留著舊值。"""
    await make_user(app, "sub-overwrite")
    await _sub_login(client, oidc, "sub-overwrite", email=ADDR, email_verified=True)
    assert await _read_notify_email(app, "sub-overwrite") == ADDR

    await _sub_login(client, oidc, "sub-overwrite", email=ADDR, email_verified=False)
    assert await _read_notify_email(app, "sub-overwrite") is None


async def test_快取欄位不出現在API回應(client, app, oidc):
    """⚠ 本條的斷言在施工中修正過(依 CI 紅線說明):

    原本寫「`/v1/me` 的回應不得含 email」—— **那是錯的**。`MeOut.email` 來自
    **IdP claims**(schema 註解明寫「由 IdP 即時提供,不存在業務庫」),
    而且那是呼叫者**自己**的信箱;L1b 管的是**快取副本**,不是 token 裡的即時值。
    照原斷言改程式,等於為了讓測試綠而拆掉一個合法且既有的功能。

    改為釘住真正該釘的:**快取欄位名與 DB 來源不得出現在 API 表面**。
    """
    await make_user(app, "sub-api-email")
    resp = await _sub_login(client, oidc, "sub-api-email", email=ADDR, email_verified=True)
    assert resp.status_code == 200
    assert "notify_email" not in resp.text, "快取欄位不得成為 API 契約的一部分"

    schema = (await client.get("/openapi.json")).json()
    assert "notify_email" not in str(schema), "OpenAPI 也不得出現這個欄位"


async def test_信箱不出現在管理後台頁面(client, app, oidc):
    """🔴 L1b 第 14 條:含管理後台的使用者清單。顯示它只會多一份外洩面。"""
    await make_user(app, "sub-admin-page", admin=True)
    await _sub_login(client, oidc, "sub-admin-page", email=ADDR, email_verified=True)
    token = oidc.issue("sub-admin-page", email=ADDR, email_verified=True)
    for path in ("/admin", "/admin/users", "/issues"):
        resp = await client.get(path, headers={**BROWSER, **auth(token)})
        assert resp.status_code == 200, path
        assert ADDR not in resp.text, path


async def test_信箱不進log(client, app, oidc, caplog):
    """🔴 L1b 第 5 條。log 是最容易漏的那一個出口:它不在畫面上,沒人會去看。

    ⚠ 本條的**層級**在施工中修正過(依 CI 紅線說明):原本用 `DEBUG`,
    而 `DEBUG` 會打開 **aiosqlite 驅動層**的 SQL 參數輸出 —— 那裡當然有地址
    (`UPDATE users SET notify_email=? …` 的參數)。那不是我們的程式在記 log,
    用它當斷言只會逼人去關掉資料庫驅動的除錯功能。

    改為驗**正式環境的層級(INFO)**,並排除第三方 logger。
    🔴 由此得到一個必須寫進文件的結論:**正式環境不得把 `LOG_LEVEL` 開成 DEBUG**
    —— 開了,信箱就會隨 SQL 參數進 log。這條寫在 dev-log T99 與 `.env.example`。
    """
    await make_user(app, "sub-log-email", admin=True)
    with caplog.at_level(logging.INFO):
        await _sub_login(client, oidc, "sub-log-email", email=ADDR, email_verified=True)
        resp = await client.post(
            "/issues/new",
            data={"title": "log 測試", "body_markdown": "內容", "page_url": ""},
            headers={
                **BROWSER,
                **auth(oidc.issue("sub-log-email", email=ADDR, email_verified=True)),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    ours = [
        r.getMessage()
        for r in caplog.records
        if not r.name.startswith(("sqlalchemy", "aiosqlite", "httpx", "httpcore"))
    ]
    assert ADDR not in "\n".join(ours), "本服務自己的 log 不得出現收件地址"


# --- 寄信 -------------------------------------------------------------------


async def test_新回報寄給每一位有信箱的管理員(client, app, oidc, mailer):
    await make_user(app, "sub-adm-1", admin=True)
    await make_user(app, "sub-adm-2", admin=True)
    await make_user(app, "sub-adm-3", admin=True)  # 沒有信箱 → 不該被寄
    await _sub_login(client, oidc, "sub-adm-1", email="a1@example.test", email_verified=True)
    await _sub_login(client, oidc, "sub-adm-2", email="a2@example.test", email_verified=True)

    await make_user(app, "sub-reporter")
    resp = await client.post(
        "/issues/new",
        data={"title": "上傳頁按發布沒反應", "body_markdown": "重現步驟…", "page_url": "/upload/"},
        headers={**BROWSER, **auth(oidc.issue("sub-reporter"))},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    recipients = sorted(to for to, _subject, _body in mailer.sent)
    assert recipients == ["a1@example.test", "a2@example.test"], mailer.sent


async def test_信件內容有標題與連結但沒有回報全文(client, app, oidc, mailer):
    """🔴 信箱不是稽核紀錄:內容可能含使用者貼進來的敏感資訊。"""
    await make_user(app, "sub-adm-body", admin=True)
    await _sub_login(client, oidc, "sub-adm-body", email="a@example.test", email_verified=True)
    await make_user(app, "sub-rep-body")
    await client.post(
        "/issues/new",
        data={"title": "標題會出現", "body_markdown": "這段全文不該進信件", "page_url": ""},
        headers={**BROWSER, **auth(oidc.issue("sub-rep-body"))},
        follow_redirects=False,
    )
    assert mailer.sent, "應該寄出一封"
    _to, subject, body = mailer.sent[0]
    assert "標題會出現" in subject or "標題會出現" in body
    assert "/issues/" in body, "要有直達連結"
    assert "這段全文不該進信件" not in body


async def test_寄信失敗不阻斷回報(client, app, oidc, mailer):
    """🔴 裁示第三題:使用者親手寫的內容不能因為 SMTP 掛掉而丟失。"""
    from sqlalchemy import func, select

    from app.models import Issue

    mailer.fail = True
    await make_user(app, "sub-adm-fail", admin=True)
    await _sub_login(client, oidc, "sub-adm-fail", email="a@example.test", email_verified=True)
    await make_user(app, "sub-rep-fail")

    resp = await client.post(
        "/issues/new",
        data={"title": "SMTP 掛掉也要收下", "body_markdown": "內容", "page_url": ""},
        headers={**BROWSER, **auth(oidc.issue("sub-rep-fail"))},
        follow_redirects=False,
    )
    assert resp.status_code == 303, "回報必須成立"
    async with app.state.sessionmaker() as session:
        total = (await session.execute(select(func.count()).select_from(Issue))).scalar_one()
    assert total == 1


async def test_預設不寄信():
    """🔴 預設關閉:沒設好 SMTP 就完全不寄,而不是每次回報都在 log 裡噴錯。"""
    from app.config import Settings

    assert Settings.model_fields["mail_enabled"].default is False


# --- 清除工具與後台入口 ------------------------------------------------------


async def test_清除工具整批清空(app, client, oidc):
    """L1b 沿用 L1 第 7 條:必須有清除工具,且涵蓋孤兒快取。"""
    from tools.purge_notify_email import purge_notify_email

    await make_user(app, "sub-purge", admin=True)
    await _sub_login(client, oidc, "sub-purge", email=ADDR, email_verified=True)
    assert await _read_notify_email(app, "sub-purge") == ADDR

    async with app.state.sessionmaker() as session:
        cleared = await purge_notify_email(session)
    assert cleared >= 1
    assert await _read_notify_email(app, "sub-purge") is None


async def test_側欄管理段有回報管理(client, app, oidc):
    await make_user(app, "sub-side-adm", admin=True)
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-side-adm"))})
    assert "回報管理" in resp.text


async def test_一般使用者側欄沒有回報管理(client, app, oidc):
    await make_user(app, "sub-side-member")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-side-member"))})
    assert "回報管理" not in resp.text


async def test_側欄不會同時反白兩個項目(client, app, oidc):
    """🐛 施工中由截圖抓到:「回報問題」與「回報管理」都指向 `/issues*`。

    兩者的 active 條件原本都是 `startswith('/issues')` → **同時反白**。
    側欄同時亮兩個,等於沒有指示現在在哪。
    """
    import re

    await make_user(app, "sub-active-one", admin=True)
    token = auth(oidc.issue("sub-active-one"))
    for path in ("/issues", "/issues/new", "/", "/admin"):
        resp = await client.get(path, headers={**BROWSER, **token})
        assert resp.status_code == 200, path
        actives = re.findall(r'class="side-link[^"]*\bactive\b[^"]*"', resp.text)
        assert len(actives) <= 1, f"{path} 有 {len(actives)} 個反白項目"
