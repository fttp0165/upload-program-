"""T112 冒煙腳本 `tools/smoke.sh` 的最小守門。

腳本跑在 VM 上、對著正式站打 curl——pytest 沒辦法在 CI 裡替它跑真網路,
但至少要守住兩件事,否則壞掉的腳本會一路睡到換版當天才被發現:

1. **語法正確**(`bash -n`):shell 腳本沒有編譯期,打錯字只有執行時才炸;
2. **關鍵檢查沒有被改掉**:哨兵、登入轉址、三個安全標頭、readiness——
   少一個,冒煙就從「四組檢查」悄悄縮水,而縮水不會有任何錯誤訊息。

真正的行為驗證在本機實跑(dev-log T112 的紅→綠證據:`--local` 全過 +
錯誤版本負向會紅),不在這裡假裝。
"""

import pathlib
import subprocess

SCRIPT = pathlib.Path("tools/smoke.sh")


def test_腳本存在且bash語法正確():
    assert SCRIPT.exists(), "tools/smoke.sh 不存在——runbook §A.4 的一鍵冒煙會落空"
    # noqa 理由:引數是寫死的常數路徑與旗標,無任何使用者輸入;S603/S607 防的
    # 是「拼接不可信輸入」與「PATH 劫持」,前者不存在,後者在 CI 的受控環境
    # 與開發機上都以系統 bash 為前提——寫絕對路徑反而在不同發行版之間更脆。
    result = subprocess.run(  # noqa: S603
        ["bash", "-n", str(SCRIPT)],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n 語法錯誤:\n{result.stderr}"


def test_關鍵檢查一個都不能少():
    """🔴 冒煙縮水不會有錯誤訊息——用字串釘住每一組檢查的存在。"""
    body = SCRIPT.read_text(encoding="utf-8")
    needles = {
        "/help": "版本哨兵載體",
        "auth/login": "匿名瀏覽器 302 → 登入",
        "nosniff": "X-Content-Type-Options",
        "default-src": "CSP",
        "x-frame-options": "XFO 恰一個",
        "/ready": "容器內 readiness",
        "/plm/": "既有系統零影響",
        "SKIP": "登入後項目要明示跳過,不得假裝驗過",
    }
    for needle, why in needles.items():
        assert needle in body, f"腳本缺了「{why}」的檢查(找不到 {needle!r})"
