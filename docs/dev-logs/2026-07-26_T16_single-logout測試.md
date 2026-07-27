# T16 Single logout 與 session cookie 測試(清償欠項 A6)

**建立日期:** 2026-07-26 07:03
**最後更新:** 2026-07-26 07:03
**版本:** v1.0
**對應任務:** T16(M3),清償欠項 A6

---

## 計畫(動工前成文,憲法第二條 2)

### 目標

SSO 契約 §4.5 明訂「登出必須導 IdP 的 logout 端點做 single logout,**不得只清本地 session
假裝登出**」。這條目前**只有程式碼、沒有任何證據**——`app/routers/auth.py` 的 logout 與
`app/session.py` 的 cookie 屬性,一行都沒被測到(欠項 A6)。

本任務補上自動化測試,把契約義務釘住,讓日後有人「順手簡化成只清 cookie」時會被測試擋下。

### 為什麼這不是 TDD 的紅→綠

程式碼先於測試存在(源於 T00 記錄的違規),因此**不會有紅燈轉綠燈的過程**。
與 T50 的補測相同性質:目的是把既有行為釘住,並藉此檢查有無隱藏缺陷。
若測出缺陷則修程式,並在此記錄根因。

### 影響範圍

| 類型 | 檔案 |
|---|---|
| 新增 | `tests/test_logout_session.py` |
| 可能修改 | `app/routers/auth.py`、`app/session.py`、`app/oidc.py`(若測出缺陷) |
| 修改 | `docs/任務表.md`(T16、A6) |
| 不動 | 其他 `app/` 模組、容器與部署設定 |

**對現有資料的影響:🟢 不動資料。**

### 驗收標準

契約與規約層面必須被釘住的行為:

1. **§4.5 single logout**:`/auth/logout` 回 302 且 **Location 指向 IdP 的 `end_session_endpoint`**
   ——不是站內路徑,不是只清 cookie
2. 登出帶 **`id_token_hint`**(有 session 時);無 id_token 時退而帶 `client_id`
3. 登出帶 **`post_logout_redirect_uri`**,值為本服務的對外網址(含子路徑前綴)
4. 登出**同時清除** session cookie 與登入往返用的 login cookie
5. IdP 連不上時,登出仍導回本站而非 500(否則使用者永遠登不出去)
6. **cookie 屬性**:`HttpOnly`、`SameSite=Lax`、`Path` 綁子路徑前綴、
   `Secure` 依設定(production 必為 true)
7. **開放轉址防護**:`?next=` 只接受站內相對路徑,`//evil` 與絕對網址一律導回 `/`
8. **cookie 竄改**即失效(簽章驗證有效)
9. callback 的 **state 不符 → 401**、缺 code → 400、login cookie 遺失 → 401
10. session cookie 可作為憑證通行(不必帶 `Authorization`)

### 回滾方式

純新增測試檔;若有程式修正一併以 `git revert` 回復。不動資料庫、不動部署。

---

## 結果(完工後補記)

### 做了什麼

新增 `tests/test_logout_session.py`,**22 條**測試。測試總數 81 → **103**。

關鍵設計:用**真的 `OidcClient`**(子類化後只覆寫會打網路的 `load_discovery` /
`exchange_code` / `verify`),所以 `logout_url()`、`authorization_url()` 的**組網址邏輯是真的在受檢**
——不是對假物件斷言,那種測試只會證明假物件寫對了。

| 分組 | 條數 | 釘住的行為 |
|---|---|---|
| §4.5 single logout | 6 | Location 必須指向 **IdP 的 `end_session_endpoint`**;帶 `id_token_hint`(無則 `client_id`);帶 `post_logout_redirect_uri`(含子路徑前綴);同時清兩個 cookie;**IdP 掛掉仍能登出而非 500**;IdP 無登出端點時導回本站 |
| cookie 屬性 | 4 | `HttpOnly`、`SameSite=Lax`、`Path` 綁前綴;production 設定下必為 `Secure`;**竄改即失效**;未竄改可作為憑證通行 |
| 登入往返 | 10 | PKCE `S256` + `nonce` + `response_type=code`;`redirect_uri` 含前綴;**state 不符 → 401**;缺 login cookie → 401;缺 code → 400;成功後建 session 並導回站內;**PKCE verifier 確實被帶進授權碼交換** |
| 開放轉址 | (含上) | `?next=` 只收站內相對路徑;`//evil`、絕對網址、`javascript:` 一律導回 `/` |
| refresh | 1 | 無 session → 401 |

