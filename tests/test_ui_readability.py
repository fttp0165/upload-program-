"""T144:前端可讀性優化(計畫書《設計_前端可讀性優化》)。

🔴 本檔釘的不是「好不好看」,是**看不看得懂**。四條紅起步的斷言各自對應
一個 2026-09-08 專案頁截圖裡實際看得到的問題。

⚠ 第 1 條是**反向斷言**(整頁不得出現裸位元組),而反向斷言最容易假綠 ——
所以它掃的是**正則**而不是某個固定字串,且已故意改壞重驗過(見 dev-log)。
"""

import re

from app.models import ProjectRole
from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 3000
ZIP = b"PK\x03\x04" + b"\x00" * 3000
PDF = b"%PDF-1.7\n" + b"\x00" * 3000

# 七位數以上 + " bytes" = 給程式看的數字出現在人看的頁面上。
# (四位數以下留著不管:`0 bytes`、`26 bytes` 這種本來就讀得懂。)
RAW_BYTES = re.compile(r"\d{7,}\s*bytes")

_TAGS = re.compile(r"<[^>]*>")


def visible(html: str) -> str:
    """只留下**畫面上直接看得到的文字**,剝掉所有標籤(連同屬性)。

    ⚠ 這個函式的存在本身是一次修正:本檔第一版直接對整份 HTML 掃 `RAW_BYTES`,
    結果咬到**我自己刻意留的 `title` 提示** —— 精確值 hover 才出現,那是設計
    而不是缺陷。
    🔴 修法是讓斷言符合它自己宣稱的意圖(「**畫面上**不該出現裸位元組」),
    而不是放寬正則 —— 放寬會讓它連 inline 的裸位元組也一起放過,那就整條廢了。
    剝標籤之後它**仍然咬得動** inline 的違規(已故意改壞重驗,見 dev-log)。
    """
    return _TAGS.sub(" ", html)


async def _project_with_files(client, token, slug="ui-tool"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "可讀性測試", "summary": "", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    rel = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": ""},
        headers=auth(token),
    )
    release_id = rel.json()["id"]
    for name, kind, body in (
        ("src.zip", "source", ZIP),
        ("tool.exe", "binary", ELF),
        ("readme.pdf", "doc", PDF),
    ):
        r = await client.put(
            f"/v1/releases/{release_id}/artifacts/{name}?kind={kind}",
            content=body,
            headers=auth(token),
        )
        assert r.status_code == 201, r.text
    r = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert r.status_code == 200, r.text
    return slug, release_id


async def _owner_page(client, app, oidc, storage, slug="ui-tool"):
    """建好專案並核准版本,回傳(作者視角的)專案頁 HTML。"""
    await make_user(app, "sub-ui-owner")
    token = oidc.issue("sub-ui-owner")
    slug, release_id = await _project_with_files(client, token, slug)
    await make_user(app, "sub-ui-admin", admin=True)
    r = await client.post(
        f"/v1/releases/{release_id}/approve", headers=auth(oidc.issue("sub-ui-admin"))
    )
    assert r.status_code == 200, r.text
    resp = await client.get(f"/projects/{slug}", headers={**BROWSER, **auth(token)})
    assert resp.status_code == 200, resp.text
    return resp.text


async def test_專案頁不得出現裸位元組(client, app, oidc, storage):
    """🔴 `195245216 / 2147483648 bytes` 這種東西人看不出是 186 MB / 2 GB。

    ⚠ 管理總覽從 T70 起就有 `human_bytes`,而**只有那一頁在用** ——
    這條斷言擋的是「工具早就有了,但新頁面沒有用它」這種漂移。
    """
    body = await _owner_page(client, app, oidc, storage)
    found = RAW_BYTES.findall(visible(body))
    assert not found, f"頁面上還有給程式看的數字:{found[:5]}"


