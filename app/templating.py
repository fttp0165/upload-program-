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


def _kind_label(kind) -> str:
    """檔案類別的中文標籤(T144)。

    🔴 **單一真相是 `REQUIRED_KINDS`**(上傳頁三格卡片用的那一份),
    不另寫一份對照表 —— 上傳頁寫「程式碼」而檔案清單寫 `source`,
    使用者不會知道那是同一件事,而兩份對照表遲早會分岔。

    ⚠ import 放在函式內:`routers.releases` 會 import 本模組(錯誤頁共用
    Environment),模組層互 import 會繞回來。
    """
    from .routers.releases import REQUIRED_KINDS

    for item in REQUIRED_KINDS:
        if item.kind is kind:
            return item.title
    # 找不到就照實回原值 —— 寧可顯示 enum 也不要顯示空白
    # (空白會讓人以為那一格壞了,而 enum 至少還說得出是什麼)。
    return getattr(kind, "value", str(kind))


def _human_bytes(value) -> str:
    """人看得懂的單位(T144)。

    🔴 **不新寫一個** —— 管理總覽從 T70 起就有 `dashboard.human_bytes`,
    而在那之前**只有那一頁在用它**,其他頁面全是裸位元組。
    註冊成 filter 是為了讓「用它」比「印原始值」更省事,否則下一頁又會忘。

    ⚠ import 放在函式內:`dashboard` → `quota` → `problems` → 本模組
    是一條**循環 import**(錯誤頁與網頁共用同一個 Environment 就是它的來源),
    模組層 import 會在啟動時炸開。這不是風格選擇。
    """
    from .dashboard import human_bytes

    return human_bytes(int(value or 0))


_env.filters["human_bytes"] = _human_bytes
_env.filters["kind_label"] = _kind_label


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
        # T144:把服務內部路徑補成**絕對網址**。用途只有一個:「固定連結」
        # 是設計來貼進文件的,而只有路徑的字串貼出去既不能點、也看不出是哪台機器。
        # 🔴 不重新實作前綴邏輯 —— 一律吃 `url()` 的結果再補 host。
        absolute=lambda path: settings.public_base_url.rstrip("/") + path,
        # T69:站名單一來源 app/branding.py——模板不得再硬編碼(有測試釘住)。
        site_name=SITE_NAME,
        # T77:使用者寫的 Markdown 轉安全 HTML。回傳 Markup,模板照常 {{ }}——
        # 輸入在轉譯器裡已全數逸出,`|safe` 的禁令維持不變(見 markdown_lite.py)。
        render=render_markdown,
        **context,
    )
