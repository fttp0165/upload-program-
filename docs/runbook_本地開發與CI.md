# Runbook:本地開發環境(WSL)與本機跑 CI

**專案:** upload-program
**建立日期:** 2026-08-12 20:10
**最後更新:** 2026-08-12 21:10
**版本:** v2.0

> 照著做就好,不需要先懂為什麼。
> **每個指令區塊上面都標了「在哪台機器、在哪個目錄」**(憲法第十條);
> 🖥️ 是貼進終端機執行,📄 是**編輯檔案內容**——這兩個混淆過一次,代價是一次失敗的換版。

---

## 0. 先看這張表:你想做的事需要什麼

| 你想做的事 | 需要 Docker? | 需要 DB / MinIO? | 看哪一節 |
|---|---|---|---|
| 跑 528 條測試 | ❌ | ❌ | §2 |
| 跑 lint / 文件同步檢查 | ❌ | ❌ | §2 |
| 用瀏覽器實際點畫面 | ✅ | ✅ | §3 |
| 跑完整 CI(含建 image + 弱點掃描) | ✅ | ❌ | §4 |

**最常做的事(改程式 → 跑測試)完全不需要 Docker。**
測試用 SQLite in-memory 與假的 S3 端點,不連任何外部服務。

🔴 **本機登入不能用**,原因見 §5——這不是壞掉,是刻意不繞過。

---

## 1. 一次性準備

### 1.1 WSL 裡要有的東西

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~`(還沒有專案目錄)

```bash
# 確認 WSL 版本與發行版(建議 Ubuntu 22.04 以上)
lsb_release -a

# Python 3.12(Ubuntu 22.04 預設是 3.10,要另外裝)
python3.12 --version || {
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt update
  sudo apt install -y python3.12 python3.12-venv
}

# git
git --version || sudo apt install -y git
```

Docker:**在 Windows 裝 Docker Desktop**,設定裡開啟
`Settings → Resources → WSL Integration → 你的發行版`。
裝好後在 WSL 裡驗證:

> 🖥️ **在哪執行:** WSL(Ubuntu)· 任何目錄(只是確認 Windows 的 Docker Desktop 有接上)

```bash
docker version && docker compose version
```

> 不想裝 Docker Desktop 的話,也可以在 WSL 裡直接裝 docker engine,
> 但那需要 `systemd` 支援(WSL2 較新版才有),Docker Desktop 是省事的路。

### 1.2 取得程式碼

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~`(還沒有專案目錄)

```bash
cd ~
git clone https://github.com/fttp0165/upload-program-.git
cd upload-program-
git checkout claude/read-article-join-project-1op5nk   # 或 main
```

🔴 **放在 WSL 的檔案系統裡(`~/`),不要放在 `/mnt/c/...`。**
跨檔案系統存取慢 5–10 倍,`pytest` 會從一分鐘變成好幾分鐘。

### 1.3 建立 venv

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt   # 已含執行期相依
```

### 1.4 `.env`

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
cp .env.example .env
```

**跑測試不需要改任何一行**(測試自己造設定,不讀 `.env`)。
只有 §3 要用瀏覽器時才需要填,填法見該節。

---

## 2. 跑測試與 lint(不需要 Docker)

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
.venv/bin/pytest -q                      # 全部測試
.venv/bin/ruff check .                   # lint
.venv/bin/python tools/render_docs.py --check   # 文件 md/HTML 同步
```

這三條就是 CI 第一個 job(`測試 / lint / 文件同步`)做的事。
**推之前先跑這三條**,可以省掉一趟 CI 往返。

常用的縮寫:

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
.venv/bin/pytest -q tests/test_filetypes.py          # 只跑一個檔
.venv/bin/pytest -q -k 中文                          # 只跑名字含「中文」的
.venv/bin/pytest -q -x                               # 第一個失敗就停
.venv/bin/pytest -q --lf                             # 只重跑上次失敗的
```

改完文件記得產生 HTML(否則 CI 的同步檢查會紅):

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
.venv/bin/python tools/render_docs.py    # 不帶 --check 才會寫檔
```

---

## 3. 用瀏覽器實際操作(需要 Docker)

### 3.1 起相依服務

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps    # 兩個都要 healthy
```

