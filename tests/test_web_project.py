"""T42 專案頁:資訊 + 最新版本置頂(F72)。

兩條紅線:
1. **匿名訪客的回應不得洩漏專案是否存在**——存在的 private 專案與不存在的 slug
   必須回一模一樣的東西。做法是匿名一律不查詢,結構上就不可能洩漏。
2. **掃毒狀態要誠實顯示**:這一頁是使用者按下載之前最後看到的畫面,
   `not_scanned` 必須看得到,不能只藏在 API 回應裡。
"""

import re

from tests.conftest import auth, complete_kinds, make_user, publish_and_approve

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

# 契約 §2.1 的平台層短網址:由 gateway 302 轉址,**刻意不帶各 App 的前綴**
# (加上前綴會變成一條不存在的路徑)。這是下面「所有連結帶前綴」那條紅線的
# **具名例外**——不是把斷言放寬,例外本身在 test_sso_contract.py 有測試保護。
# T67:平台入口(`/`)也是平台層網址,同一類具名例外。
# 🔴 白名單納入 `/` 會讓「漏掉 url() 的首頁連結」逃過這條檢查——
#    補償斷言在 test_portal_link.py(每條 `/` 都必須帶 nav-exit / side-exit 標記)。
PLATFORM_URLS = {"/account", "/login", "/"}



def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


async def _project(client, token, slug="demo-tool", name="示範工具", **extra):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": name, "summary": "一個示範用的小工具", **extra},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _publish(client, token, slug, version, *, filename="tool.bin", notes="首版說明"):
    release = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": version, "notes": notes},
        headers=auth(token),
    )
    assert release.status_code == 201, release.text
    release_id = release.json()["id"]
    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/{filename}?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text
    await complete_kinds(client, token, release_id)
    # T123:發布 = 送審;本檔測的是專案頁上的「最新已發布版本」,要核准後才算數。
    await publish_and_approve(client, token, release_id)
    return release_id, up.json()["id"]


# --- 基本內容 ---------------------------------------------------------------


async def test_專案頁顯示基本資訊(client, active_user):
    _, token = active_user
    await _project(client, token)
    await client.put(
        "/v1/projects/demo-tool/tags", json={"tags": ["python"]}, headers=auth(token)
    )

    resp = await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "示範工具" in body
    assert "demo-tool" in body
    assert "一個示範用的小工具" in body
    assert "python" in body


async def test_最新版本的說明與檔案都看得到(client, active_user):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0", notes="這是第一版的說明")

    resp = await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})
    body = resp.text
    assert "v1.0.0" in body
    assert "這是第一版的說明" in body
    assert "tool.bin" in body


async def test_最新版本置頂在專案資訊之前(client, active_user):
    """🔴 F72 的驗收重點:來這頁的人十之八九是要抓最新版,不該先捲過一堆 metadata。"""
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    assert "下載" in body and "專案資訊" in body
    assert body.index("下載") < body.index("專案資訊"), "最新版本的下載按鈕必須排在專案資訊之前"


async def test_只取已發布版本不取draft(client, active_user):
    """draft 是作者的工作區,不是給人抓的。"""
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")

    draft = await client.post(
        "/v1/projects/demo-tool/releases",
        json={"version": "v2.0.0-draft", "notes": "還沒好"},
        headers=auth(token),
    )
    assert draft.status_code == 201

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    assert "v1.0.0" in body
    assert "v2.0.0-draft" not in body
    assert "還沒好" not in body


async def test_尚無已發布版本時顯示提示而不是錯誤頁(client, active_user):
    _, token = active_user
    await _project(client, token)

    resp = await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, "沒有版本不是錯誤"
    assert "尚未發布" in resp.text or "尚無" in resp.text


# --- 🔴 可見性與不洩漏存在 --------------------------------------------------


async def test_private專案對非成員回404(client, active_user, app, oidc):
    """🔴 回 404 而非 403——403 等於承認「這個專案存在,只是你不能看」。"""
    _, owner_token = active_user
    await _project(client, owner_token, "secret-tool", "機密工具", visibility="private")

    await make_user(app, "sub-outsider42")
    outsider = oidc.issue("sub-outsider42")
    resp = await client.get("/projects/secret-tool", headers={**BROWSER, **auth(outsider)})
    assert resp.status_code == 404
    assert "機密工具" not in resp.text


