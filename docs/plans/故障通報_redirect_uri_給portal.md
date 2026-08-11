# 故障通報:upload-program 登入中斷(Keycloak 回 `invalid redirect_uri`)

**建立日期:** 2026-08-11 10:00
**最後更新:** 2026-08-11 22:45
**版本:** v1.1
**受文方:** cats-portal(Platform)
**發文方:** upload-program(負責人 Benny)
**嚴重度:** 🔴 服務不可用 —— **全體使用者無法登入**

---

## 1. 症狀

瀏覽器被導向 authorize 端點後,Keycloak 直接回錯誤頁,不進入登入畫面:

```
出錯啦!
無效參數: redirect_uri
```

DevTools:

```
GET https://catsapp.sporton.com.tw/auth/realms/sporton/protocol/openid-connect/auth?...&code_challenge=...
→ 400 (Bad Request)
```

**影響範圍:** `/upload/` 的所有使用者,包含平台管理員本人。
沒有任何人能登入,等同服務不可用(靜態頁與 `/health` 仍正常)。

---

## 2. 我方送出的 `redirect_uri`

```
https://catsapp.sporton.com.tw/upload/oidc/callback/
```

🔴 **結尾有斜線。**

此值由 `PUBLIC_BASE_URL + API_PREFIX + /oidc/callback/` 推導(`app/config.py`),
**我方近期未曾變更**:`PUBLIC_BASE_URL`、`API_PREFIX`、
`OIDC_REDIRECT_URI_OVERRIDE` 三個變數自上線以來未動過,
最近三個版本的異動也都在後台顯示與上傳介面,未觸及登入流程。

---

## 3. 時間線(兩次症狀,疑為同一根因)

| 時間 | 症狀 | 我方 trace_id | 研判 |
|---|---|---|---|
| 稍早 | 登入走完 IdP 後,**在 callback 端 401**「授權碼交換失敗」 | `5983122e-8d48-4c62-9ab6-e2804188b529` | authorize 過得去,**換 token 時被拒** |
| 目前 | **authorize 當下即 400** `invalid redirect_uri` | — | 連 authorize 都不接受了 |

兩者都指向 `redirect_uri` 比對失敗。差別在於第一次時 client 端仍接受該 URI 進入授權流程、
到 token 端點才不符,現在則在第一關就拒絕。

⚠️ 同一時段(2026-08-10 晚間)貴方公告 gateway 變更曾造成兩次全平台停機,
`/upload/` 在受影響清單內。我方無法判斷該次作業是否連帶調整了 Keycloak client 設定
——**僅陳述時間點吻合,不預設原因。**

---

## 3.1 🎯 新增證據:client 憑證確定沒問題(2026-08-11 22:31)

我方於 22:30 上線 v0.2.1,該版開始記錄 IdP 回傳的 `error` 碼。上線一分鐘後取得:

```json
{"message": "續期失敗", "status": 400, "oidc_error": "invalid_grant",
 "trace_id": "2efd2537-ec6d-4f65-8199-46703c4661cd"}
```

這是**續期**(refresh_token grant)失敗,本身是預期行為(舊 session 的 refresh token 已失效)。
但它對本案有決定性意義:

> confidential client 打 token 端點時,IdP **先驗 client 憑證**。
> client_id / secret 有誤會回 **`invalid_client`**;我方拿到的是 **`invalid_grant`**,
> 代表 **client 憑證通過驗證**,被拒的只是 grant 本身。

🎯 **因此 `client_id` 與 `client_secret` 確定是對的**,§4 第 3 項可以排除。
**請集中查 Valid Redirect URIs 清單。**

補充:同一時間 `/ready` 回 200,代表我方容器**取得 JWKS 正常** ——
與 IdP 之間的網路、DNS、憑證鏈都通。本案純粹是 client 設定的比對問題。

---

## 4. 請協助確認(§3.1 之後縮減為兩項)

| # | 事項 |
|---|---|
| 1 | 🔴 **主要嫌疑**:realm `sporton` 的 client **`upload-program`**,其 **Valid Redirect URIs** 是否**逐字**包含 §2 那一串?特別是**結尾的斜線** —— 若被登記成無斜線版本,即會產生目前的結果 |
| 2 | 2026-08-10 晚間的 gateway 變更,是否連帶動到此 client 的 redirect URI / post-logout 設定? |
| ~~3~~ | ~~client secret 是否曾經輪替~~ —— **已由 §3.1 排除**,不必查。若日後仍需交付 secret,請依契約 §5.2 走安全管道;⚠️ 不要回覆在本文件的往返或任何聊天訊息裡(它會進 git) |

---

## 5. 我方已完成的排除

- 程式碼未變更登入流程(git 歷史可查),`redirect_uri` 的推導邏輯有測試釘住;
- 錯誤發生在 **IdP 端**,我方服務未收到該請求,故本地 log 無對應紀錄;
- ~~我方僅記錄 status code、未記錄 `error` 欄位~~ **已於 2026-08-11 補上(v0.2.1)**,
  §3.1 的證據即由此而來。只記 `error`(列舉值),不記 `error_description`(自由字串),
  以免 IdP 的描述文字夾帶個資。

---

## 6. 順帶回報:front-channel logout 端點已就緒

契約 §10 的 SLO 義務,我方端點已實作完成並有自動化測試:

```
https://catsapp.sporton.com.tw/upload/oidc/frontchannel-logout
```

規格:GET / 免認證 / 冪等 / 只清本服務 cookie / 回 **204**。
我方 session 為 HttpOnly cookie 形態,故**不主張任何豁免**。

貴方登錄 `frontchannel.logout.url` 後,我方可配合重跑契約 §10 的 A→B 復現步驟,
並歡迎以 live 回讀驗證(比照貴方對 SENSE 承諾的第二層守門)。

**此項不急,請優先處理 §4。**

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.1 | 2026-08-11 22:45 | Claude(v0.2.1 上線後回寫) | 新增 §3.1:v0.2.1 開始記錄 IdP 的 `error` 碼,取得 `invalid_grant`(而非 `invalid_client`)——**證明 client 憑證通過驗證**,secret 一項可排除,請集中查 Valid Redirect URIs;並補 `/ready` 200 證明 JWKS 取得正常(網路 / DNS / 憑證鏈皆通)。§4 由三項縮為兩項;§5 的自承缺口改為已修 |
| v1.0 | 2026-08-11 10:00 | Claude(Benny 授權) | 初版:登入中斷故障通報。列出我方送出的 `redirect_uri`(結尾有斜線)、兩次症狀的時間線與 trace_id、三項請確認事項(逐字比對 / 8-10 gateway 變更是否連帶、secret 是否輪替);陳述時間吻合但**不預設原因**;附我方已完成的排除與自承的診斷缺口;順帶回報 front-channel logout 端點已就緒待登錄 |
