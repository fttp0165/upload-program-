"""T92:本機驗證用開發伺服器的**安全結構**。

`tools/devserver.py` 會偽造 session cookie(否則本機沒有 IdP 就登不進去,
T89/T90 的介面改動看不到)。這種東西的安全性**不能靠自律**,所以本檔把
三層結構保證反向驗證起來:

1. 沒有 `DEV_UNSAFE_LOCAL=1` 就**拒絕啟動**(SystemExit,不是印警告)。
2. **只綁 `127.0.0.1`**,原始碼不得出現 `0.0.0.0`。
3. 🔴 **image 裡根本沒有這支腳本**——`Dockerfile` 只 COPY `app/` 與 `alembic/`,
   而 devserver 依賴 `tests/`,兩層都不在 image 內。

外加一條:`app/` 任何檔案都不得 import 它——正式程式碼與這支腳本必須毫無關係。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "devserver.py"


def test_腳本存在():
    assert SCRIPT.is_file()


def test_沒有旗標就拒絕啟動(monkeypatch):
    """🔴 拒絕是 SystemExit,不是印一行警告然後照跑。"""
    from tools import devserver

    monkeypatch.delenv("DEV_UNSAFE_LOCAL", raising=False)
    with pytest.raises(SystemExit):
        devserver.require_dev_flag()


def test_有旗標才放行(monkeypatch):
    from tools import devserver

    monkeypatch.setenv("DEV_UNSAFE_LOCAL", "1")
    devserver.require_dev_flag()  # 不得拋


def test_只綁本機位址():
    """對外綁定會讓「本機用的假登入」變成任何人都能用的假登入。"""
    body = SCRIPT.read_text(encoding="utf-8")
    assert '"127.0.0.1"' in body
    # 註解裡解釋為什麼不綁 0.0.0.0 是可以的;真正的禁令是不得傳給 uvicorn。
    assert 'host="0.0.0.0"' not in body


def test_正式程式碼不得引用這支腳本():
    """`app/` 與這支腳本必須毫無關係——有一條 import 就等於把後門帶進 image。"""
    for path in (ROOT / "app").rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "devserver" not in body, f"{path} 不得提到 devserver"


def test_image內不得含有這支腳本或其相依():
    """🔴 結構保證:`devserver.py` 與它依賴的 `tests/` 都不在 image 內。

    這一條是三層防線裡唯一**不靠設定**的那層——正式環境連 import 都會失敗。

    ⚠ **T109(2026-08-31,Benny 裁示 A 案)把這條從「不得 COPY tools/」收窄。**
    原本的寫法把整個 `tools/` 擋在外面,連 `purge_*.py` 一起 —— 而 runbook §C.2 的
    cron 正是要在容器內跑那些腳本,於是**那兩行指令從寫下的那天起就不可能成功**
    (`can't open file '/app/tools/purge_audit.py'`),稽核保留期清理一次都沒跑過。

    🔴 **文字變了,意圖一字未減**:T92 要防的是「偽造 session 的腳本出現在正式環境」,
    而那件事成立的關鍵是 `devserver.py` 不在、它 import 的 `tests/` 也不在 ——
    不是「tools/ 整個不在」。維運腳本不偽造任何身分,把它們一起擋掉換到的不是安全,
    是一份跑不起來的維運文件,而跑不起來的維運文件 = 那件事沒有人做。

    正面守門(runbook 引用的腳本必須在 image 內)在 `tests/test_ops_scripts.py`。
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = [line for line in dockerfile.splitlines() if line.strip().startswith("COPY")]
    for line in copied:
        assert "devserver" not in line, f"Dockerfile 不得 COPY devserver.py:{line}"
        assert " tests" not in line and "/tests" not in line, f"Dockerfile 不得 COPY tests/:{line}"
        # 整個 tools/ 一起收會把 devserver.py 帶進去 —— 只准逐類挑選(purge_*.py)。
        stripped = [p for p in line.split()[1:] if not p.startswith("--")]
        assert "tools" not in stripped, f"不得整個 COPY tools/(會帶進 devserver):{line}"


def test_假登入路由只掛在devserver的app上(settings):
    """`create_app()` 不得有 `/dev/login`——那是 devserver 自己加的。

    不呼叫 `build_dev_app()` 來驗:它會建 SQLite 檔與假資料,測試不該留下檔案。
    路由由 devserver 自己 include,`create_app()` 沒有它就是這條界線的全部。
    """
    from app.main import create_app

    app = create_app(settings)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("/dev/login" in path for path in paths)
    assert "/dev/login" in SCRIPT.read_text(encoding="utf-8")


async def test_包裝層剝掉前綴才交給app():
    """🔴 正式環境是 gateway 剝前綴後才轉給我們(app 看到的是 `/`)。

    少了這層,本機瀏覽器打 `/upload/` 時側欄的 active 判斷永遠不成立
    ——**畫面看起來像壞的,而程式其實是對的**。本機驗證要能相信就得同座標系。
    """
    from tools.devserver import strip_prefix

    seen = {}

    async def stub(scope, receive, send):
        seen.update(scope)

    await strip_prefix(stub)({"type": "http", "path": "/upload/admin/users"}, None, None)
    assert seen["path"] == "/admin/users"
    assert seen["root_path"] == "/upload"

    seen.clear()
    await strip_prefix(stub)({"type": "http", "path": "/upload"}, None, None)
    assert seen["path"] == "/", "前綴本身要變成根路徑,不能變成空字串"

    seen.clear()
    await strip_prefix(stub)({"type": "http", "path": "/dev/login"}, None, None)
    assert seen["path"] == "/dev/login", "前綴外的路徑不得改動(假登入端點在前綴之外)"
