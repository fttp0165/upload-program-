"""T77:自家的 Markdown 轉譯器(`app/markdown_lite.py`)。

🔴 **這一檔是本任務的安全核心。**

設計上的關鍵:轉譯器**先把整段輸入 HTML 逸出**,再只產生我方白名單標籤。
所以「使用者的 HTML」在結構上就不可能出現在輸出裡——不是事後過濾壞東西
(那是永無止境的軍備競賽),而是**根本沒有那條路徑**。

同源之下(SSO 契約 §4.10)一次 XSS 等於全平台帳號淪陷,所以這裡的每一條
都是紅線測試,不是「加減驗一下」。
"""

import pytest

from app.markdown_lite import render_markdown


def html(text: str) -> str:
    return str(render_markdown(text))


# --- 🔴 XSS:五種載體 --------------------------------------------------------


def test_script標籤被逸出():
    out = html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_img_onerror被逸出():
    out = html('<img src=x onerror="alert(1)">')
    assert "onerror" not in out or "&lt;img" in out
    assert "<img src=x" not in out


def test_javascript協定的連結不產生連結():
    """危險的是**產生 href**,不是那串字被看到。

    非 http/https 一律不變成 `<a>`,原文以純文字留著(使用者看得到自己打了什麼);
    純文字不會執行,所以這裡斷言的是「沒有 `<a>`、沒有 `href=`」而不是「字串消失」。
    """
    out = html("[點我](javascript:alert(1))")
    assert "<a " not in out, "非 http/https 的連結不該變成 <a>"
    assert "href=" not in out
    assert 'href="javascript:' not in out


def test_data協定的圖片不產生img():
    out = html("![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)")
    assert "data:" not in out
    assert "<img" not in out


def test_內嵌HTML一律當文字():
    out = html("正常文字 <b onclick='x'>粗體</b> 結束")
    assert "<b " not in out
    assert "&lt;b" in out


# --- 🔴 外部圖片:回報頁不得成為對外發請求的載體 ------------------------------


def test_外部圖片被剝除():
    """追蹤像素、內容外洩都是從「載入外站圖片」開始的。"""
    out = html("![外站](https://evil.example/pixel.png)")
    assert "<img" not in out
    assert "evil.example" not in out


def test_我方附件路徑的圖片才會產生img():
    out = html("![截圖](/upload/v1/issues/abc/attachments/def)")
    assert "<img" in out
    assert "/upload/v1/issues/abc/attachments/def" in out


# --- 白名單內的語法確實可用 --------------------------------------------------


def test_段落與換行():
    out = html("第一段\n\n第二段")
    assert out.count("<p>") == 2


def test_粗體與行內程式碼():
    out = html("這是 **重點** 與 `code`")
    assert "<strong>重點</strong>" in out
    assert "<code>code</code>" in out


def test_程式碼區塊保留原樣且逸出():
    out = html("```\n<script>x</script>\n```")
    assert "<pre>" in out and "<code>" in out
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_清單():
    out = html("- 一\n- 二")
    assert out.count("<li>") == 2
    assert "<ul>" in out


def test_http連結會產生帶noopener的a():
    out = html("[文件](https://example.com/doc)")
    assert '<a href="https://example.com/doc"' in out
    assert 'rel="noopener noreferrer"' in out


# --- 邊界 -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_空輸入不炸(text):
    assert isinstance(html(text), str)


def test_輸出可直接放進模板而不需要safe():
    """🔴 回傳 Markup:模板照常 `{{ }}`,憲法禁 `|safe` 的規則維持不變。"""
    from markupsafe import Markup

    assert isinstance(render_markdown("x"), Markup)
