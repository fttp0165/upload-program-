"""網頁介面(T40 起)。

與 `/v1/*` 的 API 路由**刻意分開**(決策文件 §6.1):
- API 回 JSON、錯誤走 RFC 7807、未認證回 401
- 網頁回 HTML、匿名訪客看到的是登入按鈕而不是一頁錯誤

路由一律註冊在**不帶前綴**的路徑——gateway 已經把前綴剝掉了;
反過來,頁面裡的連結必須帶前綴,那由模板的 `url()` 負責(見 web_urls.py)。
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import problems
from ..queries import query_projects, query_releases
from ..quota import project_limit
from ..security import DbSession, OptionalUser, get_project, require_project_read
from ..templating import render
from ..web_urls import web_url
from .releases import latest_published_release

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


PAGE_SIZE = 20


def _page_url(settings, path: str, *, q: str | None, tag: str | None, offset: int) -> str:
    """組出帶著目前篩選條件的分頁/篩選連結。

    🔴 **在 Python 端用 `urlencode` 組,不在模板裡拼字串**:
    模板的 autoescape 管的是 **HTML 逸出**,不管 **URL 編碼**——兩者是不同的問題。
    在 Jinja 裡拼很容易只做到一半,而 `?q=a&b=c` 這種輸入就會塞進額外的參數,
    含 `#` 的輸入會把後面整段吃掉,中文標籤則根本組不出合法網址。

    參數:settings、path 服務內部路徑、q/tag 目前的篩選條件、offset 目標位移。
    回傳:帶前綴的 root-relative 網址。副作用:無。
    """
    params = [(key, value) for key, value in (("q", q), ("tag", tag)) if value]
    if offset:
        params.append(("offset", str(offset)))
    query = urlencode(params)
    url = web_url(settings, path)
    return f"{url}?{query}" if query else url


@router.get("/", summary="首頁")
async def home(
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    q: Annotated[str | None, Query(max_length=128)] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    """首頁:專案列表 + 關鍵字搜尋 + 標籤篩選 + 分頁(F71)。

    `include_in_schema=False`:網頁路由不該出現在 OpenAPI 文件裡,
    那份文件是給 API 呼叫端看的。

    未登入與待開通**不回錯誤**,而是顯示對應的提示——網頁跟 API 不同,
    匿名訪客該看到登入按鈕而不是一頁 401。但**不得因此漏出任何專案**:
    只有已開通者才會走到查詢。

    參數:q 關鍵字、tag 標籤、offset 分頁位移。回傳:HTML。副作用:無(唯讀)。
    """
    settings = request.app.state.settings
    total, projects, next_url, prev_url = 0, [], None, None

    if identity is not None and identity.user.is_active:
        total, projects = await query_projects(
            session, identity.user, q=q, tag=tag, limit=PAGE_SIZE, offset=offset
        )
        if offset + PAGE_SIZE < total:
            next_url = _page_url(settings, "/", q=q, tag=tag, offset=offset + PAGE_SIZE)
        if offset > 0:
            prev_url = _page_url(settings, "/", q=q, tag=tag, offset=max(0, offset - PAGE_SIZE))

    return HTMLResponse(
        render(
            request,
            "home.html",
            identity=identity,
            q=q,
            tag=tag,
            projects=projects,
            total=total,
            offset=offset,
            page_size=PAGE_SIZE,
            next_url=next_url,
            prev_url=prev_url,
            # 待開通的文案取自 API 用的同一個來源——兩份中文遲早會不一致,
            # 而使用者會同時從 API 與網頁看到它。
            pending_detail=problems.pending_activation().detail,
            # 標籤連結也走 _page_url,才會一併做 URL 編碼(中文標籤必然需要)。
            tag_url=lambda name: _page_url(settings, "/", q=None, tag=name, offset=0),
        )
    )


@router.get("/projects/{slug}", summary="專案頁")
async def project_page(
    slug: str, request: Request, session: DbSession, identity: OptionalUser
) -> HTMLResponse:
    """專案頁:資訊 + **最新已發布版本置頂**(F72)。

    🔴 **匿名與待開通一律不查詢**,直接顯示提示。
    如果先查專案、查不到就 404、查得到才顯示登入提示,那兩種回應本身就洩漏了
    「這個專案存不存在」。不查詢的話,兩種情況的回應必然相同——這是結構保證。

    已開通者才走 `require_project_read()`:它對 private 非成員回 **404 而非 403**
    (403 等於承認專案存在)。網頁與 API 共用同一個函式,規則不會分岔。

    參數:slug 專案短名。回傳:HTML。副作用:無(唯讀)。
    """
    settings = request.app.state.settings

    if identity is None or not identity.user.is_active:
        return HTMLResponse(
            render(
                request,
                "project.html",
                identity=identity,
                project=None,
                pending_detail=problems.pending_activation().detail,
            )
        )

    project = await get_project(session, slug)
    await require_project_read(session, project, identity)

    # `latest_published_release()` 沿用 T35 的判定(以 published_at、draft 不算);
    # 它在「尚未發布任何版本」時拋 404,但那對網頁不是錯誤,接起來改成提示。
    try:
        release = await latest_published_release(session, project)
    except problems.ProblemError:
        release = None

    return HTMLResponse(
        render(
            request,
            "project.html",
            identity=identity,
            project=project,
            release=release,
            artifacts=sorted(release.artifacts, key=lambda a: a.filename) if release else [],
            quota_bytes=project_limit(settings, project),
            # F26 的固定連結:能貼進文件而不會隨版本失效。T35 做出來的東西
            # 不放在使用者看得到的地方就沒人會用。
            latest_url=lambda filename: web_url(
                settings, f"/v1/projects/{project.slug}/releases/latest/artifacts/{filename}/download"
            ),
            # 按鈕用精確網址:使用者按的當下看到哪一版就抓哪一版。
            download_url=lambda artifact: web_url(
                settings, f"/v1/releases/{artifact.release_id}/artifacts/{artifact.id}/download"
            ),
            tag_url=lambda name: _page_url(settings, "/", q=None, tag=name, offset=0),
        )
    )


@router.get("/projects/{slug}/releases", summary="專案歷史(版本列表)")
async def project_releases_page(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    """專案歷史:版本列表,**依發布時間倒序**,可展開看各版檔案並下載(F73)。

    🔴 匿名/待開通一律不查詢,理由同專案頁:先查再判身分的話,
    存在與不存在會回不同狀態碼,回應本身就洩漏了答案。

    查詢與 draft 可見性走 `queries.query_releases()`,與 API 共用同一份。

    參數:slug 專案短名、offset 分頁位移。回傳:HTML。副作用:無(唯讀)。
    """
    settings = request.app.state.settings

    if identity is None or not identity.user.is_active:
        return HTMLResponse(
            render(
                request,
                "releases.html",
                identity=identity,
                project=None,
                pending_detail=problems.pending_activation().detail,
            )
        )

    project = await get_project(session, slug)
    role = await require_project_read(session, project, identity)

    total, releases = await query_releases(
        session,
        project,
        include_drafts=role is not None or identity.user.is_admin,
        limit=PAGE_SIZE,
        offset=offset,
    )

    base = f"/projects/{project.slug}/releases"
    next_url = (
        _page_url(settings, base, q=None, tag=None, offset=offset + PAGE_SIZE)
        if offset + PAGE_SIZE < total
        else None
    )
    prev_url = (
        _page_url(settings, base, q=None, tag=None, offset=max(0, offset - PAGE_SIZE))
        if offset > 0
        else None
    )

    return HTMLResponse(
        render(
            request,
            "releases.html",
            identity=identity,
            project=project,
            releases=releases,
            total=total,
            offset=offset,
            next_url=next_url,
            prev_url=prev_url,
            download_url=lambda artifact: web_url(
                settings, f"/v1/releases/{artifact.release_id}/artifacts/{artifact.id}/download"
            ),
        )
    )
