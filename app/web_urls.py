"""頁面內網址的組法(T40)。

🔴 為什麼需要這個模組:gateway 以尾斜線 `proxy_pass http://upload-program:8080/;`
**剝掉前綴**後轉發,於是同一個資源有兩個不同的路徑:

| | 瀏覽器看到的 | 本服務收到的 |
|---|---|---|
| 首頁 | `https://host/upload/` | `/` |
| 樣式表 | `https://host/upload/static/app.css` | `/static/app.css` |

**路由註冊用不帶前綴的路徑;頁面裡的連結是給瀏覽器用的,必須帶前綴。**
兩者搞混就是 PLM 出過的 404 事故(決策文件 §6.2:框架自動剝前綴、
gateway 卻保留前綴的錯配)。
"""


def web_url(settings, path: str) -> str:
    """把服務內部路徑組成瀏覽器可用的 **root-relative** 網址。

    為什麼不產生絕對網址:絕對網址要組 scheme 與 host,而 **TLS 在 gateway 終結**,
    從 request 推導會得到 `http://`。root-relative 讓瀏覽器沿用目前的 scheme 與 host,
    直接繞開這個問題。絕對網址只留給非絕對不可的地方(OIDC redirect_uri,見 config.py)。

    參數:settings(只用到 `api_prefix`)、path 以 `/` 開頭的服務內部路徑。
    回傳:如 `/upload/static/app.css`;`api_prefix` 為空時就是 `/static/app.css`
    ——**不會產生 `//static/...`**,那會被瀏覽器當成另一個網域的網址。
    副作用:無。
    """
    prefix = settings.api_prefix or ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{prefix}{path}"
