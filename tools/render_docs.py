#!/usr/bin/env python3
"""把 docs/ 下的正式文件從 Markdown 產生單檔 HTML 發布版。

為什麼要有這支:憲法第四條要求正式文件 **md + HTML 並存且兩版同步**。
人工維護兩份必然漂移,所以 HTML 一律由 md 產生,md 是唯一權威來源。

規則:
- 一律 **light 主題**(白底深字),不使用 prefers-color-scheme 切深色(第四條 1–3)
- 圖示以 `<!--SVG:名稱-->` 標記,產生時抽換成 `docs/assets/名稱.svg` 的內容(第四條 5)
  md 版保留其上方的 ASCII 圖,HTML 版則同時有 ASCII 與 SVG——SVG 在前、ASCII 收在
  <details> 裡,避免同一張圖在 HTML 上重複佔版面
- 開發日誌(docs/dev-logs/)依第四條 4 可僅有 md,不在產生範圍

用法:python tools/render_docs.py [--check]
  --check:只檢查 HTML 是否為最新(CI 用),不寫檔;有落差回傳非 0
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"

# 需要 HTML 版的正式文件。開發日誌刻意不列(第四條 4)。
TARGETS = [
    "功能需求大綱.md",
    "決策_前端技術選型.md",
    "開發計畫書.md",
    "任務表.md",
    "設計_MVP.md",
    "plans/SSO接入計畫.md",
]

SVG_MARKER = re.compile(r"<!--SVG:([A-Za-z0-9_-]+)-->")

# light 主題樣式;刻意不寫任何 dark media query。
STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { background:#fff; color:#1a2540;
       font-family:"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif;
       line-height:1.75; max-width:980px; margin:0 auto; padding:32px 20px; }
h1 { font-size:1.55rem; border-bottom:3px solid #12228f; padding-bottom:10px; }
h2 { font-size:1.2rem; margin-top:32px; color:#12228f; border-left:5px solid #12228f; padding-left:10px; }
h3 { font-size:1.02rem; color:#334; margin-top:22px; }
code { background:#f2f4f8; padding:1px 5px; border-radius:4px; font-size:.88em;
       font-family:Consolas,"Courier New",monospace; }
pre { background:#f7f8fb; border:1px solid #e6e9ef; border-radius:8px; padding:12px;
      overflow-x:auto; font-size:.82rem; }
pre code { background:none; padding:0; }
table { border-collapse:collapse; width:100%; margin:14px 0; font-size:.86rem; display:block; overflow-x:auto; }
th,td { border:1px solid #dfe3ea; padding:7px 10px; text-align:left; vertical-align:top; }
th { background:#f7f8fb; color:#334; }
blockquote { background:#f7f8fb; border-left:5px solid #12228f; border-radius:0 8px 8px 0;
             padding:10px 18px; margin:16px 0; color:#334; }
blockquote > :first-child { margin-top:0; }
blockquote > :last-child { margin-bottom:0; }
hr { border:none; border-top:1px solid #e6e9ef; margin:28px 0; }
ul,ol { padding-left:22px; }
li { margin:4px 0; }
a { color:#12228f; }
figure.diagram { margin:18px 0; overflow-x:auto; }
figure.diagram svg { max-width:100%; height:auto; display:block; }
details { margin:10px 0; font-size:.9rem; }
summary { cursor:pointer; color:#12228f; }
.banner { background:#f7f8fb; border:1px solid #e6e9ef; border-radius:8px;
          padding:10px 16px; font-size:.85rem; color:#6b7280; margin-bottom:18px; }
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="banner">本頁由 <code>tools/render_docs.py</code> 自 <code>docs/{source}</code> 產生。
權威版本為 Markdown,請勿直接編輯本 HTML。</div>
{body}
</body>
</html>
"""


def _inject_svg(html: str, source: str) -> str:
    """把 <!--SVG:名稱--> 換成內嵌 SVG,並把緊鄰其上的 ASCII 圖收進 <details>。"""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        svg_path = ASSETS / f"{name}.svg"
        if not svg_path.exists():
            print(f"  ⚠️  {source}: 找不到 {svg_path.relative_to(ROOT)},保留 ASCII 圖", file=sys.stderr)
            return ""
        svg = svg_path.read_text(encoding="utf-8")
        # 去掉 XML 宣告,讓 SVG 能直接內嵌在 HTML 裡
        svg = re.sub(r"<\?xml[^>]*\?>\s*", "", svg).strip()
        return f'<figure class="diagram">{svg}</figure>'

    return SVG_MARKER.sub(replace, html)


def _svg_first_ascii_folded(html: str) -> str:
    """md 是「ASCII 圖在前、SVG 標記在後」,HTML 要反過來。

    交換順序讓 SVG 先出現,並把 ASCII 版收進 <details>——兩者表達同一件事,
    在 HTML 上並排攤開只是重複佔版面(第四條 5:HTML 用 SVG、md 用文字圖)。
    """
    return re.sub(
        r"(<pre>(?:(?!</pre>).)*?</pre>)\s*(<figure class=\"diagram\">.*?</figure>)",
        r"\2<details><summary>同一張圖的文字版(md 版採用)</summary>\1</details>",
        html,
        flags=re.DOTALL,
    )


def render(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    html = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "attr_list", "sane_lists", "md_in_html"],
    )
    html = _svg_first_ascii_folded(_inject_svg(html, md_path.name))

    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else md_path.stem
    return TEMPLATE.format(title=title, style=STYLE, body=html, source=md_path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="從 Markdown 產生 light 主題單檔 HTML")
    parser.add_argument("--check", action="store_true", help="只檢查是否最新(CI 用),不寫檔")
    args = parser.parse_args()

    stale: list[str] = []
    for name in TARGETS:
        md_path = DOCS / name
        if not md_path.exists():
            print(f"  ⚠️  找不到 {md_path.relative_to(ROOT)},略過", file=sys.stderr)
            continue
        html_path = md_path.with_suffix(".html")
        rendered = render(md_path)

        if args.check:
            current = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
            if current != rendered:
                stale.append(str(html_path.relative_to(ROOT)))
            continue

        html_path.write_text(rendered, encoding="utf-8")
        print(f"  ✅ {html_path.relative_to(ROOT)}")

    if stale:
        print("\n❌ 以下 HTML 版與 md 不同步,請執行 python tools/render_docs.py:", file=sys.stderr)
        for path in stale:
            print(f"   - {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
