"""T37 下載次數統計(F43)。

計數用的是 `artifacts.download_count` 一個整數欄位,不是事件表——
F43 只要求「次數」,「誰下載了什麼」是稽核(T38/F54)的職責。
🔴 任務表要求「統計不記個資」:用計數欄位的話,這件事是**結構上做不到**,
而不是靠自律,所以本檔最後一條測試直接去釘住這個結構。

算的是「**發起**下載」(回應建構的那一刻),不是「完成下載」——
中途中斷仍算一次,見開發日誌的取捨說明。
"""

import asyncio
import uuid

from tests.conftest import auth, complete_kinds, make_user, submit_release

ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200


async def _project_release_artifact(client, token, slug="dl-demo", filename="tool.bin"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "下載統計示範", "summary": "測試用"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": "首版"},
        headers=auth(token),
    )
    assert release.status_code == 201, release.text
    release_id = release.json()["id"]

    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/{filename}?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert up.status_code == 201, up.text
    return slug, release_id, up.json()["id"]


async def _download(client, token, release_id, artifact_id):
    return await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(token)
    )


async def _artifact(client, token, release_id, artifact_id) -> dict:
    resp = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    for item in resp.json()["artifacts"]:
        if item["id"] == artifact_id:
            return item
    raise AssertionError(f"版本回應中找不到檔案 {artifact_id}")


# --- 基本累計 ---------------------------------------------------------------


async def test_剛上傳的檔案計數為零(client, active_user):
    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)
    assert (await _artifact(client, token, release_id, artifact_id))["download_count"] == 0


async def test_下載三次就累計三次(client, active_user):
    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)

    for _ in range(3):
        resp = await _download(client, token, release_id, artifact_id)
        assert resp.status_code == 200, resp.text

    assert (await _artifact(client, token, release_id, artifact_id))["download_count"] == 3


async def test_最新版捷徑下載算同一個計數器(client, active_user):
    """🔴 兩條下載路徑共用 `_download_response()`,計數不得因為換路徑就分岔。"""
    _, token = active_user
    slug, release_id, artifact_id = await _project_release_artifact(client, token)
    await complete_kinds(client, token, release_id)
    published = await submit_release(client, token, release_id, approve=True)  # T102
    assert published["status"] == "published"

    direct = await _download(client, token, release_id, artifact_id)
    assert direct.status_code == 200
    shortcut = await client.get(
        f"/v1/projects/{slug}/releases/latest/artifacts/tool.bin/download", headers=auth(token)
    )
    assert shortcut.status_code == 200, shortcut.text

    assert (await _artifact(client, token, release_id, artifact_id))["download_count"] == 2


async def test_版本計數是底下所有檔案的加總(client, active_user):
    """版本不另存計數:兩個計數器分開存,刪檔或補傳漏掉一次就永遠對不起來。"""
    _, token = active_user
    slug, release_id, first_id = await _project_release_artifact(client, token)
    second = await client.put(
        f"/v1/releases/{release_id}/artifacts/other.bin?kind=binary",
        content=ELF + b"\x01",
        headers=auth(token),
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]

    await _download(client, token, release_id, first_id)
    await _download(client, token, release_id, first_id)
    await _download(client, token, release_id, second_id)

    release = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert release.status_code == 200
    assert release.json()["download_count"] == 3


async def test_刪除檔案後版本加總跟著減少(client, active_user):
    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)
    await _download(client, token, release_id, artifact_id)

    release = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert release.json()["download_count"] == 1

    gone = await client.delete(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}", headers=auth(token)
    )
    assert gone.status_code == 204, gone.text

    release = await client.get(f"/v1/releases/{release_id}", headers=auth(token))
    assert release.json()["download_count"] == 0


# --- 失敗的下載不計數 -------------------------------------------------------


async def test_尚未上傳完成的檔案下載404且不計數(client, active_user, app):
    """計數必須在 `upload_status is ready` 檢查**之後**,否則失敗的下載也會灌水。"""
    from sqlalchemy import select

    from app.models import Artifact, UploadStatus

    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)

    async with app.state.sessionmaker() as session:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == uuid.UUID(artifact_id)))
        ).scalar_one()
        artifact.upload_status = UploadStatus.pending
        await session.commit()

    resp = await _download(client, token, release_id, artifact_id)
    assert resp.status_code == 404
    assert "尚未上傳完成" in resp.json()["detail"]

    async with app.state.sessionmaker() as session:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == uuid.UUID(artifact_id)))
        ).scalar_one()
        assert artifact.download_count == 0


