"""網頁模板(T40)。

Environment 集中在這裡,錯誤頁(`problems.py`)與網頁路由(`routers/web.py`)共用同一個
——兩份 Environment 遲早會在 autoescape 或全域設定上長歪。

🔴 `autoescape` 是這個模組存在的重點之一:頁面會顯示專案名稱、檔名、請求路徑等
**使用者可控內容**,字串拼 HTML 就是 XSS 的標準做法。模板中一律禁用 `|safe`。
本服務散布可執行檔,這條紅線比一般網站更硬。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .branding import SITE_NAME
from .markdown_lite import render_markdown
from .version import APP_VERSION
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
    # T62:側欄需要知道「現在在哪」才能標 active。gateway 已剝前綴,
    # 這裡的 path 是服務視角(/、/admin/users…),與側欄連結的 url() 參數同座標系。
    context.setdefault("current_path", str(request.url.path) or "/")
    return _env.get_template(template).render(
        url=lambda path: web_url(settings, path),
        # 🔴 平台層短網址(契約 §2.1),**不經過 url()**——它不帶本服務的前綴。
        account_url=settings.account_console_url,
        # T67:平台入口,同樣是平台層網址,**不經過 url()**(加前綴會變成 /upload/ 自己)。
        portal_url=settings.portal_home_url,
        # T68:首頁要印版本號;單一真相在 app/version.py(tag 前隨 PR 改)。
        app_version=APP_VERSION,
        # T81:靜態檔一律走這個,**不要**用 `url()`——它會多帶 `?v=<APP_VERSION>`。
        #
        # 🐛 為什麼需要:T80 改了 app.css(移除 .nav-brand::before、新增
        # .nav-brand-logo),上線後首頁卻是 logo 以原始尺寸佔滿半個畫面、
        # 而應該已經移除的小圓點還在——瀏覽器用了舊 CSS。Starlette 的
        # StaticFiles 只送 ETag/Last-Modified,不送 Cache-Control,沒有
        # Cache-Control 時瀏覽器套用**啟發式快取**,那段期間連 revalidate 都不做。
        #
        # 這個缺陷在開發與測試時**永遠看不到**:pytest 的 client 與剛開的
        # 無痕視窗都是全新 client,一定拿到最新檔案;只有已經用過這個網站的
        # 人會中招。所以它不能靠「記得測」防守,只能靠結構防守。
        #
        # 值取自 APP_VERSION 而非人工遞增的數字:發版本來就會改它,失效因此
        # 是自動的——要人記得 bump 的版本號,遲早會有人不記得。
        static=lambda path: f"{web_url(settings, path)}?v={APP_VERSION}",
        # T69:站名單一來源 app/branding.py——模板不得再硬編碼(有測試釘住)。
        site_name=SITE_NAME,
        # T77:使用者寫的 Markdown 轉安全 HTML。回傳 Markup,模板照常 {{ }}——
        # 輸入在轉譯器裡已全數逸出,`|safe` 的禁令維持不變(見 markdown_lite.py)。
        render=render_markdown,
        **context,
    )
