"""網頁介面(T40 起)。

與 `/v1/*` 的 API 路由**刻意分開**(決策文件 §6.1):
- API 回 JSON、錯誤走 RFC 7807、未認證回 401
- 網頁回 HTML、匿名訪客看到的是登入按鈕而不是一頁錯誤

路由一律註冊在**不帶前綴**的路徑——gateway 已經把前綴剝掉了;
反過來,頁面裡的連結必須帶前綴,那由模板的 `url()` 負責(見 web_urls.py)。
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..security import OptionalUser
from ..templating import render

router = APIRouter(include_in_schema=False, tags=["web"])

# 🐛 為什麼靜態檔用一般路由而不是 `app.mount("/static", StaticFiles(...))`:
#
# 本 app 設了 `root_path=api_prefix`(讓 /docs 產生正確的網址),但 gateway 是
# **剝掉前綴**後轉發的,所以我們收到的路徑是 `/static/app.css`。
# Starlette 的 `Mount` 會用 root_path 再剝一次(`get_route_path`),
# 於是傳給 StaticFiles 的子路徑變成 `static/app.css`(多了一層),檔案找不到 → **全站樣式 404**。
# 一般 `Route` 沒有這個二次剝除,不受影響。
#
# 這正是決策文件 §6.2 警告的那一類錯配(PLM 的 404 事故),只是方向相反:
# 那次是「框架剝、gateway 沒剝」,這次是「gateway 剝了、框架又剝一次」。
#
# 仍然使用 `StaticFiles` 物件本身來讀檔:它的 `lookup_path()` 會 realpath 後檢查
# commonpath,擋掉 `../` 路徑逃逸——這種安全檢查不該自己重寫。
_static = StaticFiles(directory=Path(__file__).parent.parent / "static")


@router.get("/static/{path:path}", summary="靜態檔")
async def static_file(path: str, request: Request) -> Response:
    """提供 CSS / JS 等靜態檔。

    參數:path 相對於 `app/static/` 的路徑。回傳:檔案內容或 404。副作用:讀檔。
    """
    return await _static.get_response(path, request.scope)


@router.get("/", summary="首頁")
async def home(
    request: Request,
    identity: OptionalUser,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> HTMLResponse:
    """首頁(F70 的版型骨架;專案列表與搜尋結果由 T41 填入)。

    `include_in_schema=False`:網頁路由不該出現在 OpenAPI 文件裡,
    那份文件是給 API 呼叫端看的。

    參數:q 導航列搜尋框送來的關鍵字(T40 只是把它帶回輸入框,T41 才真的查)。
    回傳:HTML。副作用:無。
    """
    return HTMLResponse(render(request, "home.html", identity=identity, q=q))
