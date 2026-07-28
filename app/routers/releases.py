"""版本(release):一次發布 = 一組檔案。draft 可改可刪,published 定版。"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import problems
from ..audit import AuditAction, record
from ..models import ProjectRole, Release, ReleaseStatus, UploadStatus
from ..queries import query_releases
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


async def latest_published_release(session, project) -> Release:
    """取專案最新的**已發布**版本(F26)。

    🔴 以 `published_at` 判定,**不得用版本號字串排序**——版本號是自由字串(非強制 SemVer),
    字串排序會讓 `v9` 排在 `v10` 前面,發到第十版就會靜默地一直給出舊版而沒人發現。
    也不用 `created_at`:先建的版本可能後發布,建立順序不等於發布順序。

    draft 不算:latest 是給使用者抓的,draft 是作者的工作區。
    """
    stmt = (
        select(Release)
        .options(selectinload(Release.project))
        .where(
            Release.project_id == project.id,
            Release.status == ReleaseStatus.published,
        )
        .order_by(Release.published_at.desc())
        .limit(1)
    )
    release = (await session.execute(stmt)).scalar_one_or_none()
    if release is None:
        raise problems.not_found(f"專案 {project.slug} 尚未發布任何版本")
    return release


@router.get(
    "/projects/{slug}/releases/latest",
    response_model=ReleaseOut,
    summary="最新已發布版本(固定網址,可寫進文件)",
)
async def get_latest_release(
    slug: str, session: DbSession, identity: CurrentUser
) -> ReleaseOut:
    project = await get_project(session, slug)
    await require_project_read(session, project, identity)
    return ReleaseOut.model_validate(await latest_published_release(session, project))


@router.get("/projects/{slug}/releases", response_model=ReleasePage, summary="列出版本")
async def list_releases(
    slug: str,
    session: DbSession,
    identity: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReleasePage:
    """列出版本。

    🐛 排序依 `published_at` 而非 `created_at`(T43 修正):
    先建的版本可能後發布,用建立時間排會讓列表第一筆與 `/latest` 指向不同版本。
    查詢與 draft 可見性抽在 `queries.query_releases()`,**與歷史頁網頁共用**。
    """
    project = await get_project(session, slug)
    role = await require_project_read(session, project, identity)

    total, rows = await query_releases(
        session,
        project,
        include_drafts=role is not None or identity.user.is_admin,
        limit=limit,
        offset=offset,
    )
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
    # 先 flush 再記稽核(同 projects.create_project):版本號重複時不留假紀錄,
    # 而且 `release.id` 要 flush 之後才存在。
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise problems.conflict(f"版本 {payload.version} 已存在") from None

    record(
        session,
        action=AuditAction.release_create,
        actor_id=identity.user.id,
        target_type="release",
        target_id=release.id,
        target_label=f"{project.slug}:{release.version}",
    )
    await session.commit()
    await session.refresh(release)
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
    release.published_at = datetime.now(UTC)
    record(
        session,
        action=AuditAction.release_publish,
        actor_id=identity.user.id,
        target_type="release",
        target_id=release.id,
        target_label=f"{release.project.slug}:{release.version}",
    )
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
    # 刪除前先取:delete 之後這些屬性就過期了,而它們正是稽核唯一有價值的部分。
    release_id_value, label = release.id, f"{project.slug}:{release.version}"
    freed = sum(a.size_bytes for a in release.artifacts if a.upload_status is UploadStatus.ready)
    await request.app.state.storage.delete_prefix(f"projects/{project.id}/releases/{release.id}/")
    await session.delete(release)
    project.total_bytes = max(0, project.total_bytes - freed)
    record(
        session,
        action=AuditAction.release_delete,
        actor_id=identity.user.id,
        target_type="release",
        target_id=release_id_value,
        target_label=label,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
