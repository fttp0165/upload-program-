"""T96:專案短名(slug)自動產生。

Benny:「專案短名(網址用) 改成自動幫專案產生」。

原本表單第一個欄位就是必填的 slug —— 使用者要建專案,第一件事卻是先想一個
網址用的英文短名,那是實作細節不是他要做的事,中文專案名的人尤其卡在這裡。

🔴 本檔釘住的界線:

1. 產生的 slug **一律符合既有的 `SLUG_RE`** —— 自動產生不是放寬規則的藉口,
   它進的是同一個欄位、同一條網址。
2. 撞名**自動換一個**,不把錯誤丟回使用者 —— 欄位都拿掉了,
   叫他「換一個短名」是叫他改一個看不到的東西。
3. 規則**穩定**:同樣的名稱得到同樣的 base(不是每次亂數)。
   slug 會出現在別人貼出去的連結裡,而本平台沒有改名功能。
"""

from tests.conftest import auth

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


# --- 產生規則本身(純函式,不碰 DB)-----------------------------------------


def test_英文名稱轉成可讀短名():
    from app.slugs import slugify

    assert slugify("My Cool Tool") == "my-cool-tool"
    assert slugify("  Report   Generator  ") == "report-generator"
    assert slugify("PLM_HY 匯出工具") == "plm-hy"


def test_產生的短名一律符合既有規則():
    """🔴 自動產生不是放寬規則的藉口:它進的是同一個欄位、同一條網址。"""
    from app.schemas import SLUG_RE
    from app.slugs import fallback_slug, slugify

    for name in ("My Cool Tool", "a", "---", "工具", "😀😀", "x" * 200):
        candidate = slugify(name) or fallback_slug()
        assert SLUG_RE.match(candidate), f"{name!r} → {candidate!r} 不符合 SLUG_RE"


def test_純中文名稱退回可用的短名():
    """不引入拼音套件(新的執行期相依 + 供應鏈風險),代價是網址不可讀——已載明於 dev-log。"""
    from app.schemas import SLUG_RE
    from app.slugs import fallback_slug, slugify

    assert slugify("示範工具") == "", "沒有可用 ASCII 時應回空字串,由呼叫端決定退路"
    fb = fallback_slug()
    assert SLUG_RE.match(fb)
    assert fb != fallback_slug(), "退路短名每次不同(它就是拿來避開撞名的)"


def test_同樣的名稱得到同樣的base():
    """🔴 規則必須穩定:slug 會出現在別人貼出去的連結裡,而本平台沒有改名功能。"""
    from app.slugs import slugify

    assert slugify("My Cool Tool") == slugify("my cool tool") == "my-cool-tool"


# --- 建立專案的實際行為 -----------------------------------------------------


async def _create(client, token, name, summary="", visibility="internal"):
    return await client.post(
        "/projects/new",
        data={"name": name, "summary": summary, "visibility": visibility},
        headers={**BROWSER, **auth(token)},
        follow_redirects=False,
    )


async def test_不填短名也能建立專案(client, active_user):
    """表單已經沒有那個欄位了,所以「沒有 slug」必須是正常路徑而不是錯誤。"""
    _, token = active_user
    resp = await _create(client, token, "My Cool Tool")
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"].endswith("/projects/my-cool-tool")


async def test_撞名自動換一個而不是回錯誤(client, active_user):
    """🔴 欄位都拿掉了,叫使用者「換一個短名」是叫他改一個看不到的東西。"""
    _, token = active_user
    first = await _create(client, token, "Same Name")
    second = await _create(client, token, "Same Name")
    assert first.status_code == 303
    assert second.status_code == 303, "第二次不得回到表單顯示錯誤"
    assert first.headers["location"] != second.headers["location"]
    assert second.headers["location"].endswith("/projects/same-name-2")


async def test_表單不再有短名欄位(client, active_user):
    _, token = active_user
    resp = await client.get("/projects/new", headers={**BROWSER, **auth(token)})
    assert 'name="slug"' not in resp.text
    assert "專案短名" not in resp.text


async def test_中文名稱也建得起來(client, active_user):
    from app.schemas import SLUG_RE

    _, token = active_user
    resp = await _create(client, token, "示範工具")
    assert resp.status_code == 303, resp.text
    slug = resp.headers["location"].rstrip("/").rsplit("/", 1)[-1]
    assert SLUG_RE.match(slug), f"{slug!r} 不符合 SLUG_RE"


async def test_API仍可指定短名(client, active_user):
    """🔴 向下相容:腳本使用者要的正是可預測的短名(契約 §5 的精神)。"""
    _, token = active_user
    resp = await client.post(
        "/v1/projects",
        json={"slug": "explicit-slug", "name": "Explicit", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "explicit-slug"


async def test_API不指定短名時自動產生(client, active_user):
    _, token = active_user
    resp = await client.post("/v1/projects", json={"name": "Api Auto Slug"}, headers=auth(token))
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "api-auto-slug"
