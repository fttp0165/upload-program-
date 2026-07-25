# upload-program MVP 設計

**建立日期:** 2026-07-25
**狀態:** 📐 草案——技術棧與路徑前綴 ⏳ 待定案後升版
**上位文件:** 平台通用規約(`platform-charter` skill)、`cats-portal/DOCS/帳號系統接入契約_SSO.md`

---

## 1. 產品目標

公司內部的**開發者程式分享平台**——「簡易型 GitHub」。

開發者登入後可以:

1. **建立專案**(一個程式/工具 = 一個專案)
2. **開啟專案**後上傳三類內容:**原始碼**、**執行檔**、**程式文件**
3. 以**版本**為單位發布,他人可瀏覽、搜尋、下載

### 明確不做(MVP 不含,避免無限膨脹)

| 不做 | 理由 |
|---|---|
| git 協定(`git clone`/`push`)、commit 圖、diff | 這是 GitHub 的核心難點;MVP 以「版本化的檔案集」取代 |
| Pull Request / code review / issue 追蹤 | 第二期再議 |
| 線上編輯、CI/建置 | 上傳既有產物即可 |
| 外網公開 | 內部平台,登入才可見 |

> 一句話界線:**我們做的是「有版本管理的程式發布倉庫」,不是分散式版本控制系統。**

---

## 2. 領域模型

```
user ──< project_member >── project ──< release >── artifact
```

| 實體 | 說明 | 關鍵欄位 |
|---|---|---|
| `user` | 平台使用者 | `id`(UUID,內部鍵)、`sub`(unique,對應 JWT `sub`)、`status`(pending/active)、`platform_role` |
| `project` | 一個程式/工具 | `id`、`slug`(unique)、`name`、`summary`、`visibility`(internal/private)、`owner_id` |
| `project_member` | 專案層權限 | `project_id`、`user_id`、`role`(owner/maintainer/viewer) |
| `release` | 一次發布 | `id`、`project_id`、`version`、`notes`、`status`(draft/published)、`created_by`、`created_at` |
| `artifact` | 發布內含的檔案 | `id`、`release_id`、`kind`(source/binary/doc)、`filename`、`size_bytes`、`sha256`、`mime`、`storage_key` |

**🔴 業務庫只存 `sub`**——`user` 表**沒有** email / 姓名 / 密碼欄。顯示名稱與 email 由 IdP 的 ID token / userinfo 即時提供,不落地(SSO 契約 §4.2)。

### 權限模型(deny-by-default)

- **平台層:** 首登自動建 `status=pending` 零角色 user → 所有業務 API 回 **403 待開通**,文案指引「找 upload-program 管理員開通」。開通在本服務後台,不碰 Keycloak。
- **專案層:** `owner`(全權,含刪除)> `maintainer`(發版、傳檔)> `viewer`(讀、下載)。
- `visibility=internal` = 所有 **active** 使用者可讀;`private` = 僅成員可讀。

---

## 3. 檔案儲存

- **檔案本體走物件儲存**(S3 相容 / MinIO);容器內**不寫本地檔案**作為狀態(charter 鐵則)。
- DB 只存 metadata + `storage_key`。
- 物件 key 格式:`projects/<project_id>/releases/<release_id>/<artifact_id>/<filename>`
- 大檔上傳走 **presigned PUT**(前端直傳物件儲存),後端只發簽章與收確認 → 服務不當檔案中繼、記憶體不爆。

### 上傳安全(🔴 執行檔平台的重點)

1. 驗 **MIME 與 magic bytes**,不信任副檔名與前端送的 Content-Type。
2. 每個 artifact 計算並保存 **SHA-256**,下載頁顯示供使用者自行校驗。
3. 單檔與專案容量上限走環境變數(`MAX_ARTIFACT_BYTES`、`MAX_PROJECT_BYTES`),超過回 413。
4. 下載一律 `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`;
   **絕不**讓上傳內容以 HTML/JS 在本服務網域被瀏覽器執行(否則等於開放 XSS 打自己的 session)。
5. 病毒掃描(ClamAV)⏳ 待定:MVP 至少保留 `scan_status` 欄位與「未掃描」標示,別讓「內部平台」變成惡意執行檔散播管道。

---

## 4. API 草案

- 路徑前綴 ⏳ **由 Platform 團隊分配,不得自選**(charter);以下用 `<prefix>` 代表。
- 版本走路徑:`<prefix>/v1/...`;錯誤回應一律 **RFC 7807**;時間 **ISO 8601 含時區**。

| Method | 路徑 | 用途 |
|---|---|---|
| GET | `/health` / `/ready` | liveness / readiness(charter 必要) |
| GET | `<prefix>/v1/me` | 目前身分 + 開通狀態 + 專案角色 |
| GET/POST | `<prefix>/v1/projects` | 列出 / 建立專案 |
| GET/PATCH/DELETE | `<prefix>/v1/projects/{slug}` | 專案詳情 / 修改 / 刪除 |
| GET/PUT/DELETE | `<prefix>/v1/projects/{slug}/members` | 專案成員與角色 |
| GET/POST | `<prefix>/v1/projects/{slug}/releases` | 列出 / 建立版本(draft) |
| POST | `<prefix>/v1/releases/{id}/artifacts` | 登記檔案 → 回 presigned PUT URL |
| POST | `<prefix>/v1/releases/{id}/artifacts/{aid}/complete` | 直傳完成回報(驗 size/sha256/magic bytes) |
| POST | `<prefix>/v1/releases/{id}/publish` | draft → published |
| GET | `<prefix>/v1/releases/{id}/artifacts/{aid}/download` | 302 至短效 presigned GET |
| GET | `<prefix>/v1/search?q=` | 跨專案搜尋(名稱/摘要/標籤) |

寫入類 `POST`/`PUT`/`DELETE` 支援 `Idempotency-Key`(charter 跨服務規範)。

---

## 5. 對齊平台規約的落點

| 規約 | 本服務怎麼做 |
|---|---|
| 只有一個身份來源 | 接 Keycloak OIDC(Auth Code + PKCE);不自建帳號、不簽 token |
| JWT 驗證 | RS256、JWKS 快取 1h、支援 `kid` 輪替、驗 `iss`/`aud`/`exp`、±30s;失敗 401 |
| 業務庫不存個資 | `user` 表只有 `sub` + 本地角色 |
| Log | stdout JSON 單行,含 `trace_id`/`user_id`;**不記** JWT、檔案內容、完整 email |
| 容器 | multi-stage build、non-root、EXPOSE 8080、HEALTHCHECK `/health` |
| DB | 一服務一 database(`upload_db` / `upload_user`),連線池 ≤20,不跨庫 join |
| 設定 | 全走環境變數,缺必要變數 **fail-fast**;`.env` 不進 git,附 `.env.example` |
| 發布 | SemVer tag,production compose 指明版本不用 `latest`,Trivy 掃描 |

---

## 6. 待定案(⏳)

| 項目 | 待誰決定 |
|---|---|
| 技術棧(語言/框架) | 開發者 |
| 物件儲存(MinIO 自架 / 既有 S3) | 開發者 + Platform |
| 路徑前綴、hostname、repo/image 命名 | Platform 團隊分配 |
| SSO client(`client_id`、redirect URI) | 走 SSO 契約 §5 向 portal 申請 |
| 病毒掃描是否納入 MVP | 開發者 |