async def test_成員看得到private專案(client, active_user):
    _, token = active_user
    await _project(client, token, "secret-tool", "機密工具", visibility="private")
    resp = await client.get("/projects/secret-tool", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "機密工具" in resp.text


async def test_匿名訪客的回應不洩漏專案是否存在(client, active_user):
    """🔴 若「存在就顯示登入提示、不存在就 404」,那兩種回應本身就是答案。

    做法是匿名一律不查詢,所以兩邊必然相同——這是結構保證,不是巧合。
    """
    _, owner_token = active_user
    await _project(client, owner_token, "secret-tool", "機密工具", visibility="private")

    exists = await client.get("/projects/secret-tool", headers=BROWSER)
    missing = await client.get("/projects/no-such-project-at-all", headers=BROWSER)

    assert exists.status_code == missing.status_code
    assert "機密工具" not in exists.text
    # 兩份頁面只差在網址本身,內容應完全一致
    assert exists.text.replace("secret-tool", "X") == missing.text.replace(
        "no-such-project-at-all", "X"
    )


async def test_待開通者看到與API相同的指引文案(client, app, oidc):
    from app.models import UserStatus
    from app.problems import pending_activation

    await make_user(app, "sub-pending-proj", status=UserStatus.pending)
    token = oidc.issue("sub-pending-proj")

    resp = await client.get("/projects/anything", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert pending_activation().detail in resp.text


# --- 🔴 下載與校驗資訊 ------------------------------------------------------


async def test_掃毒狀態誠實顯示(client, active_user):
    """🔴 紅線:掃毒未接上前必須誠實標示 not_scanned。

    這一頁是使用者按下載之前最後看到的畫面,不能只把它藏在 API 回應裡。
    """
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    assert "not_scanned" in body or "未掃描" in body


async def test_顯示校驗資訊與下載次數(client, active_user):
    import hashlib

    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    assert hashlib.sha256(ELF).hexdigest()[:16] in body, "要顯示 SHA-256 供自行校驗"
    assert str(len(ELF)) in body or "位元組" in body or "bytes" in body.lower()
    assert "下載次數" in body or "次下載" in body


async def test_下載按鈕指向正確的下載網址(client, active_user):
    _, token = active_user
    await _project(client, token)
    release_id, artifact_id = await _publish(client, token, "demo-tool", "v1.0.0")

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    expect = f"{PREFIX}/v1/releases/{release_id}/artifacts/{artifact_id}/download"
    assert expect in body, "下載按鈕要精確指向這一版的這個檔"


async def test_頁面提供F26的固定連結(client, active_user):
    """T35 做出來的固定連結,不放在使用者看得到的地方就沒人會用。"""
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")

    body = (await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})).text
    assert "/releases/latest/artifacts/tool.bin/download" in body


# --- 與首頁的銜接 -----------------------------------------------------------


async def test_首頁的專案卡片連到專案頁(client, active_user):
    """T41 遺留 #1:卡片當時刻意沒有連結,因為這一頁還不存在。"""
    _, token = active_user
    await _project(client, token)

    body = (await client.get("/", headers={**BROWSER, **auth(token)})).text
    assert f'href="{PREFIX}/projects/demo-tool"' in body


# --- T40 的紅線維持 ---------------------------------------------------------


async def test_專案頁所有連結帶前綴且無絕對網址(client, active_user):
    _, token = active_user
    await _project(client, token)
    await _publish(client, token, "demo-tool", "v1.0.0")
    await client.put(
        "/v1/projects/demo-tool/tags", json={"tags": ["python"]}, headers=auth(token)
    )

    resp = await client.get("/projects/demo-tool", headers={**BROWSER, **auth(token)})
    found = _links(resp.text)
    assert found
    for link in found:
        if link in PLATFORM_URLS:
            continue
        assert link.startswith(f"{PREFIX}/"), link
        assert not link.startswith(("http://", "https://", "//")), link