### 測試結果

```
$ pytest tests/test_logout_session.py -q
......................                                                   [100%]
22 passed in 1.65s

$ pytest -q          # 全套
103 passed in 8.89s

$ ruff check .
All checks passed!
```

**沒有測出缺陷——22 條全部一次通過。** 實作本來就是對的,只是先前無人知曉。
這與 T50 的 `test_token_verify.py` 情況相同:補測的價值不在於「一定會抓到 bug」,
而在於**把契約義務從「程式碼看起來有做」變成「有證據證明有做」**,並讓日後的簡化改動撞牆。

### 為什麼這樣做(決策理由與捨棄的替代方案)

| 決策 | 理由 | 捨棄的方案 |
|---|---|---|
| 子類化真的 `OidcClient` 而非另寫 Fake | 要驗的正是 `logout_url()` 的組法;用 Fake 等於驗自己寫的假物件 | 像其他測試檔那樣用 `FakeOidc` |
| 測 Location **指向 IdP 網域** | 契約 §4.5 的重點就是「不能只清本地」;只斷言「有 302」的話,導去站內首頁也會過 | 只驗狀態碼 302 |
| 特地測「IdP 掛掉仍能登出」 | 這是我實作時刻意寫的 try/except,但沒有測試就等於沒人知道它存在,日後重構容易被拿掉 | 不測失敗路徑 |
| cookie 值用 helper `_bake()` 產生 | cookie path 綁 `/upload/`,而測試直接打 `/v1/*`(gateway 剝前綴後的路徑),httpx 不會自動帶;必須顯式塞 | 靠 client 的 cookie jar 自動帶(會靜默不帶,測試變成假通過) |
| 開放轉址用參數化涵蓋 4 種攻擊形態 | `//evil` 這種 protocol-relative 最容易漏 | 只測一種 |

### 對現有資料的實際影響

**無。** 與計畫一致,🟢 不動資料。

### 遺留問題與後續建議

| # | 問題 | 建議 |
|---|---|---|
| 1 | **`/auth/logout` 是 GET,屬狀態變更操作** | `SameSite=Lax` 讓子資源請求(`<img>`)帶不到 cookie,但使用者被誘導點擊的頂層導覽仍會觸發登出。危害僅止於「被登出」的騷擾等級,不影響資料。**建議 T45 做前端時把登出鍵改成 POST 表單**;若維持 GET 需為明示的決定 |
| 2 | callback 失敗(state 不符等)時未清除 login cookie | 會留下一個 10 分鐘後自然過期的無效 cookie,無實害;可在後續整理 |
| 3 | 未驗證 id_token 的 `sub` 與 access token 的 `sub` 一致 | 同一次交換取得的 token 理應一致;若要更嚴謹可加一行比對 |
| 4 | 大檔上傳的記憶體行為仍未實測 | 併入 T27 或 T29 冒煙 |

### 中斷點

**T16 完成,欠項 A6 結案。** 至此除 A2(已發生的違規,永久紀錄)外**所有欠項皆已清償**,
M1–M3、M5 的既有項目全部 ✅ 且有測試背書。
下一步建議:進 M4 的新功能(T34 轉移擁有權 → T35 最新版捷徑 → T36 標籤),
這些是 2026-07-26 定案後新增的 P1 需求,且可以**真正照 TDD 走紅→綠**。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-07-26 07:03 | Claude(Benny 授權) | 初版:計畫段(動工前成文)+ 結果段;22 條測試釘住 single logout / cookie 屬性 / 登入往返 / 開放轉址;未測出缺陷;四項遺留問題(含 logout 用 GET 的 CSRF 觀察) |
