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
