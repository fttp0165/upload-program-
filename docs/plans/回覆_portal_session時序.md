# 回覆 cats-portal:§6 時序更正與 §6.5 三問的答案

**建立日期:** 2026-08-12 11:40
**最後更新:** 2026-08-12 11:40
**版本:** v1.0
**回覆對象:** cats-portal《回覆 upload-program——登入中斷》**v2.0**(2026-08-12 10:30)
**From:** upload-program 維運
**To:** cats-portal 維運

> 先致意:v2.0 的查證品質很高——用實際流量而不是推測,並且**撤回自己 v1.0 的
> 錯誤判定、保留原文留軌跡**。我方照做:本函也會更正我方通報 v1.0 的一個錯
> (DevTools 截錄縮掉了 `redirect_uri`,§6.1 說得對)。
> 另請留意:貴方回的是我方通報 **v1.0**;我方 **v1.1**(08-11 22:45)已含
> §3.1 的 `invalid_grant` 證據——與貴方 §0 ③ 的結論相同,雙方獨立到達同一點。

---

## 1. 🔴 §6.2 的時序,有一個關鍵誤讀

> 「剛完成授權碼交換的那一刻,它拿一張已失效的 refresh token 去換。」

**不是 callback 之後——是同一秒裡「之前」的那個請求。**
把貴方 §6 的 gateway log 逐行對回我方程式路徑:

| gateway 行(08:18:21) | 我方程式路徑 |
|---|---|
| `GET /upload/` → **302** | 瀏覽器帶著**上一次(已過期)的 session cookie** 進站。`_session_token` 驗 access token 過期 → 拿 cookie 裡的舊 refresh token 問 IdP → **這就是那筆 `REFRESH_TOKEN_ERROR / Token is not active`** → 視為未登入 → 依 v0.2.1 的入口導流送去登入 |
| `GET /upload/auth/login` → 302 | 發起 Authorization Code + PKCE |
| authorize → **302** | IdP 有 SSO session,免畫面直接發 code |
| callback → 302 | 授權碼交換(`CODE_TO_TOKEN`);**新 session cookie 以同名同 path 覆蓋舊的** |
| `GET /upload/` → **200** | 新 cookie、新 access token,正常進站 |

整條鏈在同一秒內走完(因為 IdP 有 SSO session,三個 302 都是瞬時的),
所以「refresh 失敗」與「授權碼交換」落在同一秒——但**因果順序是前者在先**。

**我方程式中不存在「callback 之後主動 refresh」的路徑**:refresh 只發生在
`_session_token`(每個請求讀 cookie、發現 access 過期時),callback 流程
從頭到尾不會呼叫它。

由此,**9 筆 / 120 小時的 `Token is not active` 有一個無害的解釋**:
每一筆是「某個舊 session 過期後的第一次回訪」——舊 refresh token 已過
IdP 的 idle 期,失敗一次、使用者被送去(通常是無感的)重新登入、new cookie
覆蓋。這是設計行為:我方刻意**真的去問 IdP** 而不是自行延長,正是為了讓
「IdP 端收權後既發 token 最長只活 5 分鐘」成立。

## 2. §6.5 三問的正面回答

| # | 問 | 答 |
|---|---|---|
| 1 | callback 之後為何立刻 refresh? | **沒有這件事**——見 §1,事件屬於 authorize 之前的進站請求 |
| 2 | 新 refresh token 有沒有取代舊的? | **有。** 續期結果(含輪替後的 refresh token)由 `SessionRenewalMiddleware` 在回應階段寫回 cookie;登入時 `set_session` 同名同 path 覆蓋。此路徑有自動化測試(`test_session_refresh.py`) |
| 3 | `oidc.py` 補記 `error`? | **已於 v0.2.1 上線**(2026-08-11 22:30,貴方重現前約 10 小時)。我方通報 v1.1 §3.1 那筆 `{"oidc_error": "invalid_grant"}` 就是新欄位的產物——只記 `error`(列舉值),不記 `error_description`(自由字串可能夾帶個資) |

## 3. 🔴 真正還沒解釋的:08:18:44 的踢回

這一項我方**推不掉也推不出**:新 cookie 的 access token 有效 300 秒,
:44(+23s)理應仍有效。誠實列出三種可能與各自需要的資料:

| 可能 | 誰能提供什麼 |
|---|---|
| (a) :44 我方有嘗試續期且失敗 | **我方**:v0.2.1 log 已含 `oidc_error` + trace_id,將撈 08:18(UTC)窗口比對;**貴方**:Keycloak 事件在 :44 有無任何 token 端點事件? |
| (b) :44 cookie 有送達但**無法解析** | 🔴 這條路在 v0.2.1 **仍是靜默的**(壞簽章直接視為未登入、不留痕)。**已補**:下一版起「cookie 存在但讀不動」會記 warning(只記長度,不記內容——cookie 內含 token)。若 :44 是這型,目前雙方都看不見 |
| (c) :44 根本沒帶 cookie(另一分頁/手動登出/與其他 client 交錯) | **貴方**:該行的毫秒序、UA / 來源 IP 是否與 :21 同一 client?:44 之後流程是否又**靜默完成**(IdP session 仍在)?若是,使用者體感只是「閃一下」,與(a)合併即為完整圖像 |

我方待辦(將主動執行並回報):撈 08-12 08:18 UTC 窗口的容器 log;
之後與貴方核對毫秒序。

## 4. 貴方 §1.3 / §5 的執行:請照原計畫做

front-channel logout 的登記(旗標 + URL 成對)與根因無關、本來就要做——
**請執行**。套用後我方將依契約 §10 重跑 A→B 復現,並歡迎 live 回讀。
`client-upload-program.sh` 不輪替 secret 的確認收到,謝謝。

## 5. 我方通報 v1.0 的更正(對等回報)

- §1 的 DevTools 截錄確實把 `redirect_uri` 縮掉了——貴方 §6.1 用 gateway
  原始值補上了雙方都缺的那個觀測,我方通報的「主要嫌疑」判定**撤回**。
- 貴方「監控仍吃 301、不會替你們發現問題」的提醒收到;我方已有自己的
  換版冒煙腳本(逐項檢查對外路徑/版本哨兵/標頭),不依賴貴方告警。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-12 11:40 | Claude(Benny 授權) | 初版:§6.2 時序更正(refresh 事件屬 authorize 之前的進站請求,附逐行對照;我方無 callback 後 refresh 的路徑);§6.5 三問正面回答(1 無此事、2 有且有測試、3 已於 v0.2.1 上線);:44 踢回誠實列三種可能與雙方各需提供的資料,並自承(b)型在 v0.2.1 仍靜默、已補觀測;撤回我方 v1.0 的主要嫌疑判定 |
