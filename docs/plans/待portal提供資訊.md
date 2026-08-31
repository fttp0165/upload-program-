# 待 portal 提供的資訊(upload-program 接入用)

**專案:** upload-program
**發文專案:** upload-program
**受文專案:** cats-portal
**發文時間:** 2026-07-28 04:05
**建立日期:** 2026-07-28 04:05
**最後更新:** 2026-08-12 17:20
**版本:** v1.4
**用途:** 交付清單 —— portal 把值填進「回填」欄交還即可,我方據以完成部署
**相關:** [SSO接入申請_給portal.md](SSO接入申請_給portal.md)(申請內容與合規聲明)、
[SSO接入計畫.md](SSO接入計畫.md)(我方內部施工計畫)

> 這份是**表單**,不是說明書。每一列都是一個我方無法自己決定、
> 必須由平台方指定的值。**全部到齊前無法部署**;缺哪一項會卡住什麼,寫在「卡住什麼」欄。
>
> 標 🔴 的項目缺了會**直接鎖死或連不上**,不是體驗差而已。

---

## A. SSO(契約 §5)

| # | 項目 | 我方建議值 | **回填** | 卡住什麼 |
|---|---|---|---|---|
| A1 | `client_id` | `upload-program` | ✅ `upload-program`(施工單 §0) | — |
| A2 | 🔴 `client_secret` | —(由貴方產生) | 🔵 **已產出**(2026-07-29 腳本執行完畢,`idp/.env.keycloak.upload-program`,600 權限);⏳ 待安全管道交付到我方手上 | 無法換 token |
| A3 | client 型別 | **confidential** | ✅ confidential + PKCE S256(施工單 §0) | — |
| A4 | 🔴 redirect URI(需**含子路徑前綴**) | `https://catsapp.sporton.com.tw/«PREFIX»/oidc/callback/` | ✅ `https://catsapp.sporton.com.tw/upload/oidc/callback/`(施工單 §0、§9 登記表) | — |
| A5 | 是否核發 refresh token | **需要** | ✅ 已認可我方伺服器端 refresh 實作為「契約建議的正解」(施工單 §4.3) | — |
| A6 | claims / mapper | `email`、`email_verified`、`preferred_username` / `name` | ⬜ client 腳本已備,細項未逐一列出——留待冒煙時核對 | 導航列顯示不出姓名 |
| A7 | Account Console 短網址 | `/account`(契約 §2.1) | ⬜ 未於本輪回覆中提及,沿用既有值 | 「帳號設定」連結失效 |

> **A6 補充:** 這些 claims **不落地**,只在記憶體中傳遞供顯示。
> 我方業務庫結構上沒有 email/姓名欄位,不援引 §6.1 的 PLM 快取例外。

---

## B. 路由與部署(接入指南)

| # | 項目 | 我方建議值 | **回填** | 卡住什麼 |
|---|---|---|---|---|
| B1 | 🔴 路徑前綴 `«PREFIX»` | `/upload/` | ✅ **定案 `/upload/`**(裁決函 §1;施工單 §1 誠實回報過與規約命名表的字面不一致,但建議並定案維持 `/upload/`) | — |
| B2 | cats-edge 別名 | `upload-program` | ✅ gateway 以此名解析上游(施工單 §3);我方已補上 `container_name: upload-program` 求字面一致(T54) | — |
| B3 | 🔴 `client_max_body_size` | **≥ 128 MB** | ✅ 128MB 已併入權威 gateway 設定(施工單 §0、§2.1) | — |
| B4 | 靜態檔 location 寫法 | 剝前綴:`proxy_pass http://upload-program:8080/static/;` | ✅ 已依此寫法併入設定(施工單 §2.1) | — |
| B5 | GHCR image 路徑 | `ghcr.io/fttp0165/upload-program` | ⬜ 未於本輪回覆中提及,沿用既有值 | 部署拉不到映像 |
| B6 | repo 是否改名 | 現為 `upload-program-`(**尾端多一個連字號**,不符 `service-<name>`) | ⚪ **明確答覆:非 portal 權責,轉交 Platform**(裁決函 §4、施工單 §6.1;施工單並給出規約推導的目標值 `service-upload-program`,決定權仍在 Platform) | 影響 B5 與既有連結 |

### B1 的相依範圍(為什麼它最該先給)

前綴一到手,以下**全部**同時定案,不需要再回頭問:

