"""跨 API 與網頁共用的查詢(T41)。

🔴 為什麼要有這個模組:專案列表的**可見性規則**(internal 全站可見、private 只有成員
與 owner、admin 全部看得到)若在 API 與網頁各寫一份,兩份遲早會分岔,
而分岔的後果是 **private 專案外洩**。

這與 T35(下載回應)、T37(下載計數)、T47(錯誤協商)是同一個模式:
**同一件事只能有一條路。**
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    Project,
    ProjectMember,
    ProjectTag,
    Release,
    ReleaseStatus,
    User,
    Visibility,
)
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


async def query_releases(
    session: AsyncSession,
    project: Project,
    *,
    include_drafts: bool,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Release]]:
    """列出專案的版本(含分頁),API 與網頁共用。

    🐛 **排序用 `published_at` 而非 `created_at`**(T43 修正的既有缺陷)。
    `latest_published_release()` 從 T35 起就是用 `published_at`,但這支列表原本用
    `created_at`——先建的版本可能後發布,於是**列表第一筆與 `/latest` 會指向不同版本**,
    而且兩邊都不會報錯。使用者在歷史頁看到最上面是 v9,點 latest 卻拿到 v10。

    draft 的 `published_at` 是 NULL,排在最前面(NULLS FIRST):
    那是作者正在做的東西,對看得到它的人最相關。

    參數:session、project、include_drafts 是否含 draft(非成員一律 False)、limit/offset。
    回傳:`(總數, 本頁的 Release 清單)`。副作用:無(唯讀)。
    """
    conditions = [Release.project_id == project.id]
    if not include_drafts:
        # draft 是作者的工作區,不是給別人看的。
        conditions.append(Release.status == ReleaseStatus.published)

    total = (
        await session.execute(select(func.count()).select_from(Release).where(*conditions))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Release)
            .options(selectinload(Release.artifacts))
            .where(*conditions)
            .order_by(Release.published_at.desc().nullsfirst(), Release.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return total, list(rows)