async def test_檔案類別顯示中文(client, app, oidc, storage):
    """🔴 上傳頁三格寫「說明 / 程式碼 / 執行檔」,檔案清單卻顯示 `source`。

    同一個東西兩種名字,而使用者不知道那是同一件事。
    """
    body = await _owner_page(client, app, oidc, storage, slug="kind-label-tool")
    assert "程式碼" in body and "執行檔" in body
    # 反向:英文 enum 不該出現在檔案那一行(kind 的 data-* 屬性不算,故只掃 muted 那段)
    assert not re.search(r'class="muted">\s*(source|binary|doc)\s*·', body)


async def test_固定連結是可點的絕對網址(client, app, oidc, storage, settings):
    """🔴 「固定連結」存在的意義是**貼進文件**。

    純文字、又只有路徑沒有 host —— 貼出去不能點,也不知道是哪一台機器。
    """
    body = await _owner_page(client, app, oidc, storage, slug="permalink-tool")
    assert f'href="{settings.public_base_url}' in body, "固定連結要是絕對網址且可點"


async def test_不再出現待平台核准的過時文案(client, app, oidc, storage):
    """契約 v3.4(2026-08-31)已核准顯示名稱,T132 也已改成真名 ——
    那句話從那天起就是錯的,而它還在頁面上。"""
    body = await _owner_page(client, app, oidc, storage, slug="stale-copy-tool")
    assert "待平台核准" not in body


def test_human_bytes_filter已註冊且算得對():
    """單一真相 = `dashboard.human_bytes`,不另寫一份。"""
    from app.templating import _env

    fn = _env.filters.get("human_bytes")
    assert fn is not None, "human_bytes 沒有註冊成 Jinja filter"
    assert fn(0) == "0 B"
    assert fn(1023) == "1023 B"
    assert fn(1024) == "1.0 KB"
    assert fn(2 * 1024**3) == "2.0 GB"


def test_kind標籤的單一真相是REQUIRED_KINDS():
    """🔴 不另寫一份中文對照表 —— 上傳頁卡片與檔案清單必須說同一組字。"""
    from app.routers.releases import REQUIRED_KINDS
    from app.templating import _env

    fn = _env.filters.get("kind_label")
    assert fn is not None, "kind_label 沒有註冊成 Jinja filter"
    for item in REQUIRED_KINDS:
        assert fn(item.kind) == item.title


async def test_成員頁的個別上限也用人看得懂的單位(client, app, oidc):
    """T141 的欄位當時寫的是 `524288000 bytes`,同一個病。"""
    target = await make_user(app, "sub-ui-target")
    await make_user(app, "sub-ui-admin2", admin=True)
    headers = auth(oidc.issue("sub-ui-admin2"))
    await client.post(
        f"/admin/users/{target.id}/upload-limit",
        data={"limit_mb": "1"},
        headers={**BROWSER, **headers},
        follow_redirects=False,
    )
    resp = await client.get("/admin/users", headers={**BROWSER, **headers})
    assert resp.status_code == 200
    assert not RAW_BYTES.search(visible(resp.text)), "使用者管理頁還有裸位元組"


async def test_viewer看得到的專案頁同樣不留裸位元組(client, app, oidc, storage):
    """守門:改的是模板不是某一種角色的分支 —— 非作者視角也要一起對。"""
    await make_user(app, "sub-ui-owner2")
    owner_token = oidc.issue("sub-ui-owner2")
    slug, release_id = await _project_with_files(client, owner_token, "viewer-view-tool")
    await make_user(app, "sub-ui-admin3", admin=True)
    await client.post(
        f"/v1/releases/{release_id}/approve", headers=auth(oidc.issue("sub-ui-admin3"))
    )
    member = await make_user(app, "sub-ui-viewer")
    await client.put(
        f"/v1/projects/{slug}/members",
        json={"user_id": str(member.id), "role": ProjectRole.viewer.value},
        headers=auth(owner_token),
    )
    resp = await client.get(
        f"/projects/{slug}", headers={**BROWSER, **auth(oidc.issue("sub-ui-viewer"))}
    )
    assert resp.status_code == 200
    assert not RAW_BYTES.search(visible(resp.text))
