# CLAUDE.md — upload-program

> 上傳平台:讓開發者上傳公司內部開發程式。
> 本檔給任何在本 repo 開工的 Claude session 讀:先知道平台契約在哪,再動手。

---

## 專案定位

- 角色:平台上的一個 App 服務(後端 + 上傳介面)。
- 帳號:**不自建**——走全平台共用帳號(Keycloak SSO),見下方〈平台整合契約〉。
- 目前狀態:骨架未建立;新增任何服務骨架(Dockerfile / compose / 第一個 endpoint)前,
  先讀 platform charter 與下列契約,不要先寫程式再回頭補規約。

---

## 平台整合契約(權威在 cats-portal,勿在本 repo 另立版本)

- **SSO 接入**:`cats-portal/DOCS/帳號系統接入契約_SSO.md`——接 Keycloak 前必讀
  (RS256/JWKS、業務庫只存 sub、首登「待開通」deny-by-default、single logout、PKCE)。
- **gateway / cats-edge**:`cats-portal/DOCS/App服務對齊指南_portal-gateway整合.md`
  ——改 compose network、部署重建(先 disconnect→up→reload gateway)、要新增/修改路由時必讀。
- **gateway 變更公告**:`cats-portal/DOCS/gateway變更通知_D8接管.md`(portal-gateway 已接管 80/443)。
- 上述文件之修改一律走 cats-portal 的 PR;本 repo **只引用、不複製維護**(避免兩真相)。
- Claude session 要讀最新版:`add repo fttp0165/cats-portal` 後讀 `DOCS/` 對應檔案。

---

## 本 repo 的 SSO 接入狀態

本 App 屬接入契約 §6 表列的「**未來新 App**」——尚未申請 client,**接入前一律不自建 auth**。

| 項目 | 值 | 狀態 |
|---|---|---|
| client_id | `upload-program`(暫定) | ⏳ 待 §5 向 portal 申請 |
| redirect URI | `https://<本服務 hostname>/oidc/callback/` | ⏳ hostname 待定案 |
| 需要的 claims | `sub`(必要)、`email`/`email_verified`(顯示與綁定用,不落地)、`groups`(參考) | ⏳ 申請時提出 |
| S2S(client_credentials) | 目前無需求;若上傳流程要呼叫其他服務再提 | ⚪ 未開始 |
| 登記 | 接入契約 §9 登記表 | ⚪ 未掛號 |

接入時要做的事(細節以契約為準,這裡只列不可忘的紅線):

1. Authorization Code + **PKCE**;禁 implicit、禁 HS256、禁自簽 token。
2. **每個 API 都要驗 token**:RS256、JWKS 快取 1h 且支援 `kid` 輪替、驗 `iss`/`aud`/`exp` + 簽章、時鐘容忍 ±30s。
3. 本地 user 表**只存 `sub`(unique、不可變)+ 本地角色 + 業務欄位**;無 email/姓名/密碼欄。
4. 首登自動建零角色 user → 業務功能回 **403 待開通頁**(文案要指引找本 App 管理員開通),deny-by-default。
5. 登出導 IdP logout 端點(single logout),不得只清本地 session。
6. `401` = token 無效;`403` = 已認證但未開通/無權限——不得混用。
7. 端點從 Discovery(`/.well-known/openid-configuration`)動態取,**不寫死路徑**。
8. client secret 走 `.env`(不進 git),`.env.example` 只列變數名;測試一律假帳號,**嚴禁真實個資**。

---

## 本 repo 紅線

- 🔴 不自建帳號系統、不存密碼、不自簽 JWT。
- 🔴 上傳的檔案(程式碼包)視同敏感內容:必須驗 MIME 與 magic bytes,不得把檔案內容或 token 寫進 log。
- 🔴 業務庫不落地個資;log 為 stdout JSON 單行,不含 JWT/密碼/個資。
- 🔴 容器 non-root、production compose 不用 `latest` tag、DB/Redis 不對主機發布 port。
- 🔴 契約文件有疑義時以 cats-portal `DOCS/` 最新版為準;本檔與其衝突,以 cats-portal 為準並回頭修本檔。

平台通用規約(容器化、HTTP 介面、log、命名、發布、跨服務通訊)見 `platform-charter` skill,
動任何骨架/CI/auth/DB 程式碼前先讀。

> 待確認:本 repo 名 `upload-program-`(含尾端連字號)不符平台命名規約的 `service-<name>` 格式。
> 若要對齊,需 Platform 團隊決定改名(例:`service-upload`);在改名前 image / database / 路徑前綴命名先向 Platform 確認,勿自選。