🔴 **一定要帶 `-f docker-compose.dev.yml`。**
不帶的話 compose 會去讀正式機那份,它要 `cats-edge` 網路與 GHCR image,
在本機必然失敗(而且那份本來就不是給本機用的)。

### 3.2 `.env` 改四行

> 📄 **編輯哪個檔:** `~/upload-program-/.env`(用 `nano .env` 或 VS Code 開,**不是**貼進終端機)

```bash
DATABASE_URL=postgresql+asyncpg://upload_program_user:devpassword@127.0.0.1:15432/upload_program_db
S3_ENDPOINT_URL=http://127.0.0.1:19000
S3_ACCESS_KEY=devaccesskey
S3_SECRET_KEY=devsecretkey
```

另外這兩個要有值(內容隨便,但不能空):

> 📄 **編輯哪個檔:** `~/upload-program-/.env`(用 `nano .env` 或 VS Code 開,**不是**貼進終端機)

```bash
SESSION_SECRET=local-dev-only-not-a-real-secret
OIDC_CLIENT_SECRET=local-dev-placeholder
```

🔴 **正式機的 secret 一個字都不要貼進本機 `.env`。** 本機用假值就好——
反正登入本來就不能用(§5),真 secret 放進來只是多一份外洩面。

### 3.3 建表並啟動

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
.venv/bin/alembic upgrade head           # 建表(第一次,或有新 migration 時)
.venv/bin/uvicorn app.asgi:app --reload --port 8000
```

打開 <http://127.0.0.1:8000/upload/help> —— 看得到使用教學頁就成功了。
`--reload` 會在你存檔時自動重啟。

**啟動時會看到這一行,是正常的,不是壞掉:**

```json
{"level": "error", "message": "啟動時取不到 OIDC discovery,稍後由 /ready 重試"}
```

本機連不到 Keycloak,而 discovery 失敗**刻意不擋啟動**(設計如此:`/health`
要在 60 秒內綠,IdP 沒起來由 `/ready` 反映)。連帶的預期是:

| 端點 | 本機預期 |
|---|---|
| `/health` | ✅ 200(liveness,不查 DB 也不查 IdP) |
| `/ready` | ❌ 本機必然失敗(它會去取 JWKS)——**這不是 bug** |
| `/upload/help` | ✅ 200 |

**MinIO 主控台**在 <http://127.0.0.1:19001>(帳密 `devaccesskey` / `devsecretkey`),
可以直接看上傳的物件長什麼樣。

### 3.4 收工 / 重來

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
docker compose -f docker-compose.dev.yml down          # 停掉,資料留著
docker compose -f docker-compose.dev.yml down -v       # 連資料一起砍掉,下次從乾淨的來
```

---

## 4. 在本機跑完整 CI

CI 有三個 job,本機能跑前兩個:

### 4.1 job 1:測試 / lint / 文件同步

見 §2 的三條指令,加上 CI 另外做的兩項檢查:

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
# 不該進 git 的檔案
git ls-files | grep -E '\.(sql|xlsx)$|^\.env$|node_modules/|\.venv/' && echo "❌ 有不該進 git 的檔案" || echo "✅"

# SSO 紅線(零 Authentik、零 HS256)
grep -ri "authentik\|HS256" app/ tests/ && echo "❌ 踩到 SSO 紅線" || echo "✅"
```

### 4.2 job 2:建 image + 弱點掃描(需要 Docker)

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
# 🔴 --pull 不可省(T96):少了它,base image 會被本機快取釘在舊版,
#    掃描結果與 CI 不一致,而且會一直帶著早就修好的 CVE。
docker build --pull -t upload-program:ci .

# image 大小(目標 ≤300 MB)
docker image inspect upload-program:ci --format '{{.Size}}' | awk '{print $1/1024/1024 " MB"}'

# 以 non-root 啟動(必須是 1000 或 app)
docker image inspect upload-program:ci --format '{{.Config.User}}'

# 弱點掃描 —— 與 CI 逐字相同的指令
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:0.70.0 image \
  --format table --exit-code 1 \
  --severity HIGH,CRITICAL --ignore-unfixed \
  upload-program:ci
```

