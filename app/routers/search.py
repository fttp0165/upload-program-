"""跨專案搜尋與標籤瀏覽。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from ..models import Project, ProjectMember, ProjectTag, Visibility
from ..schemas import ProjectPage, TagCount, TagPage
from ..security import CurrentUser, DbSession, project_out

router = APIRouter(prefix="/v1", tags=["search"])


def _visible_condition(identity: CurrentUser):
    """當事人看得到哪些專案。admin 全部看得到,回傳 None 表示不必過濾。"""
    if identity.user.is_admin:
        return None
    return or_(
        Project.visibility == Visibility.internal,
        Project.owner_id == identity.user.id,
        Project.id.in_(
            select(ProjectMember.project_id).where(ProjectMember.user_id == identity.user.id)
        ),
    )


@router.get("/search", response_model=ProjectPage, summary="搜尋專案")
async def search(
    request: Request,
    session: DbSession,
    identity: CurrentUser,
    q: Annotated[str, Query(min_length=1, max_length=128)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectPage:
    pattern = f"%{q.strip()}%"
    keyword = or_(
        Project.name.ilike(pattern),
        Project.slug.ilike(pattern),
        Project.summary.ilike(pattern),
    )
    conditions = [keyword]
    if (visible := _visible_condition(identity)) is not None:
        conditions.append(visible)

    total = (
        await session.execute(select(func.count()).select_from(Project).where(*conditions))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Project)
            .where(*conditions)
            .order_by(Project.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    settings = request.app.state.settings
    items = [await project_out(session, project, identity, settings) for project in rows]
    return ProjectPage(total=total, limit=limit, offset=offset, items=items)


@router.get("/tags", response_model=TagPage, summary="列出標籤與使用計數")
async def list_tags(session: DbSession, identity: CurrentUser) -> TagPage:
    """列出標籤與各自的專案數(F42),供前端做標籤篩選。

    🔴 只計算**當事人看得到**的專案。private 專案的標籤若出現在非成員的清單裡,
    等於洩漏該專案存在——連標籤名稱本身都可能是機密。
    """
    conditions = []
    if (visible := _visible_condition(identity)) is not None:
        conditions.append(visible)

    stmt = (
        select(ProjectTag.tag, func.count(ProjectTag.project_id).label("n"))
        .join(Project, Project.id == ProjectTag.project_id)
        .where(*conditions)
        .group_by(ProjectTag.tag)
        .order_by(func.count(ProjectTag.project_id).desc(), ProjectTag.tag)
    )
    rows = (await session.execute(stmt)).all()
    return TagPage(items=[TagCount(tag=tag, project_count=n) for tag, n in rows])
