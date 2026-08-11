# 故障通報:upload-program 登入中斷(Keycloak 回 `invalid redirect_uri`)

**建立日期:** 2026-08-11 10:00
**最後更新:** 2026-08-11 10:00
**版本:** v1.0
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

## 4. 請協助確認(三項)

| # | 事項 |
|---|---|
| 1 | realm `sporton` 的 client **`upload-program`**,其 **Valid Redirect URIs** 是否**逐字**包含 §2 那一串?🔴 特別是**結尾的斜線** —— 若被登記成無斜線版本,即會產生目前的結果 |
| 2 | 2026-08-10 晚間的 gateway 變更,是否連帶動到此 client 的設定(redirect URI / post-logout / secret)? |
| 3 | client secret 是否曾經輪替?若有,請依契約 §5.2 走安全管道交付。⚠️ **不要回覆在本文件的往返或任何聊天訊息裡** —— 它會進 git |

---

## 5. 我方已完成的排除

- 程式碼未變更登入流程(git 歷史可查),`redirect_uri` 的推導邏輯有測試釘住;
- 錯誤發生在 **IdP 端**,我方服務未收到該請求,故本地 log 無對應紀錄;
- 我方 `app/oidc.py` 目前**僅記錄 token 端點回應的 status code、未記錄 `error` 欄位**,
  這是我方的診斷缺口,已列為待辦(錯誤回應 body 僅含 `error` / `error_description`,不含 token,
  補記錄不違反「log 不記完整 JWT」的紅線)。

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
| v1.0 | 2026-08-11 10:00 | Claude(Benny 授權) | 初版:登入中斷故障通報。列出我方送出的 `redirect_uri`(結尾有斜線)、兩次症狀的時間線與 trace_id、三項請確認事項(逐字比對 / 8-10 gateway 變更是否連帶、secret 是否輪替);陳述時間吻合但**不預設原因**;附我方已完成的排除與自承的診斷缺口;順帶回報 front-channel logout 端點已就緒待登錄 |
