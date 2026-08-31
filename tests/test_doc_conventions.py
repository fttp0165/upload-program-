"""T114 憲法第九條守門:文件標明專案,跨專案文件檔名標明收發雙方與時間。

為什麼要有這一檔:規約若只寫在 CLAUDE.md,三個月後就是一句沒人記得的話。
第九條唯一能長久生效的方式,是**每次 CI 都真的去看那些檔案**。

守的是四件事:

1. 已登記的跨專案文件,抬頭四欄齊備(專案 / 發文專案 / 受文專案 / 發文時間)——
   文件離開 repo(當附件寄出、列印、貼進聊天)之後,只剩抬頭能自證身分。
2. 新格式檔名解析得出「誰發給誰、何時」——這正是規約要救的痛點:
   對方常常只拿到一個檔名(上一封收到的是 `catsportal____uploadprogram_____.md`,
   中文全被吃掉),檔名說不出話,對帳就得靠人腦。
3. 索引與現實不漂移:索引列的檔案必須存在。
4. 用新格式命名的檔案必須登記進索引——否則索引會慢慢變成半真半假的清冊。

🔴 本檔刻意**不**要求既有 9 份舊檔改名:它們被開發日誌引用 40+ 處,
為了套用今天才訂的規則去回頭改寫日誌,是拿新規則竄改舊歷史(第六條精神反對)。
規約對「生效後新建的文件」有強制力,新舊之間的橋是索引。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANS = ROOT / "docs" / "plans"
INDEX = PLANS / "跨專案文件索引.md"

# 第九條的檔名格式:<發文專案>_致_<受文專案>_<類別>_<主題>_<YYYYMMDD-HHmm>.md
FILENAME_RE = re.compile(
    r"^(?P<sender>[A-Za-z0-9.-]+)_致_(?P<receiver>[A-Za-z0-9.-]+)"
    r"_(?P<kind>申請|通報|回覆|聲明|同步|交付)_(?P<topic>.+)"
    r"_(?P<stamp>\d{8}-\d{4})\.md$"
)

REQUIRED_FIELDS = ("專案", "發文專案", "受文專案", "發文時間")


def _registered() -> list[str]:
    """從索引取出登記在案的跨專案文件檔名。

    索引是「哪些文件算跨專案文件」的唯一登記處——測試不自己猜,
    否則規則會散成兩份(一份在憲法、一份在測試)。
    """
    assert INDEX.exists(), "第九條要求 docs/plans/跨專案文件索引.md 存在"
    text = INDEX.read_text(encoding="utf-8")
    # 表格裡以 markdown 連結指向檔案:[顯示名](檔名.md)
    return sorted(set(re.findall(r"\]\(([^)]+\.md)\)", text)))


def test_索引列的檔案都存在():
    """索引與現實漂移 = 對外承諾「發過這份」但檔案已不在,比沒有索引更糟。"""
    missing = [name for name in _registered() if not (PLANS / name).exists()]
    assert not missing, f"索引列了不存在的檔案:{missing}"


def test_索引至少涵蓋所有致portal的文件():
    """檔名寫著 `給portal` / `致` 的都是對外文件,不得漏登記。"""
    registered = set(_registered())
    outbound = [
        p.name
        for p in PLANS.glob("*.md")
        if ("portal" in p.name or "_致_" in p.name) and p.name != INDEX.name
    ]
    missing = sorted(set(outbound) - registered)
    assert not missing, f"對外文件未登記進索引:{missing}"


def test_跨專案文件抬頭四欄齊備():
    """離開 repo 之後,只剩抬頭能自證「哪個專案、發給誰、何時」。"""
    broken: dict[str, list[str]] = {}
    for name in _registered():
        # 只看抬頭區(第一個 --- 之前),避免內文提到欄位名就誤判通過
        head = (PLANS / name).read_text(encoding="utf-8").split("\n---", 1)[0]
        lacking = [f for f in REQUIRED_FIELDS if f"**{f}:**" not in head]
        if lacking:
            broken[name] = lacking
    assert not broken, f"抬頭缺欄位:{broken}"


def test_新格式檔名解析得出收發雙方與時間():
    """規約的核心承諾:對方只拿到檔名時,檔名本身就要說得出話。"""
    new_style = [p.name for p in PLANS.glob("*_致_*.md")]
    assert new_style, "第九條生效後應至少有一份文件採用新格式命名"
    for name in new_style:
        m = FILENAME_RE.match(name)
        assert m, f"檔名不符第九條格式:{name}"
        assert m.group("sender") != m.group("receiver"), f"發文與受文專案相同:{name}"


def test_憲法第九條存在且列出命名格式():
    """規約的權威在 CLAUDE.md;測試只是它的執行面,不能反過來變成唯一來源。"""
    charter = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "第九條" in charter, "CLAUDE.md 應有第九條"
    assert "_致_" in charter, "第九條應寫明檔名格式(含 _致_ 分隔)"
    for field in REQUIRED_FIELDS:
        assert field in charter, f"第九條應列出抬頭欄位:{field}"


# --- T120 憲法第十條:指令必須標明「在哪台機器、在哪個目錄」-------------------
#
# 為什麼需要守門:文件裡同時有三種機器的指令(WSL 開發機 / Windows 的 Docker
# Desktop / Cats VM),每個區塊長得一模一樣——黑底、`$` 開頭。在 WSL 跑到 VM
# 的指令頂多報錯;**在 VM 跑到本機的指令會出事**(dev compose 一貼就把資料庫
# 的 port 開到主機上)。
#
# 🔴 而且已經出過事:2026-08-11 換版第一次失敗,根因是
# 「改 docker-compose.yml 的 image tag」那一步**是編輯檔案,卻被當成 shell
# 指令貼進終端機」。所以標記分兩種:🖥️ 執行 / 📄 編輯。
#
# 新增區塊時忘記標**不會有任何錯誤訊息**,文件會慢慢退回原狀而沒人看得見——
# 這正是要用測試而不是靠自律的理由(與第九條同一課)。

RUNBOOKS = ("runbook_本地開發與CI.md", "runbook_換版與備份還原.md")
RUNNABLE_FENCES = ("```bash", "```cron")
PLATFORM_MARKERS = ("在哪執行:", "編輯哪個檔:")
LOOKBACK = 8


def _unmarked_blocks(name: str) -> list[int]:
    """回傳該文件中「沒有標平台/路徑」的可貼區塊行號(1-based)。"""
    lines = (ROOT / "docs" / name).read_text(encoding="utf-8").split("\n")
    bad = []
    for i, line in enumerate(lines):
        if not line.startswith(RUNNABLE_FENCES):
            continue
        window = lines[max(0, i - LOOKBACK): i]
        if not any(m in w for w in window for m in PLATFORM_MARKERS):
            bad.append(i + 1)
    return bad


def test_runbook的每個可貼區塊都標了平台與路徑():
    broken = {name: _unmarked_blocks(name) for name in RUNBOOKS}
    broken = {k: v for k, v in broken.items() if v}
    assert not broken, (
        f"以下區塊沒標「在哪執行/編輯哪個檔」(行號):{broken}\n"
        "🔴 憲法第十條:指令要說明在哪一台機器、在哪個目錄"
    )


def test_憲法第十條存在且列出標記():
    charter = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "第十條" in charter, "CLAUDE.md 應有第十條"
    for marker in PLATFORM_MARKERS:
        assert marker in charter, f"第十條應列出標記:{marker}"


def test_標記要寫得出實際機器名():
    """只寫「在哪執行:」而不寫機器名等於沒標。"""
    machines = ("WSL", "Cats VM", "Windows")
    for name in RUNBOOKS:
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        for line in text.split("\n"):
            if "在哪執行:" in line:
                assert any(m in line for m in machines), f"{name} 標記未指明機器:{line}"


# --------------------------------------------------------------------------
# T128:第四條 4 × 第九條 —— 對外文件必須有 HTML 版
#
# 為什麼補這一條:`render_docs.py --check` 只掃它自己的 TARGETS 清單,
# 所以**新建一份正式文件卻忘了加進 TARGETS,--check 會照樣印「All checks passed」**
# ——漏掉的東西不在清單裡,清單當然檢查不出來。
#
# 這正是 2026-08-26 實際發生的事:第 11 份申請書建好、登記進索引、CI 綠燈,
# 但它**沒有 HTML 版**,而其他兩份第九條文件都有。發現的方式是人眼看了一次
# TARGETS,不是任何一道自動守門——那就是「規約沒有測試」的樣子(第九條 7)。
#
# 🔴 判準刻意綁在**索引**而不是 glob:索引是「哪些算跨專案文件」的唯一登記處
# (第九條 5)。綁 glob 等於讓規則散成兩份,而兩份遲早分岔。
# --------------------------------------------------------------------------


def _render_targets() -> set[str]:
    """讀 tools/render_docs.py 的 TARGETS 清單。

    以正規表示式讀原始碼而非 import:render_docs 需要 markdown 套件,
    而本檔的其餘測試都不需要任何相依——為了一條測試把整檔綁上第三方套件不划算。
    """
    src = (ROOT / "tools" / "render_docs.py").read_text(encoding="utf-8")
    block = re.search(r"TARGETS\s*=\s*\[(.*?)\]", src, re.S)
    assert block, "render_docs.py 找不到 TARGETS 清單"
    return set(re.findall(r'"([^"]+\.md)"', block.group(1)))


def test_登記在案的對外文件都有HTML版():
    """第四條 4:正式文件 md 與 HTML 並存;跨專案文件尤其如此。

    對方收到的往往是**單檔 HTML**(可離線開、不必有 markdown 檢視器)。
    只給 md 等於要求對方自己有工具——而這份文件的整個重點就是「離開 repo
    之後還能自己說話」。
    """
    targets = _render_targets()
    missing = [
        name
        for name in _registered()
        if f"plans/{name}" not in targets and name != INDEX.name
    ]
    assert not missing, (
        f"對外文件已登記進索引卻沒有 HTML 版(請加進 tools/render_docs.py 的 TARGETS):{missing}"
    )
