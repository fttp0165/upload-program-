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

# T102:版本狀態的中文標籤。集中一份——歷史頁、上傳頁、審核佇列都用它,
# 各寫各的話,同一狀態在兩頁叫不同名字只是時間問題。
RELEASE_STATUS_LABELS = {
    "draft": "草稿",
    "in_review": "審核中",
    "published": "已發布",
}


def release_status_label(status) -> str:
    """版本狀態 → 人看的中文;未知值原樣回傳(誠實顯示,不假裝認識)。"""
    value = getattr(status, "value", status)
    return RELEASE_STATUS_LABELS.get(value, str(value))

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
        # T69:站名單一來源 app/branding.py——模板不得再硬編碼(有測試釘住)。
        site_name=SITE_NAME,
        # T77:使用者寫的 Markdown 轉安全 HTML。回傳 Markup,模板照常 {{ }}——
        # 輸入在轉譯器裡已全數逸出,`|safe` 的禁令維持不變(見 markdown_lite.py)。
        render=render_markdown,
        # T102:版本狀態標籤(草稿/審核中/已發布),單一來源見上方常數。
        status_label=release_status_label,
        **context,
    )
