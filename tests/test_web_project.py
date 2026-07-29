"""T42 專案頁:資訊 + 最新版本置頂(F72)。

兩條紅線:
1. **匿名訪客的回應不得洩漏專案是否存在**——存在的 private 專案與不存在的 slug
   必須回一模一樣的東西。做法是匿名一律不查詢,結構上就不可能洩漏。
2. **掃毒狀態要誠實顯示**:這一頁是使用者按下載之前最後看到的畫面,
   `not_scanned` 必須看得到,不能只藏在 API 回應裡。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

# 契約 §2.1 的平台層短網址:由 gateway 302 轉址,**刻意不帶各 App 的前綴**
# (加上前綴會變成一條不存在的路徑)。這是下面「所有連結帶前綴」那條紅線的
# **具名例外**——不是把斷言放寬,例外本身在 test_sso_contract.py 有測試保護。
PLATFORM_URLS = {"/account", "/login"}



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
    done = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert done.status_code == 200, done.text
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
