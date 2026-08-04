"""T81:靜態檔快取破壞 —— CSS/JS 的引用必須帶版本查詢字串。

**為什麼值得用測試釘住**

T80 換 logo 時改了 `app.css`(移除 `.nav-brand::before`、新增 `.nav-brand-logo`),
上線後首頁卻是:logo 以原始尺寸 320×328 佔滿半個畫面,而**應該已經移除的
小圓點還在**。伺服器吐的 HTML 是對的,CSS 是舊的 —— 瀏覽器用了快取。

根因是 `base.html` 引用靜態檔時**沒有任何快取破壞機制**。Starlette 的
`StaticFiles` 只送 `ETag` / `Last-Modified`,不送 `Cache-Control`;沒有
`Cache-Control` 時瀏覽器套用**啟發式快取**,在那段期間連 revalidate 都不做。

🔴 這個缺陷在開發與測試時**永遠看不到**:pytest 的 client、剛開的無痕視窗
都是全新 client,一定拿到最新檔案。只有**已經用過這個網站的人**會中招
——也就是所有現有使用者。T80 的測試全綠、CI 全綠、伺服器內容正確,
卻仍然壞在使用者眼前。

因此本檔驗的是「引用網址長什麼樣」與「回應的快取標頭」,
**不是**「檔案內容對不對」—— 內容對不對從來不是這個缺陷的形態。
"""

import re
from pathlib import Path

from app.version import APP_VERSION

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
_TEMPLATES = Path(__file__).parent.parent / "app" / "templates"

# 抓 <link href> / <script src> 指向本服務靜態檔的引用
_ASSET_RE = re.compile(r"""(?:href|src)\s*=\s*["']([^"']*/static/[^"']*)["']""", re.I)


async def test_首頁的靜態資源引用都帶版本(client):
    resp = await client.get("/", headers=BROWSER)
    assets = _ASSET_RE.findall(resp.text)
    assert assets, "首頁沒有任何 /static/ 引用,測試抓錯地方了"
    missing = [a for a in assets if f"?v={APP_VERSION}" not in a]
    assert not missing, (
        f"這些靜態資源引用沒帶版本,改了內容瀏覽器不會知道:{missing}"
    )


async def test_版本值取自APP_VERSION而非寫死(client):
    """🔴 寫死一個數字等於又製造一個「要記得改」的地方 —— 而那正是它會漂掉的原因。

    版本單一來源是 `app/version.py`,發版時本來就會改,失效因此是自動的。
    """
    resp = await client.get("/", headers=BROWSER)
    assert f"?v={APP_VERSION}" in resp.text
    # 反向:換一個假版本號不該出現在頁面上
    assert "?v=0.0.0-not-a-real-version" not in resp.text


def test_模板不得直接用url引用靜態檔():
    """🔴 靜態檔一律走 `static()`,不走 `url()`。

    分散在 7 個 call site 各自記得加 `?v=`,總有一天會漏掉一個 —— 而漏掉的
    那一個不會報錯,只會在某些人的瀏覽器上顯示舊樣式。收斂成一個 helper,
    這條斷言則確保沒有人繞過它。
    """
    offenders = []
    for path in _TEMPLATES.glob("*.html"):
        for m in re.finditer(r"""url\(\s*['"]/static/[^'"]*['"]""", path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {m.group(0)}")
    assert not offenders, f"這些引用應改用 static():{offenders}"


async def test_帶版本的靜態檔可長期快取(client):
    """網址已經帶版本 → 同一個網址的內容永遠不會變 → 可以放心長期快取。"""
    resp = await client.get(f"/static/app.css?v={APP_VERSION}")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc, f"帶版本的靜態檔應可長期快取,實得:{cc!r}"
    assert "max-age=" in cc


async def test_未帶版本的靜態檔不得標immutable(client):
    """🔴 `immutable` 只對**帶版本的網址**成立。

    固定網址(例如換版冒煙用的哨兵檔 `/static/logo.png`)若也標 immutable,
    哪天真要從那個網址換內容就再也換不掉了。
    """
    resp = await client.get("/static/app.css")
    assert resp.status_code == 200
    assert "immutable" not in resp.headers.get("cache-control", "")


async def test_哨兵檔在無查詢字串時仍可取得(client):
    """runbook 的換版冒煙用裸網址驗 `/static/logo.png`,不能因本次改動而失效。"""
    resp = await client.get("/static/logo.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