Trivy 第一次跑要下載弱點資料庫,約 1–2 分鐘,之後有快取。

### 4.3 job 3:Push GHCR

**本機不跑,也不該跑。** 它只在版本 tag 觸發,而且推 image 是 CI 的事——
從本機推等於繞過「一個 git tag 對一個 image tag」的紀律。

---

## 5. 🔴 本機做不到的事(誠實清單)

| 做不到 | 為什麼 | 怎麼辦 |
|---|---|---|
| **登入** | OIDC 打的是真的 Keycloak,而 `http://localhost:8000/...` 這個 redirect URI **沒有登記在 client 上**(契約 §4:逐字比對)。這不是 bug | 登入後的行為靠 528 條測試涵蓋;真的要點畫面就上正式站 |
| 跨 App 單一登出 | 同上,需要真實 IdP session | 測試項目清單標 🖐 人工,在正式站驗 |
| gateway 行為(剝前綴、標頭) | 本機沒有 portal-gateway | 換版冒煙時在正式站驗(`tools/smoke.sh`) |

**不會為了本機方便去要求 portal 加一個 localhost 的 redirect URI**——
那等於在正式 client 上開一個指向某台開發機的洞,不成比例。

---

## 6. 常見卡關

| 症狀 | 原因與解法 |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | 用了系統 python。指令一律加 `.venv/bin/` 前綴,或先 `source .venv/bin/activate` |
| `docker compose up` 說找不到 `cats-edge` | 忘了加 `-f docker-compose.dev.yml`(§3.1) |
| `connection refused` 到 5432 | dev compose 的 port 是 **15432** 不是 5432(避免與你機器上其他 PG 打架) |
| pytest 很慢 | 專案放在 `/mnt/c/...`。搬到 `~/`(§1.2) |
| `render_docs --check` 紅燈 | 改了 md 沒產生 HTML。跑一次不帶 `--check` 的 |
| Trivy 說有 HIGH CVE | 先確認有加 `--pull`。若上游尚未修,**不要**用 `.trivyignore` 換綠燈——見 T96 / T82 |
| `.env` 缺值,啟動就退出 | 這是設計(fail-fast,不安靜地跑起來)。錯誤訊息會指名缺哪個變數 |
| 啟動 log 有「取不到 OIDC discovery」 | **正常**,本機連不到 Keycloak。不擋啟動,見 §3.3 |
| `/ready` 不通 | **正常**,它要取 JWKS。本機用 `/health` 判活著 |

---

## 7. 一頁速查

> 🖥️ **在哪執行:** WSL(Ubuntu)· 工作目錄 `~/upload-program-`

```bash
# 日常:改程式 → 驗證
.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/python tools/render_docs.py --check

# 要看畫面
docker compose -f docker-compose.dev.yml up -d
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.asgi:app --reload --port 8000

# 推之前跑完整 CI
docker build --pull -t upload-program:ci .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.70.0 \
  image --format table --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed upload-program:ci
```

---

## 相關文件

- [runbook_換版與備份還原.md](runbook_換版與備份還原.md) —— 正式機的換版與還原
- [測試SOP.md](測試SOP.md) —— 怎麼寫測試(流程)
- [測試項目清單.md](測試項目清單.md) —— 測什麼(97 項)

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v2.0 | 2026-08-12 21:10 | Claude(Benny 裁示) | 依**憲法第十條**為全部 16 個指令區塊標明機器與工作目錄;區分 🖥️ 執行 / 📄 編輯(`.env` 那兩段是編輯不是貼指令);導言改述新的閱讀方式 |
| v1.1 | 2026-08-12 20:25 | Claude(Benny:部屬本地 WSL 跑 CI) | §3.3 補上「啟動 log 會有 OIDC discovery 失敗」是**預期行為**(不擋啟動)與三個端點的本機預期(`/ready` 本機必然紅);§6 對應加兩列 |
| v1.0 | 2026-08-12 20:10 | Claude(Benny:部屬本地 WSL 跑 CI) | 初版:WSL 準備、venv、`docker-compose.dev.yml` 起相依、本機跑 CI 三個 job(job 3 不跑的理由)、🔴 本機做不到的事(登入)、常見卡關七項、一頁速查 |
