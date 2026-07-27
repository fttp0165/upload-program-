"""magic bytes 判型的單元測試(不經 HTTP)。"""

import pytest

from app.filetypes import check, sniff
from app.models import ArtifactKind


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\x7fELF\x02\x01", "application/x-elf"),
        (b"MZ\x90\x00", "application/vnd.microsoft.portable-executable"),
        (b"PK\x03\x04\x14", "application/zip"),
        (b"\x1f\x8b\x08", "application/gzip"),
        (b"%PDF-1.4", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
        (b"#!/bin/sh\necho hi\n", "text/plain"),
        (b"<!DOCTYPE html><html>", "text/html"),
        (b"\x00\x01\x02\x03\xff\xfe", "application/octet-stream"),
    ],
)
def test_判型(head: bytes, expected: str):
    assert sniff(head) == expected


def test_tar的magic在offset257():
    head = b"\x00" * 257 + b"ustar\x00"
    assert sniff(head) == "application/x-tar"


def test_HTML與SVG在任何kind都被拒():
    for kind in ArtifactKind:
        ok, mime, reason = check(b"<!DOCTYPE html><html>", kind)
        assert not ok
        assert mime == "text/html"
        assert "瀏覽器" in reason


def test_kind白名單():
    ok, _, _ = check(b"\x7fELF\x02", ArtifactKind.binary)
    assert ok
    # 執行檔不該以「文件」名義上傳
    ok, _, _ = check(b"\x7fELF\x02", ArtifactKind.doc)
    assert not ok
    # PDF 不該以「執行檔」名義上傳
    ok, _, _ = check(b"%PDF-1.4", ArtifactKind.binary)
    assert not ok


def test_空檔案不當成文字():
    assert sniff(b"") == "application/octet-stream"