async def test_專案頁對使用者可控內容逸出(client, active_user):
    _, token = active_user
    await client.post(
        "/v1/projects",
        json={
            "slug": "evil-tool",
            "name": '<script>alert("name")</script>',
            "summary": '<img src=x onerror=alert(1)>',
        },
        headers=auth(token),
    )
    resp = await client.get("/projects/evil-tool", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200
    assert "<script>alert(" not in resp.text
    assert "<img src=x onerror" not in resp.text
    assert "&lt;script&gt;" in resp.text


# --- T118 專案頁的擁有者欄位 ------------------------------------------------
#
# Benny:「沒有顯示作者的名字」。專案頁原本完全沒有擁有者欄位——對程式分享平台
# 來說是核心缺口:要下載一支執行檔,「這是誰放的」是判斷信不信任它的第一個依據,
# 尤其掃毒還沒接上。
#
# 🔴 但名字現在放不上去:名字的唯一來源 display_name_cache 受契約 §4.2a L1 管,
# 而那條限制是我方自己寫進申請書的——「僅管理後台顯示,不出現在一般使用者可見
# 頁面」。專案頁正是一般使用者可見頁面。要顯示名字得先送申請擴大用途,
# 不能自行放寬(偏離平台契約的權限不在本專案)。
#
# 所以過渡做法是顯示 sub 前 8 碼:sub 是不透明識別碼、不是個資,
# 而且業務庫本來就只存它。下面三條把「欄位要有」與「名字不准漏」一起釘住。

async def test_專案頁顯示擁有者識別碼(client, app, oidc):
    """🔴 由**另一個人**來看:檢視者自己的識別碼本來就會出現在導覽列,
    同一人看自己的專案測不出「擁有者欄位有沒有做出來」。
    """
    await make_user(app, "sub-owner-t97-abcdef123456")
    owner_token = oidc.issue("sub-owner-t97-abcdef123456")
    await _project(client, owner_token, slug="owned-tool", name="有主人的工具")

    await make_user(app, "sub-visitor-t97")
    visitor = oidc.issue("sub-visitor-t97")

    resp = await client.get(f"{PREFIX}/projects/owned-tool", headers={**BROWSER, **auth(visitor)})
    assert resp.status_code == 200
    assert "擁有者" in resp.text
    assert "sub-owne" in resp.text, "應顯示擁有者 sub 前 8 碼供對照"


async def test_專案頁顯示名字但不得出現email(client, app, oidc):
    """⚠ **T132 改寫這一條,依 CI 紅線說明理由(不是放水)。**

    原本斷言的是「名字不得出現在一般使用者頁面」—— 那釘的是契約 **v3.3**。
    **Benny 2026-08-31 裁決准了我方 2026-08-12 的申請,契約升 v3.4**,
    用途擴及「專案 / 版本 / 內容頁的擁有者、作者、上傳者辨識」。
    照舊斷言改程式,等於讓一條**已經被裁決放寬的限制繼續生效** ——
    那不是嚴謹,是拿過期文件當現況(第七條存在的理由)。

    🔴 **反向斷言不刪除,改釘沒有放寬的那一半**:
    L1b(通知用信箱)一個字未動 —— 它仍然「**不得顯示在任何頁面**」。
    姓名回答「這是誰做的」,信箱回答「怎麼聯絡他」,後者放上頁面沒有業務理由。

    兩個原有的講究保留:
    1. 名字走**登入路徑**寫入(§4.2a 每次登入覆寫),手動塞會被下次登入清成 NULL
       ——那樣測到的是「本來就沒有名字」,是假綠(T84 的教訓);
    2. 由**別人**來看,否則導覽列會顯示檢視者自己的名字。
    """
    await make_user(app, "sub-named-t97")
    owner_token = oidc.issue(
        "sub-named-t97", name="林小明", email="ming@sporton.com.tw", email_verified=True
    )
    await _project(client, owner_token, slug="named-owner-tool", name="工具")

    # 前提斷言:名字真的進了快取,否則下面的反向斷言毫無意義
    await make_user(app, "sub-admin-t97", admin=True)
    admin_token = oidc.issue("sub-admin-t97")
    admin = await client.get(f"{PREFIX}/admin/users", headers={**BROWSER, **auth(admin_token)})
    assert "林小明" in admin.text, "前提不成立:名字沒進快取,反向斷言會假綠"

    await make_user(app, "sub-visitor-t97b")
    visitor = oidc.issue("sub-visitor-t97b")
    resp = await client.get(
        f"{PREFIX}/projects/named-owner-tool", headers={**BROWSER, **auth(visitor)}
    )
    assert resp.status_code == 200
    assert "林小明" in resp.text, "契約 v3.4 已准:專案頁得顯示擁有者名字"
    assert "@" not in resp.text.split("<main")[-1], (
        "🔴 L1b 一個字未放寬:通知用信箱不得顯示在任何頁面"
    )


async def test_專案頁不得洩漏擁有者完整sub(client, app, oidc):
    """截斷值供人眼辨識即可;完整 UUID 對人眼沒有更多幫助,少給少一分外洩面。"""
    sub = "sub-full-value-must-not-leak-t97"
    await make_user(app, sub)
    owner_token = oidc.issue(sub)
    await _project(client, owner_token, slug="trunc-tool", name="截斷測試")

    await make_user(app, "sub-visitor-t97c")
    visitor = oidc.issue("sub-visitor-t97c")
    resp = await client.get(f"{PREFIX}/projects/trunc-tool", headers={**BROWSER, **auth(visitor)})
    assert resp.status_code == 200
    assert sub not in resp.text, "🔴 不得輸出擁有者的完整 sub"


# --- T133 主要動作改成明顯的按鈕 --------------------------------------------
#
# Benny 截圖回報:專案頁底部「建立新版本 · 查看版本歷史」是兩個純文字連結,
# 而那是這一頁唯一「往下走」的動作。
#
# 🔴 盤點時發現的才是重點:那一段**沒有任何權限判斷**,viewer 也看得到
# 「建立新版本」,點進去會 403。現在它不明顯所以代價小;**做成醒目的主要按鈕之後,
# 它就從「不明顯的連結」變成「醒目的陷阱」** —— 版面上最搶眼的東西,
# 按下去告訴你沒有權限。所以本任務不是純樣式改動。


async def _member(client, app, oidc, slug, sub, role):
    """把 sub 加進專案並給角色;回傳其 token。"""
    user = await make_user(app, sub)
    owner_token = oidc.issue(f"{slug}-owner")
    put = await client.put(
        f"{PREFIX}/v1/projects/{slug}/members",
        json={"user_id": str(user.id), "role": role},
        headers=auth(owner_token),
    )
    assert put.status_code == 200, put.text
    return oidc.issue(sub)


def _main(html: str) -> str:
    """只取 `<main>` 內容再比對。

    ⚠ 與 `test_web_releases.py::_main` 同一個理由(T67 的坑):版型骨架與側欄
    也含連結與 class,在整頁 HTML 上斷言會測到版面而不是內容。
    """
    start = html.find("<main")
    end = html.find("</main>", start)
    return html[start:end] if start >= 0 and end > start else html


async def _btn_page(client, app, oidc, slug, viewer_token):
    resp = await client.get(f"{PREFIX}/projects/{slug}", headers={**BROWSER, **auth(viewer_token)})
    assert resp.status_code == 200, resp.text
    return resp.text


async def test_maintainer看到的建立新版本是按鈕(client, app, oidc):
    await make_user(app, "press-a-owner")
    owner = oidc.issue("press-a-owner")
    await _project(client, owner, slug="press-a", name="按鈕測試")
    mt = await _member(client, app, oidc, "press-a", "press-a-mt", "maintainer")

    body = _main(await _btn_page(client, app, oidc, "press-a", mt))

    # ⚠ 第一版斷言是**假綠**:`class="btn"` 在 <main> 裡本來就有(編輯表單的送出鈕),
    #   所以那條在改成按鈕之前就會綠。改為抓「那一個連結自己」的 tag 再看它的 class
    #   ——同 T90 的教訓:整頁比對會撞到別的東西,區塊比對才保護得到東西。
    anchor = re.search(r'<a[^>]*/projects/press-a/releases/new"[^>]*>', body)
    assert anchor, "找不到建立新版本的連結"
    # 🔴 斷 `class="btn` 而不是 `"btn" in tag`:第一版寫後者,而測試 slug 當時叫
    #   `btn-proj` —— 斷言等於在檢查自己的測試資料,永遠綠。假綠的第三種形狀
    #   (T90 是 placeholder、T132 是空迴圈,這次是**測試資料撞到關鍵字**)。
    assert 'class="btn' in anchor.group(0), f"建立新版本要是主要按鈕,實際:{anchor.group(0)}"
    assert "建立新版本" in body


async def test_viewer看不到建立新版本(client, app, oidc):
    """🔴 本任務真正的修正:醒目的按鈕不得是按下去會 403 的陷阱。"""
    await make_user(app, "press-b-owner")
    owner = oidc.issue("press-b-owner")
    await _project(client, owner, slug="press-b", name="按鈕測試二")
    viewer = await _member(client, app, oidc, "press-b", "press-b-viewer", "viewer")

    body = _main(await _btn_page(client, app, oidc, "press-b", viewer))
    assert "建立新版本" not in body, "viewer 建不了版本,不該看到那個入口"
    # 守門:別把讀得到的人一起擋掉。
    assert "查看版本歷史" in body


async def test_改樣式不得改行為(client, app, oidc):
    """守門:兩個動作的網址一字不變。"""
    await make_user(app, "press-c-owner")
    owner = oidc.issue("press-c-owner")
    await _project(client, owner, slug="press-c", name="按鈕測試三")

    body = _main(await _btn_page(client, app, oidc, "press-c", owner))
    assert "/projects/press-c/releases/new" in body
    assert "/projects/press-c/releases" in body
