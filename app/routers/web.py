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
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import problems
from ..audit import AuditAction, record
from ..dashboard import (
    QUOTA_WARN_RATIO,
    STALE_DRAFT_DAYS,
    collect_kpis,
    collect_todos,
    human_bytes,
)
from ..members import project_members, remove_member, search_active_users, set_member
from ..models import (
    AuditEvent,
    PlatformRole,
    Project,
    ProjectComment,
    ProjectRole,
    Release,
    ReleaseStatus,
    UploadStatus,
    User,
    UserStatus,
    Visibility,
)
from ..queries import query_projects, query_releases, ready_artifacts
from ..quota import project_limit
from ..schemas import (
    ProjectCommentCreate,
    ProjectCreate,
    ProjectUpdate,
    ReleaseCreate,
    ReleaseReject,
)
from ..security import (
    DbSession,
    OptionalUser,
    get_project,
    may_manage_releases,
    parse_uuid,
    project_role,
    require_admin,
    require_project_read,
    require_project_role,
)
from ..slugs import unique_slug
from ..templating import render
from ..web_urls import web_url
from .projects import create_comment, delete_comment
from .releases import (
    REQUIRED_KINDS,
    approve_release,
    latest_published_release,
    load_release,
    missing_required_kinds,
    reject_release,
)

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
    # T81 入口導流(裁示 2026-08-04):從 portal 卡片進來的人已經知道這是什麼系統,
    # 他要的是進去,不是再讀一次介紹——**瀏覽器不再停在落地頁**,依身分分三條路。
    #
    # 這推翻了 T53「首頁留落地頁」的那一半(深層頁 302 的那一半仍然有效)。
    #
    # 🔴 只有瀏覽器(Accept 含 text/html,沿用 T47 內容協商精神)才轉址:
    #    冒煙與監控用 `Accept: */*` 打首頁、以 200 當服務活著的判準(runbook §A.4),
    #    把它們一起改成 302 會讓監控在換版當下集體變紅,而那與登入完全無關。
    #
    # T64 的靜默探測(prompt=none)在這裡功成身退:互動式登入同樣達成
    # 「portal 登入過的人免再按登入」(IdP 有 session 就直接導回、不顯示畫面),
    # 差別只在沒有 session 時——靜默會無聲送回落地頁,正是要消滅的那一頁。
    if "text/html" in request.headers.get("accept", ""):
        if identity is None:
            return _login_redirect(request, "/")
        if not identity.user.is_active:
            return _portal_redirect(request)

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


def _portal_redirect(request: Request) -> RedirectResponse:
    """已登入但未開通/已停用 → 302 回平台入口(T81 裁示「沒權限就回入口」)。

    🔴 **迴圈防線**:入口網址若被誤設成落在本服務前綴之內(例如填成 `/upload/`,
    `config.portal_home_url` 的註解早就警告過這個坑),未開通者會在 portal 與本服務
    之間無限彈跳。這時退回 `/pending`——那一頁停得下來,而且正好是他需要的東西
    (自己的 `sub`,見 `pending_page` 的說明)。

    參數:request。回傳:302。副作用:無。
    """
    settings = request.app.state.settings
    target = settings.portal_home_url
    if urlparse(target).path.startswith(settings.api_prefix):
        return RedirectResponse(web_url(settings, "/pending"), status_code=302)
    return RedirectResponse(target, status_code=302)


def _login_redirect(request: Request, next_path: str) -> RedirectResponse:
    """未登入 → 302 到 IdP 登入,並帶 `next` 導回原頁(T53 深層頁;T81 起含首頁)。

    裁示(2026-07-28):**深層頁 302、首頁留落地頁**——首頁承擔「這是什麼系統、
    找誰開通」的說明功能;深層頁則沒有這個需要,直接送人去登入才是最短路徑,
    也符合 SSO 契約 §7 冒煙第 1 項。
    **2026-08-04 修正(T81):首頁那一半被推翻**,首頁的瀏覽器請求同樣走這條;
    落地頁只剩非瀏覽器(冒煙/監控)看得到。深層頁的部分不變。

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
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    visibility: Annotated[str, Form()] = "internal",
) -> Response:
    """建立專案。

    T96:**不再收 slug 欄位** —— 短名由 `slugs.unique_slug()` 從名稱產生。

    驗證失敗或短名重複時**回到表單並顯示訊息**,不丟一頁錯誤讓使用者重打;
    使用者填過的值一併帶回(逸出由 autoescape 負責)。
    """
    if (blocked := await _require_web_user(request, identity, "/projects/new")) is not None:
        return blocked
    form = {"name": name, "summary": summary, "visibility": visibility}

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
        payload = ProjectCreate(name=name, summary=summary, visibility=Visibility(visibility))
    except Exception as exc:
        return _back(f"欄位不正確:{exc}")

    # T96:短名自動產生。
    # 🔴 撞名要**自動換一個**,不是把錯誤丟回使用者——表單已經沒有那個欄位了,
    #    叫他「換一個短名」是叫他改一個看不到的東西。
    # 🔴 為什麼還要重試:`unique_slug()` 的查詢擋不住併發(兩個請求同時挑到同一個
    #    名字,先寫入的贏)。真正的保證是 DB 的 UNIQUE 約束,這裡負責優雅地讓步。
    for attempt in range(3):
        project = Project(
            slug=await unique_slug(session, payload.name),
            name=payload.name,
            summary=payload.summary,
            visibility=payload.visibility,
            owner_id=identity.user.id,
        )
        session.add(project)
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            # 🐛 rollback 會讓 session 裡**所有** ORM 物件過期。導航列要讀
            # `identity.user.is_active`,而模板算繪是同步的——過期屬性在那裡 lazy load
            # 就是 `MissingGreenlet`。先在還有 async 上下文的地方把它取回來。
            # (這是 T50「序列化時才 lazy load」那個家族的第三次變形。)
            await session.refresh(identity.user)
            if attempt == 2:
                return _back("短名產生失敗,請再試一次(同名專案過多)。")

    # T96:轉址用**實際寫進 DB 的那個 slug**,不是 payload 的(它現在可能是 None,
    # 而且撞名重試時換過)。
    return _redirect(request, f"/projects/{project.slug}")


@router.get("/projects/{slug}", summary="專案頁")
async def project_page(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    member_q: Annotated[str, Query(max_length=64)] = "",
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

    # T100:只有擁有者(或平台管理員)看得到「查看權限」區塊。
    # 🔴 與 API 同一條界線:成員異動是 owner 的職權;maintainer 能發版但不能改誰看得到。
    can_manage = project.owner_id == identity.user.id or identity.user.is_admin
    # T101:改標題 / 簡介是 maintainer 的職權(與 API 同線);改「誰看得到」是 owner 的。
    member_role = await project_role(session, project, identity.user)
    can_edit = can_manage or member_role is ProjectRole.maintainer
    members = await project_members(session, project) if can_manage else []
    candidates = (
        await search_active_users(session, member_q, exclude={m["user_id"] for m in members})
        if can_manage
        else []
    )

    # `latest_published_release()` 沿用 T35 的判定(以 published_at、draft 不算);
    # 它在「尚未發布任何版本」時拋 404,但那對網頁不是錯誤,接起來改成提示。
    try:
        release = await latest_published_release(session, project)
    except problems.ProblemError:
        release = None

    # T118:擁有者識別碼。程式分享平台上「這是誰放的」是使用者決定要不要信任
    # 一支執行檔的第一個依據——尤其掃毒還沒接上。
    # 🔴 這裡刻意**只取 sub、不取 display_name_cache**:契約 §4.2a L1 的用途
    # 限「管理後台顯示」,本頁是一般使用者可見頁面,放名字等於自行放寬契約。
    # 已另送申請請 portal 擴大用途;獲准前以識別碼過渡(sub 是不透明識別碼,
    # 不是個資,業務庫本來就只存它)。截斷 8 碼比照 T59 慣例:僅供人眼對照。
    owner_sub = (
        await session.execute(select(User.sub).where(User.id == project.owner_id))
    ).scalar_one_or_none()

    # T124 專案留言板。可見性已由上面的 `require_project_read` 決定——
    # 走到這裡就代表這個人讀得到本專案,留言跟著專案走,不另立規則。
    comments = (
        (
            await session.execute(
                select(ProjectComment)
                .where(ProjectComment.project_id == project.id)
                .order_by(ProjectComment.created_at)
            )
        )
        .scalars()
        .all()
    )
    # 🔴 一次批次取留言者識別碼(同 T125 的理由:逐列查就是 N+1)。
    comment_subs = await _subs_by_id(session, {c.author_id for c in comments})

    return HTMLResponse(
        render(
            request,
            "project.html",
            can_manage=can_manage,
            can_edit=can_edit,
            members=members,
            candidates=candidates,
            member_q=member_q,
            identity=identity,
            project=project,
            release=release,
            # T106:檢視面只列上傳成功的檔案(按了會 404 的按鈕不該存在)。
            artifacts=ready_artifacts(release),
            quota_bytes=project_limit(settings, project),
            owner_sub8=(owner_sub or "")[:8],
            comments=comments,
            # 🔴 顯示識別碼不是名字——契約 §4.2a L1(與 T118 / T125 同一個處境)。
            comment_sub8=lambda c: (comment_subs.get(c.author_id) or "")[:8],
            # 🔴 只有留言者本人與平台管理員能刪。**專案擁有者刻意不在內**:
            # 擁有者若能刪掉別人的評語,留言板就只會剩下好話,而一個只留得住
            # 讚美的回饋區比沒有回饋區更糟。伺服器端同樣擋(routers/projects.py)。
            may_delete_comment=lambda c: (
                c.author_id == identity.user.id or identity.user.is_admin
            ),
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


@router.post("/projects/{slug}/edit", summary="改專案標題與簡介")
async def edit_project_form(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
) -> Response:
    """改標題與簡介(maintainer 以上,與 API 同一條界線)。

    參數:slug、name、summary。回傳:303 回專案頁;驗證失敗回 200 顯示錯誤。
    副作用:改 `projects.name/summary` + 一筆稽核。

    🔴 **短名(slug)刻意不可改**:它在網址裡,而本平台沒有轉址 ——
    改它會讓別人已經貼出去的連結直接死掉(T96 已載明這個代價)。
    🔴 **可見性不在這裡**:那是 owner 的職權(T100),走 `/visibility`。
    """
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.maintainer)

    # 驗證沿用 API 的同一份 schema,介面不得比它寬鬆。
    try:
        payload = ProjectUpdate(name=name.strip(), summary=summary.strip())
    except Exception as exc:
        return HTMLResponse(
            render(
                request,
                "project.html",
                identity=identity,
                project=project,
                release=None,
                quota_bytes=project_limit(request.app.state.settings, project),
                can_manage=project.owner_id == identity.user.id or identity.user.is_admin,
                can_edit=True,
                members=await project_members(session, project),
                candidates=[],
                member_q="",
                error=f"欄位不正確:{exc}",
                latest_url=lambda filename: "",
                download_url=lambda artifact: "",
                tag_url=lambda tag_name: "",
            ),
            status_code=200,
        )

    before = project.name
    project.name = payload.name
    project.summary = payload.summary or ""
    record(
        session,
        action=AuditAction.project_update,
        actor_id=identity.user.id,
        target_type="project",
        target_id=project.id,
        # 舊名字放進 label:業務庫只留最新值,「以前叫什麼」只有稽核回答得出來。
        target_label=f"{project.slug}:{before} → {payload.name}",
    )
    await session.commit()
    return _redirect(request, f"/projects/{slug}")


@router.post("/projects/{slug}/visibility", summary="改可見性(誰看得到)")
async def set_visibility(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    visibility: Annotated[str, Form()] = "",
) -> Response:
    """切換 internal / private。

    參數:slug、visibility 表單值。回傳:303 回專案頁。
    副作用:改 `projects.visibility` + 一筆稽核。

    🔴 這個動作改變的是「誰看得到」,所以**必須留痕**:事後問「這個專案什麼時候
    變成全公司可見的」,沒有紀錄就等於沒有答案。
    """
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    project = await get_project(session, slug)
    # 🔴 與 API 同一條界線:owner(admin 視同 owner)才能改誰看得到。
    await require_project_role(session, project, identity, ProjectRole.owner)

    try:
        wanted = Visibility(visibility)
    except ValueError:
        return _redirect(request, f"/projects/{slug}?error=bad-visibility")

    if wanted is not project.visibility:
        project.visibility = wanted
        record(
            session,
            action=AuditAction.project_set_visibility,
            actor_id=identity.user.id,
            target_type="project",
            target_id=project.id,
            target_label=f"{project.slug}:{wanted.value}",
        )
        await session.commit()
    return _redirect(request, f"/projects/{slug}")


@router.post("/projects/{slug}/members", summary="加入或調整成員(誰看得到)")
async def add_member_form(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    user_id: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "viewer",
) -> Response:
    """把某人加進專案(或調整角色)。

    參數:slug、user_id、role。回傳:303 回專案頁。副作用:見 `members.set_member()`。

    規則與稽核都在 `app/members.py` —— 網頁不另寫一套(兩套權限規則遲早分岔)。
    """
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)

    try:
        wanted_role = ProjectRole(role)
    except ValueError:
        return _redirect(request, f"/projects/{slug}?error=bad-role")
    # 🔴 owner 不從這裡指派:那會產生「沒有 owner 的專案」,而權限判斷全靠 owner。
    if wanted_role is ProjectRole.owner:
        return _redirect(request, f"/projects/{slug}?error=owner-transfer-only")

    await set_member(session, project, identity.user, parse_uuid(user_id, "成員"), wanted_role)
    return _redirect(request, f"/projects/{slug}")


@router.post("/projects/{slug}/members/{user_id}/remove", summary="移除成員")
async def remove_member_form(
    slug: str, user_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """移除成員。private 專案的人被移除後就看不到了 —— 這正是本功能的意義。

    參數:slug、user_id。回傳:303 回專案頁。副作用:見 `members.remove_member()`。
    """
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)
    await remove_member(session, project, identity.user, parse_uuid(user_id, "成員"))
    return _redirect(request, f"/projects/{slug}")


@router.post("/projects/{slug}/comments", summary="留一則回饋(送出)")
async def project_comment_form(
    slug: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    body_markdown: Annotated[str, Form()],
) -> Response:
    """網頁表單版的留言。與 `POST /v1/projects/{slug}/comments` 同一段語意。"""
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    if not identity.user.is_active:
        return _portal_redirect(request)
    await create_comment(slug, ProjectCommentCreate(body_markdown=body_markdown), session, identity)
    return _redirect(request, f"/projects/{slug}")


@router.post("/projects/{slug}/comments/{comment_id}/delete", summary="刪除留言(送出)")
async def project_comment_delete_form(
    slug: str, comment_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """網頁表單版的刪除。權限由 `delete_comment` 把關——🔴 表單可以偽造,
    畫面上不顯示按鈕只是不給機會按,真正的界線在那裡。"""
    if identity is None:
        return _login_redirect(request, f"/projects/{slug}")
    if not identity.user.is_active:
        return _portal_redirect(request)
    await delete_comment(slug, comment_id, session, identity)
    return _redirect(request, f"/projects/{slug}")


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

    creator_subs = await _subs_by_id(session, {r.created_by_id for r in releases})

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
            # T106:模板逐版取用;計數也走同一份,否則會出現「4 個檔案」只列 3 個。
            ready_artifacts=ready_artifacts,
            # T121:草稿的「繼續編輯」入口。上傳頁原本**只在建立版本送出後被導向一次**,
            # 使用者一離開就再也回不去——草稿於是變成看得見卻點不進去的孤兒。
            # 🔴 判準與發布同一條(maintainer 以上):viewer 看得見草稿但改不動,
            # 給他一個按下去必然 403 的連結,比不給更糟。
            can_edit_releases=may_manage_releases(role, identity.user),
            edit_url=lambda release: web_url(settings, f"/releases/{release.id}/upload"),
            # T125:每一版的建立者。🔴 一次批次查完(見 `_subs_by_id`),
            # 且刻意取 sub 不取名字——契約 §4.2a L1 的名稱快取僅限管理後台。
            creator_sub8=lambda release: (creator_subs.get(release.created_by_id) or "")[:8],
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


# T90:建立版本表單要看得到「這個專案已經有哪些版本號」。
# 🔴 版本號有 UNIQUE 約束,撞號要送出之後才看得到錯誤,而正確值就是「上一版的下一個」
#    ——那個資訊本來就該在輸入框旁邊,不該逼人離開這一頁去查歷史。
_VERSION_HINT_LIMIT = 5


async def _recent_versions(session, project) -> tuple[int, list[Release]]:
    """這個專案最近的幾個版本(給建立版本表單當提示)。

    參數:session、project。回傳:`(版本總數, 最近幾筆 Release)`。副作用:無(唯讀)。

    include_drafts=True 的理由:能進到這一頁的人至少是 maintainer,草稿是他自己的
    工作區;**模板必須把草稿標示出來**,否則會看到版本號已存在卻找不到它發布在哪。
    排序與版本歷史頁、API 共用 `query_releases()`,不另寫一份(分岔的後果是同一個
    專案在兩個頁面上「最新版」不一樣,而兩邊都不會報錯)。
    """
    return await query_releases(
        session, project, include_drafts=True, limit=_VERSION_HINT_LIMIT, offset=0
    )


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
    version_total, recent = await _recent_versions(session, project)
    return HTMLResponse(
        render(
            request,
            "release_new.html",
            identity=identity,
            project=project,
            form={},
            error=None,
            version_total=version_total,
            recent_releases=recent,
        )
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

    async def _back(message: str) -> Response:
        """回到表單並顯示訊息。

        🔴 **版本清單在這裡才查,不在函式外面**(T90):撞號那條路徑會先 `rollback()`,
        rollback 讓先前查出來的 ORM 物件全部過期,模板一讀就 MissingGreenlet
        ——本 repo 的第四次同型事故,由 `test_版本號重複時回到表單並顯示訊息` 當場抓到。
        填錯的那一次最需要看到已有哪些版本號,所以清單不能省。
        """
        version_total, recent = await _recent_versions(session, project)
        return HTMLResponse(
            render(
                request,
                "release_new.html",
                identity=identity,
                project=project,
                form=form,
                error=message,
                version_total=version_total,
                recent_releases=recent,
            ),
            status_code=200,
        )

    try:
        payload = ReleaseCreate(version=version, notes=notes)
    except Exception as exc:
        return await _back(f"欄位不正確:{exc}")

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
        return await _back(f"版本 {payload.version} 已存在,請換一個版本號。")

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
            # T86:三格卡片。每格配上「這一類目前已經有哪個檔」——
            # 只認 ready,傳到一半的不算數(與 missing_required_kinds 同一條規則)。
            upload_cards=[
                (
                    item,
                    next(
                        (
                            a
                            for a in release.artifacts
                            if a.kind is item.kind and a.upload_status is UploadStatus.ready
                        ),
                        None,
                    ),
                )
                for item in REQUIRED_KINDS
            ],
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


@router.post("/admin/users/{user_id}/role", summary="指派/取消管理員")
async def admin_set_role(
    user_id: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    role: Annotated[str, Form()],
) -> Response:
    """把一位使用者設為管理員或取回一般成員。

    參數:user_id 目標使用者、role `admin` 或 `member`。
    回傳:302 回使用者清單。副作用:改寫 `users.platform_role` 並留稽核。

    T122:後端(`PATCH /v1/admin/users/{id}`)早就做得到這件事,本路由只是把
    入口搬到網頁上——**會打 API 的人不需要這個系統的後台**,所以「只能打 API」
    等於沒有。語意與該 API 完全相同,稽核也刻意產生**同一個 action**
    (`user_set_role`):紀錄不該因為管理員用的是網頁還是 API 而長得不一樣。
    """
    handled = await _require_web_admin(request, identity, "/admin/users")
    if handled is not None:
        return handled

    if role not in (PlatformRole.admin.value, PlatformRole.member.value):
        raise problems.unprocessable("bad-role", "角色不正確", "只能是 admin 或 member。")
    wanted = PlatformRole(role)

    user = (
        await session.execute(select(User).where(User.id == parse_uuid(user_id, "使用者")))
    ).scalar_one_or_none()
    if user is None:
        raise problems.not_found("找不到該使用者")

    # 🔴 防呆 1:不能取消自己。平台沒有 root 後門——最後一個管理員把自己降級,
    # 就沒有人能再指派任何人,只剩改 `.env` 重啟容器才救得回來。一個手滑不該有這種代價。
    # (刻意**不做**「最後一個管理員不能被降級」:那要數管理員人數,而計數在並行下
    #  不可靠——兩人同時降對方,兩邊都讀到「還有 2 個」。本條只看「你是不是你」,永遠正確。)
    if user.id == identity.user.id and wanted is PlatformRole.member:
        raise problems.conflict(
            "不能取消自己的管理員身分——請由另一位管理員操作,"
            "否則可能沒有任何人能再指派管理員。"
        )

    # 🔴 防呆 2:待開通是 deny-by-default(契約 §3)。跳過開通直接給管理權,
    # 等於用後門繞過自己的門禁。要給就先開通,兩個動作各留一筆稽核。
    if wanted is PlatformRole.admin and user.status is not UserStatus.active:
        raise problems.conflict("要先開通這個帳號,才能設為管理員。")

    if user.platform_role is not wanted:
        user.platform_role = wanted
        record(
            session,
            action=AuditAction.user_set_role,
            actor_id=identity.user.id,
            target_type="user",
            target_id=user.id,
            target_label=wanted.value,
        )
        await session.commit()
        log.info(
            "調整平台角色",
            extra={"user_id": str(user.id), "new_role": wanted.value, "by": str(identity.user.id)},
        )

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



async def _subs_by_id(session: AsyncSession, ids: set) -> dict:
    """批次取使用者的 `sub`(T125)。回傳 {user_id: sub}。副作用:無(唯讀,固定一次查詢)。

    🔴 **一次 `IN` 查完,不逐列查**:版本歷史頁一次列 20 版,逐列查就是 20 次查詢。
    比照 T84 的 `_display_names()`,而且同樣有一條測試以 `before_cursor_execute`
    **實際計數**——查詢數必須與版本數無關,不能靠「應該不會慢」的直覺。

    🔴 這裡取的是 `sub` 而**不是** `display_name_cache`:契約 §4.2a L1 的名稱快取
    僅限管理後台顯示,版本歷史頁是一般使用者可見頁面(與 T118 專案頁同一個處境)。
    已另送申請請 portal 擴大用途;獲准前以識別碼過渡。
    """
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.sub).where(User.id.in_(ids)))).all()
    return dict(rows)

async def _display_names(session: AsyncSession, ids: set) -> dict:
    """批次取顯示名稱(T84)。回傳 {user_id: 名稱};查不到或沒有快取的**不放進字典**。

    為什麼「查不到就不放」而不是放 None:模板端一律 `names.get(id) or id`,
    沒有名字就退回 UUID。差別在於——稽核頁的欄位**不得空白**,那會讓人以為紀錄壞了。
    名字空掉是常態不是例外:`name` claim 由 firstName + lastName 推導,兩者皆空即為 NULL
    (`models.py` 已註明這是會真的走到的路徑);目標使用者也可能已被刪除
    (`target_id` 刻意不是外鍵,「查不回去也無妨」)。

    參數:session、ids 使用者 id 集合。回傳:dict。副作用:無(唯讀,固定一次查詢)。
    """
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(User.id, User.display_name_cache).where(User.id.in_(ids))
        )
    ).all()
    return {uid: name for uid, name in rows if name}


@router.get("/admin/reviews", summary="管理後台:待審核的版本")
async def admin_reviews_page(
    request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """T123 審核佇列:作者送審的版本停在這裡,核准後才會讓其他人下載。

    參數:無。回傳:HTML。副作用:無(唯讀)。

    🔴 這一頁本身就是「沒有通知管道」的緩解措施——平台沒有 email 也沒有推播,
    管理員只有主動走進來才會知道有東西要審。總覽的待辦區必須指得到這裡,
    兩者是一組的;拆開任何一半,版本就會安靜地卡住。
    """
    handled = await _require_web_admin(request, identity, "/admin/reviews")
    if handled is not None:
        return handled

    settings = request.app.state.settings
    releases = (
        (
            await session.execute(
                select(Release)
                .where(Release.status == ReleaseStatus.pending_review)
                .options(selectinload(Release.project))
                .order_by(Release.created_at)  # 先送先審,不讓新的插隊
            )
        )
        .scalars()
        .all()
    )
    return HTMLResponse(
        render(
            request,
            "admin_reviews.html",
            identity=identity,
            releases=releases,
            download_url=lambda artifact: web_url(
                settings, f"/v1/releases/{artifact.release_id}/artifacts/{artifact.id}/download"
            ),
        )
    )


@router.post("/admin/reviews/{release_id}/approve", summary="核准版本(送出)")
async def admin_approve_release(
    release_id: str, request: Request, session: DbSession, identity: OptionalUser
) -> Response:
    """核准一個待審版本。與 `POST /v1/releases/{id}/approve` 同一段語意,不另立規則。"""
    handled = await _require_web_admin(request, identity, "/admin/reviews")
    if handled is not None:
        return handled
    await approve_release(release_id, session, identity)
    return _redirect(request, "/admin/reviews")


@router.post("/admin/reviews/{release_id}/reject", summary="退回版本(送出)")
async def admin_reject_release(
    release_id: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    note: Annotated[str, Form()],
) -> Response:
    """退回一個待審版本,附上理由。

    🔴 理由必填。表單的 `required` 只是不給機會送出——**真正的把關在這裡**
    (`ReleaseReject` 的 schema 驗證),因為表單可以偽造。
    """
    handled = await _require_web_admin(request, identity, "/admin/reviews")
    if handled is not None:
        return handled
    await reject_release(release_id, ReleaseReject(note=note), session, identity)
    return _redirect(request, "/admin/reviews")


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

    # T84:把本頁會用到的顯示名稱一次撈齊。
    #
    # 🔴 只在這裡組,**不進 schema、不進 API**——契約 §4.2a L1 的例外只涵蓋
    #    「管理後台顯示」;`AuditEventOut` 的 docstring 也寫明「稽核不是繞過個資紅線的
    #    後門」。API 一旦帶名字,拿得到 admin token 的程式就能整批匯出姓名對照表。
    #
    # 🔴 **一次 IN 查完**,不是每列查一次:稽核頁一次 20 列,逐列查就是 N+1,
    #    而且會隨分頁大小惡化(設計文件《管理員後台與數據面板》§2:查詢數固定)。
    #    `target_id` 只有 `target_type == "user"` 時才是使用者 id——其他型別的 id
    #    丟進來查不但無意義,還會誤把剛好撞號的東西當成人。
    name_ids = {e.actor_id for e in rows if e.actor_id}
    name_ids |= {e.target_id for e in rows if e.target_id and e.target_type == "user"}
    names = await _display_names(session, name_ids)

    settings = request.app.state.settings
    return HTMLResponse(
        render(
            request,
            "admin_audit.html",
            identity=identity,
            events=rows,
            names=names,
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
