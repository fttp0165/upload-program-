"""檔案上傳與下載。

🔴 拓撲決定了做法:MinIO 只在 `backend` 網路,瀏覽器碰不到它,所以**上傳與下載都由本服務
串流轉送**。上傳用 raw body(不是 multipart)——multipart 解析會把大檔落到暫存檔,
違反「容器內不寫檔當狀態」。

🔴 安全:
- 判型看 **magic bytes**,不信副檔名也不信前端送的 Content-Type
- SHA-256 邊收邊算,可與呼叫端宣告值比對
- 下載一律 `Content-Disposition: attachment` + `nosniff`,絕不讓上傳內容在本網域被執行
"""

import logging
import re
from datetime import UTC
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import filetypes, problems
from ..models import Artifact, ArtifactKind, ProjectRole, ReleaseStatus, UploadStatus
from ..schemas import ArtifactOut
from ..security import (
    CurrentUser,
    DbSession,
    get_project,
    parse_uuid,
    require_project_read,
    require_project_role,
)
from ..storage import TooLarge
from .releases import latest_published_release, load_release

router = APIRouter(prefix="/v1/releases", tags=["artifacts"])
log = logging.getLogger(__name__)

FILENAME_RE = re.compile(r"^[^\x00-\x1f/\\]{1,255}$")


class _Rejected(Exception):
    """magic bytes 判型不過;在還沒寫出任何物件前中止上傳。"""

    def __init__(self, mime: str, reason: str) -> None:
        super().__init__(reason)
        self.mime = mime
        self.reason = reason


def _check_filename(filename: str) -> str:
    name = filename.strip()
    if not FILENAME_RE.match(name) or name in {".", ".."}:
        raise problems.bad_request("檔名不合法(不得含路徑分隔字元或控制字元)", "invalid-filename")
    return name


