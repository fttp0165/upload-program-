# upload-program MVP 設計

**建立日期:** 2026-07-25 15:35
**最後更新:** 2026-07-27 14:52
**版本:** v2.6

> 技術設計文件。產品範圍與里程碑見 [開發計畫書.md](開發計畫書.md);任務追蹤見 [任務表.md](任務表.md)。
> **上位文件:** 平台通用規約(`platform-charter`)、Cats 新服務接入指南 v2.0、
> `cats-portal/DOCS/帳號系統接入契約_SSO.md`——本文只引用不複製。

---

## 1. 部署拓撲(決定了下面所有設計)

```
                        內網使用者
                            │ HTTPS 443
                  ┌─────────▼──────────┐
                  │   portal-gateway   │  全 VM 唯一持 80/443,依路徑前綴分流並剝掉前綴
                  └─────────┬──────────┘
                    external network: cats-edge
                            │  /«PREFIX»/
        ┌───────────────────▼────────────────────┐
        │  upload-program stack                  │
        │  ┌──────────────────────────────────┐  │
        │  │ svc:8080(FastAPI,對外容器)      │  │  ← 只有它上 cats-edge
        │  └────────┬──────────────┬──────────┘  │
        │  ┌────────▼───────┐ ┌────▼──────────┐  │
        │  │ db(PostgreSQL)│ │ minio(S3 相容)│  │  ← 不曝 port、不上 cats-edge
        │  └────────────────┘ └───────────────┘  │
        └────────────────────────────────────────┘
                            ╎
                    ┌───────▼────────┐
                    │ Keycloak(IdP) │  只驗 token,不自建帳號
                    └────────────────┘
```
<!--SVG:architecture-->

### 1.1 兩個由拓撲推導出的關鍵決定

| 決定 | 為什麼 |
|---|---|
| **上傳/下載由服務串流轉送**,不用 presigned 直傳 | MinIO 在 `backend` 網路、不曝 port,全 VM 只有 gateway 持 443——**瀏覽器根本連不到 MinIO**。presigned 需另開 gateway 路由,且簽章綁 host/path,前綴剝除會破壞簽章 |
| **路由註冊在根路徑**,只設 `root_path` | gateway 以尾斜線 `proxy_pass http://<alias>:8080/;` **剝掉**前綴,容器收到的是根路徑;自己再加前綴會變雙前綴 404 |

### 1.2 子路徑的連帶影響

- 對外絕對網址(OIDC redirect、登入後導回)一律用 `PUBLIC_BASE_URL + API_PREFIX` 組出,
  **不從 request 推導**——TLS 在 gateway 終結,推導會得到 `http://`。
- **cookie path 綁到 `/«PREFIX»/`**,避免與同主機其他 App 的 cookie 互蓋。
- OIDC redirect URI 實際為 `https://catsapp.sporton.com.tw/«PREFIX»/oidc/callback/`,
  與 SSO 契約 §4.1 的字面(`https://<hostname>/oidc/callback/`)不同,申請 client 時需登記後者並向 portal 確認。
- gateway 需要放寬 `client_max_body_size` 與逾時(≥ `MAX_ARTIFACT_BYTES`),申請路由時一併提出。

---

## 2. 領域模型

```
user ──< project_member >── project ──< release >── artifact
```

| 實體 | 說明 | 關鍵欄位 |
|---|---|---|
| `user` | 平台使用者 | `id`(UUID 內部鍵)、`sub`(unique)、`status`(pending/active/disabled)、`platform_role` |
| `project` | 一個程式/工具 | `id`、`slug`(unique)、`name`、`summary`、`visibility`、`owner_id`、`total_bytes`、`quota_tier` |
| `project_member` | 專案層權限 | `project_id`、`user_id`、`role`(owner/maintainer/viewer) |
| `project_tag` | 專案標籤(F42) | `project_id`、`tag`(正規化後的小寫字串);**刻意不做標籤正規化表**,避免孤兒標籤的維運負擔 |
| `release` | 一次發布 | `id`、`project_id`、`version`、`notes`、`status`(draft/published)、`created_by_id` |
| `artifact` | 發布內含的檔案 | `id`、`release_id`、`kind`、`filename`、`size_bytes`、`sha256`、`content_type`、`storage_key`、`upload_status`、`scan_status`、`download_count` |