async def test_無權限的下載不計數(client, active_user, app, oidc):
    """private 專案對非成員回 404;那次請求不該讓計數動。"""
    from sqlalchemy import select

    from app.models import Artifact

    _, token = active_user
    resp = await client.post(
        "/v1/projects",
        json={"slug": "secret-dl", "name": "機密", "visibility": "private"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release = await client.post(
        "/v1/projects/secret-dl/releases", json={"version": "v1.0.0"}, headers=auth(token)
    )
    release_id = release.json()["id"]
    up = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    artifact_id = up.json()["id"]

    await make_user(app, "sub-nosy")
    outsider = oidc.issue("sub-nosy")
    blocked = await _download(client, outsider, release_id, artifact_id)
    assert blocked.status_code == 404
    assert "找不到" in blocked.json()["detail"]

    async with app.state.sessionmaker() as session:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == uuid.UUID(artifact_id)))
        ).scalar_one()
        assert artifact.download_count == 0


# --- 併發 -------------------------------------------------------------------


async def test_併發下載不掉數(client, active_user):
    """🔴 `download_count += 1` 是讀-改-寫,併發會默默掉數且不會有任何錯誤訊息。

    必須把加法交給資料庫(`SET download_count = download_count + 1`)。
    """
    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)

    results = await asyncio.gather(
        *[_download(client, token, release_id, artifact_id) for _ in range(5)]
    )
    assert all(r.status_code == 200 for r in results)
    assert (await _artifact(client, token, release_id, artifact_id))["download_count"] == 5


# --- 🔴 不記個資 -------------------------------------------------------------


async def test_統計在結構上不可能記到個資(client, active_user, app):
    """🔴 任務表:「統計不記個資」。

    這條測試釘住的是**結構**而不是行為:`artifacts` 沒有任何可以放「誰下載」的欄位,
    而且一次下載不會在任何表新增列。所以「不記個資」不是靠自律,是做不到。
    """
    from sqlalchemy import func, select

    from app.db import Base
    from app.models import Artifact

    _, token = active_user
    _, release_id, artifact_id = await _project_release_artifact(client, token)

    # 1) 計數欄位本身不帶身分,且整張表沒有下載者相關欄位
    columns = {c.name for c in Artifact.__table__.columns}
    assert "download_count" in columns
    forbidden = {"downloaded_by", "downloader_id", "downloader_sub", "last_downloaded_by", "ip"}
    assert not (columns & forbidden), f"artifacts 出現了下載者欄位:{columns & forbidden}"

    async def _row_counts() -> dict[str, int]:
        async with app.state.sessionmaker() as session:
            return {
                name: (
                    await session.execute(select(func.count()).select_from(table))
                ).scalar_one()
                for name, table in Base.metadata.tables.items()
            }

    before = await _row_counts()
    resp = await _download(client, token, release_id, artifact_id)
    assert resp.status_code == 200
    after = await _row_counts()

    # 2) 一次下載只在 `audit_events` 新增一列,其餘各表一列都不增。
    #
    # ⚠️ 本條在 T38 之前是「**任何**表都不增」。T38(F54 稽核紀錄)之後那句話不再成立
    # ——但這**不是回歸,是需求本來就要的**:F54 的字面就寫著「上傳與下載了什麼」。
    #
    # 這裡採**具名例外**而不是把斷言放寬(同 `/account` 那條前綴紅線的處理方式)。
    # T37 的保證因此縮小到它真正保護的範圍:**統計這條路**不記個資 ——
    # `artifacts` 沒有下載者欄位(上面第 1 點),統計數字永遠只是個總數。
    # 「誰下載了什麼」只存在 `audit_events`,而那張表:
    #   - 只有平台管理員查得到(routers/admin.py::list_audit_events)
    #   - 有保留期(app/audit.py::purge_expired)
    # 這兩件事正是 T37 當初把它推給稽核時講好的條件,不是事後才補的說法。
    AUDIT_TABLE = "audit_events"
    grew = {k for k in before if before[k] != after[k]}
    assert grew == {AUDIT_TABLE}, f"下載新增了非預期的資料列:{ {k: (before[k], after[k]) for k in grew} }"
    assert after[AUDIT_TABLE] == before[AUDIT_TABLE] + 1
