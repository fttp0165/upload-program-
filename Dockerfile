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
# ⚠ 2026-08-24 更正(T93):「apt 套件庫不可達」**這件事已經不成立**——CI 上實測
#   回 `APT_OK`。builder 這裡仍然不裝 apt 套件(理由改為「不需要編譯器」這一條本身,
#   它與網路可達性無關),但 runtime 階段自此**會**用 apt 升級 OS 套件(見下)。
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

# 🔴 T93(2026-08-24):升級 base image 帶來的 util-linux 系列套件。
#
# 為什麼要在這裡動 apt,而檔頭寫著「apt 不可達」:
# **那則紀錄已經過期。** 2026-08-24 在 CI 上實測(診斷步驟,見 dev-log T93)得到
# `APT_OK` —— 網路條件與 2026-07-30 run #85 當時不同了。憲法第二條 5:發現計畫與
# 現實不符要回寫,不是繼續照著錯的前提做決定。
#
# 為什麼非升級不可:Trivy(`--severity HIGH,CRITICAL --ignore-unfixed`)在
# base image `python:3.12-slim`(建立於 **2026-07-14**,`--pull` 後仍是同一個 digest
# → 上游未重建)裡抓到 **36 條 HIGH,全部來自 src:util-linux**:
#   CVE-2026-53612/53613 mount 的 TOCTOU、53614 SUID mount 繞過 nosuid/noexec、
#   53615 libblkid 整數溢位。Installed 2.41-5 → Debian 修版 2.41.5-0+deb13u1。
# 🔴 **不用 `.trivyignore` 換綠燈**(T82 立下的界線:本服務散布可執行檔,
#    調鬆掃描是拿紅線換方便),也不等上游重建(那會讓這批改動無限期不能上線)。
#
# 套件清單為什麼寫死而不是 `apt-get upgrade`:這八個就是 Trivy 表列出的全部,
# 明列讓「這次修了什麼」在 diff 上看得見;`upgrade` 會把不相關的套件一起動,
# 出問題時分不出是誰。日後其他來源的 CVE 就再開一個任務、再加一行。
# 🔴 在 `USER app` **之前**(apt 需要 root),裝完刪 lists 不留快取。
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
       util-linux mount login bsdutils \
       libblkid1 libmount1 libsmartcols1 libuuid1 liblastlog2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f='util-linux 升級後版本:${Version}\n' util-linux

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
