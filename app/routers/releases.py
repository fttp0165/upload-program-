"""版本(release):一次發布 = 一組檔案。draft 可改可刪,published 定版。"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import problems
from ..audit import AuditAction, record
from ..models import ArtifactKind, ProjectRole, Release, ReleaseStatus, UploadStatus
from ..queries import query_releases
from ..schemas import ReleaseCreate, ReleaseOut, ReleasePage, ReleaseReject, ReleaseUpdate
from ..security import (
    AdminUser,
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


# T65:發布的三類齊備規則(Benny 2026-07-31 裁示)。
# 每一版必須是「可用的交付」:更新文件、執行檔、原始碼包各至少一個——
# 缺任何一類的版本對使用者都是半成品(有檔沒文件、有文件沒程式)。
#
# T86:同一份資料現在有兩個用途——「還缺什麼」那句話,以及上傳頁的三格卡片。
# 合成一個結構而不是兩張表:分成兩份的話,哪天加一類就會出現「卡片有、缺項沒有」
# 這種只在特定情境才看得出來的不一致。
# 🔴 **順序有意義**:卡片與缺項訊息都照這裡的順序,兩處念起來才一樣
#    (Benny 2026-08-05 指定:說明 → 程式碼 → 執行檔)。
@dataclass(frozen=True)
class RequiredKind:
    """一個必備類別:技術值 + 缺項訊息用的長標籤 + 卡片用的短標題與說明。"""

    kind: ArtifactKind
    label: str
    title: str
    hint: str


REQUIRED_KINDS: tuple[RequiredKind, ...] = (
    RequiredKind(ArtifactKind.doc, "更新文件(doc)", "說明", "使用說明、更新內容(PDF / Markdown / 文字檔)"),
    RequiredKind(ArtifactKind.source, "原始碼包(source)", "程式碼", "原始碼壓縮檔(zip / tar.gz)"),
    RequiredKind(ArtifactKind.binary, "執行檔(binary)", "執行檔", "可以直接跑的檔案或安裝包"),
)


def missing_required_kinds(release: Release) -> list[str]:
    """回傳缺少的類別中文標籤(依 `REQUIRED_KINDS` 的固定順序);齊了回空 list。

    只算 upload_status=ready 的檔案——上傳到一半的不算數。
    API 與網頁共用這一條,規則只存在一份。
    """
    present = {a.kind for a in release.artifacts if a.upload_status is UploadStatus.ready}
    return [item.label for item in REQUIRED_KINDS if item.kind not in present]


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
    missing = missing_required_kinds(release)
    if missing:
        raise problems.unprocessable(
            "release-missing-kinds",
            "發布內容不齊",
            "每一版發布必須包含:更新文件(doc)、執行檔(binary)、原始碼包(source)"
            f"各至少一個;目前缺:{'、'.join(missing)}。",
        )

    # T123:作者按「發布」= **送審**,不是直接上架。
    # 🔴 `published_at` 刻意留到核准當下才寫——它是「何時變成可下載」,
    # `latest_published_release()` 依它排序,語意不能因為多了一道審核而漂移。
    release.status = ReleaseStatus.pending_review
    release.review_note = ""  # 重送時清掉上一次的退回理由,否則畫面會顯示已經處理過的舊話
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
    log.info("送出審核", extra={"release_id": str(release.id), "artifacts": len(ready)})
    return ReleaseOut.model_validate(release)


@router.post("/releases/{release_id}/approve", response_model=ReleaseOut, summary="核准版本")
async def approve_release(
    release_id: str, session: DbSession, identity: AdminUser
) -> ReleaseOut:
    """核准一個待審版本,使其正式可下載。

    參數:release_id。回傳:更新後的版本。副作用:改寫狀態與 `published_at`、留稽核。

    🔴 **只有平台管理員能審**(`AdminUser`)。專案 maintainer 能送審,但不能核准
    自己的東西——否則審核只是多按一個鈕。

    ⚠️ 已知取捨:管理員可以核准**自己**發布的版本。本系統的管理員是平台維運者
    而非外部稽核;禁止自審會讓「只有一位管理員」的情境完全動不了,而那正是現況。
    """
    release = await load_release(session, release_id)
    if release.status is ReleaseStatus.published:
        return ReleaseOut.model_validate(release)  # 冪等:重複核准不算錯
    if release.status is not ReleaseStatus.pending_review:
        raise problems.conflict("只有待審中的版本可以核准。")

    release.status = ReleaseStatus.published
    release.published_at = datetime.now(UTC)
    release.reviewed_by_id = identity.user.id
    release.reviewed_at = datetime.now(UTC)
    release.review_note = ""
    record(
        session,
        action=AuditAction.release_approve,
        actor_id=identity.user.id,
        target_type="release",
        target_id=release.id,
        target_label=f"{release.project.slug}:{release.version}",
    )
    await session.commit()
    await session.refresh(release)
    log.info("核准版本", extra={"release_id": str(release.id)})
    return ReleaseOut.model_validate(release)


@router.post("/releases/{release_id}/reject", response_model=ReleaseOut, summary="退回版本")
async def reject_release(
    release_id: str, payload: ReleaseReject, session: DbSession, identity: AdminUser
) -> ReleaseOut:
    """退回一個待審版本,並附上理由。

    參數:release_id、payload.note 退回理由(必填)。
    回傳:更新後的版本。副作用:改寫狀態與 `review_note`、留稽核。

    🔴 **理由必填**(schema 層已擋空字串與純空白)。沒有理由的退回,作者只能猜,
    然後重傳一次一模一樣的東西——理由欄位不是裝飾,是這個流程能不能運轉的關鍵。

    退回後回到 `draft` 而不是留在待審:作者要能改,而 `draft` 正是「可以改」的狀態。
    """
    release = await load_release(session, release_id)
    if release.status is not ReleaseStatus.pending_review:
        raise problems.conflict("只有待審中的版本可以退回。")

    release.status = ReleaseStatus.draft
    release.review_note = payload.note.strip()
    release.reviewed_by_id = identity.user.id
    release.reviewed_at = datetime.now(UTC)
    record(
        session,
        action=AuditAction.release_reject,
        actor_id=identity.user.id,
        target_type="release",
        target_id=release.id,
        target_label=f"{release.project.slug}:{release.version}",
    )
    await session.commit()
    await session.refresh(release)
    # 🔴 log 不記理由:那是使用者寫的自由文字,可能夾帶任何東西。
    log.info("退回版本", extra={"release_id": str(release.id)})
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
