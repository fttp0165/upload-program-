"""版本(release):一次發布 = 一組檔案。draft 可改可刪,published 定版。"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import problems
from ..models import ProjectRole, Release, ReleaseStatus, UploadStatus
from ..schemas import ReleaseCreate, ReleaseOut, ReleasePage, ReleaseUpdate
from ..security import (
    CurrentUser,
    DbSession,
    get_project,
    parse_uuid,
    require_project_read,
    require_project_role,
)

router = APIRouter(prefix="/v1", tags=["releases"])
log = logging.getLogger(__name__)


async def load_release(session, release_id: str) -> Release:
    stmt = (
        select(Release)
        .options(selectinload(Release.artifacts), selectinload(Release.project))
        .where(Release.id == parse_uuid(release_id, "版本"))
    )
    release = (await session.execute(stmt)).scalar_one_or_none()
    if release is None:
        raise problems.not_found("找不到該版本")
    return release


@router.get("/projects/{slug}/releases", response_model=ReleasePage, summary="列出版本")
async def list_releases(
    slug: str,
    session: DbSession,
    identity: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReleasePage:
    project = await get_project(session, slug)
    role = await require_project_read(session, project, identity)

    stmt = select(Release).where(Release.project_id == project.id)
    count_stmt = select(func.count()).select_from(Release).where(Release.project_id == project.id)
    # 非成員只看得到已發布的版本;draft 是作者的工作區。
    if role is None and not identity.user.is_admin:
        stmt = stmt.where(Release.status == ReleaseStatus.published)
        count_stmt = count_stmt.where(Release.status == ReleaseStatus.published)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.options(selectinload(Release.artifacts))
            .order_by(Release.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return ReleasePage(
        total=total,
        limit=limit,
        offset=offset,
        items=[ReleaseOut.model_validate(r) for r in rows],
    )


@router.post(
    "/projects/{slug}/releases",
    response_model=ReleaseOut,
    status_code=status.HTTP_201_CREATED,
    summary="建立版本(draft)",
)
async def create_release(
    slug: str, payload: ReleaseCreate, session: DbSession, identity: CurrentUser
) -> ReleaseOut:
    project = await get_project(session, slug)
    await require_project_role(session, project, identity, ProjectRole.maintainer)

    release = Release(
        project_id=project.id,
        version=payload.version,
        notes=payload.notes,
        created_by_id=identity.user.id,
    )
    session.add(release)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise problems.conflict(f"版本 {payload.version} 已存在") from None

    await session.refresh(release)
    log.info("建立版本", extra={"release_id": str(release.id), "version": release.version})
    return ReleaseOut.model_validate(release)


@router.get("/releases/{release_id}", response_model=ReleaseOut, summary="版本詳情")
async def get_release(release_id: str, session: DbSession, identity: CurrentUser) -> ReleaseOut:
    release = await load_release(session, release_id)
    role = await require_project_read(session, release.project, identity)
    if release.status is ReleaseStatus.draft and role is None and not identity.user.is_admin:
        raise problems.not_found("找不到該版本")
    return ReleaseOut.model_validate(release)


@router.patch("/releases/{release_id}", response_model=ReleaseOut, summary="修改版本說明")
async def update_release(
    release_id: str, payload: ReleaseUpdate, session: DbSession, identity: CurrentUser
) -> ReleaseOut:
    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)
    if payload.notes is not None:
        release.notes = payload.notes
    await session.commit()
    await session.refresh(release)
    return ReleaseOut.model_validate(release)


@router.post("/releases/{release_id}/publish", response_model=ReleaseOut, summary="發布版本")
async def publish_release(
    release_id: str, session: DbSession, identity: CurrentUser
) -> ReleaseOut:
    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)

    if release.status is ReleaseStatus.published:
        return ReleaseOut.model_validate(release)  # 冪等:重複發布不算錯
    ready = [a for a in release.artifacts if a.upload_status is UploadStatus.ready]
    if not ready:
        raise problems.unprocessable(
            "empty-release", "版本沒有檔案", "至少要有一個上傳完成的檔案才能發布。"
        )

    release.status = ReleaseStatus.published
    release.published_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(release)
    log.info("發布版本", extra={"release_id": str(release.id), "artifacts": len(ready)})
    return ReleaseOut.model_validate(release)


@router.delete(
    "/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT, summary="刪除版本"
)
async def delete_release(
    release_id: str, request: Request, session: DbSession, identity: CurrentUser
) -> Response:
    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.owner)

    project = release.project
    freed = sum(a.size_bytes for a in release.artifacts if a.upload_status is UploadStatus.ready)
    await request.app.state.storage.delete_prefix(f"projects/{project.id}/releases/{release.id}/")
    await session.delete(release)
    project.total_bytes = max(0, project.total_bytes - freed)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
