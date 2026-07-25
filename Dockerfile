# syntax=docker/dockerfile:1
# 平台規約:multi-stage、base image 鎖版本(禁 latest)、non-root、EXPOSE 8080、
# HEALTHCHECK 指向 /health(liveness,不查 DB)、正式級伺服器、優雅關閉。

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# asyncpg / cryptography 需要編譯工具;它們留在 builder,不進 runtime。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

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
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
