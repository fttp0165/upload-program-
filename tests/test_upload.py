"""上傳 / 下載:magic bytes 判型、SHA-256、配額、下載強制 attachment。"""

import hashlib

from tests.conftest import auth, complete_kinds

ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
ZIP = b"PK\x03\x04" + b"\x00" * 200
HTML = b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>"
PDF = b"%PDF-1.7\n" + b"\x00" * 100


async def _project_and_release(client, token, slug="demo-tool"):
    resp = await client.post(
        "/v1/projects",
        json={"slug": slug, "name": "示範工具", "summary": "測試用"},
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    release = await client.post(
        f"/v1/projects/{slug}/releases",
        json={"version": "v1.0.0", "notes": "首版"},
        headers=auth(token),
    )
    assert release.status_code == 201, release.text
    return slug, release.json()["id"]


async def test_上傳執行檔並算出SHA256(client, active_user, storage):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sha256"] == hashlib.sha256(ELF).hexdigest()
    assert body["size_bytes"] == len(ELF)
    assert body["content_type"] == "application/x-elf"
    assert body["upload_status"] == "ready"
    # MVP 未接掃毒,必須誠實標示
    assert body["scan_status"] == "not_scanned"
    assert len(storage.objects) == 1


async def test_HTML偽裝成執行檔會被擋掉且不留物件(client, active_user, storage):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/evil.exe?kind=binary",
        content=HTML,
        headers=auth(token),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"].endswith("/rejected-file-type")
    assert body["detected_type"] == "text/html"
    assert storage.objects == {}  # 判型不過就中止,不該留下任何物件


async def test_型別與kind不符會被擋(client, active_user):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/manual.pdf?kind=binary",
        content=PDF,
        headers=auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["detected_type"] == "application/pdf"

    ok = await client.put(
        f"/v1/releases/{release_id}/artifacts/manual.pdf?kind=doc",
        content=PDF,
        headers=auth(token),
    )
    assert ok.status_code == 201


async def test_宣告的SHA256不符就作廢(client, active_user, storage):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers={**auth(token), "X-Content-SHA256": "0" * 64},
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/checksum-mismatch")
    assert storage.objects == {}


async def test_超過單檔上限回413(client, active_user, settings):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    too_big = ELF + b"\x00" * settings.max_artifact_bytes
    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/huge.bin?kind=binary",
        content=too_big,
        headers=auth(token),
    )
    assert resp.status_code == 413


async def test_檔名不得含路徑分隔字元(client, active_user):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    resp = await client.put(
        f"/v1/releases/{release_id}/artifacts/..%2F..%2Fetc%2Fpasswd?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    assert resp.status_code in (400, 404)


async def test_下載一律attachment且帶雜湊(client, active_user):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)
    put = await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    artifact_id = put.json()["id"]

    resp = await client.get(
        f"/v1/releases/{release_id}/artifacts/{artifact_id}/download", headers=auth(token)
    )
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-artifact-sha256"] == hashlib.sha256(ELF).hexdigest()
    assert resp.content == ELF


async def test_空版本不能發布發完就鎖住(client, active_user):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)

    empty = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert empty.status_code == 422
    assert empty.json()["type"].endswith("/empty-release")

    await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    await complete_kinds(client, token, release_id)
    published = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert published.status_code == 200
    # T102:按下發布 = **送審**,不是直接上架。
    assert published.json()["status"] == "pending_review"

    # 🔴 T102 起,送審當下就鎖住(原本是發布後才鎖)——待審中若還能換檔,
    # 管理員核准的就不是他看過的那一份,審核會變成可以繞過的形式。
    locked = await client.put(
        f"/v1/releases/{release_id}/artifacts/another.zip?kind=source",
        content=ZIP,
        headers=auth(token),
    )
    assert locked.status_code == 409


async def test_重複發布是冪等的(client, active_user):
    _, token = active_user
    _, release_id = await _project_and_release(client, token)
    await client.put(
        f"/v1/releases/{release_id}/artifacts/tool.bin?kind=binary",
        content=ELF,
        headers=auth(token),
    )
    await complete_kinds(client, token, release_id)
    first = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    second = await client.post(f"/v1/releases/{release_id}/publish", headers=auth(token))
    assert first.status_code == second.status_code == 200
    # T102:兩次都停在待審,重送不會產生兩筆待審或改變狀態。
    assert first.json()["status"] == second.json()["status"] == "pending_review"


async def test_專案容量上限(client, active_user, settings):
    _, token = active_user
    slug, release_id = await _project_and_release(client, token)

    payload = ELF + b"\x00" * (settings.max_artifact_bytes - len(ELF) - 1)
    for i in range(6):
        resp = await client.put(
            f"/v1/releases/{release_id}/artifacts/part{i}.bin?kind=binary",
            content=payload,
            headers=auth(token),
        )
        if resp.status_code == 413:
            assert resp.json()["type"].endswith("/payload-too-large")
            return
    raise AssertionError("專案容量上限未生效")
