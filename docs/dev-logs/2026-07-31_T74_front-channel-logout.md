# T74 單一登出:front-channel logout 端點(SSO 契約 v2.0 §10)

**建立日期:** 2026-07-31 22:20
**最後更新:** 2026-07-31 23:00
**版本:** v1.1
**對應任務:** T74(新開)——SSO 契約升版 **v2.0**(2026-07-31)新增第 10 章
「單一登出(SLO)義務」,§10.6 將 **upload-program 列為「⏸ 待其聲明」**

---

## 計畫(動工前成文,憲法第二條 2)

### 目標

依契約 §10.2 **聲明 session 形態**,並依 §10.3 提供 front-channel logout 端點。

**我方的 session 形態:cookie(HttpOnly、簽章、path 綁 `/upload`)**
——對照 §10.1 的三分類,這一類是 🔴 **MUST**,**沒有豁免空間**
(豁免只給「session 僅存在記憶體變數」的 App)。

要解決的實際問題(契約緣起段的情境):使用者 A 在入口登出、B 登入後,
**進我們的站仍然是 A、且擁有 A 的全部權限**。我方 session 是自己簽的 cookie,
IdP 結束 session 時我們不會知道——共用電腦上這是安全事故,不是體驗問題。

### 端點規格(照 §10.3 逐條)

| 項目 | 規格 |
|---|---|
| 路徑 | 對外 `https://catsapp.sporton.com.tw/upload/oidc/frontchannel-logout`(服務內部註冊為 `/oidc/frontchannel-logout`,gateway 已剝前綴) |
| 方法 | `GET` |
| 行為 | 清除本服務的 session cookie,回 **204** |
| 認證 | **免認證**——iframe 載入時不會帶我方 token;要求認證等於這個端點永遠不會生效 |
| 冪等 | 重複呼叫、本來就沒有 session,一律 204 |
| 副作用 | 🔴 **只准刪 cookie**:不建 session、不寫業務資料、不寫稽核。被任意第三方呼叫的最壞後果必須是「使用者被登出」 |
| 參數 | Keycloak 會帶 `iss` / `sid`;**帶與不帶都要能處理**——我方 session 無狀態,無從比對 `sid`,忽略即可 |

🔴 **刪 cookie 的 `Path` 必須與當初種下時相同**(`/upload`),否則瀏覽器
根本不會刪那個 cookie——這是「看起來有做、實際不做事」的典型死法,
正是契約緣起段全平台四支 client 踩的同一類坑。因此**要有測試釘住 Path**。

### 為什麼不需要 back-channel(§10.5)

契約 v2.0 明文不要求,兩個觸發條件(Keycloak 遷出共用 hostname、
出現「登出須即時且可稽核」的需求)目前都不成立。屆時由 portal 依 §8 通知。
**front-channel 是 best-effort**,不是保證(使用者直接關瀏覽器就不會生效),
所以我方 session 壽命維持現狀,不因為接了 SLO 就放長。

### 影響範圍

`app/routers/auth.py`(新增端點)、`tests/test_frontchannel_logout.py`(新)、
`docs/plans/聲明_SLO_session形態_給portal.md`(新;§10.2 的聲明文)、
CLAUDE.md 平台整合契約段(對齊版本 v1.7 → **v2.0**)、任務表。
**對現有資料的影響:🟢 純新增端點,不寫任何資料。**

### 驗收標準

紅→綠:
1. 無 cookie 呼叫 → **204**(不是 401、不是 302)。
2. 帶 session cookie 呼叫 → 204,且回應清除 **session cookie 與 login state cookie**。
3. 🔴 清除用的 `Set-Cookie` 其 `Path` **等於**設定的 `cookie_path`。
4. 帶 `?iss=...&sid=...` → 204(參數忽略,不報錯)。
5. 連呼兩次 → 兩次都 204(冪等)。
6. 🔴 **零副作用**:呼叫前後 `users` 筆數不變、稽核表筆數不變、
   回應不得種下任何 session cookie。
7. 全站既有測試維持全綠。

### 回滾方式

git revert(單一 commit;純新增端點,無資料異動)。端點移除後 portal 側的
註冊會 404——回滾前需知會 portal(§10.3 時序不敏感,但不該留死設定)。

---

## 結果(完工後補記)

### 做了什麼(異動檔案)

- `app/routers/auth.py`:新增 `GET /oidc/frontchannel-logout`(免認證、冪等、204,
  只呼叫 `codec.clear_session()` 與 `clear_login_state()`)。
  `iss` / `sid` **刻意不宣告參數**——不使用就不該出現在簽名裡,但帶了也不會壞。
- `tests/test_frontchannel_logout.py`(新):7 條。
- `docs/plans/聲明_SLO_session形態_給portal.md`(新):§10.2 的聲明文 + 請 portal 註冊。
- 任務表、CLAUDE.md 契約對齊版本 v1.7 → **v2.0**。

### 為什麼這樣做

- **不主張豁免**:契約 §10.1 的豁免只給「session 僅存在記憶體變數」的 App;
  我方是 HttpOnly cookie,結構上 front-channel 完全有效,沒有豁免空間。
  契約也明講「聲明錯誤視同未聲明」——這種地方沒有便宜可佔。
- **免認證是規格要求不是疏漏**:iframe 載入時不會帶我方 token,
  要求認證等於這個端點永遠不生效,而那正是「看起來有做、實際不做事」。
- **零副作用有測試**:端點免認證,任何人都能打。所以測試斷言呼叫前後
  使用者數與稽核筆數不變、回應不得種下 session cookie——
  最壞後果只能是「使用者被登出」。
- **Path 有測試**:刪除用的 `Set-Cookie` 若 Path 與種下時不同,瀏覽器根本不刪。
  契約緣起段全平台四支 client 就是死在這一類「設定存在但不生效」。

### 測試結果(紅→綠)

- 紅:端點不存在時 **7 failed**(全部 404)。
- 綠:`test_frontchannel_logout.py` **7 passed**;全套 **409 passed**
  (含 T75 的 3 條),`ruff check` 全過。

### 對現有資料的實際影響

🟢 純新增端點;不寫任何資料、無 migration、無 `.env` 變更。

### 遺留問題與後續建議

1. **端點要先上線,portal 才能註冊**(§10.3 時序不敏感,但死設定比沒設定更糟)
   ——隨 v0.1.8 出貨後,把聲明文送 portal 請他們註冊 `frontchannel.logout.url`。
2. 註冊完成後,**雙方一起重跑契約 §10 緣起段的重現步驟**(A 登出 → B 登入 →
   進 `/upload/` 應為 B),通過才算真的接上;T29 冒煙的「single logout」那一項
   可一併結案。
3. front-channel 是 best-effort;session 壽命維持 10 小時,不因為接了 SLO 而放長。
