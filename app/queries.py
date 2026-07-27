"""跨 API 與網頁共用的查詢(T41)。

🔴 為什麼要有這個模組:專案列表的**可見性規則**(internal 全站可見、private 只有成員
與 owner、admin 全部看得到)若在 API 與網頁各寫一份,兩份遲早會分岔,
而分岔的後果是 **private 專案外洩**。

這與 T35(下載回應)、T37(下載計數)、T47(錯誤協商)是同一個模式:
**同一件事只能有一條路。**
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project, ProjectMember, ProjectTag, User, Visibility
from .schemas import normalise_tag


def visible_projects_condition(user: User):
    """當事人看得到哪些專案;admin 回 None 表示不必過濾。

    參數:user。回傳:SQLAlchemy 條件或 None。副作用:無。
    """
    if user.is_admin:
        return None
    return or_(
        Project.visibility == Visibility.internal,
        Project.owner_id == user.id,
        Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)),
    )


async def query_projects(
    session: AsyncSession,
    user: User,
    q: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Project]]:
    """列出當事人看得到的專案(含關鍵字與標籤篩選、分頁)。

    參數:session、user 當事人、q 關鍵字(比對 name/slug/summary)、
    tag 標籤(會先正規化)、limit/offset 分頁。
    回傳:`(符合條件的總數, 本頁的 Project 清單)`。副作用:無(唯讀)。
    """
    conditions = []
    if (visible := visible_projects_condition(user)) is not None:
        conditions.append(visible)

    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                Project.name.ilike(pattern),
                Project.slug.ilike(pattern),
                Project.summary.ilike(pattern),
            )
        )

    if tag:
        # 查詢字串同樣要正規化,否則 `?tag=PYTHON` 會查不到存成 `python` 的標籤。
        # 這裡不驗長度/空白(那是寫入端的事),只求比對得上;正規化失敗就當作查不到。
        try:
            wanted = normalise_tag(tag)
        except ValueError:
            wanted = None
        conditions.append(
            Project.id.in_(select(ProjectTag.project_id).where(ProjectTag.tag == wanted))
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
    return total, list(rows)
