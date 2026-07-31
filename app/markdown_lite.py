"""極簡 Markdown 轉譯器(T77)——**白名單由結構保證,不是事後過濾**。

## 為什麼自己寫

問題回報要能貼路徑、錯誤訊息與截圖,純文字不夠用;但**接受使用者的 HTML 是不可能的**:
依 SSO 契約 §4.10,IdP 與各 App **同源**,我們出現一次 XSS 等於全平台帳號淪陷。

現成的 `markdown` 套件目前只是 dev 相依(給 `tools/render_docs.py` 用),而且它
**預設允許原始 HTML 通過**——要安全就得再配一個消毒器(bleach 之類)。那是
兩個新的執行期相依,換來一個「預設不安全、靠事後過濾補救」的架構,方向是錯的。

## 這裡的做法

1. **先把整段輸入 HTML 逸出**(`escape()`),此後輸入裡再也沒有活的標籤;
2. 再由本模組**逐字產生**我方白名單標籤。

所以「使用者的 HTML」在結構上不可能出現在輸出裡——這不是黑名單過濾,
是**根本沒有那條路徑**。新增語法時務必維持這個順序:先逸出、後產生。

## 為什麼回傳 Markup 而不是要模板寫 `|safe`

憲法禁 `|safe` 的用意是「不要把使用者輸入當 HTML 輸出」。本函式的輸出是我方
組出來的字串、輸入早已逸出,語意上就是安全的 HTML;以 `Markup` 回傳可讓模板
維持 `{{ }}` 的寫法,`|safe` 的禁令不必開任何例外。
"""

import re

from markupsafe import Markup, escape

# 只有指向**我方附件端點**的圖片會被產生。外部圖片一律不產生 <img>:
# 那會讓回報頁變成對外發請求的載體(追蹤像素、內容外洩)。
_INTERNAL_IMAGE = re.compile(r"^/[A-Za-z0-9_\-/.]*$")

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^\s)]+)\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
_CODE = re.compile(r"`([^`\n]+)`")


def _inline(text: str) -> str:
    """處理行內語法。**輸入必須already逸出**(呼叫端負責)。

    順序有意義:圖片先於連結(`![]()` 是 `[]()` 的超集),
    行內程式碼最後——它的內容不該再被其他規則動到。
    """

    def image(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        # 🔴 只准我方路徑;http(s)、data:、javascript: 一律不產生 <img>
        if not _INTERNAL_IMAGE.match(src):
            return f"[圖片:{alt}]" if alt else "[圖片]"
        return f'<img src="{src}" alt="{alt}" class="issue-image">'

    def link(match: re.Match[str]) -> str:
        # 🔴 只接受 http/https(正則已限定);target=_blank 必須配 noopener,
        # 否則新分頁能以 window.opener 操作我們這一頁。
        label, href = match.group(1), match.group(2)
        return f'<a href="{href}" rel="noopener noreferrer" target="_blank">{label}</a>'

    text = _IMAGE.sub(image, text)
    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    return text


def render_markdown(raw: str) -> Markup:
    """把使用者寫的 Markdown 轉成安全的 HTML。

    支援:段落、換行、`**粗體**`、`*斜體*`、`` `行內程式碼` ``、``` 區塊、
    `- ` / `1. ` 清單、`> ` 引言、`### ` 小標、http(s) 連結、我方路徑的圖片。

    參數:raw 使用者輸入的 Markdown 原文(**不需要事先處理**)。
    回傳:`Markup`(可直接放進模板)。副作用:無。
    """
    if not raw or not raw.strip():
        return Markup("")

    # 🔴 第一步就是逸出:此後輸入裡沒有任何活的標籤,後續規則只會「加上」我方標籤。
    text = str(escape(raw)).replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    in_code = False
    code_buffer: list[str] = []
    list_items: list[str] = []
    list_tag = ""
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            out.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
            list_tag = ""

    for line in text.split("\n"):
        stripped = line.strip()

        # 程式碼區塊:內容**不套用任何行內規則**,原樣輸出(已逸出)。
        if stripped.startswith("```"):
            if in_code:
                out.append("<pre><code>" + "\n".join(code_buffer) + "</code></pre>")
                code_buffer.clear()
            else:
                flush_paragraph()
                flush_list()
            in_code = not in_code
            continue
        if in_code:
            code_buffer.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            if list_tag == "ol":
                flush_list()
            list_tag = "ul"
            list_items.append(stripped[2:])
            continue

        ordered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered:
            flush_paragraph()
            if list_tag == "ul":
                flush_list()
            list_tag = "ol"
            list_items.append(ordered.group(1))
            continue

        flush_list()

        if stripped.startswith("&gt; "):  # 逸出後的 "> "
            flush_paragraph()
            out.append(f"<blockquote>{_inline(stripped[5:])}</blockquote>")
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
            continue

        paragraph.append(stripped)

    # 收尾:未關閉的程式碼區塊也要輸出,不能整段吞掉使用者寫的東西。
    if in_code and code_buffer:
        out.append("<pre><code>" + "\n".join(code_buffer) + "</code></pre>")
    flush_paragraph()
    flush_list()

    # noqa 的理由:S704 標的是「把字串當成安全 HTML」這個危險動作,標得沒錯——
    # 但本模組的整個設計就是為了讓這一行安全:輸入在函式開頭已 escape(),
    # 之後的每個標籤都是本模組逐字產生的,沒有任何一條路徑會讓使用者的 HTML 走到這裡。
    # 🔴 若日後有人在上面加了「不先逸出就拼接」的規則,這個 noqa 就成了謊言——
    #    test_markdown_lite.py 的五條 XSS 測試是這行的安全網,不得刪。
    return Markup("".join(out))  # noqa: S704
