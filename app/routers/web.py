"""網頁介面(T40 起)。

與 `/v1/*` 的 API 路由**刻意分開**(決策文件 §6.1):
- API 回 JSON、錯誤走 RFC 7807、未認證回 401
- 網頁回 HTML、匿名訪客看到的是登入按鈕而不是一頁錯誤

路由一律註冊在**不帶前綴**的路徑——gateway 已經把前綴剝掉了;
反過來,頁面裡的連結必須帶前綴,那由模板的 `url()` 負責(見 web_urls.py)。
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import problems
from ..audit import AuditAction, record
from ..dashboard import (
    QUOTA_WARN_RATIO,
    STALE_DRAFT_DAYS,
    collect_kpis,
    collect_todos,
    human_bytes,
)
from ..models import (
    AuditEvent,
    Project,
    ProjectRole,
    Release,
    ReleaseStatus,
    UploadStatus,
    User,
    UserStatus,
    Visibility,
)
from ..queries import query_projects, query_releases
from ..quota import project_limit
from ..schemas import ProjectCreate, ReleaseCreate
from ..security import (
    DbSession,
    OptionalUser,
    get_project,
    parse_uuid,
    require_admin,
    require_project_read,
    require_project_role,
)
from ..templating import render
from ..web_urls import web_url
from .releases import latest_published_release, load_release, missing_required_kinds

router = APIRouter(include_in_schema=False, tags=["web"])
log = logging.getLogger(__name__)

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
    # T64 靜默 SSO:瀏覽器首訪(Accept 含 text/html,沿用 T47 內容協商精神)
    # 且這 5 分鐘內沒探測過 → 無聲問一次 IdP。portal 登入過的人直接進站;
    # 沒 session 的人會被無聲送回這裡(帶著探測 cookie,不再發起)。
    # curl / 冒煙(Accept: */*)不觸發——監控看到的行為與從前完全相同。
    codec = request.app.state.cookies
    if (
        identity is None
        and "text/html" in request.headers.get("accept", "")
        and request.cookies.get(codec.sso_probe_cookie_name) is None
    ):
        settings = request.app.state.settings
        return RedirectResponse(f"{settings.external_base}/auth/login?silent=1", status_code=302)

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


def _login_redirect(request: Request, next_path: str) -> RedirectResponse:
    """未登入的深層頁 → 302 到 IdP 登入,並帶 `next` 導回原頁(T53)。

    裁示(2026-07-28):**深層頁 302、首頁留落地頁**。首頁承擔「這是什麼系統、
    找誰開通」的說明功能;深層頁則沒有這個需要——直接送人去登入才是最短路徑,
    也符合 SSO 契約 §7 冒煙第 1 項。

    🔴 **這個轉址不得洩漏專案是否存在**:呼叫端必須在**查詢之前**就決定要不要轉,
    否則「存在就 302、不存在就 404」會讓轉址與否本身變成答案
    (T42 好不容易做到的結構保證會就此破功)。

    🔴 `next` 由 `auth.login()` 的 `_safe_next()` 再驗一次(只收站內相對路徑),
    擋開放轉址——它終究來自使用者的網址。

    參數:request、next_path 登入後要回去的服務內部路徑。
    回傳:302。副作用:無。
    """
    settings = request.app.state.settings
    target = _page_url(settings, "/auth/login", q=None, tag=None, offset=0)
    return RedirectResponse(f"{target}?{urlencode({'next': next_path})}", status_code=302)


# 🐛 **路由順序有意義**:FastAPI 依註冊順序比對,`/projects/new` 必須排在
# `/projects/{slug}` **之前**,否則 "new" 會被當成 slug,永遠回 404。
# 這種錯不會有任何警告——只會在某一條網址上安靜地壞掉。
@router.get("/projects/new", summary="建立專案(表單)")
async def new_project_form(request: Request, identity: OptionalUser) -> Response:
    """建立專案的表單。純 HTML,不需要 JS。"""
    if (blocked := await _require_web_user(request, identity, "/projects/new")) is not None:
        return blocked
    return HTMLResponse(
        render(
            request,
            "project_new.html",
            identity=identity,
            form={},
            error=None,
            pending_detail=problems.pending_activation().detail,
        )
    )


@router.post("/projects/new", summary="建立專案(送出)")
async def create_project_form(
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    slug: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    visibility: Annotated[str, Form()] = "internal",
) -> Response:
    """建立專案。

    驗證失敗或短名重複時**回到表單並顯示訊息**,不丟一頁錯誤讓使用者重打;
    使用者填過的值一併帶回(逸出由 autoescape 負責)。
    """
    if (blocked := await _require_web_user(request, identity, "/projects/new")) is not None:
        return blocked
    form = {"slug": slug, "name": name, "summary": summary, "visibility": visibility}

    def _back(message: str) -> Response:
        return HTMLResponse(
            render(
                request,
                "project_new.html",
                identity=identity,
                form=form,
                error=message,
                pending_detail=problems.pending_activation().detail,
            ),
            status_code=200,
        )

    if identity is None or not identity.user.is_active:
        return _back(problems.pending_activation().detail)

    # 走與 API 完全相同的 schema 驗證,不另寫一套規則。
    try:
        payload = ProjectCreate(
            slug=slug, name=name, summary=summary, visibility=Visibility(visibility)
        )
    except Exception as exc:
        return _back(f"欄位不正確:{exc}")

    project = Project(
        slug=payload.slug,
        name=payload.name,
        summary=payload.summary,
        visibility=payload.visibility,
        owner_id=identity.user.id,
    )
    session.add(project)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # 🐛 rollback 會讓 session 裡**所有** ORM 物件過期。導航列要讀
        # `identity.user.is_active`,而模板算繪是同步的——過期屬性在那裡 lazy load
        # 就是 `MissingGreenlet`。先在還有 async 上下文的地方把它取回來。
        # (這是 T50「序列化時才 lazy load」那個家族的第三次變形。)
        await session.refresh(identity.user)
        return _back(f"專案短名 {payload.slug} 已被使用,請換一個。")

    return _redirect(request, f"/projects/{payload.slug}")


@router.get("/projects/{slug}", summary="專案頁")
async def project_page(
    slug: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """專案頁:資訊 + **最新已發布版本置頂**(F72)。

    🔴 **匿名與待開通一律不查詢**,直接顯示提示。
    如果先查專案、查不到就 404、查得到才顯示登入提示,那兩種回應本身就洩漏了
    「這個專案存不存在」。不查詢的話,兩種情況的回應必然相同——這是結構保證。

    已開通者才走 `require_project_read()`:它對 private 非成員回 **404 而非 403**
    (403 等於承認專案存在)。網頁與 API 共用同一個函式,規則不會分岔。

    參數:slug 專案短名。回傳:HTML。副作用:無(唯讀)。
    """
    settings = request.app.state.settings

    # 🔴 匿名/待開通一律**在查詢之前**就決定,不碰資料庫——否則存在與不存在會回不同東西。
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    if not identity.user.is_active:
        # 已登入但未開通:再送去 IdP 只會轉一圈回來,要給的是指引。
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
) -> Response:
    """專案歷史:版本列表,**依發布時間倒序**,可展開看各版檔案並下載(F73)。

    🔴 匿名/待開通一律不查詢,理由同專案頁:先查再判身分的話,
    存在與不存在會回不同狀態碼,回應本身就洩漏了答案。

    查詢與 draft 可見性走 `queries.query_releases()`,與 API 共用同一份。

    參數:slug 專案短名、offset 分頁位移。回傳:HTML。副作用:無(唯讀)。
    """
    settings = request.app.state.settings

    # 🔴 理由同專案頁:在查詢之前決定,轉址與否不得洩漏專案是否存在。
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}/releases")
    if not identity.user.is_active:
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


# --- T44 上傳介面(F74)-----------------------------------------------------
#
# 🔴 為什麼只有「傳檔」需要 JS:HTML <form> 只能發 GET/POST,且只能送 urlencoded
# 或 multipart,**發不出 raw body 的 PUT**(決策文件 §5.1)。建專案/建版本/發布
# 一律走純表單 POST——沒有 JS 的人仍能做完前三步,只有傳檔那一步需要 JS。
#
# 🔴 CSRF:決策文件 §6.5 要求「新增以 POST 表單送出的狀態變更時另評估」。
# 已評估(T44):`SameSite=Lax` 只在頂層 GET 導覽時送 cookie,跨站 <form method="post">
# 不會帶上 → 被當成未登入 → 擋下。因此**不加 CSRF token**。
# 這個結論完全建立在該 cookie 屬性上,所以那個屬性本身有測試釘住
# (test_web_upload.py::test_session_cookie為SameSite_Lax)。


def _redirect(request: Request, path: str) -> RedirectResponse:
    """導向服務內部路徑(自動補前綴)。用 303:POST 之後要換成 GET。

    `path` 可帶查詢字串(例:`/admin/users?error=self-disable`)——
    前綴只加在路徑部分。
    """
    head, sep, query = path.partition("?")
    url = web_url(request.app.state.settings, head)
    return RedirectResponse(f"{url}{sep}{query}", status_code=303)


async def _require_web_user(request: Request, identity, next_path: str):
    """網頁的「必須已開通」關卡。

    未登入 → 302 到 IdP(沿用 T53);待開通 → None 交給呼叫端顯示指引。
    回傳 Response 表示「已經處理掉了」,回傳 None 表示可以繼續。
    """
    if identity is None:
        return _login_redirect(request, next_path)
    return None


@router.get("/projects/{slug}/releases/new", summary="建立版本(表單)")
async def new_release_form(
    slug: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}/releases/new")
    if not identity.user.is_active:
        return HTMLResponse(
            render(
                request,
                "release_new.html",
                identity=identity,
                project=None,
                form={},
                error=None,
                pending_detail=problems.pending_activation().detail,
            )
        )
    project = await get_project(session, slug)
    # 🔴 權限與 API 同一套:private 非成員 404、成員但權限不足 403。
    await require_project_role(session, project, identity, ProjectRole.maintainer)
    return HTMLResponse(
        render(request, "release_new.html", identity=identity, project=project, form={}, error=None)
    )


@router.post("/projects/{slug}/releases/new", summary="建立版本(送出)")
async def create_release_form(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    version: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}/releases/new")
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.maintainer)

    form = {"version": version, "notes": notes}

    def _back(message: str) -> Response:
        return HTMLResponse(
            render(
                request,
                "release_new.html",
                identity=identity,
                project=project,
                form=form,
                error=message,
            ),
            status_code=200,
        )

    try:
        payload = ReleaseCreate(version=version, notes=notes)
    except Exception as exc:
        return _back(f"欄位不正確:{exc}")

    release = Release(
        project_id=project.id,
        version=payload.version,
        notes=payload.notes,
        created_by_id=identity.user.id,
    )
    session.add(release)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # 同上:rollback 讓 ORM 物件過期,模板讀到就炸。
        await session.refresh(identity.user)
        await session.refresh(project)
        return _back(f"版本 {payload.version} 已存在,請換一個版本號。")

    await session.refresh(release)
    return _redirect(request, f"/releases/{release.id}/upload")


@router.get("/releases/{release_id}/upload", summary="上傳檔案(頁面)")
async def upload_page(
    release_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """上傳頁:XHR PUT + 進度條(F74)。

    🔴 JS 一律外部檔案(`static/upload.js`)——CSP `default-src 'self'` 會擋 inline script。
    上傳靠 **HttpOnly session cookie**,JS 完全碰不到 token,也不需要碰(契約 §4.10)。
    """
    if identity is None:
        return _login_redirect(request, f"/releases/{release_id}/upload")
    if not identity.user.is_active:
        return HTMLResponse(
            render(
                request,
                "upload.html",
                identity=identity,
                release=None,
                pending_detail=problems.pending_activation().detail,
            )
        )

    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)

    settings = request.app.state.settings
    return HTMLResponse(
        render(
            request,
            "upload.html",
            identity=identity,
            release=release,
            project=release.project,
            artifacts=sorted(release.artifacts, key=lambda a: a.filename),
            # JS 要打的端點:由伺服器算好前綴放進 data-* 屬性,
            # JS 不自己拼路徑(它不知道前綴是什麼)。
            upload_base=web_url(settings, f"/v1/releases/{release.id}/artifacts"),
            max_artifact_bytes=settings.max_artifact_bytes,
            # T65:三類齊備規則——模板據此畫檢查表並停用發布鈕
            missing_kinds=missing_required_kinds(release),
        )
    )


@router.post("/releases/{release_id}/publish", summary="發布版本(送出)")
async def publish_release_form(
    release_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """發布。純表單 POST,不需要 JS。"""
    if identity is None:
        return _login_redirect(request, f"/releases/{release_id}/upload")
    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)

    if release.status is not ReleaseStatus.published:
        if not any(a.upload_status is UploadStatus.ready for a in release.artifacts):
            # 與 API 同一條規則:空版本不可發布。
            return _redirect(request, f"/releases/{release.id}/upload?error=empty")
        if missing_required_kinds(release):
            # T65:三類齊備(規則本體在 releases.missing_required_kinds,只存在一份)
            return _redirect(request, f"/releases/{release.id}/upload?error=missing-kinds")
        release.status = ReleaseStatus.published
        release.published_at = datetime.now(UTC)
        await session.commit()

    return _redirect(request, f"/projects/{release.project.slug}")


# --- T66 使用教學頁 ----------------------------------------------------------


@router.get("/help", summary="使用教學頁")
async def help_page(request: Request, identity: OptionalUser) -> HTMLResponse:
    """使用教學 + 回報問題/功能需求管道(T66)。

    **匿名可看**:教學頁擋登入毫無道理——待開通者、還沒登入的同事
    最需要它;頁面純靜態說明,不查資料庫、不漏任何專案內容。

    參數:無。回傳:HTML。副作用:無。
    """
    return HTMLResponse(render(request, "help.html", identity=identity))


# --- T45 待開通頁與管理後台(F75、F76)--------------------------------------


@router.get("/pending", summary="待開通指引頁")
async def pending_page(request: Request, identity: OptionalUser) -> Response:
    """待開通的指引頁(F75)。

    🔴 **這一頁最重要的內容是使用者自己的 `sub`。**

    契約 §4.2 規定業務庫只存 `sub`,沒有 email、沒有姓名——所以管理後台的清單
    只有一排 UUID。使用者要怎麼告訴管理員「我是誰」?管理員又要怎麼認出他?
    唯一的答案是把 `sub` 顯示給使用者,讓他複製給管理員。
    少了這一步,這兩邊永遠對不上。

    (這一頁也正好是 SSO 接入計畫 §4.3 說的「第一個管理員先登入一次取得 sub」
    的取得處——原本那是個沒有落點的手工步驟。)

    已開通者導回首頁:停在一頁「你已經開通了」沒有意義,還會讓人以為出錯。

    參數:無。回傳:HTML 或轉址。副作用:無。
    """
    if identity is None:
        return _login_redirect(request, "/pending")
    if identity.user.is_active:
        return _redirect(request, "/")

    return HTMLResponse(
        render(
            request,
            "pending.html",
            identity=identity,
            pending_detail=problems.pending_activation().detail,
        )
    )


async def _require_web_admin(request: Request, identity, next_path: str):
    """網頁的「必須是平台管理員」關卡。

    🔴 為什麼不直接用 `AdminUser` 依賴:那條路徑對**未登入**者會拋 401,
    而網頁的未登入語意是「送去 IdP」(T53 裁示的深層頁 302),不是一頁錯誤。
    非管理員的判斷則**沿用 API 的同一個函式** `require_admin()`——
    權限規則只能有一條路,兩邊各寫一份遲早分岔,而分岔的後果是越權。

    回傳 Response 表示「已經處理掉了」,回傳 None 表示可以繼續。
    """
    if identity is None:
        return _login_redirect(request, next_path)
    await require_admin(identity)  # 待開通 / 非管理員 → 403(與 API 同一段語意)
    return None


@router.get("/admin", summary="管理後台:總覽(數據面板)")
async def admin_dashboard_page(
    request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """管理後台總覽(T70)。

    KPI + **需要管理員動作的待辦** + 系統資訊。頁面**唯讀**:所有操作留在
    既有頁面(看數據與改狀態混在一起,誤點成本高而收益低)。

    🔴 只做聚合,不按人拆解——下載統計是總數,「誰下載了什麼」屬稽核頁的職責
    (設計文件《管理員後台與數據面板》§2 原則 1、§4.6(b))。

    參數:無。回傳:HTML。副作用:無(唯讀查詢)。
    """
    handled = await _require_web_admin(request, identity, "/admin")
    if handled is not None:
        return handled

    settings = request.app.state.settings
    kpis = await collect_kpis(session, settings)
    todos = await collect_todos(session, settings)

    return HTMLResponse(
        render(
            request,
            "admin_dashboard.html",
            identity=identity,
            kpis=kpis,
            todos=todos,
            human_bytes=human_bytes,
            stale_draft_days=STALE_DRAFT_DAYS,
            quota_warn_percent=int(QUOTA_WARN_RATIO * 100),
            retention_days=settings.audit_retention_days,
            environment=settings.environment,
        )
    )


@router.get("/admin/users", summary="管理後台:使用者")
async def admin_users_page(
    request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """管理後台(F76):待開通清單與一鍵開通。

    沒有這一頁的話,管理員得手打 API 才能開通任何人。

    🔴 清單裡**只有 `sub`**——業務庫依契約不存 email/姓名。第一次用的管理員
    一定會問「怎麼沒有名字」,所以頁面上直接寫明原因:那是紅線不是缺陷。
    """
    handled = await _require_web_admin(request, identity, "/admin/users")
    if handled is not None:
        return handled

    rows = (
        await session.execute(select(User).order_by(User.created_at.desc()).limit(200))
    ).scalars().all()
    pending = [u for u in rows if u.status is UserStatus.pending]
    others = [u for u in rows if u.status is not UserStatus.pending]

    return HTMLResponse(
        render(
            request,
            "admin_users.html",
            identity=identity,
            pending_users=pending,
            other_users=others,
            me=identity.user,
            error=request.query_params.get("error"),
        )
    )


@router.post("/admin/users/{user_id}/activate", summary="一鍵開通")
async def admin_activate(
    user_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """開通一位使用者。與 `PATCH /v1/admin/users/{id}` 同一段語意,不另立規則。"""
    handled = await _require_web_admin(request, identity, "/admin/users")
    if handled is not None:
        return handled

    user = (
        await session.execute(select(User).where(User.id == parse_uuid(user_id, "使用者")))
    ).scalar_one_or_none()
    if user is None:
        raise problems.not_found("找不到該使用者")

    if user.status is not UserStatus.active:
        user.status = UserStatus.active
        if user.activated_at is None:
            user.activated_at = datetime.now(UTC)
        # 與 `PATCH /v1/admin/users/{id}` 產生**相同的 action**——稽核紀錄不該
        # 因為管理員用的是網頁還是 API 而長得不一樣(test_audit.py 釘住)。
        record(
            session,
            action=AuditAction.user_activate,
            actor_id=identity.user.id,
            target_type="user",
            target_id=user.id,
        )
        await session.commit()
        log.info("開通使用者", extra={"user_id": str(user.id), "by": str(identity.user.id)})

    return _redirect(request, "/admin/users")


@router.post("/admin/users/{user_id}/disable", summary="停用使用者")
async def admin_disable(
    user_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """停用一位使用者。

    🔴 **不得停用自己**——否則平台可能一個管理員都不剩。這條規則 API 已經有,
    網頁沿用同一條,不另寫判斷。
    """
    handled = await _require_web_admin(request, identity, "/admin/users")
    if handled is not None:
        return handled

    target_id = parse_uuid(user_id, "使用者")
    if target_id == identity.user.id:
        return _redirect(request, "/admin/users?error=self-disable")

    user = (
        await session.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    if user is None:
        raise problems.not_found("找不到該使用者")

    user.status = UserStatus.disabled
    record(
        session,
        action=AuditAction.user_disable,
        actor_id=identity.user.id,
        target_type="user",
        target_id=user.id,
    )
    await session.commit()
    log.info("停用使用者", extra={"user_id": str(user.id), "by": str(identity.user.id)})
    return _redirect(request, "/admin/users")


@router.get("/admin/audit", summary="管理後台:稽核紀錄")
async def admin_audit_page(
    request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """稽核紀錄頁(F54)。

    有 API 就夠了嗎?不夠——與 T45 同一個理由:沒有頁面的話管理員得手打 API,
    而一個要手打 curl 才看得到的稽核紀錄,實際上等於沒有人會去看。

    🔴 權限與 `GET /v1/admin/audit` 同一條(平台管理員),經 `_require_web_admin()`
    轉呼叫 API 用的 `require_admin()`——不另寫一份判斷。
    """
    handled = await _require_web_admin(request, identity, "/admin/audit")
    if handled is not None:
        return handled

    action = request.query_params.get("action") or None
    conditions = [AuditEvent.action == action] if action else []
    offset = max(0, int(request.query_params.get("offset") or 0))

    total = (
        await session.execute(select(func.count()).select_from(AuditEvent).where(*conditions))
    ).scalar_one()
    rows = (
        await session.execute(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(PAGE_SIZE)
            .offset(offset)
        )
    ).scalars().all()

    settings = request.app.state.settings
    return HTMLResponse(
        render(
            request,
            "admin_audit.html",
            identity=identity,
            events=rows,
            total=total,
            offset=offset,
            action=action,
            actions=sorted(a.value for a in AuditAction),
            retention_days=settings.audit_retention_days,
            prev_url=(
                _audit_url(settings, action, max(0, offset - PAGE_SIZE)) if offset else None
            ),
            next_url=(
                _audit_url(settings, action, offset + PAGE_SIZE)
                if offset + PAGE_SIZE < total
                else None
            ),
        )
    )


def _audit_url(settings, action: str | None, offset: int) -> str:
    """稽核頁的分頁/篩選連結。

    與 `_page_url()` 同一個理由:在 Python 端用 `urlencode` 組,不在模板裡拼字串
    ——模板的 autoescape 管 HTML 逸出,不管 URL 編碼,兩者是不同的問題。
    """
    params = [("action", action)] if action else []
    if offset:
        params.append(("offset", str(offset)))
    query = urlencode(params)
    url = web_url(settings, "/admin/audit")
    return f"{url}?{query}" if query else url
