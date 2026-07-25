"""平台管理後台:開通使用者、指派平台角色。

🔴 契約 §4.4:派角色/收權都在**本服務**做,不碰 Keycloak。
IdP 只負責「這個人是誰」,「這個人能做什麼」是我們自己的事。
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .. import problems
from ..models import User, UserStatus
from ..schemas import UserOut, UserPage, UserPatch
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
            user.activated_at = datetime.now(timezone.utc)
        user.status = payload.status
    if payload.platform_role is not None:
        user.platform_role = payload.platform_role

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
