"""T67:回到平台入口的連結。

本服務掛在 `/upload/` 之下,使用者從 portal 卡片進來後沒有回頭路。

🔴 這條連結**刻意不經過 `url()`**:平台入口是平台層網址,加上本服務前綴
會變成 `/upload/` 自己(原地打轉)。與契約 §2.1 的 `/account` 同一類具名例外。

同時補償「所有連結帶前綴」那條紅線因白名單納入 `/` 而鬆動的部分:
釘住「頁面上恰好只有一條 `/` 連結」與「品牌/專案總覽仍是帶前綴的首頁」。
"""

import re

from tests.conftest import auth, make_user

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/upload"

_LINK_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _links(html: str) -> list[str]:
    return _LINK_RE.findall(html)


async def test_匿名者有回到平台入口的連結(client, app):
    # T81:匿名瀏覽器開首頁會被送去登入,改在 /help 驗導航列
    resp = await client.get("/help", headers=BROWSER)
    assert resp.status_code == 200
    assert "回到平台入口" in resp.text
    assert f'href="{app.state.settings.portal_home_url}"' in resp.text


async def test_已開通者側欄有回到平台入口(client, app, oidc):
    await make_user(app, "sub-portal-link")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-portal-link"))})
    assert resp.status_code == 200
    assert "回到平台入口" in resp.text
    # 側欄的那條:出現在 side-link 之中
    assert re.search(r'class="side-link[^"]*"\s+href="/"', resp.text), "側欄應有回到入口的連結"


async def test_入口連結不帶本服務前綴(client, app):
    """🔴 加上前綴就變成 /upload/ 自己——那不是回到入口,是原地打轉。"""
    resp = await client.get("/help", headers=BROWSER)  # T81:匿名者的導航列改在 /help 驗
    assert f'href="{PREFIX}/"' in resp.text, "首頁連結本身仍應帶前綴"
    portal = app.state.settings.portal_home_url
    assert not portal.startswith(PREFIX), "平台入口不得帶本服務前綴"


async def test_入口網址可由設定覆寫(app):
    """平台的東西由平台決定——與 account_console_url 同樣設定化。"""
    from app.config import Settings

    assert Settings.model_fields["portal_home_url"].default == "/"


# --- 補償:白名單納入 `/` 之後,原本靠「白名單很小」得到的保護要換成明確斷言 ---


async def test_每一條根連結都是具名的入口連結(client, app, oidc):
    """🔴 漏掉 `url()` 的首頁連結也會長成 `/`,白名單放行後就抓不到了。

    改以**具名標記**釘住:頁面上每一條 `/` 都必須帶 `nav-exit` 或 `side-exit`
    (平台入口專用的 class)。任何漏掉 `url()` 的連結都是沒有標記的裸 `/`,
    數量對不上,這條就會紅。
    """
    await make_user(app, "sub-portal-count")
    # T81:匿名瀏覽器開首頁會被送去登入,匿名這一側改用 /help(同一份 base.html 導航列)
    for path, headers in (
        ("/help", BROWSER),
        ("/", {**BROWSER, **auth(oidc.issue("sub-portal-count"))}),
    ):
        resp = await client.get(path, headers=headers)
        roots = [link for link in _links(resp.text) if link == "/"]
        marked = len(re.findall(r'class="[^"]*(?:nav-exit|side-exit)[^"]*"\s+href="/"', resp.text))
        assert roots, "頁面應有回到平台入口的連結"
        assert marked == len(roots), (
            f"有 {len(roots) - marked} 條沒有標記的 `/` 連結——可能是漏掉 url() 的站內連結"
        )


async def test_品牌與專案總覽仍指向帶前綴的首頁(client, app, oidc):
    await make_user(app, "sub-portal-home")
    resp = await client.get("/", headers={**BROWSER, **auth(oidc.issue("sub-portal-home"))})
    assert f'class="nav-brand navbar-brand" href="{PREFIX}/"' in resp.text
    assert re.search(rf'class="side-link[^"]*"\s+href="{PREFIX}/"', resp.text), (
        "側欄「專案總覽」應指向帶前綴的首頁"
    )
