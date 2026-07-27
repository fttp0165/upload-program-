"""網頁模板(T40)。

Environment 集中在這裡,錯誤頁(`problems.py`)與網頁路由(`routers/web.py`)共用同一個
——兩份 Environment 遲早會在 autoescape 或全域設定上長歪。

🔴 `autoescape` 是這個模組存在的重點之一:頁面會顯示專案名稱、檔名、請求路徑等
**使用者可控內容**,字串拼 HTML 就是 XSS 的標準做法。模板中一律禁用 `|safe`。
本服務散布可執行檔,這條紅線比一般網站更硬。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .web_urls import web_url

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render(request, template: str, **context) -> str:
    """算出模板的 HTML。

    自動注入兩個東西:
    - `url(path)`:把服務內部路徑加上 `api_prefix`(見 web_urls.py 的說明)。
      模板裡所有 `href`/`src`/`action` 都必須經過它,漏一個就是一次 404 事故。
    - `identity`:目前身分(可為 None),導航列用來決定顯示「登入」還是名字 + 「登出」。
      呼叫端有傳就用傳的,沒傳則為 None。

    參數:request(取 app.state.settings)、template 檔名、context 其餘變數。
    回傳:算好的 HTML 字串。副作用:無。
    """
    settings = request.app.state.settings
    context.setdefault("identity", None)
    return _env.get_template(template).render(
        url=lambda path: web_url(settings, path),
        **context,
    )
