"""T98 守門:本機開發用的 compose 絕不能長成正式機的樣子,反之亦然。

為什麼需要這一檔:`docker-compose.dev.yml` 為了讓本機連得到,**必須把
PostgreSQL / MinIO 的 port 發布到 localhost**——而那正是正式機的紅線
(「DB / MinIO 不上 cats-edge、不對主機發布 port」)。

兩個檔案因此在**同一件事上要求相反**。相反的東西放在同一個 repo,遲早有人
把其中一個當成另一個用。所以這裡兩邊一起釘:

- dev 檔不得帶正式機的特徵(`cats-edge`、GHCR image、restart 政策);
- 正式檔不得帶 dev 的特徵(db / minio 發布 port),且必須保有 `cats-edge`。

🔴 另外刻意檢查**沒有** `docker-compose.override.yml`:那個檔名會被
`docker compose up` **自動載入**,dev 設定一旦叫這個名字,任何人在 VM 上
打一次 `docker compose up` 就會把 DB 的 port 開到主機上,而且不會有任何警告。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "docker-compose.dev.yml"
PROD = ROOT / "docker-compose.yml"


def test_dev_compose_存在():
    assert DEV.exists(), "T98:本機開發用的 compose 應存在"


def test_dev_compose_不得帶正式機特徵():
    text = DEV.read_text(encoding="utf-8")
    assert "cats-edge" not in text, "🔴 cats-edge 是 VM 上 gateway 的網路,本機沒有"
    assert "ghcr.io" not in text, "🔴 本機要跑的是你剛改的程式碼,不是 registry 上的舊 image"
    assert "restart:" not in text, (
        "🔴 不設 restart 政策:正式機要自動重啟,本機不要——這個差異本身就是一道標記"
    )


def test_dev_compose_只起相依不起服務本體():
    """程式用 venv 的 uvicorn 跑,改一行立刻看得到,不必重建 image。"""
    text = DEV.read_text(encoding="utf-8")
    assert "postgres" in text and "minio" in text
    assert "container_name: upload-program" not in text, (
        "🔴 那是 gateway 用來解析上游的正式名稱,本機不得佔用"
    )


def test_正式compose不得被改成dev的形狀():
    """反向護欄:免得有人「順手」把發布 port 加到正式檔上。"""
    text = PROD.read_text(encoding="utf-8")
    assert "cats-edge" in text, "🔴 正式 compose 必須上 cats-edge,否則 gateway 反代不到"

    # db / minio 兩段都不得出現 ports:(發布到主機)
    for service in ("db:", "minio:"):
        start = text.index(f"  {service}")
        rest = text[start:]
        # 下一個同層服務或頂層區塊為界
        end = min(
            (rest.index(marker) for marker in ("\nnetworks:", "\nvolumes:", "\n  minio:")
             if marker in rest[1:]),
            default=len(rest),
        )
        block = rest[:end]
        assert "ports:" not in block, f"🔴 {service} 不得對主機發布 port(平台鐵則)"


def test_不得存在自動載入的override檔():
    """🔴 docker-compose.override.yml 會被 `docker compose up` 自動吃進去。

    dev 設定若叫這個名字,任何人在 VM 上打一次 `docker compose up` 就會把
    DB 的 port 開到主機上,而且**不會有任何警告**。
    """
    assert not (ROOT / "docker-compose.override.yml").exists()
    assert not (ROOT / "docker-compose.override.yaml").exists()