```
«PREFIX»  ──┬─▶ A4 redirect URI(含前綴)
            ├─▶ session cookie 的 Path=/«PREFIX»/   （契約 §4.10 同源義務）
            ├─▶ 頁面內所有連結與靜態檔路徑
            └─▶ gateway 的 location 區塊
```

我方程式**全部由設定值導出**,拿到前綴只需改一個環境變數,不改程式碼。

---

## C. 資料庫與物件儲存

| # | 項目 | 我方建議值 | **回填** | 卡住什麼 |
|---|---|---|---|---|
| C1 | PostgreSQL 資料庫名 / 使用者 | ~~`upload_db` / `upload_user`~~ → `upload_program_db` / `upload_program_user` | ✅ **不需 portal 回填,我方自行對齊平台規約命名表**(施工單 §6 給出目標命名,T54 已改;尚未上線,現在改不動任何資料) | — |
| C2 | 🔴 DB 密碼 | — | ⬜ **另行傳遞** | 服務啟動失敗(fail-fast) |
| C3 | MinIO bucket 名 | `upload-program` | ⬜ 未於本輪回覆中提及,沿用既有值 | 上傳失敗 |
| C4 | 🔴 MinIO access key / secret | — | ⬜ **另行傳遞** | 同上 |
| C5 | 可用磁碟空間(Q12) | — | ⬜ | 影響擴充級距(10 GB/專案)能開給幾個專案 |

---

## D. 🔴 第一個管理員的 `sub`(最容易漏掉,漏了系統會鎖死)

**這一項不是設定值,是一個必須在部署前完成的動作。**

契約 §4.3 要求首登一律建**零角色**帳號,業務功能全部 403 待開通。
但**開通別人的人自己也要先登入** —— 於是第一個使用者登入後,
沒有任何人有權開通他,系統實質鎖死。

我方以 `BOOTSTRAP_ADMIN_SUBS` 解決(清單中的 `sub` 首登即為 active + admin)。
所以部署前需要:

| # | 步驟 | 由誰 | **完成** |
|---|---|---|---|
| v1.4 | 2026-08-12 17:20 | Claude(T93 第九條) | 依憲法第九條補上抬頭四欄(專案 / 發文專案 / 受文專案 / 發文時間);內容未變動 |
| D1 | 指定第一個管理員人選(預設 Benny) | 我方 | ⬜ |
| D2 | 該人到 `https://catsapp.sporton.com.tw/account` 登入一次 | 該人 | ⬜ |
| D3 | 取得其 `sub`(UUID) | **該人自助**:登入 upload-program 後停在 `/«PREFIX»/pending`,頁面上直接顯示自己的 `sub`(2026-07-28 起);取不到時才請 portal 從 Keycloak 後台查 | ⬜ **回填:** |
| D4 | 填入部署環境的 `BOOTSTRAP_ADMIN_SUBS` | 我方 | ⬜ |

> `sub` **不是 secret**(只是一個 UUID),可直接寫在回填欄;但仍走環境變數,不寫死在程式裡。
>
> **D3 已改為自助,portal 通常不需要為此做任何事。** 我方的待開通頁會把使用者自己的
> `sub` 顯示出來——這是「業務庫只存 sub」的必然配套:不顯示的話,使用者與管理員之間
> 沒有任何可以對照的識別。順序上因此可以「先部署、後拿 sub」,D2–D4 不再卡住 B 段的部署。

---

## E. 需要 portal 裁決的一項

| # | 事項 | 我方做法 | **裁決** |
|---|---|---|---|
| E1 | 契約 §7 冒煙第 1 項「未登入 → 302」 | **深層頁 302、首頁留落地頁**(理由見申請書 §4) | ✅ **核准**(裁決函 §2),但**明確劃線**:核准範圍僅限首頁本身,任何深於此的路徑一律照契約字面 302,不因本次核准產生「App 可自行決定哪些頁面免登入」的模糊空間。我方原本的分層設計本就如此,不需調整 |

---

## G. 施工單新增事項(portal 主動補充,非我方原始表單項目)

portal 的施工單額外交付了以下我方原本沒有問、但接入時會踩到的資訊,一併記錄:

