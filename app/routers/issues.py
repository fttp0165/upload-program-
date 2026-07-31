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
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import problems
from ..audit import AuditAction, record
from ..models import Issue, IssueComment, IssueStatus
from ..security import DbSession, OptionalUser
from ..templating import render
from ..version import APP_VERSION
from .web import _login_redirect, _redirect

router = APIRouter(include_in_schema=False, tags=["issues"])

PAGE_SIZE = 20

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
        .options(selectinload(Issue.comments), selectinload(Issue.reporter))
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


@router.post("/issues/new", summary="回報問題(送出)")
async def create_issue(
    request: Request,
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
