"""問題回報系統的網頁路由(T77 / 施工計畫書第一期)。

與 `/v1/*` 的 API 一樣分開:這裡回 HTML、未登入導去登入,不丟 401。

🔴 三條紅線在這個檔案裡具體化:
1. **非本人非管理員一律 404**(不是 403)——與 private 專案同一個立場:
   403 等於承認「這件回報存在」,那本身就是洩漏。
2. **待開通者可以回報**——他們最可能遇到問題,卻是最沒有管道的人;
   所以這裡用的是「已登入」而非既有的 `_require_web_user`(那條要求已開通)。
3. **狀態只有平台管理員能改**;每次變更寫稽核。

內容一律存 Markdown 原文,顯示時才由 `markdown_lite` 轉譯(不存 HTML)。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import problems
from ..audit import AuditAction, record
from ..filetypes import sniff
from ..models import (
    Issue,
    IssueAttachment,
    IssueComment,
    IssueStatus,
    PlatformRole,
    User,
    UserStatus,
)
from ..security import DbSession, OptionalUser
from ..storage import TooLarge
from ..templating import render
from ..version import APP_VERSION
from ..web_urls import web_url
from .web import _login_redirect, _redirect

router = APIRouter(include_in_schema=False, tags=["issues"])

PAGE_SIZE = 20

# 附件上限(T78 / 施工計畫書 §4.2 第 6 條)。放在模組常數而非設定檔:
# 這是「回報要附截圖」的尺度判斷,不是部署參數。
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 5

# 🔴 inline 顯示只放行這三種。SVG 刻意不在其中——它是可執行的 XML,
# 而這條路徑正是本專案唯一會讓瀏覽器直接呈現上傳內容的地方。
INLINE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif"})

# 狀態的中文顯示。放這裡而不是模板:模板要用兩次(清單與詳情),
# 而且日後 API 若要回傳同一組字,也只有這一份。
STATUS_LABELS: dict[IssueStatus, str] = {
    IssueStatus.open: "待處理",
    IssueStatus.in_progress: "處理中",
    IssueStatus.resolved: "已修正",
    IssueStatus.closed: "已關閉",
    IssueStatus.wontfix: "不處理",
}

# 管理員可以指派的狀態。刻意不做「只能照順序前進」的限制——
# 現實裡問題會來回(修好了又壞、關掉又重開),硬性流程只會逼人繞路;
# 真正需要守的是「誰能改」與「改了要留痕」,那兩件在下面做足。
ASSIGNABLE = tuple(STATUS_LABELS)


async def _load(session, issue_id: str) -> Issue | None:
    """依 id 取回報(含討論串)。id 不合法或不存在都回 None——呼叫端一律當 404。"""
    try:
        key = uuid.UUID(issue_id)
    except ValueError:
        return None
    result = await session.execute(
        select(Issue)
        .where(Issue.id == key)
        .options(
            selectinload(Issue.comments),
            selectinload(Issue.reporter),
            selectinload(Issue.attachments),
        )
    )
    return result.scalar_one_or_none()


def _may_read(issue: Issue, identity) -> bool:
    """回報者本人或平台管理員。其他人一律當作「不存在」。"""
    return issue.reporter_id == identity.user.id or identity.user.is_admin


@router.get("/issues/new", summary="回報問題(表單)")
async def new_issue_form(request: Request, identity: OptionalUser) -> Response:
    if identity is None:
        return _login_redirect(request, "/issues/new")
    return HTMLResponse(
        render(
            request,
            "issue_new.html",
            identity=identity,
            form={"page_url": request.query_params.get("from", "")},
            error=None,
        )
    )


async def _notify_admins(request: Request, session, background: BackgroundTasks, issue) -> None:
    """把新回報通知每一位**有已驗證信箱**的平台管理員(T99)。

    參數:request(取 settings / mailer)、session、background、issue。
    回傳:None。副作用:排一個背景任務寄信(不在本函式內連線 SMTP)。

    🔴 **信件內容刻意不含回報全文**:信箱不是稽核紀錄,而使用者可能在描述裡
       貼上截圖說明、路徑、甚至客戶資訊。信裡只放標題 + 直達連結,
       要看內容請登入 —— 那條路上有權限判斷,信箱沒有。
    🔴 **一人一封**:不用 To 塞多人,管理員彼此看得到對方的信箱是沒必要的外洩面。
    🔴 收件地址**不進 log**(L1b 第 5 條),所以這裡連「寄給誰」都不記。
    """
    mailer = request.app.state.mailer
    if not mailer.enabled:
        return

    settings = request.app.state.settings
    rows = (
        (
            await session.execute(
                select(User.notify_email).where(
                    User.platform_role == PlatformRole.admin,
                    User.status == UserStatus.active,
                    User.notify_email.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return

    link = f"{settings.external_base}{web_url(settings, f'/issues/{issue.id}')}"
    subject = f"[upload-program] 新的問題回報:{issue.title[:80]}"
    body = (
        "有人在 upload-program 送出問題回報。\n\n"
        f"標題:{issue.title}\n"
        f"版本:{issue.app_version}\n"
        f"發生頁面:{issue.page_url or '(未提供)'}\n\n"
        f"內容請到平台查看(需登入):\n{link}\n\n"
        "—— 本信由系統自動寄出,請勿直接回覆。"
    )
    for address in rows:
        background.add_task(mailer.send, address, subject, body)


@router.post("/issues/new", summary="回報問題(送出)")
async def create_issue(
    request: Request,
    background: BackgroundTasks,
    session: DbSession,
    identity: OptionalUser,
    title: Annotated[str, Form()] = "",
    body_markdown: Annotated[str, Form()] = "",
    page_url: Annotated[str, Form()] = "",
) -> Response:
    """建立回報。

    🔴 `app_version` 與 `page_url` 由系統帶入,不要求使用者填——
    使用者不會記得自己看到問題時是哪一版,而那常常是排查的第一條線索。
    """
    if identity is None:
        return _login_redirect(request, "/issues/new")

    title, body_markdown = title.strip(), body_markdown.strip()
    if not title or not body_markdown:
        # 回表單並帶回填過的值,不丟一頁錯誤讓人重打。
        return HTMLResponse(
            render(
                request,
                "issue_new.html",
                identity=identity,
                form={"title": title, "body_markdown": body_markdown, "page_url": page_url},
                error="請填寫標題與問題描述。",
            ),
            status_code=200,
        )

    issue = Issue(
        reporter_id=identity.user.id,
        title=title[:200],
        body_markdown=body_markdown,
        page_url=page_url[:512],
        app_version=APP_VERSION,
    )
    session.add(issue)
    await session.flush()
    record(
        session,
        action=AuditAction.issue_create,
        actor_id=identity.user.id,
        target_type="issue",
        target_id=issue.id,
        # 標題是使用者自由文字,但稽核需要「刪掉之後還知道是哪一件」——
        # 與 T38 存 slug/filename 同一個用途,截斷長度避免撐爆欄位。
        target_label=issue.title[:255],
    )
    await session.commit()

    # T99:通知管理員。
    # 🔴 在 **commit 之後**、用 BackgroundTasks 排到**回應之後**才寄:
    #    使用者的回應不等 SMTP,而寄信失敗絕不能讓已經寫進 DB 的回報看起來像失敗
    #    (Benny 2026-08-25 裁示 + 契約 §4.2a L1b 第 16 條)。
    await _notify_admins(request, session, background, issue)
    return _redirect(request, f"/issues/{issue.id}")


@router.get("/issues", summary="問題回報清單")
async def list_issues(
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    status: Annotated[str | None, Query(max_length=32)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """清單:一般使用者只看得到自己的,管理員看得到全部(可依狀態篩選)。"""
    if identity is None:
        return _login_redirect(request, "/issues")

    stmt = select(Issue).options(selectinload(Issue.comments))
    count_stmt = select(func.count()).select_from(Issue)
    if not identity.user.is_admin:
        stmt = stmt.where(Issue.reporter_id == identity.user.id)
        count_stmt = count_stmt.where(Issue.reporter_id == identity.user.id)
    if status:
        try:
            wanted = IssueStatus(status)
        except ValueError:
            raise problems.bad_request("狀態值不正確。") from None
        stmt = stmt.where(Issue.status == wanted)
        count_stmt = count_stmt.where(Issue.status == wanted)

    total = int((await session.execute(count_stmt)).scalar() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(Issue.created_at.desc()).limit(PAGE_SIZE).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return HTMLResponse(
        render(
            request,
            "issue_list.html",
            identity=identity,
            issues=rows,
            total=total,
            offset=offset,
            page_size=PAGE_SIZE,
            status_labels=STATUS_LABELS,
            current_status=status or "",
        )
    )


@router.get("/issues/{issue_id}", summary="問題回報詳情")
async def issue_detail(
    request: Request, issue_id: str, session: DbSession, identity: OptionalUser
) -> Response:
    if identity is None:
        return _login_redirect(request, f"/issues/{issue_id}")

    issue = await _load(session, issue_id)
    # 🔴 找不到與沒權限走同一條路徑:回應必須無法區分,否則轉址/狀態碼本身就是答案。
    if issue is None or not _may_read(issue, identity):
        raise problems.not_found("找不到這件回報。")

    return HTMLResponse(
        render(
            request,
            "issue_detail.html",
            identity=identity,
            issue=issue,
            status_labels=STATUS_LABELS,
            assignable=ASSIGNABLE,
        )
    )


@router.post("/issues/{issue_id}/comments", summary="回覆問題回報")
async def add_comment(
    request: Request,
    issue_id: str,
    session: DbSession,
    identity: OptionalUser,
    body_markdown: Annotated[str, Form()] = "",
) -> Response:
    if identity is None:
        return _login_redirect(request, f"/issues/{issue_id}")

    issue = await _load(session, issue_id)
    if issue is None or not _may_read(issue, identity):
        raise problems.not_found("找不到這件回報。")

    body = body_markdown.strip()
    if not body:
        return _redirect(request, f"/issues/{issue.id}?error=empty-comment")

    session.add(
        IssueComment(
            issue_id=issue.id,
            author_id=identity.user.id,
            body_markdown=body,
            # 管理員的回覆要標示為官方——使用者得一眼看出哪一則是平台方說的。
            is_staff_reply=identity.user.is_admin,
        )
    )
    issue.updated_at = datetime.now(UTC)
    record(
        session,
        action=AuditAction.issue_comment,
        actor_id=identity.user.id,
        target_type="issue",
        target_id=issue.id,
        target_label=issue.title[:255],
    )
    await session.commit()
    return _redirect(request, f"/issues/{issue.id}")


@router.post("/issues/{issue_id}/status", summary="變更問題回報狀態(僅管理員)")
async def change_status(
    request: Request,
    issue_id: str,
    session: DbSession,
    identity: OptionalUser,
    status: Annotated[str, Form()] = "",
) -> Response:
    """🔴 只有平台管理員能改;回報者不能自己把問題關掉。

    這不是不信任使用者,而是「已修正」是我方的宣告:
    若回報者能自行標記,狀態就不再代表平台的處理進度。
    """
    if identity is None:
        return _login_redirect(request, f"/issues/{issue_id}")
    if not identity.user.is_admin:
        raise problems.forbidden("只有平台管理員可以變更狀態。")

    issue = await _load(session, issue_id)
    if issue is None:
        raise problems.not_found("找不到這件回報。")

    try:
        wanted = IssueStatus(status)
    except ValueError:
        raise problems.bad_request("狀態值不正確。") from None

    issue.status = wanted
    issue.updated_at = datetime.now(UTC)
    if wanted in (IssueStatus.closed, IssueStatus.wontfix):
        issue.closed_at = datetime.now(UTC)
        issue.closed_by_id = identity.user.id
    else:
        # 重新開啟時要把關閉紀錄清掉,否則「已關閉時間」會留著誤導人。
        issue.closed_at = None
        issue.closed_by_id = None

    record(
        session,
        action=AuditAction.issue_status_change,
        actor_id=identity.user.id,
        target_type="issue",
        target_id=issue.id,
        target_label=f"{issue.title[:200]} → {wanted.value}",
    )
    await session.commit()
    return _redirect(request, f"/issues/{issue.id}")


# --- T78 附件 ---------------------------------------------------------------


async def _load_for_attachment(session, issue_id: str, identity) -> Issue:
    """取回報並檢查讀寫權;不存在與無權限**回應相同**(404)。"""
    issue = await _load(session, issue_id)
    if issue is None or not _may_read(issue, identity):
        raise problems.not_found("找不到這件回報。")
    return issue


async def _store_attachment(
    request: Request,
    session,
    issue: Issue,
    identity,
    filename: str,
    chunks: AsyncIterator[bytes],
) -> IssueAttachment:
    """把上傳內容寫進物件儲存並建立附件紀錄。

    🔴 型別由 **magic bytes** 判定(`on_head`),判不過就中止——
    `upload_stream` 保證此時不會留下任何物件(與 T22/T23 同一條路徑的保證)。

    參數:filename 使用者給的檔名(僅供顯示)、chunks 位元組流。
    回傳:已 add 進 session 的附件(呼叫端負責 commit)。
    副作用:寫物件儲存、session.add、寫稽核。
    """
    existing = len(issue.attachments)
    if existing >= MAX_ATTACHMENTS:
        raise problems.unprocessable(
            "too-many-attachments",
            "附件數量超過上限",
            f"每則回報最多 {MAX_ATTACHMENTS} 張圖片,目前已有 {existing} 張。",
        )

    detected: dict[str, str] = {}

    def on_head(head: bytes) -> None:
        mime = sniff(head)
        if mime not in INLINE_IMAGE_TYPES:
            # 🔴 這裡的訊息要說清楚「看的是內容不是副檔名」,否則使用者會一直改檔名重試。
            raise problems.unprocessable(
                "rejected-file-type",
                "附件型別不接受",
                f"附件只收 PNG / JPEG / GIF 圖片。這個檔案的實際內容判定為 {mime},"
                "不予接受(判型看的是檔案內容,不是副檔名)。",
            )
        detected["mime"] = mime

    key = f"issues/{issue.id}/{uuid.uuid4()}"
    storage = request.app.state.storage
    try:
        result = await storage.upload_stream(
            key, chunks, max_bytes=MAX_ATTACHMENT_BYTES, on_head=on_head
        )
    except TooLarge:
        raise problems.payload_too_large(
            f"單張圖片上限 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB。"
        ) from None

    attachment = IssueAttachment(
        issue_id=issue.id,
        filename=filename[:255],
        content_type=detected.get("mime", "application/octet-stream"),
        size_bytes=result.size_bytes,
        sha256=result.sha256,
        storage_key=key,
        uploaded_by_id=identity.user.id,
    )
    session.add(attachment)
    await session.flush()
    record(
        session,
        action=AuditAction.issue_attachment_upload,
        actor_id=identity.user.id,
        target_type="issue_attachment",
        target_id=attachment.id,
        target_label=attachment.filename,
    )
    return attachment


@router.put(
    "/v1/issues/{issue_id}/attachments/{filename}",
    status_code=status.HTTP_201_CREATED,
    summary="上傳回報附件(request body 為原始位元組;貼上/拖曳用)",
)
async def upload_attachment_xhr(
    issue_id: str,
    filename: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
) -> dict:
    """XHR 路徑:JS 貼上截圖時走這裡(沿用 T44 的形態,raw body PUT)。

    回傳附件 id 與 markdown 片段,讓 JS 直接插進輸入框——
    使用者不必自己拼路徑,也就不會拼錯。
    """
    if identity is None:
        raise problems.unauthorized("請先登入。")
    issue = await _load_for_attachment(session, issue_id, identity)
    attachment = await _store_attachment(
        request, session, issue, identity, filename, request.stream()
    )
    await session.commit()

    from ..web_urls import web_url

    src = web_url(
        request.app.state.settings, f"/v1/issues/{issue.id}/attachments/{attachment.id}"
    )
    return {"id": str(attachment.id), "markdown": f"![{attachment.filename}]({src})"}


@router.post("/issues/{issue_id}/attachments", summary="上傳回報附件(純表單,無需 JS)")
async def upload_attachment_form(
    issue_id: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
    attachment: Annotated[UploadFile, File()],
) -> Response:
    """🔴 漸進增強的**下層**:沒有 JS 也要能附圖。

    網站壞掉時,JS 更可能就是壞掉的那一部分——而那正是使用者最需要回報的時候。
    """
    if identity is None:
        return _login_redirect(request, f"/issues/{issue_id}")
    issue = await _load_for_attachment(session, issue_id, identity)

    async def chunks() -> AsyncIterator[bytes]:
        while data := await attachment.read(1024 * 1024):
            yield data

    stored = await _store_attachment(
        request, session, issue, identity, attachment.filename or "image", chunks()
    )
    # 附件本身不會出現在內文裡,所以順手把 markdown 追加到回報內容末尾,
    # 使用者才看得到圖(而不是只在附件清單裡)。
    from ..web_urls import web_url

    src = web_url(request.app.state.settings, f"/v1/issues/{issue.id}/attachments/{stored.id}")
    issue.body_markdown = f"{issue.body_markdown}\n\n![{stored.filename}]({src})"
    issue.updated_at = datetime.now(UTC)
    await session.commit()
    return _redirect(request, f"/issues/{issue.id}")


@router.get(
    "/v1/issues/{issue_id}/attachments/{attachment_id}",
    summary="讀取回報附件(🔴 本專案唯一的 inline 顯示路徑)",
)
async def read_attachment(
    issue_id: str,
    attachment_id: str,
    request: Request,
    session: DbSession,
    identity: OptionalUser,
) -> Response:
    """🔴 **這是本專案唯一不用 attachment 的下載路徑**(施工計畫書 §4.2)。

    收窄條件全部在這裡兌現:
    - 型別在**上傳時**就已由 magic bytes 判定並只放行三種圖片,這裡直接用判定值;
    - 仍帶 `nosniff`:即使某天判定出錯,也不讓瀏覽器自行猜測型別;
    - 僅本人與管理員可讀,其他人 404(不洩漏存在);
    - `/v1/releases/.../download` **完全沒有被動到**(有回歸測試守著)。
    """
    if identity is None:
        raise problems.unauthorized("請先登入。")

    issue = await _load(session, issue_id)
    if issue is None or not _may_read(issue, identity):
        raise problems.not_found("找不到這個附件。")
    try:
        key = uuid.UUID(attachment_id)
    except ValueError:
        raise problems.not_found("找不到這個附件。") from None

    attachment = next((a for a in issue.attachments if a.id == key), None)
    if attachment is None:
        raise problems.not_found("找不到這個附件。")
    # 🔴 就算資料庫裡的值被動過手腳,也不讓非圖片型別走 inline。
    if attachment.content_type not in INLINE_IMAGE_TYPES:
        raise problems.not_found("找不到這個附件。")

    storage = request.app.state.storage
    return StreamingResponse(
        storage.iter_object(attachment.storage_key),
        media_type=attachment.content_type,
        headers={
            # 🔴 inline 是這條路徑的**目的**,不是疏漏——但仍帶 nosniff:
            # 即使判型某天出錯,也不讓瀏覽器自行猜測型別。
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )
