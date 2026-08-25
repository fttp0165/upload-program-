# upload-program — AI 小程式分享平台

**專案:** upload-program
**建立日期:** 2026-07-25 12:00
**最後更新:** 2026-08-12 20:15
**版本:** v2.1

公司內部的**開發者程式分享平台**(簡易型 GitHub):登入後建立專案,
在專案內上傳**原始碼、執行檔、程式文件**,以**版本**為單位發布,同事可以瀏覽、搜尋、下載。

做的是「**有版本管理的程式發布倉庫**」,不是分散式版本控制系統。
MVP **不做**:git 協定(clone/push)、PR / code review、issue、線上編輯、CI 建置、外網公開。

| | |
|---|---|
| 線上網址 | `https://catsapp.sporton.com.tw/upload/`(公司內部;登入走平台 SSO) |
| 目前版本 | 見首頁頁尾;程式內的單一真相是 [`app/version.py`](app/version.py) |
| 進度 | M1–M5 完成、M6 僅餘還原演練(T27)、M7 前端全數完成 |
| 測試 | **409 條**,CI(自架 runner)測試 / image + Trivy / GHCR push 三段全綠 |

---

## 功能(已實作,均有測試背書)

**專案與版本**
- 建立專案(短名、名稱、簡介、標籤);可見性 `internal`(全員可讀)/ `private`(僅成員,對非成員回 404 不洩漏存在)
- 成員與角色:owner / maintainer / viewer;**轉移擁有權**(人員異動不會讓專案變孤兒)
- 版本 draft → published;**發布後鎖定**不可換檔;最新版有固定網址捷徑,連結寫進文件不會因發新版失效

**檔案**
- 三類:`doc` 更新文件 / `binary` 執行檔 / `source` 原始碼包
- 🔴 **每一版發布必須三類齊備**——缺任一類無法發布,缺項會明確列出
- 串流上傳(記憶體不隨檔案大小成長)、**magic bytes 判型**(不信副檔名)、每檔 SHA-256
- 單檔上限 100 MB;專案容量分級(標準 2 GB / 擴充 10 GB,需管理員核可)
- 下載一律 `Content-Disposition: attachment` + `nosniff`;下載次數統計

**帳號與管理**
- SSO(Keycloak,Authorization Code + PKCE);**首登建零角色帳號 → 待開通 → 管理員開通**
- 靜默 SSO:已在平台登入過的人進站免再按登入
- 管理後台:待開通清單與一鍵開通、停用、指派平台角色、**稽核紀錄**(保留 365 天)
- **管理總覽 `/admin`**:KPI 六卡 + 需要處理的待辦(待開通、逼近容量、停滯 draft、未掃描檔案、擁有者已停用的專案)

**其他**
- 使用教學頁 `/help`(匿名可看)含回報問題管道;單一登出(front-channel logout 端點)

**尚未做**:病毒掃描(掃毒未接上前,所有檔案誠實標示 `not_scanned`,不假裝安全)。

---

## 技術棧

Python 3.12 · FastAPI · SQLAlchemy 2.0(async)· Alembic · PostgreSQL 15 · MinIO ·
Jinja2 + Bootstrap 5(自託管)· gunicorn · Docker Compose

前端**沒有框架、沒有 CDN**:CSP 是 `default-src 'self'`,外部資源與 inline script/style 一律被擋。
圖示用行內 SVG,JS 一律外部檔案。

### 路徑拓撲(最容易誤解的一點)

```
瀏覽器  https://catsapp.sporton.com.tw/upload/static/app.css
          │
   portal-gateway   proxy_pass http://upload-program:8080/;   ← 尾斜線會「剝掉」前綴
          ▼
本服務收到          /static/app.css
```

**路由註冊用不帶前綴的路徑;頁面裡的連結必須帶前綴**(由模板的 `url()` 負責)。
兩者搞混就是一次全站 404 事故。詳見 [`app/web_urls.py`](app/web_urls.py)。

---

## 本地開發

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # 含 requirements.txt 的執行期相依
cp .env.example .env          # 填入必要變數;缺值會 fail-fast,不會安靜地跑起來
.venv/bin/python -m pytest -q # 測試(SQLite in-memory,不需要 DB/MinIO)
.venv/bin/ruff check .        # lint
.venv/bin/python tools/render_docs.py   # 由 md 產生文件的 HTML 版
```

要用瀏覽器實際操作才需要 PostgreSQL 與 MinIO,起法是:

```bash
docker compose -f docker-compose.dev.yml up -d   # 🔴 一定要帶 -f
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.asgi:app --reload --port 8000
```

🔴 **不要用根目錄的 `docker-compose.yml`**——那是正式機形狀(要 `cats-edge`
外部網路、拉 GHCR image、掛 VM 上的 CA 憑證),在本機必然失敗。
WSL 從零開始、以及在本機跑完整 CI 的逐條指令見
[runbook_本地開發與CI.md](docs/runbook_本地開發與CI.md);
🔴 本機**登入不能用**(redirect URI 未登記),原因與界線見該文件 §5。

### 專案結構

```
app/            routers/(API 與網頁分離)· models · dashboard · filetypes · quota · audit
                templates/(Jinja2)· static/(app.css、vendor/)
tests/          409 條,與功能同步成長
alembic/        migration(revision id 慣例 = 檔名全名)
tools/          render_docs.py · backup.sh · purge_audit.py · purge_name_cache.py
docs/           開發計畫書 · 任務表 · 功能需求大綱 · 設計 · runbook · dev-logs/ · plans/
```

---

## 部署與換版

正式環境是 Cats 共用 VM 上的 docker compose,gateway 由 portal 管理。
**完整步驟一律以 [docs/runbook_換版與備份還原.md](docs/runbook_換版與備份還原.md) 為準**,這裡只放三條必記的:

- 🔴 正式機**不 build、不 `git pull`**——部署 = 改 image tag 後 `pull`
- 🔴 production compose **禁 `latest` tag**
- 🔴 換版重建容器後**必須**通知 portal `docker exec portal-gateway nginx -s reload`,否則靜默 502

換版是否成功,看**首頁頁尾的版本號**,不必再挑靜態檔當哨兵。

---

## 平台整合

本服務是 Cats 平台的一個 App,受平台技術規約管轄(權威在 `cats-portal/DOCS/`,本 repo 只引用不複製):

- **SSO 接入契約**:已對齊 **v2.0**(2026-07-31)。業務庫**只存 `sub`**;
  顯示名稱快取依 §4.2a L1 通則;§10 單一登出義務已實作 front-channel logout 端點。
- **路徑前綴 `/upload/` 由平台分配**,不自選;`upload-program` 是平台登記的技術識別名
  (image、compose 服務名、log 的 `service` 欄位都是它)——與對外顯示的中文名稱是兩件事。

---

## 開發規則

**動手前先讀 [CLAUDE.md](CLAUDE.md)(開發憲法,八條)。** 摘要:

1. 小步快跑,每個任務獨立可交付、可回滾
2. **先寫計畫再動工**(小任務寫 dev-log 的計畫段,大任務先出 `docs/plans/` 文件)
3. **TDD**:先寫一條會失敗的紅測試,再改程式
4. 文件一律 light 主題,md + HTML 並存(HTML 由 `tools/render_docs.py` 產生)
5. 中文註釋,寫「為什麼」不只寫「做了什麼」
6. 完工寫開發日誌(`docs/dev-logs/`)
7. 文件要有日期、版本與版本歷史
8. **發版必附 title 與 content**;`APP_VERSION` 要在打 tag 前改好

### 幾條與改這個 repo 最相關的紅線

- 🔴 secret / `.env` / 私鑰**絕不進 git**;`.env.example` 只列變數名
- 🔴 上傳驗 magic bytes、**HTML/SVG 一律拒收**;下載強制 attachment + nosniff
- 🔴 log 走 stdout、JSON 單行,**不記密碼 / 完整 JWT / 個資**
- 🔴 CSP 逐字不放寬(連 `data:` URI 都擋);token 不得進 `localStorage`
- 🔴 動資料的 migration 必須有 backward,且先在 staging 雙向演練

---

## 文件索引

| 文件 | 用途 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 開發憲法(八條)與本專案紅線 |
| [docs/開發計畫書.md](docs/開發計畫書.md) | 里程碑 M1–M7、資料分級、回滾策略 |
| [docs/任務表.md](docs/任務表.md) | 每個任務(Tnn)的狀態與驗收標準 |
| [docs/功能需求大綱.md](docs/功能需求大綱.md) | 功能項(Fnn)與覆蓋現況 |
| [docs/設計_MVP.md](docs/設計_MVP.md) | 領域模型、API、儲存、安全設計 |
| [docs/runbook_換版與備份還原.md](docs/runbook_換版與備份還原.md) | 換版、回滾、備份、還原演練 |
| [docs/決策_前端技術選型.md](docs/決策_前端技術選型.md) | 為什麼是 Jinja2 + 原生 JS |
| [docs/plans/](docs/plans/) | 設計文件與往來 portal 的申請/聲明 |
| [docs/dev-logs/](docs/dev-logs/) | 每個任務的開發日誌(計畫 → 結果) |

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v2.1 | 2026-08-12 20:15 | Claude(Benny:部屬本地 WSL 跑 CI) | 🐛 §本地開發回寫一句**錯誤指示**:原本寫「最省事的方式是 `docker compose up -d`」,但根目錄的 compose 是正式機形狀(要 `cats-edge` 外部網路、拉 GHCR image、掛 VM 的 CA 憑證),在本機必然失敗;改指向新增的 `docker-compose.dev.yml` 與 [runbook_本地開發與CI.md](docs/runbook_本地開發與CI.md),並標明本機登入不能用。依第九條補抬頭「專案」 |
| v2.0 | 2026-07-31 23:30 | Claude(Benny 指示) | **改寫**(原本僅標題 + 一句描述):補上定位與線上狀態、功能一覽、技術棧與路徑拓撲圖、本地開發與測試指令、部署三條必記紅線(細節指向 runbook)、平台整合與契約對齊版本、開發規則摘要與紅線、文件索引;依第七條補日期/版本/本表 |
| v1.0 | 2026-07-25 12:00 | Benny | 初版:一句話描述 |