@router.put(
    "/{release_id}/artifacts/{filename}",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
    summary="上傳檔案(request body 為檔案原始位元組)",
)
async def upload_artifact(
    release_id: str,
    filename: str,
    request: Request,
    session: DbSession,
    identity: CurrentUser,
    kind: Annotated[ArtifactKind, Query(description="source / binary / doc")],
    content_length: Annotated[int | None, Header()] = None,
    x_content_sha256: Annotated[str | None, Header()] = None,
) -> ArtifactOut:
    settings = request.app.state.settings
    storage = request.app.state.storage

    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)
    if release.status is ReleaseStatus.published:
        raise problems.conflict("已發布的版本不可再變更檔案;請建立新版本。")

    name = _check_filename(filename)

    # 先用 Content-Length 擋掉明顯過大的請求,不用等收完才發現。
    if content_length is not None:
        if content_length > settings.max_artifact_bytes:
            raise problems.payload_too_large(
                f"單檔上限 {settings.max_artifact_bytes} bytes,本次 {content_length} bytes"
            )
        if release.project.total_bytes + content_length > settings.max_project_bytes:
            raise problems.payload_too_large(
                f"專案容量上限 {settings.max_project_bytes} bytes,已用 {release.project.total_bytes} bytes"
            )

    existing = (
        await session.execute(
            select(Artifact).where(Artifact.release_id == release.id, Artifact.filename == name)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.upload_status is UploadStatus.ready:
        raise problems.conflict(f"檔案 {name} 已存在;請先刪除或換名。")

    artifact = existing or Artifact(
        release_id=release.id,
        kind=kind,
        filename=name,
        size_bytes=0,
        sha256="",
        storage_key="",
        created_by_id=identity.user.id,
    )
    artifact.kind = kind
    artifact.upload_status = UploadStatus.pending
    if existing is None:
        session.add(artifact)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise problems.conflict(f"檔案 {name} 已存在") from None
    await session.refresh(artifact)

    key = f"projects/{release.project_id}/releases/{release.id}/{artifact.id}/{name}"
    detected = filetypes.OCTET_STREAM

    def _on_head(head: bytes) -> None:
        nonlocal detected
        ok, mime, reason = filetypes.check(head, kind)
        detected = mime
        if not ok:
            raise _Rejected(mime, reason)

    try:
        result = await storage.upload_stream(
            key, request.stream(), settings.max_artifact_bytes, on_head=_on_head
        )
    except _Rejected as exc:
        await _mark_failed(session, artifact)
        log.warning(
            "上傳遭拒:型別不符",
            extra={"artifact_id": str(artifact.id), "detected": exc.mime, "kind": kind.value},
        )
        raise problems.unprocessable(
            "rejected-file-type", "檔案型別不被接受", exc.reason, detected_type=exc.mime
        ) from None
    except TooLarge as exc:
        await _mark_failed(session, artifact)
        raise problems.payload_too_large(f"單檔上限 {exc.limit} bytes") from None
    except Exception:
        await _mark_failed(session, artifact)
        raise

    if x_content_sha256 and x_content_sha256.strip().lower() != result.sha256:
        await storage.delete(key)
        await _mark_failed(session, artifact)
        raise problems.unprocessable(
            "checksum-mismatch",
            "SHA-256 不符",
            "實際內容的雜湊與 X-Content-SHA256 宣告值不同,上傳已作廢。",
            expected=x_content_sha256.strip().lower(),
            actual=result.sha256,
        )

    if release.project.total_bytes + result.size_bytes > settings.max_project_bytes:
        await storage.delete(key)
        await _mark_failed(session, artifact)
        raise problems.payload_too_large(f"專案容量上限 {settings.max_project_bytes} bytes")

    artifact.storage_key = key
    artifact.size_bytes = result.size_bytes
    artifact.sha256 = result.sha256
    artifact.hash_verified = True
    artifact.content_type = detected
    artifact.upload_status = UploadStatus.ready
    artifact.completed_at = _now()
    release.project.total_bytes += result.size_bytes
    await session.commit()
    await session.refresh(artifact)

    log.info(
        "上傳完成",
        extra={
            "artifact_id": str(artifact.id),
            "size_bytes": artifact.size_bytes,
            "content_type": detected,
        },
    )
    return ArtifactOut.model_validate(artifact)


def _now():
    from datetime import datetime

    return datetime.now(UTC)


async def _mark_failed(session, artifact: Artifact) -> None:
    artifact.upload_status = UploadStatus.failed
    await session.commit()


@router.get("/{release_id}/artifacts/{artifact_id}/download", summary="下載檔案")
async def download_artifact(
    release_id: str,
    artifact_id: str,
    request: Request,
    session: DbSession,
    identity: CurrentUser,
) -> StreamingResponse:
    release = await load_release(session, release_id)
    role = await require_project_read(session, release.project, identity)
    if release.status is ReleaseStatus.draft and role is None and not identity.user.is_admin:
        raise problems.not_found("找不到該檔案")

    artifact = _find_artifact(release, artifact_id)
    return _download_response(request, artifact)


def _download_response(request: Request, artifact: Artifact) -> StreamingResponse:
    """建構下載回應。

    抽成共用函式的原因:最新版捷徑(F26)也要下載檔案,而**安全標頭絕不能因為換了一條
    路徑就鬆掉**。兩個端點共用同一段程式碼,就不可能有一邊漏掉 attachment 或 nosniff。

    副作用:從物件儲存串流讀取,並寫一筆下載 log。
    """
    if artifact.upload_status is not UploadStatus.ready:
        raise problems.not_found("該檔案尚未上傳完成")

    # 🔴 一律 attachment + octet-stream:不讓上傳內容在本服務網域被瀏覽器直接執行。
    safe_name = quote(artifact.filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
        "Content-Length": str(artifact.size_bytes),
        "X-Content-Type-Options": "nosniff",
        "X-Artifact-SHA256": artifact.sha256,
        "X-Artifact-Scan-Status": artifact.scan_status.value,
    }
    log.info("下載檔案", extra={"artifact_id": str(artifact.id)})
    return StreamingResponse(
        request.app.state.storage.iter_object(artifact.storage_key),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.delete(
    "/{release_id}/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除檔案",
)
async def delete_artifact(
    release_id: str,
    artifact_id: str,
    request: Request,
    session: DbSession,
    identity: CurrentUser,
) -> Response:
    release = await load_release(session, release_id)
    await require_project_role(session, release.project, identity, ProjectRole.maintainer)
    if release.status is ReleaseStatus.published:
        raise problems.conflict("已發布的版本不可刪除檔案;請建立新版本。")

    artifact = _find_artifact(release, artifact_id)
    if artifact.storage_key:
        await request.app.state.storage.delete(artifact.storage_key)
    if artifact.upload_status is UploadStatus.ready:
        release.project.total_bytes = max(0, release.project.total_bytes - artifact.size_bytes)
    await session.delete(artifact)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _find_artifact(release, artifact_id: str) -> Artifact:
    wanted = parse_uuid(artifact_id, "檔案")
    for artifact in release.artifacts:
        if artifact.id == wanted:
            return artifact
    raise problems.not_found("找不到該檔案")


# --- 最新版捷徑(F26)-------------------------------------------------------
#
# 需要另立一個 router 是因為路徑前綴不同(/v1/projects 而非 /v1/releases),
# 而 APIRouter 的 prefix 是整個 router 共用的,無法逐條覆寫。

latest_router = APIRouter(prefix="/v1/projects", tags=["artifacts"])


@latest_router.get(
    "/{slug}/releases/latest/artifacts/{filename}/download",
    summary="下載最新已發布版本的指定檔案",
)
async def download_latest_artifact(
    slug: str,
    filename: str,
    request: Request,
    session: DbSession,
    identity: CurrentUser,
) -> StreamingResponse:
    """以**檔名**定位最新版的檔案(F26)。

    為什麼用檔名而不是 UUID:這條網址的存在意義就是「能寫進文件而不會失效」。
    `.../latest/artifacts/tool.exe/download` 可讀、可預測、跨版本不變;
    UUID 每發一次新版就換一組,寫進 wiki 隔天就壞。
    """
    project = await get_project(session, slug)
    await require_project_read(session, project, identity)
    release = await latest_published_release(session, project)

    name = filename.strip()
    for artifact in release.artifacts:
        if artifact.filename == name:
            return _download_response(request, artifact)
    raise problems.not_found(f"最新版本 {release.version} 中沒有檔案 {name}")
