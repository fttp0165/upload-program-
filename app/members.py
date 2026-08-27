"""專案成員的異動邏輯(T100)。

為什麼獨立一支模組:成員管理原本只有 API(`PUT/DELETE /v1/projects/{slug}/members`),
T100 要在網頁上也能操作。**如果網頁自己寫一套,就會有兩套規則** ——
而兩套權限規則遲早分岔,那是 private 專案外洩的典型起點。
所以規則只有一份,API 與網頁都呼叫這裡。

🔴 授權**不在這裡**做:呼叫端各自用 `require_project_role(..., ProjectRole.owner)`
(它同時處理「admin 視同 owner」與「private 非成員回 404 不回 403」)。
本模組只負責「已經確認有權限之後」該怎麼改資料與留稽核。
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import problems
from .audit import AuditAction, record
from .models import Project, ProjectMember, ProjectRole, User, UserStatus


async def set_member(
    session: AsyncSession,
    project: Project,
    actor: User,
    user_id: uuid.UUID,
    role: ProjectRole,
) -> ProjectMember:
    """新增或調整一位成員的角色。

    參數:session、project、actor 操作者、user_id 對象、role 角色。
    回傳:`ProjectMember`。副作用:新增/更新 `project_members` 一列 + 一筆稽核;commit。

    🔴 擁有者不得在成員清單裡被調整角色 —— 那會產生「沒有 owner 的專案」,
    而權限判斷全靠 owner。要換人請走轉移擁有權(`project.transfer_owner`)。
    """
    if user_id == project.owner_id:
        raise problems.conflict("擁有者的角色不能在成員清單調整;請先轉移擁有權。")

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
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
        member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
        session.add(member)
    else:
        member.role = role

    record(
        session,
        action=AuditAction.member_set,
        actor_id=actor.id,
        target_type="project",
        target_id=project.id,
        # 🔴 label 存的是**被異動的成員 id 與角色**,不是姓名——業務庫本來就沒有姓名。
        target_label=f"{project.slug}:{user.id}:{role.value}",
    )
    await session.commit()
    await session.refresh(member)
    return member


async def remove_member(
    session: AsyncSession, project: Project, actor: User, user_id: uuid.UUID
) -> None:
    """移除一位成員(private 專案的人被移除後就看不到了,這正是本功能的意義)。

    參數:session、project、actor、user_id。回傳:None。
    副作用:刪除 `project_members` 一列 + 一筆稽核;commit。
    """
    member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise problems.not_found("該使用者不是本專案成員")

    member_user_id = member.user_id
    await session.delete(member)
    record(
        session,
        action=AuditAction.member_remove,
        actor_id=actor.id,
        target_type="project",
        target_id=project.id,
        target_label=f"{project.slug}:{member_user_id}",
    )
    await session.commit()


async def project_members(session: AsyncSession, project: Project) -> list[dict]:
    """成員清單(含擁有者),給畫面用。

    參數:session、project。回傳:每筆含 `user_id` / `sub` / `display_name` / `role` /
    `is_owner`。副作用:無(唯讀)。

    🔴 **不含 email**(契約 §4.2a L1b 第 14 條:不得顯示在任何頁面);
    顯示名稱來自 L1 快取,沒有就 fallback 到截斷的識別碼 —— 認不出人的清單
    等於逼操作者亂猜,而這個清單旁邊就是「移除」按鈕。
    """
    owner = (
        await session.execute(select(User).where(User.id == project.owner_id))
    ).scalar_one_or_none()
    rows: list[dict] = []
    if owner is not None:
        rows.append(
            {
                "user_id": owner.id,
                "sub": owner.sub,
                "display_name": owner.display_name_cache,
                "role": ProjectRole.owner,
                "is_owner": True,
            }
        )

    members = (
        await session.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
            .order_by(ProjectMember.created_at)
        )
    ).all()
    for member, user in members:
        if user.id == project.owner_id:
            continue  # 擁有者已在上面,不重複列
        rows.append(
            {
                "user_id": user.id,
                "sub": user.sub,
                "display_name": user.display_name_cache,
                "role": member.role,
                "is_owner": False,
            }
        )
    return rows


async def search_active_users(
    session: AsyncSession, q: str, exclude: set[uuid.UUID] | None = None, limit: int = 5
) -> list[dict]:
    """依名字(或識別碼片段)找**已開通**使用者,給「加人」用(T100)。

    參數:session、q 搜尋字串、exclude 要排除的 user_id(已是成員的人)、limit。
    回傳:每筆含 `user_id` / `sub` / `display_name`。副作用:無(唯讀)。

    🔴 這是本任務**唯一的新外洩面** —— 任何專案擁有者都能用它確認某位同仁的存在。
    Benny 2026-08-25 裁示接受這個代價(選項:輸入名字搜尋),所以把它收到最小:

    1. 🔴 **空白 `q` 一律回空清單,不列全部** —— 少了這條,它就是一支
       「整份名單匯出」的端點,而那從來不是任何人要的功能。
    2. 🔴 最多 `limit`(預設 5)筆 —— 要找特定的人,5 筆夠了。
    3. 🔴 只回 **active**:pending / disabled 帳號的存在不該外洩。
    4. 🔴 **絕不回 email**(契約 §4.2a L1b 第 14 條)。
    """
    needle = (q or "").strip()
    if not needle:
        return []

    pattern = f"%{needle}%"
    stmt = (
        select(User)
        .where(
            User.status == UserStatus.active,
            or_(User.display_name_cache.ilike(pattern), User.sub.ilike(pattern)),
        )
        .order_by(User.display_name_cache, User.sub)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    excluded = exclude or set()
    return [
        {"user_id": user.id, "sub": user.sub, "display_name": user.display_name_cache}
        for user in rows
        if user.id not in excluded
    ]
