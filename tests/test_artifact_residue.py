"""T107:被拒收 / 中斷的上傳不留殘骸。

T106 讓 0 bytes 的殘骸不顯示,但**列還在** —— 而每一次被 magic bytes 擋下的
上傳都會再留一列。留著的那一列不會告訴任何人原因,它只顯示 0 bytes:
🔴 **一個不帶資訊的殘骸不是紀錄,是垃圾。**

失敗原因當下就以 RFC 7807 回給呼叫端了(`rejected-file-type` 含 `detected_type`),
log 也有一行。所以改成失敗即刪列,並補一條稽核 `artifact.upload_rejected` ——
「誰在何時試著傳了一個被擋下的檔」對散布可執行檔的平台是有意義的訊號。
"""

from sqlalchemy import func, select

from app.models import Artifact, AuditEvent
from tests.conftest import auth, make_user

HTML = b"<!DOCTYPE html><html><body>x</body></html>" + b" " * 200
EXE = b"MZ\x90\x00" + b"\x00" * 60


async def _release(client, app, oidc, slug):
    await make_user(app, f"{slug}-owner")
    token = oidc.issue(f"{slug}-owner")
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "殘骸測試", "summary": "x", "visibility": "internal"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "1.0.0", "notes": "n"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    return token, resp.json()["id"]


async def _artifact_count(app, release_id) -> int:
    import uuid as _uuid

    async with app.state.sessionmaker() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.release_id == _uuid.UUID(release_id))
            )
        ).scalar_one()


async def _actions(app) -> list[str]:
    async with app.state.sessionmaker() as session:
        rows = (await session.execute(select(AuditEvent.action))).scalars().all()
    return list(rows)


async def test_被拒收的上傳不留下artifact列(client, app, oidc):
    """HTML 一律拒收(本專案自我加嚴的紅線)。擋下之後不該留殘骸。"""
    token, release_id = await _release(client, app, oidc, "res-a")

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/dashboard.html?kind=doc",
        content=HTML,
        headers=auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"].endswith("rejected-file-type")

    assert await _artifact_count(app, release_id) == 0, "被擋下的上傳留下了 0 bytes 的殘骸"


async def test_被拒收時留下稽核(client, app, oidc):
    token, release_id = await _release(client, app, oidc, "res-b")

    await client.put(
        f"/v1/releases/{release_id}/artifacts/dashboard.html?kind=doc",
        content=HTML,
        headers=auth(token),
    )

    actions = await _actions(app)
    assert "artifact.upload_rejected" in actions, (
        "刪了列就更需要稽核 —— 否則「有人反覆試著傳 HTML」這件事完全沒有痕跡"
    )


async def test_超過單檔上限也不留列(client, app, oidc, settings):
    token, release_id = await _release(client, app, oidc, "res-c")

    too_big = EXE + b"\x00" * (settings.max_artifact_bytes + 10)
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/big.exe?kind=binary",
        content=too_big,
        headers=auth(token),
    )
    assert resp.status_code == 413, resp.text
    assert await _artifact_count(app, release_id) == 0


async def test_成功的上傳完全不受影響(client, app, oidc):
    """守門:別把好的一起刪掉。"""
    token, release_id = await _release(client, app, oidc, "res-d")

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/app.exe?kind=binary",
        content=EXE,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["upload_status"] == "ready"
    assert await _artifact_count(app, release_id) == 1
    assert "artifact.upload_rejected" not in await _actions(app)


async def test_拒收之後重傳同名檔案仍然成功(client, app, oidc):
    """守門:刪列不能擋住下一次 —— 若殘骸還在,同名重傳會撞 UNIQUE 或 conflict。"""
    token, release_id = await _release(client, app, oidc, "res-e")

    bad = await client.put(
        f"/v1/releases/{release_id}/artifacts/thing.exe?kind=binary",
        content=HTML,
        headers=auth(token),
    )
    assert bad.status_code == 422, bad.text

    good = await client.put(
        f"/v1/releases/{release_id}/artifacts/thing.exe?kind=binary",
        content=EXE,
        headers=auth(token),
    )
    assert good.status_code == 201, good.text
    assert await _artifact_count(app, release_id) == 1
