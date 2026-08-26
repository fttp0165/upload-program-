# (原本第一行的 `# syntax=docker/dockerfile:1` 已移除——它會讓 buildkit 去
#  docker.io 抓 frontend image,而本 VM 連不到 docker.io(dockerd 走 mirror.gcr.io
#  鏡像,但該指令繞過 dockerd)。本檔未用進階語法,內建 frontend 足夠。)
# 平台規約:multi-stage、base image 鎖版本(禁 latest)、non-root、EXPOSE 8080、
# HEALTHCHECK 指向 /health(liveness,不查 DB)、正式級伺服器、優雅關閉。

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# 2026-07-30:原本這裡 apt-get 裝 build-essential「給 asyncpg/cryptography 編譯」
# ——實際上兩者在 cp312 都有官方 manylinux wheel,從未真的編譯過;而 CI 改跑
# VM self-hosted 後,apt 套件庫從該網路不可達,這步直接炸(run #85)。
# 拿掉 apt,並以 --only-binary=:all: 把「全程用 wheel、不需編譯器」變成強制:
# 哪天有相依只出 sdist,會在這裡大聲失敗,而不是默默要求 gcc。
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# 2026-08-26(T106):這裡**曾經**加過一段 `apt-get --only-upgrade` 想把 openssl
# 升到 Debian 已發布的安全版本(CVE-2026-14456),當場實測失敗,已移除。留下這段
# 註釋是為了讓下一個想到同一招的人不必再撞一次:
#
# 1. deb.debian.org 從 CI runner 的容器網路**仍然不可達**(connection timed out)
#    ——與上面 2026-07-30 run #85 同一堵牆,不是只擋 builder stage。
# 2. 🔴 **`apt-get update` 抓不到索引時只印 `W:`,exit code 仍是 0。**
#    於是後面的 `--only-upgrade` 拿舊索引去比,回報「已經是最新版」,整條 `&&`
#    鏈**成功**,建置產出一個看起來已修好、實際沒動過的 image。
#    當時刻意不寫 `|| true` 就是為了防這件事,而 apt 自己把它繞過去了——
#    抓到的是 Trivy,不是這條護欄。
#    ⚠️ 日後網路若開通、要把 apt 加回來,**必須自己驗 `apt-get update` 的結果**
#    (例如接 `apt-get -o Acquire::Retries=3 update` 後再 `grep` 目標版本存在),
#    不能只靠 `&&`。
#
# 在網路開通或上游 image 重建之前,本檔不再嘗試 apt。詳見 T106 日誌。

# 🔴 non-root:UID 1000
RUN useradd -m -u 1000 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app

USER app
EXPOSE 8080

# liveness:不查 DB —— 查了的話 DB 抖一下容器就被判死重啟。
HEALTHCHECK --interval=30s --timeout=3s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status==200 else 1)"

# 正式級伺服器,綁 0.0.0.0(綁 127.0.0.1 的話 gateway 連不到)。
# graceful-timeout 30s:收到 SIGTERM 後停收新請求、讓既有請求跑完。
CMD ["gunicorn", "app.asgi:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
