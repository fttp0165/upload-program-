"""T109:runbook 上的維運指令必須真的跑得起來。

起因是撞到的,不是 review 出來的:Benny 在 VM 上照 runbook §C.3 清殘骸,
拿到 `can't open file '/app/tools/purge_failed_artifacts.py'`。

根因不是「那支工具太新」,而是 **`Dockerfile` 從來沒有 COPY `tools/`** ——
於是 runbook §C.2 的兩行 cron(`purge_audit.py --apply` / `purge_issues.py --yes`)
**從寫下的那天起就不可能執行成功**。實測 `crontab -l` 沒有任何一行,
所以沒有每晚靜默失敗,但稽核保留期清理也就從來沒跑過。

🔴 本檔擋的不是那次的 bug(已經修了),是**下一次**:
有人寫了新的維運腳本、runbook 也寫了指令,卻忘了改 Dockerfile。
"""

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
RUNBOOK = ROOT / "docs" / "runbook_換版與備份還原.md"


def _copy_sources() -> list[str]:
    """取出 Dockerfile 每一行 COPY 的來源路徑(略過 `--from=` 的階段間複製)。"""
    sources: list[str] = []
    for line in DOCKERFILE.splitlines():
        line = line.strip()
        if not line.startswith("COPY"):
            continue
        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        if len(parts) < 2 or "--from=" in line:
            continue
        sources.extend(parts[:-1])  # 最後一個是目的地
    return sources


def _covered(path: str) -> bool:
    """這個 repo 相對路徑會不會被某一行 COPY 帶進 image。"""
    for src in _copy_sources():
        src = src.rstrip("/")
        if fnmatch.fnmatch(path, src) or path.startswith(f"{src}/"):
            return True
    return False


def test_runbook引用的維運腳本都在image內():
    """🔴 文件寫得出來、容器裡卻沒有,就是一份跑不起來的維運文件。

    而跑不起來的維運文件,結果就是那件事沒有人做 —— 稽核清理正是如此。
    """
    body = RUNBOOK.read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"python (tools/[\w./-]+\.py)", body)))
    assert referenced, "runbook 應該至少引用一支維運腳本;抓不到代表這條守門失效了"

    for script in referenced:
        assert (ROOT / script).exists(), f"runbook 引用了不存在的腳本:{script}"
        assert _covered(script), (
            f"runbook 叫人在容器裡跑 {script},但 Dockerfile 沒有把它 COPY 進 image"
        )


def test_devserver不得進入image():
    """T92 第三層防線的**意圖**:偽造 session 的腳本不得出現在正式環境。

    ⚠ T109 把「不得 COPY tools/」收窄成這一條 —— 文字變了,意圖一字未減:
    `devserver.py` 進不去,而且它 import 的 `tests/` 也進不去。
    """
    assert not _covered("tools/devserver.py"), "devserver.py 被 COPY 進 image 了"
    # ⚠ 只檢查 COPY 指令,不檢查整份檔案 —— Dockerfile 的註解**應該**提到
    # devserver(說明為什麼只收 purge_*.py)。把註解也一起禁掉,會逼人把理由刪掉,
    # 而理由被刪掉之後,下一個人就會「順手」把整個 tools/ 收進來。
    for line in DOCKERFILE.splitlines():
        if line.strip().startswith("COPY"):
            assert "devserver" not in line, f"COPY 指令不得提到 devserver:{line}"


def test_tests目錄不得進入image():
    assert not _covered("tests/conftest.py")
    assert not _covered("tests/test_ops_scripts.py")
