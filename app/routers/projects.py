"""專案 CRUD 與成員管理(專案層授權在本服務,不碰 Keycloak)。"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import problems
from ..models import Project, ProjectMember, ProjectRole, User, Visibility
from ..schemas import (
    MemberIn,
    MemberOut,
    OwnerTransfer,
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectUpdate,
)
from ..security import (
    CurrentUser,
    DbSession,
    get_project,
    project_role,
    require_project_read,
    require_project_role,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])
log = logging.getLogger(__name__)


def _visible_filter(identity: CurrentUser):
    """internal 全站可見;private 只有成員看得到。"""
    if identity.user.is_admin:
        return None
    return or_(
        Project.visibility == Visibility.internal,
        Project.owner_id == identity.user.id,
        Project.id.in_(
            select(ProjectMember.project_id).where(ProjectMember.user_id == identity.user.id)
        ),
    )


@router.get("", response_model=ProjectPage, summary="列出可見的專案")
async def list_projects(
    session: DbSession,
    identity: CurrentUser,
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectPage:
    stmt = select(Project)
    count_stmt = select(func.count()).select_from(Project)
    if (visible := _visible_filter(identity)) is not None:
        stmt = stmt.where(visible)
        count_stmt = count_stmt.where(visible)
    if q:
        pattern = f"%{q.strip()}%"
        keyword = or_(Project.name.ilike(pattern), Project.slug.ilike(pattern), Project.summary.ilike(pattern))
        stmt = stmt.where(keyword)
        count_stmt = count_stmt.where(keyword)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(stmt.order_by(Project.updated_at.desc()).limit(limit).offset(offset))
    ).scalars().all()

    items = []
    for project in rows:
        out = ProjectOut.model_validate(project)
        out.my_role = await project_role(session, project, identity.user)
        items.append(out)
    return ProjectPage(total=total, limit=limit, offset=offset, items=items)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="建立專案")
async def create_project(
    payload: ProjectCreate, session: DbSession, identity: CurrentUser
) -> ProjectOut:
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
        raise problems.conflict(f"專案短名 {payload.slug} 已被使用") from None
    await session.refresh(project)

    out = ProjectOut.model_validate(project)
    out.my_role = ProjectRole.owner
    log.info("建立專案", extra={"project_id": str(project.id), "slug": project.slug})
    return out


@router.get("/{slug}", response_model=ProjectOut, summary="專案詳情")
async def get_project_detail(slug: str, session: DbSession, identity: CurrentUser) -> ProjectOut:
    project = await get_project(session, slug)
    role = await require_project_read(session, project, identity)
    out = ProjectOut.model_validate(project)
    out.my_role = role
    return out


@router.patch("/{slug}", response_model=ProjectOut, summary="修改專案")
async def update_project(
    slug: str, payload: ProjectUpdate, session: DbSession, identity: CurrentUser
) -> ProjectOut:
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.maintainer)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)

    out = ProjectOut.model_validate(project)
    out.my_role = await project_role(session, project, identity.user)
    return out


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT, summary="刪除專案(連同檔案)")
async def delete_project(
    slug: str, request: Request, session: DbSession, identity: CurrentUser
) -> Response:
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)

    # 先刪物件再刪 metadata:反過來的話 metadata 沒了就找不到物件,會留下孤兒佔空間。
    await request.app.state.storage.delete_prefix(f"projects/{project.id}/")
    await session.delete(project)
    await session.commit()
    log.info("刪除專案", extra={"project_id": str(project.id), "slug": slug})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{slug}/owner", response_model=ProjectOut, summary="轉移擁有權")
async def transfer_ownership(
    slug: str, payload: OwnerTransfer, session: DbSession, identity: CurrentUser
) -> ProjectOut:
    """把專案擁有權移交給另一位已開通使用者(F16)。

    為什麼需要:owner 離職後專案會變成孤兒——沒人能改設定、加成員、刪除。
    平台管理員也能代為執行,那正是「owner 已經走了」時唯一的救援路徑。

    副作用:改寫 `projects.owner_id`,並調整 `project_members`
    (新 owner 的成員列移除、原 owner 新增/降為 maintainer)。
    """
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)

    # 冪等:重複轉給現任 owner 不算錯,直接回現況。
    if payload.user_id == project.owner_id:
        out = ProjectOut.model_validate(project)
        out.my_role = await project_role(session, project, identity.user)
        return out

    new_owner = (
        await session.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if new_owner is None:
        raise problems.not_found("找不到該使用者(對方需先登入過一次才會有帳號)")
    # 待開通的人連業務 API 都進不來,讓他當 owner 等於製造下一個孤兒專案。
    if not new_owner.is_active:
        raise problems.conflict("該使用者尚未開通,無法成為專案擁有者;請先在管理後台開通。")

    previous_owner_id = project.owner_id

    # 新 owner 若原本是成員,移除那一列 —— 擁有權由 projects.owner_id 表示,不重複記。
    existing = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == new_owner.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)

    project.owner_id = new_owner.id

    # 原 owner 降為 maintainer 而非踢出:他通常還要繼續維護,只是不再是負責人。
    previous_member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == previous_owner_id,
            )
        )
    ).scalar_one_or_none()
    if previous_member is None:
        session.add(
            ProjectMember(
                project_id=project.id,
                user_id=previous_owner_id,
                role=ProjectRole.maintainer,
            )
        )
    else:
        previous_member.role = ProjectRole.maintainer

    await session.commit()
    await session.refresh(project)

    log.info(
        "轉移擁有權",
        extra={
            "project_id": str(project.id),
            "previous_owner_id": str(previous_owner_id),
            "new_owner_id": str(new_owner.id),
            "performed_by_admin": identity.user.is_admin,
        },
    )
    out = ProjectOut.model_validate(project)
    out.my_role = await project_role(session, project, identity.user)
    return out


# --- 成員 -------------------------------------------------------------------


@router.get("/{slug}/members", response_model=list[MemberOut], summary="專案成員")
async def list_members(slug: str, session: DbSession, identity: CurrentUser) -> list[MemberOut]:
    project = await get_project(session, slug)
    await require_project_read(session, project, identity)

    owner = (await session.execute(select(User).where(User.id == project.owner_id))).scalar_one()
    members = [
        MemberOut(
            user_id=owner.id, sub=owner.sub, role=ProjectRole.owner, created_at=project.created_at
        )
    ]
    rows = (
        await session.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
        )
    ).all()
    members.extend(
        MemberOut(user_id=user.id, sub=user.sub, role=member.role, created_at=member.created_at)
        for member, user in rows
    )
    return members


@router.put("/{slug}/members", response_model=MemberOut, summary="新增或調整成員角色")
async def put_member(
    slug: str, payload: MemberIn, session: DbSession, identity: CurrentUser
) -> MemberOut:
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)

    if payload.user_id == project.owner_id:
        raise problems.conflict("擁有者的角色不能在成員清單調整;請先轉移擁有權。")

    user = (
        await session.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise problems.not_found("找不到該使用者(對方需先登入過一次才會有帳號)")

    member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        member = ProjectMember(project_id=project.id, user_id=user.id, role=payload.role)
        session.add(member)
    else:
        member.role = payload.role
    await session.commit()
    await session.refresh(member)
    return MemberOut(
        user_id=user.id, sub=user.sub, role=member.role, created_at=member.created_at
    )


@router.delete(
    "/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="移除成員"
)
async def delete_member(
    slug: str, user_id: str, session: DbSession, identity: CurrentUser
) -> Response:
    from ..security import parse_uuid

    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.owner)

    member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == parse_uuid(user_id, "成員"),
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise problems.not_found("該使用者不是本專案成員")
    await session.delete(member)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
