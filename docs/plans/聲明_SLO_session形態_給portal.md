# 聲明:session 形態與 front-channel logout 端點(upload-program)

**建立日期:** 2026-07-31 22:50
**最後更新:** 2026-07-31 22:50
**版本:** v1.0
**發文方:** upload-program
**收文方:** cats-portal / Platform
**依據:** 帳號系統接入契約 **v2.0** §10.2(session 形態聲明為申請 client 的必填項;
既有接入者補聲明)、§10.3(front-channel logout 端點規格)、§10.6(本服務原列「⏸ 待其聲明」)

---

## 1. session 形態聲明(§10.1 三選一)

**本服務為「cookie / 伺服器 session」** —— 因此依 §10.1 屬 🔴 **MUST**,**不主張豁免**。

具體形態:登入成功後由本服務簽發自己的 session,存於 **HttpOnly cookie**
(`Secure`、`SameSite=Lax`、`Path=/upload`,壽命 10 小時)。
不使用 `localStorage`(§4.10 紅線),前端 JS 完全碰不到 token。

## 2. front-channel logout 端點(§10.3)

| 項目 | 我方實作 |
|---|---|
| 路徑 | `https://catsapp.sporton.com.tw/upload/oidc/frontchannel-logout` |
| 方法 | `GET` |
| 行為 | 清除本服務的 session cookie 與登入往返 cookie,回 **204**(無內容) |
| 認證 | **免認證** |
| 冪等 | 是——沒有 session 時同樣 204 |
| 副作用 | **只刪 cookie**:不建 session、不寫業務資料、不寫稽核(有測試釘住) |
| `iss` / `sid` | 帶與不帶都回 204;我方 session 無狀態,`sid` 忽略 |
| 刪除的 `Path` | `/upload`,**與種下時相同**(Path 不符瀏覽器不會刪,有測試釘住) |

上線版本:**v0.1.8**(本聲明發出時該版尚未發布;端點已合併於 main,任務 T74)。

## 3. 請 portal 執行

1. 於 `upload-program` client 註冊 `frontchannel.logout.url` =
   `https://catsapp.sporton.com.tw/upload/oidc/frontchannel-logout`,
   並開 `session.required=true`(依 §10.3 慣例)。
2. 回讀驗證後知會我方,雙方一起重跑契約 §10 緣起段的重現步驟
   (A 登出 → B 登入 → 進 `/upload/` 應為 B,不得仍是 A)。
3. §10.6 表格與 §9 登記表可將本服務由「⏸ 待其聲明」改為已完成。

> §10.3 明文時序不敏感:端點先上或註冊先設都不會壞,兩者到位才生效。
> 我方端點會**先**上線(隨 v0.1.8),屆時再請貴方註冊。

## 4. 我方理解的限制(不假裝它是保證)

- front-channel 是 **best-effort**:使用者直接關瀏覽器、iframe 未載入、分頁被凍結,
  都會靜默不生效。我方**不因為接了 SLO 而放長 session 壽命**(維持 10 小時)。
- 本機制成立的前提是 IdP 與各 App **同站**(§10.4)。若 Keycloak 日後遷出
  共用 hostname,本端點會**無聲失效**;屆時請依 §8 通知,我方配合改 back-channel(§10.5)。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-07-31 22:50 | Claude(Benny 授權) | 初版:聲明 session 形態為 cookie(不主張豁免)、front-channel logout 端點規格對照表、請 portal 註冊與重跑重現步驟、明列 best-effort 與同站前提兩項限制 |
