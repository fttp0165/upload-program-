"""T47 錯誤回應內容協商(HTML vs problem+json)。

規則是**兩個條件都要成立**才回 HTML:
1. 路徑不是 `/v1/*`、`/health`、`/ready` 這類機器端點
2. `Accept` 標頭**明示**含 `text/html`(`*/*` 不算)

只看 Accept 的話,`curl -H 'Accept: text/html' /v1/projects` 就能把整個 API 表面的
錯誤格式改掉——平台鐵則「錯誤回應一律 RFC 7807」不該被呼叫端的一個標頭鬆動。

🔴 錯誤頁會顯示 `detail` 與 `instance`(= 請求路徑),兩者都是使用者可控內容。
本檔有一條測試專門釘住逸出——本服務散布可執行檔,絕不能自己開一個 XSS。
"""

from tests.conftest import auth

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"
PROBLEM_JSON = "application/problem+json"


def _is_html(resp) -> bool:
    return resp.headers["content-type"].startswith("text/html")


def _is_problem(resp) -> bool:
    return resp.headers["content-type"].startswith(PROBLEM_JSON)


# --- API 路徑一律 JSON ------------------------------------------------------


async def test_API路徑即使宣告接受HTML仍回problem_json(client, active_user):
    """🔴 平台鐵則「錯誤回應一律 RFC 7807」不因呼叫端的 Accept 而鬆動。"""
    _, token = active_user
    resp = await client.get(
        "/v1/projects/no-such-project", headers={**auth(token), "Accept": BROWSER_ACCEPT}
    )
    assert resp.status_code == 404
    assert _is_problem(resp), resp.headers["content-type"]
    assert resp.json()["type"].endswith("/not-found")


async def test_未認證的API呼叫也是JSON(client):
    resp = await client.get("/v1/projects", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 401
    assert _is_problem(resp)


def _request(path: str, accept: str | None):
    """造一個最小的 Request 供純函式測試用(不經過真實路由)。"""
    from starlette.requests import Request

    headers = [(b"accept", accept.encode())] if accept is not None else []
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": headers, "query_string": b""}
    )


async def test_協商規則的完整真值表():
    """把「路徑 AND Accept 兩個條件都要成立」這條規則直接攤開來釘住。

    用純函式測試的原因:`/health`、`/ready` 這類機器端點在正常情況下不會產生
    problem 回應(200 或 503 的純 JSON),沒有現成的路由可以打;但規則本身
    必須涵蓋它們——監控系統送什麼 Accept 都不該改變回應格式。
    """
    from app.problems import wants_html

    cases = [
        # (路徑, Accept, 期望 HTML?)
        ("/no-such-page", BROWSER_ACCEPT, True),
        ("/", BROWSER_ACCEPT, True),
        ("/projects/demo", "text/html", True),
        # 機器端點:路徑條件不成立
        ("/v1/projects", BROWSER_ACCEPT, False),
        ("/v1/releases/x/artifacts/y/download", BROWSER_ACCEPT, False),
        ("/health", BROWSER_ACCEPT, False),
        ("/ready", BROWSER_ACCEPT, False),
        # Accept 條件不成立
        ("/no-such-page", "*/*", False),
        ("/no-such-page", "application/json", False),
        ("/no-such-page", "application/problem+json", False),
        ("/no-such-page", "", False),
        ("/no-such-page", None, False),
    ]
    for path, accept, expected in cases:
        assert wants_html(_request(path, accept)) is expected, f"{path!r} + {accept!r}"


# --- 非 API 路徑依 Accept 協商 ----------------------------------------------