| # | 項目 | 值 |
|---|---|---|
| G1 | Discovery / JWKS / Issuer 端點 | `https://catsapp.sporton.com.tw/auth/realms/sporton/...`(已與我方 `.env.example` 的 `OIDC_ISSUER` 一致) |
| G2 | `aud` 即 `client_id` | portal 未設自訂 audience mapper,我方原有驗證邏輯不需調整 |
| G3 | Token 壽命 | access 300s / SSO 閒置 1800s / SSO 最長 36000s / auth code 60s / 時鐘容忍 ±30s(由我方函式庫設定,IdP 無此開關)—— 皆與我方現況一致 |
| G4 | 容器重建後須通知 portal reload gateway | ⚠️ **新的維運義務**——nginx 只在啟動/reload 當下解析一次上游 IP,換版後 IP 變、gateway 仍指向舊 IP,症狀是「健康檢查全綠但真實使用者拿到 502」。已記入部署 runbook(T27)待辦 |
| G5 | 安全標頭分工 | `X-Content-Type-Options`/`X-Frame-Options` 由 gateway 送;**我方不得自行送 `X-Frame-Options`**(語意不支援多值合併,行為未定義)——T54 已拿掉 |
| G6 | 🔴 全平台統一 **PG15** | 施工單 §3 範例明載;我方 compose 原為 `postgres:16.4`,T54 核對時漏抓,2026-07-29 補正為 `15.8`(尚未上線,不涉資料遷移) |
| G7 | 🔴 施工順序(v1.2 §3.0) | **我方容器先上線 → `getent` 確認解析 → portal 解除 `/upload/` 註解 + 低峰 reload**——nginx 對不存在的上游是 `[emerg]` 整份載入失敗,順序弄反 = 全平台中斷;首次上線八步見 [runbook §A0](../runbook_換版與備份還原.md) |

我方回覆施工單 §9 五項的完整內容見 [回覆_portal施工單.md](回覆_portal施工單.md)。

---

## F. 交付後我方的動作

拿到 A–D 之後,我方會:

1. 填入部署環境的 `.env`(**secret 不進 git**,`.env.example` 只列變數名)
2. 部署並執行契約 §7 六項冒煙,留存證據
3. 回報結果,請 portal 在契約 §9 登記表加上一列

預估:**A–D 到齊後約 1 個工作天**。

---

## 附註

### 1. 為什麼需要 refresh token(A5)

契約 §3.3 將 access token 定為 300 秒。我方網頁是**伺服器端算繪、無前端 JS**,
沒有任何角色會主動呼叫 refresh —— 不核發 refresh token 的話,
使用者登入 5 分鐘後所有頁面都會退回「未登入」。

我方已實作伺服器端續期(向 IdP 走 refresh_token grant,**不自行延長**,
IdP 停用帳號時 refresh 失敗即登出,收權即時性不受影響)。
此議題可能同樣影響 compliance 與 sporton_core 的網頁部分,詳見申請書 §7.2。

### 2. 我方不需要的東西(免得貴方多做)

- ❌ S2S / client_credentials(目前無服務對服務需求)
- ❌ `groups` mapper(業務授權以本地角色為準,契約 §3.1 明載)
- ❌ 任何個資回寫機制(我方不落地 email/姓名)

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.3 | 2026-07-29 07:30 | Claude(Benny 授權) | A2 secret **已產出待交付**(portal 腳本執行完畢);G 段補 G6(全平台統一 PG15,T54 漏抓已補正)與 G7(施工順序反轉);依施工單 v1.1 移除所有「卡 Keycloak 部署」的記載 |
| v1.2 | 2026-07-29 03:20 | Claude(Benny 授權) | **portal 裁決函與施工單回覆,A/B/E 段大部分項目已回填**:路徑前綴 `/upload/` 定案、`client_max_body_size` 128MB 與剝前綴靜態檔寫法已併入 gateway 設定、redirect URI 確認、refresh token 實作獲認可、E1 核准但明確劃線(僅限首頁);C1 DB 命名改為我方自行對齊規約(不需 portal 回填);新增 **G 段**記錄施工單主動補充的維運義務(容器重建須通知 reload、禁止自送 X-Frame-Options) |
| v1.1 | 2026-07-28 06:40 | Claude(Benny 授權) | **D3 改為自助**:我方的待開通頁(T45)會顯示使用者自己的 `sub`,portal 通常不需為此查 Keycloak 後台;連帶讓 D2–D4 不再卡住 B 段部署(可先部署、後拿 sub) |
| v1.0 | 2026-07-28 04:05 | Claude(Benny 授權) | 初版:A SSO 七項、B 路由與部署六項(含前綴的相依範圍說明)、C 資料庫與物件儲存五項、**D 第一個管理員的 sub 四步驟**(漏了系統會鎖死)、E 一項待裁決、F 交付後動作與時程;附註說明為何需要 refresh token、以及我方**不**需要的三項 |
