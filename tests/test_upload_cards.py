"""T86 上傳頁改三格卡片,並補上「取消上傳」。

Benny 實測回報:「1. 上傳錯誤 無法取消 2. 直接三成三格卡片 說明 程式碼 執行檔」。

改版的理由不是好看:T65 已經規定每一版必須 doc + binary + source **三類齊備**,
但介面把它表達成「一個檔案輸入 + 一個類型下拉」,再在頁面最底用一行紅字說還缺什麼。
下拉有預設值,**不選就是錯的那一類**(Benny 的截圖正是 `files.zip` 配 `doc`),
而且「還缺什麼」離「要在哪裡補」隔了整頁。三格卡片讓需求本身成為介面。

🔴 本檔釘住的界線:

1. **已發布的版本不得出現任何上傳入口**——改版不是放寬既有規則的機會。
2. 每張卡片的 `kind` 必須是**合法的 `ArtifactKind` 值**;打錯字會讓上傳靜靜失敗。
3. 三類與其標籤的單一真相是 `_REQUIRED_KINDS`,模板不得另寫一份。
4. `upload.js` 仍不得出現任何瀏覽器儲存 API(§4.10,既有掃描續保)。
"""

import re

from app.models import ArtifactKind
from tests.conftest import auth

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200


async def _release(client, token, slug="card-tool"):
    headers = {**BROWSER, **auth(token)}
    await client.post(
        "/projects/new",
        # T96:短名由名稱產生,所以名稱就是想要的短名。
        data={"name": slug, "summary": "", "visibility": "internal"},
        headers=headers,
        follow_redirects=False,
    )
    resp = await client.post(
        f"/projects/{slug}/releases/new",
        data={"version": "v1.0.0", "notes": ""},
        headers=headers,
        follow_redirects=False,
    )
    return resp.headers["location"].rstrip("/").split("/")[-2]


async def _page(client, token, release_id):
    resp = await client.get(f"/releases/{release_id}/upload", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    return resp.text


def _cards(body: str) -> list[str]:
    """取出每張卡片宣告的 kind,依出現順序。"""
    return re.findall(r'class="[^"]*upload-card[^"]*"[^>]*data-kind="([^"]+)"', body)


# --- 三格卡片 ---------------------------------------------------------------


async def test_三張卡片依說明程式碼執行檔的順序(client, active_user):
    _, token = active_user
    release_id = await _release(client, token)

    assert _cards(await _page(client, token, release_id)) == ["doc", "source", "binary"]


async def test_卡片的kind都是合法的ArtifactKind(client, active_user):
    """🔴 打錯字不會有任何警告——上傳會被伺服器退回,而使用者只看到一句失敗。"""
    _, token = active_user
    release_id = await _release(client, token)

    valid = {k.value for k in ArtifactKind}
    for kind in _cards(await _page(client, token, release_id)):
        assert kind in valid, f"卡片宣告了不存在的類別:{kind}"


async def test_不再有類型下拉(client, active_user):
    """下拉的預設值就是錯配的來源;類型改由卡片決定之後它不該再存在。"""
    _, token = active_user
    release_id = await _release(client, token)

    body = await _page(client, token, release_id)
    assert 'id="kind-input"' not in body
    assert "<select" not in body[body.index("<main") : body.index("</main>")]


async def test_卡片顯示該類已上傳的檔名(client, active_user):
    _, token = active_user
    release_id = await _release(client, token)
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text

    body = await _page(client, token, release_id)
    card = body[body.index('data-kind="binary"') :]
    card = card[: card.index("</section>")]
    assert "tool.bin" in card, "已上傳的檔名要出現在對應的那一格,而不是只在下面的清單"


async def test_未上傳的卡片標示尚缺(client, active_user):
    _, token = active_user
    release_id = await _release(client, token)

    body = await _page(client, token, release_id)
    card = body[body.index('data-kind="doc"') :]
    card = card[: card.index("</section>")]
    assert "尚缺" in card


# --- 🔴 已發布不得有上傳入口 -------------------------------------------------


async def test_已發布版本沒有任何上傳卡片(client, active_user):
    """🔴 既有規則:發布後不可再變更檔案。改版不是放寬它的機會。"""
    from tests.conftest import complete_kinds

    _, token = active_user
    release_id = await _release(client, token)
    await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    await complete_kinds(client, token, release_id)
    published = await client.post(
        f"/releases/{release_id}/publish", headers={**BROWSER, **auth(token)}, follow_redirects=False
    )
    assert published.status_code in (302, 303), published.text

    body = await _page(client, token, release_id)
    assert _cards(body) == []
    assert 'id="file-input"' not in body


# --- 取消上傳 ---------------------------------------------------------------


async def test_每張卡片都有取消鈕(client, active_user):
    """送出後唯一的按鈕會被停用;沒有第二個可按的東西就等於沒有退出路徑。"""
    _, token = active_user
    release_id = await _release(client, token)

    body = await _page(client, token, release_id)
    assert len(re.findall(r'class="[^"]*upload-cancel', body)) == 3


async def test_upload_js會中止並設逾時(client):
    """🔴 沒有 timeout 的 XHR,連線卡住(不是斷線)時 `onerror` 不會觸發——

    進度條停住、按鈕永遠停用,使用者只能重新整理。這正是 Benny 說的「無法取消」。
    """
    body = (await client.get("/static/upload.js")).text
    assert ".abort()" in body, "要有中止的路徑"
    assert "timeout" in body, "要設逾時,否則卡住的連線會永久佔住介面"
    assert "ontimeout" in body, "逾時要有處理,不能設了不接"


async def test_upload_js仍不碰瀏覽器儲存空間(client):
    """🔴 §4.10:同源之下瀏覽器的儲存空間全平台可讀。改版不得順手引入。"""
    body = (await client.get("/static/upload.js")).text
    assert "localStorage" not in body
    assert "sessionStorage" not in body
