"""平台管理後台:開通使用者、指派平台角色。

🔴 契約 §4.4:派角色/收權都在**本服務**做,不碰 Keycloak。
IdP 只負責「這個人是誰」,「這個人能做什麼」是我們自己的事。
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .. import problems
from ..audit import AuditAction, record
from ..models import AuditEvent, User, UserStatus
from ..schemas import AuditEventOut, AuditPage, UserOut, UserPage, UserPatch
from ..security import AdminUser, DbSession, parse_uuid

router = APIRouter(prefix="/v1/admin", tags=["admin"])
log = logging.getLogger(__name__)


@router.get("/users", response_model=UserPage, summary="列出使用者(可篩待開通)")
async def list_users(
    session: DbSession,
    admin: AdminUser,
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    conditions = [User.status == status_filter] if status_filter else []
    total = (
        await session.execute(select(func.count()).select_from(User).where(*conditions))
    ).scalar_one()
    rows = (
        await session.execute(
            select(User).where(*conditions).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return UserPage(
        total=total, limit=limit, offset=offset, items=[UserOut.model_validate(u) for u in rows]
    )


@router.patch("/users/{user_id}", response_model=UserOut, summary="開通 / 停用 / 指派平台角色")
async def patch_user(
    user_id: str, payload: UserPatch, session: DbSession, admin: AdminUser
) -> UserOut:
    user = (
        await session.execute(select(User).where(User.id == parse_uuid(user_id, "使用者")))
    ).scalar_one_or_none()
    if user is None:
        raise problems.not_found("找不到該使用者")

    if user.id == admin.user.id and payload.status is not None and payload.status is not UserStatus.active:
        # 停用自己會讓平台可能一個管理員都不剩。
        raise problems.conflict("不能停用自己的帳號;請由其他管理員操作。")

    if payload.status is not None:
        if payload.status is UserStatus.active and user.activated_at is None:
            user.activated_at = datetime.now(UTC)
        user.status = payload.status
        # 🔴 稽核的 action 由**新狀態**決定,不是由「呼叫了哪個端點」決定。
        # 網頁那條路(web.py 的 activate/disable)因此會產生完全相同的 action,
        # 查詢時不必知道管理員當時用的是哪個介面——test_audit.py 釘住這一點。
        record(
            session,
            action=(
                AuditAction.user_activate
                if payload.status is UserStatus.active
                else AuditAction.user_disable
            ),
            actor_id=admin.user.id,
            target_type="user",
            target_id=user.id,
            # 🔴 label 一律留空:唯一能寫的「人可讀名稱」是 sub,而 target_id 已經夠回查。
            # 寫 email/姓名進來就是把稽核表變成個資的第二個落地處。
        )
    if payload.platform_role is not None:
        user.platform_role = payload.platform_role
        record(
            session,
            action=AuditAction.user_set_role,
            actor_id=admin.user.id,
            target_type="user",
            target_id=user.id,
            target_label=payload.platform_role.value,
        )

    await session.commit()
    await session.refresh(user)
    log.info(
        "調整使用者",
        extra={
            "target_user_id": str(user.id),
            "new_status": user.status.value,
            "new_role": user.platform_role.value,
        },
    )
    return UserOut.model_validate(user)


# --- 稽核查詢(F54 / T38)---------------------------------------------------


@router.get("/audit", response_model=AuditPage, summary="查詢稽核紀錄(平台管理員)")
async def list_audit_events(
    session: DbSession,
    admin: AdminUser,
    action: Annotated[str | None, Query(description="精確比對,例:project.delete")] = None,
    actor_id: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
    target_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="含,ISO 8601")] = None,
    until: Annotated[datetime | None, Query(description="不含,ISO 8601")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    """查詢稽核紀錄(F54:誰在何時做了什麼)。

    🔴 **只開給平台管理員**(`AdminUser`),專案 owner 也不行。

    這不是新增的限制,是 T37 那個決定的延續:T37 讓下載統計停在「次數」這個粒度
    是刻意的個資決定。若專案 owner 能從這裡看到個別下載者,那個決定就被從另一扇門
    繞過了——表面上沒有 `download_events` 表,實際上同樣的資訊照樣流出去。

    參數:各篩選條件(皆選填)、limit/offset。
    回傳:依 `occurred_at` 倒序的 `AuditPage`。副作用:無(唯讀)。
    """
    conditions = []
    if action:
        conditions.append(AuditEvent.action == action)
    if actor_id:
        conditions.append(AuditEvent.actor_id == parse_uuid(actor_id, "操作者"))
    if target_type:
        conditions.append(AuditEvent.target_type == target_type)
    if target_id:
        conditions.append(AuditEvent.target_id == parse_uuid(target_id, "目標"))
    if since:
        conditions.append(AuditEvent.occurred_at >= since)
    if until:
        conditions.append(AuditEvent.occurred_at < until)

    total = (
        await session.execute(select(func.count()).select_from(AuditEvent).where(*conditions))
    ).scalar_one()
    rows = (
        await session.execute(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return AuditPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditEventOut.model_validate(row) for row in rows],
    )