async def test_瀏覽器導覽未知路徑得到HTML錯誤頁(client):
    """最常見的情境:使用者在網址列打錯字。"""
    resp = await client.get("/no-such-page", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 404
    assert _is_html(resp), resp.headers["content-type"]
    assert "<html" in resp.text.lower()


async def test_HTML錯誤頁含狀態碼標題說明與trace_id(client):
    resp = await client.get("/no-such-page", headers={"Accept": BROWSER_ACCEPT, "X-Trace-Id": "trace-for-error-page"})
    assert resp.status_code == 404
    body = resp.text
    assert "404" in body
    assert "trace-for-error-page" in body, "頁面要顯示 trace_id,使用者才有東西可以回報"


async def test_星號Accept視為API呼叫回JSON(client):
    """`*/*` 是 fetch / XHR / curl 的預設值——那是程式在呼叫,不是人在看。"""
    resp = await client.get("/no-such-page", headers={"Accept": "*/*"})
    assert resp.status_code == 404
    assert _is_problem(resp)


async def test_沒有Accept標頭回JSON(client):
    resp = await client.get("/no-such-page", headers={"Accept": ""})
    assert resp.status_code == 404
    assert _is_problem(resp)


async def test_明確要求JSON就回JSON(client):
    resp = await client.get("/no-such-page", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert _is_problem(resp)


# --- 🔴 逸出 ----------------------------------------------------------------


async def test_HTML錯誤頁對使用者可控內容做逸出(client):
    """🔴 `instance` 就是請求路徑,原樣輸出等於自開 XSS。

    本服務散布可執行檔,紅線寫著「絕不讓上傳內容在本服務網域被瀏覽器執行」——
    錯誤頁自己開一個洞是同一件事的反面。
    """
    resp = await client.get(
        "/%3Cscript%3Ealert(1)%3C/script%3E", headers={"Accept": BROWSER_ACCEPT}
    )
    assert resp.status_code == 404
    assert _is_html(resp)
    assert "<script>alert(1)</script>" not in resp.text, "使用者可控內容未逸出"
    assert "&lt;script&gt;" in resp.text, "應以逸出後的形式出現"


# --- 回應標頭 ---------------------------------------------------------------


async def test_HTML回應帶Vary_Accept(client):
    """同一個 URL 有兩種表述;不標的話快取會把 HTML 餵給 API 呼叫端。"""
    resp = await client.get("/no-such-page", headers={"Accept": BROWSER_ACCEPT})
    assert "accept" in resp.headers.get("vary", "").lower()


async def test_HTML回應帶nosniff(client):
    resp = await client.get("/no-such-page", headers={"Accept": BROWSER_ACCEPT})
    assert resp.headers.get("x-content-type-options") == "nosniff"


async def test_401的挑戰標頭不因為換成HTML就消失(app):
    """`WWW-Authenticate` 是 401 語意的一部分,不是 JSON 專屬的東西。

    直接呼叫 `problem_response()`:目前還沒有會回 401 的網頁路由(那是 T40 之後的事),
    但協商邏輯就住在這個函式裡,從這裡驗證的是同一段程式。
    """
    from app.problems import problem_response

    request = _request("/some-page", BROWSER_ACCEPT)
    request.scope["app"] = app
    resp = problem_response(
        request, 401, "unauthorized", "Unauthorized", "請先登入。",
        headers={"WWW-Authenticate": 'Bearer realm="upload-program"'},
    )
    assert resp.media_type.startswith("text/html") or resp.headers["content-type"].startswith("text/html")
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")
    assert resp.status_code == 401


# --- 憲法第四條:light 主題、無外部資源 --------------------------------------


async def test_錯誤頁為light主題且無外部資源(client):
    """憲法第四條 1–2:一律 light,禁 dark,不用 prefers-color-scheme 自動切深色。

    無外部資源則是另一個理由:錯誤頁在服務半殘、外網不通時也要能顯示。
    """
    resp = await client.get("/no-such-page", headers={"Accept": BROWSER_ACCEPT})
    body = resp.text
    assert "prefers-color-scheme" not in body
    assert "http://" not in body and "https://" not in body, "錯誤頁不得依賴外部資源"
