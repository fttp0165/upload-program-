"""T91:使用教學頁的視覺化圖示(內嵌 SVG)。

Benny 要求:「教學加上 頁面圖 的 svg 檔 視覺化教學」。

使用者卡住的地方通常是**空間問題**(「建立版本的按鈕在哪一頁」、
「三個上傳格子是同一頁還是三頁」),而文字清單回答不了空間問題。

🔴 本檔釘住三條界線:

1. **內嵌 SVG,不得引用外部資源**——CSP 是 `default-src 'self'`,
   `data:` URI 與外部網址都會被擋;而且教學頁要離線也開得起來。
2. SVG 內**不得有 `<script>`**:本平台散布執行檔,自家頁面更不能有可執行內容。
3. **圖裡的步驟與文字清單不得漂移**——圖與文字互相矛盾比沒有圖更糟。
"""

import re

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

# 教學清單的七步(與 help.html 的 <ol class="help-steps"> 同一組字)
STEPS = ["登入", "等待開通", "建立專案", "建立版本", "上傳三類檔案", "發布", "分享與下載"]


def _svgs(html: str) -> list[str]:
    return re.findall(r"<svg\b.*?</svg>", html, re.DOTALL)


async def _help(client) -> str:
    resp = await client.get("/help", headers=BROWSER)
    assert resp.status_code == 200
    return resp.text


def _diagrams(html: str) -> list[str]:
    """教學圖示:帶 `role="img"` 的那些(側欄/導航列的小圖示是 aria-hidden)。"""
    return [svg for svg in _svgs(html) if 'role="img"' in svg]


async def test_教學頁有三張圖示(client):
    diagrams = _diagrams(await _help(client))
    assert len(diagrams) >= 3, f"教學頁應有流程圖、頁面圖、三類齊備圖,實得 {len(diagrams)} 張"


async def test_每張圖都有標題供螢幕閱讀器使用(client):
    """圖是教學內容而不是裝飾,拿不到文字的人也要拿得到同一份資訊。"""
    for svg in _diagrams(await _help(client)):
        assert "<title" in svg, f"缺 <title> 的圖:{svg[:120]}"


async def test_圖示不引用任何外部資源(client):
    """🔴 CSP `default-src 'self'` 擋 data: 與外部網址;教學頁也要離線開得起來。"""
    for svg in _diagrams(await _help(client)):
        for forbidden in ("http://", "https://", "data:", "<image", "xlink:href"):
            assert forbidden not in svg, f"圖示不得出現 {forbidden}"


async def test_圖示內不得有可執行內容(client):
    """本平台散布執行檔,自家頁面更不能有可執行內容(與拒收 SVG 上傳同一個理由)。"""
    body = await _help(client)
    for svg in _diagrams(body):
        assert "<script" not in svg
        assert "onload" not in svg and "onclick" not in svg


async def test_流程圖的七步與教學清單一致(client):
    """🔴 圖與文字漂移比沒有圖更糟——改一邊沒改另一邊,這條就紅。"""
    body = await _help(client)
    flow = next((svg for svg in _diagrams(body) if "快速上手" in svg), None)
    assert flow is not None, "應有一張標題含「快速上手」的流程圖"
    for step in STEPS:
        assert step in flow, f"流程圖缺少步驟「{step}」"
        assert step in body


async def test_頁面圖標出側欄與三個主要畫面(client):
    """Benny 要的「頁面圖」本體:按鈕在哪一頁是最常卡住的問題。"""
    body = await _help(client)
    page_map = next((svg for svg in _diagrams(body) if "頁面圖" in svg), None)
    assert page_map is not None, "應有一張「頁面圖」"
    for label in ("側欄", "專案總覽", "建立版本", "上傳頁"):
        assert label in page_map, f"頁面圖缺少「{label}」"
