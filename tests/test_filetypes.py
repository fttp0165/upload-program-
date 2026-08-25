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


# --- T94 🐛 中文文字檔被判成 binary ---------------------------------------
#
# 根本原因:判型只看前 4096 bytes,尾端幾乎必然切在字元中間。舊的補救寫法是
# 「decode 失敗就砍掉尾端 3 bytes 再試」——對 ASCII 沒事,對中文是錯的:
# 中文一字 3 bytes,殘尾若是 1 或 2 bytes,砍 3 bytes 會把前一個完整的字也砍破,
# 製造出新的不完整字元 → 仍失敗 → 判成 octet-stream → 白名單擋下。
#
# 症狀因此是「有時可以有時不行」(三種對齊有兩種失敗),而且**專打中文**:
# 英文文件單 byte 一字,砍 3 bytes 不會破壞前一個字元。

_中文段落 = "本文件說明客戶端的功能需求與驗收標準,包含介面規格與例外處理。\n"


def test_大中文文字檔在任何切齊下都要判成文字():
    """🔴 一個 ASCII 前綴就會讓 4096 切點落在不同位置——三種都必須過。"""
    for pad in range(3):
        raw = ("x" * pad + _中文段落 * 300).encode("utf-8")
        assert len(raw) > 4096, "要大於判型窗口才會被截斷,否則測不到這個 bug"
        head = raw[:4096]
        assert sniff(head) == "text/plain", f"前綴 {pad} bytes 時誤判為 binary"


def test_中文說明文件可以上傳為doc():
    """卡片上寫著收 Markdown,就必須真的收得下去(Benny 實測的那份規格書)。"""
    raw = ("# 客戶規格書\n\n" + _中文段落 * 300).encode("utf-8")
    ok, mime, reason = check(raw[:4096], ArtifactKind.doc)
    assert ok, f"中文 Markdown 被擋:{mime} / {reason}"
    assert mime == "text/plain"


def test_中文註釋的原始碼也可以上傳為source():
    """同一個 bug 也打到原始碼卡片——公司內部的 .py 帶中文註釋是常態。"""
    code = ('# 這個模組負責計算客戶報表的月結金額,對應需求單 R-123。\n'
            'def 月結(金額):\n    return 金額 * 1.05\n')
    raw = (code * 200).encode("utf-8")
    ok, mime, _ = check(raw[:4096], ArtifactKind.source)
    assert ok and mime == "text/plain"


def test_修正不得放寬到收二進位():
    """護欄:把截斷容錯做寬了,就會變成什麼都當文字收。"""
    # 含 NUL 的二進位
    assert sniff(b"\x00\x01\x02\x03" * 100) == "application/octet-stream"
    # 沒有 NUL,但是非法的 UTF-8 序列(不是被截斷,是根本不合法)
    assert sniff(b"\xff\xfe\xfd\xfc" * 100) == "application/octet-stream"
    # 合法中文開頭、但中段夾雜非法位元組
    raw = (_中文段落 * 10).encode("utf-8") + b"\xff\xff\xff" + (_中文段落 * 10).encode("utf-8")
    assert sniff(raw[:4096]) == "application/octet-stream"


def test_中文HTML仍然一律拒收():
    """修正不得在中文這條路上開一個 HTML 後門。"""
    raw = ("<!DOCTYPE html><html><body>中文網頁內容說明文件。</body></html>\n" * 200).encode("utf-8")
    for kind in ArtifactKind:
        ok, mime, _ = check(raw[:4096], kind)
        assert not ok and mime == "text/html"
