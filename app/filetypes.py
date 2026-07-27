"""Magic bytes 判型。

平台安全規範:檔案上傳必須驗 **MIME 與 magic bytes**——不信任副檔名,也不信任
前端送來的 Content-Type。本模組刻意不依賴 libmagic,避免 image 多帶一個系統相依。

本服務散布的是**可執行檔**,所以判型不只是好習慣:誤把 HTML/腳本當成無害內容供應,
等於在自己的網域上開 XSS。下載端另有 `Content-Disposition: attachment` + `nosniff` 兜底。
"""

from .models import ArtifactKind

OCTET_STREAM = "application/octet-stream"

# (offset, 位元組樣式, MIME) —— 依特徵長度由長到短比對。
_SIGNATURES: list[tuple[int, bytes, str]] = [
    (0, b"\x7fELF", "application/x-elf"),  # Linux 執行檔 / .so
    (0, b"MZ", "application/vnd.microsoft.portable-executable"),  # Windows .exe/.dll
    (0, b"\xca\xfe\xba\xbe", "application/x-mach-binary"),  # macOS universal
    (0, b"\xcf\xfa\xed\xfe", "application/x-mach-binary"),  # macOS 64-bit
    (0, b"\xfe\xed\xfa\xce", "application/x-mach-binary"),
    (0, b"\xca\xfe\xba\xbe", "application/java-vm"),  # class 檔(與 mach-o 同前綴,先判 mach-o)
    (0, b"PK\x03\x04", "application/zip"),  # zip / jar / whl / docx / apk
    (0, b"PK\x05\x06", "application/zip"),  # 空 zip
    (0, b"\x1f\x8b", "application/gzip"),  # .gz / .tgz
    (0, b"BZh", "application/x-bzip2"),
    (0, b"\xfd7zXZ\x00", "application/x-xz"),
    (0, b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (0, b"Rar!\x1a\x07", "application/vnd.rar"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),  # .msi / 舊 Office
    (0, b"!<arch>\ndebian", "application/vnd.debian.binary-package"),
    (0, b"\xed\xab\xee\xdb", "application/x-rpm"),
    (0, b"%PDF-", "application/pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"GIF8", "image/gif"),
    (257, b"ustar", "application/x-tar"),  # tar 的 magic 在 offset 257
]

# 各 kind 允許的 MIME。刻意採白名單:不認得的型別一律擋下,由人決定要不要放行。
_ALLOWED: dict[ArtifactKind, set[str]] = {
    ArtifactKind.binary: {
        "application/x-elf",
        "application/vnd.microsoft.portable-executable",
        "application/x-mach-binary",
        "application/java-vm",
        "application/zip",
        "application/gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/x-ole-storage",
        "application/vnd.debian.binary-package",
        "application/x-rpm",
        OCTET_STREAM,
    },
    ArtifactKind.source: {
        "application/zip",
        "application/gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/vnd.rar",
        "text/plain",
    },
    ArtifactKind.doc: {
        "application/pdf",
        "application/zip",  # docx / odt
        "application/x-ole-storage",  # 舊版 .doc
        "text/plain",  # 含 .md
        "image/png",
        "image/jpeg",
        "image/gif",
    },
}

# 這些型別即使 kind 允許也一律擋:會在瀏覽器裡被當成可執行內容。
_ALWAYS_REJECT_PREFIXES = ("text/html", "image/svg+xml")


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # 截斷的多位元組字元會造成誤判,允許尾端 3 bytes 的不完整。
        try:
            head[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def sniff(head: bytes) -> str:
    """依檔頭判 MIME;判不出來時回 application/octet-stream 或 text/plain。"""
    for offset, pattern, mime in sorted(_SIGNATURES, key=lambda s: -len(s[1])):
        if head[offset : offset + len(pattern)] == pattern:
            return mime
    lowered = head[:512].lstrip().lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "text/html"
    if lowered.startswith(b"<svg") or b"<svg" in lowered[:200]:
        return "image/svg+xml"
    return "text/plain" if _looks_like_text(head) else OCTET_STREAM


def check(head: bytes, kind: ArtifactKind) -> tuple[bool, str, str]:
    """回傳 (是否放行, 判定的 MIME, 拒絕理由)。"""
    mime = sniff(head)
    if mime.startswith(_ALWAYS_REJECT_PREFIXES):
        return False, mime, f"偵測到 {mime}:可在瀏覽器中被當成可執行內容,本平台一律不收。"
    if mime not in _ALLOWED[kind]:
        allowed = ", ".join(sorted(_ALLOWED[kind]))
        return False, mime, f"檔案實際型別為 {mime},不在 kind={kind.value} 的允許清單({allowed})。"
    return True, mime, ""
