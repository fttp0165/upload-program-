"""跨專案搜尋(MVP:名稱 / 短名 / 摘要的關鍵字比對)。"""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from ..models import Project, ProjectMember, Visibility
from ..schemas import ProjectOut, ProjectPage
from ..security import CurrentUser, DbSession, project_role

router = APIRouter(prefix="/v1", tags=["search"])


@router.get("/search", response_model=ProjectPage, summary="搜尋專案")
async def search(
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
    if not identity.user.is_admin:
        conditions.append(
            or_(
                Project.visibility == Visibility.internal,
                Project.owner_id == identity.user.id,
                Project.id.in_(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_id == identity.user.id
                    )
                ),
            )
        )

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

    items = []
    for project in rows:
        out = ProjectOut.model_validate(project)
        out.my_role = await project_role(session, project, identity.user)
        items.append(out)
    return ProjectPage(total=total, limit=limit, offset=offset, items=items)