**🔴 業務庫只存 `sub`**——`user` 表**沒有** email / 姓名 / 密碼欄。顯示用的 email 與名稱由 IdP 的
ID token / userinfo 即時提供,不落地(SSO 契約 §4.2)。PLM 的姓名快取是 §6.1 具名特例,本專案不繼承。

### 2.1 權限模型(deny-by-default)

- **平台層:** 首登自動建 `status=pending` 零角色 user → 所有業務 API 回 **403 待開通**,
  文案指引「找 upload-program 管理員開通」。開通在本服務後台,不碰 Keycloak。
  `/v1/me` 刻意對 pending 也開放——否則使用者只會撞到 403 卻不知道自己是誰、該找誰。
- **專案層:** `owner`(全權,含刪除)> `maintainer`(發版、傳檔)> `viewer`(讀、下載)。
- `visibility=internal` = 所有 active 使用者可讀;`private` = 僅成員可讀,
  且對非成員一律回 **404**(不洩漏 private 專案是否存在)。

---

## 3. 檔案處理

### 3.1 上傳

`PUT /v1/releases/{release_id}/artifacts/{filename}?kind=binary`,**request body 為檔案原始位元組**。

不用 `multipart/form-data`:Starlette 的 multipart 解析會把大檔落到暫存檔,
違反平台鐵則「容器內不寫檔當狀態」。raw body 可直接串流。

流程:

1. `Content-Length` 先擋掉明顯過大的請求(單檔上限、專案配額),不必等收完才發現
2. 邊收邊送 S3 **multipart**(每 part ≥5 MiB),同時 `hashlib.sha256` 增量計算
3. 收到前 4 KB 時做 **magic bytes 判型**——不過就中止,**此時尚未寫出任何 part,不留物件**
4. 完成後比對呼叫端宣告的 `X-Content-SHA256`(若有),不符即刪物件並作廢
5. 記憶體用量固定在一個 chunk,不隨檔案大小成長

物件 key:`projects/<project_id>/releases/<release_id>/<artifact_id>/<filename>`

### 3.2 判型白名單(`app/filetypes.py`)

不依賴 libmagic(避免 image 多帶系統相依),自建簽章表:ELF、PE(MZ)、Mach-O、
zip、gzip、bzip2、xz、7z、rar、tar(offset 257)、OLE、deb、rpm、PDF、PNG、JPEG、GIF。

- 每個 `kind` 有各自的允許清單:執行檔不得以 `doc` 名義上傳,PDF 不得以 `binary` 名義上傳
- **`text/html` 與 `image/svg+xml` 在任何 kind 都拒收**——本服務散布可執行檔,
  讓上傳內容在本網域被瀏覽器執行等同自開 XSS
- 認不得的型別一律擋下,由人決定要不要放行

### 3.3 下載

`GET /v1/releases/{id}/artifacts/{aid}/download` → 由服務串流讀出物件,並強制:

- `Content-Disposition: attachment; filename*=UTF-8''…`
- `Content-Type: application/octet-stream` + `X-Content-Type-Options: nosniff`
- `X-Artifact-SHA256`(供使用者自行校驗)、`X-Artifact-Scan-Status`

### 3.4 容量級距(F17 / `app/quota.py`)

專案總容量分兩級距,`projects.quota_tier` 只存**代號**,對應的位元組數來自設定:

| 級距 | 設定值 | 預設 | 誰能改 |
|---|---|---|---|
| `standard` | `MAX_PROJECT_BYTES` | 2 GB | ——(預設) |
| `extended` | `MAX_PROJECT_EXTENDED_BYTES` | 10 GB | **只有平台管理員** |

**為什麼存代號而不是位元組數字**:政策數字若複製到每一列,日後調整級距就變成一次
資料遷移,而且分不清哪些列是政策預設、哪些是個案調整。存代號則改設定值即可。

- 上傳有兩道同樣的檢查:`Content-Length` 預檢(省頻寬)與收完後檢查(chunked 沒有
  Content-Length)。兩道共用 `quota.too_large()` 組訊息,否則必然漂移。
- 超限的 413 **不只丟一句 Payload Too Large**:detail 含級距、上限、已用量、本次大小,
  並給依級距而異的指引(standard 說「可申請擴充」、extended 說「請清理舊版本」——
  對已是最大級距的人講「可申請」是錯誤指引)。數值同時以 RFC 7807 擴充成員
  `quota_tier` / `quota_bytes` / `used_bytes` / `incoming_bytes` 帶出。
- **降級允許且不刪檔**:管理員可能正是要用降級逼專案清理;既有檔案仍可下載,
  只擋新上傳,並記一筆 warning。

### 3.5 下載次數統計(F43)

`artifacts.download_count` 一個整數欄位;**版本的次數是底下所有檔案的加總,不另存欄位**
(兩個計數器分開存,刪檔或補傳漏掉一次就永遠對不起來)。

- **刻意不做下載事件表**:F43 只要求「次數」,「誰下載了什麼」是稽核(F54)的職責。
  用計數欄位的話,「統計不記個資」是**結構上做不到**而不是靠自律——表裡根本沒有可以放人的欄位。
- 計數點就是 `_download_response()`,與安全標頭同一個地方——換一條路徑就不算數的統計等於沒有統計。
- 🔴 累計一律用 `UPDATE ... SET download_count = download_count + 1`。
  Python 端的 `+= 1` 是讀-改-寫,併發會默默掉數且不會有任何錯誤訊息。
- 算的是「**發起**下載」(回應建構那一刻),不是「完成下載」;中途中斷仍算一次。
  這個數字的用途是熱門度而非計費,不值得為此把串流路徑複雜化。
- `upload_status is ready` 的檢查在計數之前,所以失敗的下載(404)不會灌水。

### 3.6 尚未做的

**病毒掃描未接**(T25 待決)。`scan_status` 預設 `not_scanned` 並隨下載回傳——
「內部平台」不是把未掃描執行檔說成安全的理由。

---

## 3.7 網頁介面(T40 起)

Jinja2 伺服器端模板,與 `/v1/*` 的 API 路由分離(決策文件 §6.1):

```
app/templates/base.html   版型骨架 + 導航列(全站共用)
app/templates/home.html   首頁
app/templates/error.html  錯誤頁(繼承 base)
app/static/app.css        全站樣式(light、零外部資源)
app/routers/web.py        網頁路由 + 靜態檔
```

### 🔴 子路徑:兩個方向都會出錯

gateway 以尾斜線 `proxy_pass` **剝掉前綴**後轉發,所以同一個資源有兩個路徑:

| | 瀏覽器看到的 | 本服務收到的 |
|---|---|---|
| 首頁 | `https://host/upload/` | `/` |
| 樣式表 | `https://host/upload/static/app.css` | `/static/app.css` |

- **路由註冊用不帶前綴的路徑**;**頁面裡的連結必須帶前綴**,一律經過
  `web_urls.web_url(settings, path)`,漏一個就是一次 404。
- 產出的是 **root-relative** 網址,不組 scheme/host——TLS 在 gateway 終結,
  從 request 推導會得到 `http://`。絕對網址只留給 OIDC redirect_uri。
- 🐛 **靜態檔不能用 `app.mount("/static", StaticFiles(...))`**:本 app 設了
  `root_path=api_prefix`(供 `/docs` 產生正確網址),而 Starlette 的 `Mount` 會依
  root_path **再剝一次**前綴,傳給 StaticFiles 的子路徑就多了一層 → 正式環境全站樣式 404。
  改用一般路由 `GET /static/{path:path}`,內部仍委派 `StaticFiles.get_response()`
  以保留其路徑逃逸防護。詳見 T40 開發日誌。

### 身分:網頁不因未登入而 401

網頁用 `optional_identity`(取不到身分回 `None` 而非拋錯):匿名訪客該看到登入按鈕,
不是一頁錯誤。顯示名稱來自 **IdP claims**,不從業務庫讀(業務庫只存 `sub`)。

### CSP

`SecurityHeadersMiddleware` 對所有回應加上:

```
default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'
```

導入時機刻意選在 T40——當時全站零 JS、CSS 也正要搬進 `static/`,是成本最低的一刻。
`default-src 'self'` 同時涵蓋 script-src 與 style-src,**內嵌 `<style>`/`<script>` 一律被擋**,
這正好把樣式與(未來的)上傳 JS 逼成外部檔案。

---

## 4. API

- 路徑前綴 ⏳ 由 Platform 分配;版本走路徑 `/v1/...`;錯誤一律 **RFC 7807**;時間 **ISO 8601 含時區**

### 4.1 錯誤回應的內容協商(T47)

錯誤回應有兩種表述,規則是**兩個條件都要成立**才回 HTML:

| 條件 | 規則 |
|---|---|
| 路徑 | **不是** `/v1/*`,也不是 `/health`、`/ready` |
| `Accept` | **明示**含 `text/html`(`*/*` 不算) |

- **為什麼要有「路徑」這一關**:只看 `Accept` 的話,`curl -H 'Accept: text/html' /v1/projects`
  就能把整個 API 表面的錯誤格式改掉。平台鐵則「錯誤回應一律 RFC 7807」不該被呼叫端的
  一個標頭鬆動。
- **為什麼 `*/*` 不算**:那是 fetch、XHR、curl 的預設值——程式在呼叫,不是人在看。
  瀏覽器導覽送的是 `text/html,application/xhtml+xml,...,*/*;q=0.8`,會命中。
- 協商實作在 `problems.problem_response()` **一個函式**裡,四個 exception handler 全經過它,
  所以不可能有某一類錯誤漏掉。
- 🔴 錯誤頁顯示 `detail` 與 `instance`(= 請求路徑),兩者都是使用者可控內容;
  靠 **Jinja2 autoescape** 逸出,模板中禁用 `|safe`。本服務散布可執行檔,
  錯誤頁自己開一個 XSS 與「不讓上傳內容在本網域執行」是同一條紅線的反面。
- 回應一律帶 `Vary: Accept`(同一 URL 兩種表述,不標會被快取餵錯)與 `X-Content-Type-Options: nosniff`。
- 錯誤頁 **light 主題、零外部資源**(憲法第四條;而且它正是服務半殘時要顯示的東西)。

| Method | 路徑 | 用途 |
|---|---|---|
| GET | `/health` / `/ready` | liveness(不查相依)/ readiness(查 DB、MinIO、JWKS) |
| GET | `/auth/login` | 導向 IdP(Auth Code + PKCE) |
| GET | `/oidc/callback/` | IdP 導回,換 token、建/取本地 user |
| POST | `/auth/refresh` | 以 refresh token 換新 access token |
| GET | `/auth/logout` | single logout(導 IdP end_session) |
| GET | `/v1/me` | 目前身分 + 開通狀態(pending 亦可讀) |
| GET/POST | `/v1/projects` | 列出 / 建立專案 |
| GET/PATCH/DELETE | `/v1/projects/{slug}` | 專案詳情 / 修改 / 刪除 |
| GET/PUT | `/v1/projects/{slug}/members` | 成員與角色 |
| PUT | `/v1/projects/{slug}/owner` | **轉移擁有權**(原 owner 降 maintainer;管理員可代為執行)|
| PUT | `/v1/projects/{slug}/tags` | **整組取代專案標籤**(冪等;小寫正規化、去重)|
| GET | `/v1/tags` | **標籤與使用計數**(只計當事人看得到的專案)|
| PUT | `/v1/projects/{slug}/quota` | **設定容量級距**(standard / extended;**僅平台管理員**)|
| DELETE | `/v1/projects/{slug}/members/{user_id}` | 移除成員 |
| GET/POST | `/v1/projects/{slug}/releases` | 列出 / 建立版本(draft) |
| GET/PATCH/DELETE | `/v1/releases/{id}` | 版本詳情 / 改說明 / 刪除 |
| POST | `/v1/releases/{id}/publish` | draft → published(冪等) |
| GET | `/v1/projects/{slug}/releases/latest` | **最新已發布版本**(固定網址,依 `published_at` 判定)|
| GET | `/v1/projects/{slug}/releases/latest/artifacts/{filename}/download` | **以檔名下載最新版檔案**(連結可寫進文件)|
| PUT | `/v1/releases/{id}/artifacts/{filename}` | 上傳檔案 |
| GET | `/v1/releases/{id}/artifacts/{aid}/download` | 下載 |
| DELETE | `/v1/releases/{id}/artifacts/{aid}` | 刪除檔案 |
| GET | `/v1/search?q=` | 跨專案搜尋(只回可見的) |
| GET | `/v1/projects?tag=` | 依標籤篩選專案(查詢字串同樣正規化)|
| GET/PATCH | `/v1/admin/users` | 開通 / 停用 / 指派平台角色 |

**發布鎖定:** 已 published 的版本不可再增刪檔案,避免「同一版內容被偷換」;要改就發新版本。

---

## 5. 對齊平台規約的落點

| 規約 | 本服務怎麼做 | 檔案 |
|---|---|---|
| 只有一個身份來源 | OIDC Auth Code + PKCE;不自建帳號、不簽 token | `app/oidc.py` |
| JWT 驗證 | RS256、JWKS 快取 1h、`kid` 輪替、驗 iss/aud/exp、±30s、失敗 401 | `app/oidc.py` |
| 業務庫不存個資 | `user` 表只有 `sub` + 本地角色 | `app/models.py` |
| 401/403 語意 | 401=token 無效;403=已認證未開通/無權限,型別 `pending-activation` | `app/problems.py` |
| Log | stdout JSON 單行,含 `trace_id`/`user_id`;不記 JWT、query、檔案內容 | `app/logging_setup.py` |
| 追蹤 | 收 `X-Trace-Id` 沿用,無則產生 UUID v4 並回寫 header | `app/middleware.py` |
| 容器 | multi-stage、python:3.12-slim、non-root UID 1000、EXPOSE 8080、HEALTHCHECK、SIGTERM 30s | `Dockerfile` |
| compose | cats-edge `external: true` + `name`;DB/MinIO 只在 backend、不曝 port | `docker-compose.yml` |
| DB | 一服務一 database、連線池 ≤20、走 service name、不跨庫 join | `app/db.py` |
| 設定 | 全走環境變數,缺必要變數 fail-fast;`.env` 不進 git | `app/config.py` |
| migration | 有 `downgrade()` 可回滾 | `alembic/versions/0001_initial.py` |

---

## 6. 待定案(⏳)

| 項目 | 待誰決定 | 任務 |
|---|---|---|
| 服務短名 / 路徑前綴 / GHCR / DB 命名 | Platform 團隊分配 | — |
| SSO client_id 與 secret | 走 SSO 契約 §5 申請 | T26 |
| 病毒掃描是否納入 MVP | 開發者決策 | T25 |
| gateway `client_max_body_size` 與逾時 | 申請路由時提出 | T28 |
| 前端介面 | M7 | T30 |

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-07-25 15:35 | Claude(Benny 授權) | 初版:產品目標與 MVP 界線、領域模型、presigned 直傳的儲存設計、API 草案、對齊平台規約 |
| v2.1 | 2026-07-26 08:20 | Claude(Benny 授權) | API 表補上 T34 轉移擁有權與 T35 最新版捷徑(含以檔名下載的固定網址) |
| v2.2 | 2026-07-27 05:50 | Claude(Benny 授權) | 領域模型新增 `project_tag`(含單表設計的理由);API 表補上標籤三個端點(T36) |
| v2.6 | 2026-07-27 14:52 | Claude(Benny 授權) | 新增 §3.7 網頁介面(檔案配置、子路徑的兩個方向、`Mount` 二次剝前綴的根因、optional_identity、CSP)(T40) |
| v2.5 | 2026-07-27 14:35 | Claude(Benny 授權) | 新增 §4.1 錯誤回應的內容協商(路徑 AND Accept 兩條件、`*/*` 不算的理由、autoescape 與 `Vary`/`nosniff`)(T47) |
| v2.4 | 2026-07-27 13:55 | Claude(Benny 授權) | `artifact` 加 `download_count`;新增 §3.5 下載次數統計(不做事件表的理由、原子 UPDATE、發起 vs 完成的語意),原 §3.5 順延為 §3.6(T37) |
| v2.3 | 2026-07-27 06:05 | Claude(Benny 授權) | `project` 加 `quota_tier`;新增 §3.4 容量級距(存代號而非數字的理由、兩道檢查共用訊息、413 的內容要求、降級不刪檔),原 §3.4 順延為 §3.5;API 表補上 `PUT /v1/projects/{slug}/quota`(T49) |
| v2.0 | 2026-07-25 15:53 | Claude(Benny 授權) | **依 Cats 接入指南 v2.0 全面修正**:新增 §1 部署拓撲(含 SVG/ASCII 架構圖)與子路徑連帶影響;**儲存設計由 presigned 直傳改為服務串流轉送**(瀏覽器連不到 backend 網路的 MinIO),並說明捨棄 multipart 的理由;新增判型白名單與下載強制 attachment 細節;API 表更新為實際實作的端點;§5 對齊表補上對應檔案;依憲法第七條補日期、版本與本歷史表。產品範圍與里程碑移至開發計畫書 |
