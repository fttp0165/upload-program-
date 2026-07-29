# upload-program SSO 接入計畫

**建立日期:** 2026-07-28 02:19
**最後更新:** 2026-07-28 06:40
**版本:** v1.2
**對應契約:** `cats-portal/DOCS/帳號系統接入契約_SSO.md` **v1.7**(2026-07-28)
**相關任務:** T26、T28、T29、T45 ✅、T51、T52 ✅、T53
**狀態:** ✅ **S0(可自主部分)已全數完成**;等待平台方配發 client 與路徑前綴

> 依開發憲法第二條 3:接 SSO 是跨多任務的大工程,先立獨立計畫文件,經確認後才動工。
> 契約權威在 cats-portal,本檔**只引用、不複製維護**;本檔負責的是
> 「**upload-program 這一側要做什麼、由誰做、怎麼驗**」。

---

## 1. 目的與範圍

讓 upload-program 成為 realm `sporton` 的一個 OIDC client,使用者以公司帳號登入,
帳號/密碼/2FA 全部由 IdP 保管,本服務只認 `sub` 並自行管理業務角色。

**範圍內:** OIDC 登入/登出、token 驗證、首登建號、待開通、同源環境的額外義務、
gateway 路由、冒煙驗收、契約 §9 登記。

**範圍外(明確不做):** 自建註冊/改密碼/忘記密碼頁(契約 §4.8 禁止)、
S2S client_credentials(目前無需求)、把 email/姓名寫進業務庫(§4.2 紅線;
PLM 的 §6.1 快取例外**本專案不繼承**)。

---

## 2. 現況:對照契約 v1.7 的逐條結果

程式面大部分已完成(M3 五個任務),此處只列**對照結果**,不重複設計細節。

### 2.1 已符合(有測試背書)

| 契約條款 | 落實處 | 證據 |
|---|---|---|
| §4.1 Auth Code + PKCE、禁 implicit | `app/routers/auth.py` | `test_auth.py` |
| §3.2 只接受 RS256;`alg=none`/HS256 拒 | `app/oidc.py` | `test_token_verify.py`(15 條,真 RSA) |
| §3.2 JWKS 快取 1h、支援 kid 輪替 | `app/oidc.py` | 同上 |
| §3.2 驗 `iss`/`aud`/`exp`、±30s | `app/oidc.py` | 同上 |
| §3.2 驗失敗回 **401**(不與 403 混用) | `app/problems.py` | `test_auth.py` |
| §4.2 業務庫**只存 `sub`** | `app/models.py` | 結構上無 email/姓名/密碼欄 |
| §4.3 首登建零角色 → **403 待開通** | `app/security.py` | `test_auth.py` |
| §4.4 deny-by-default;派角色在自己後台 | `app/routers/admin.py` | 權限矩陣測試 |
| §4.5 single logout(導 IdP `end_session`) | `app/routers/auth.py` | `test_logout_session.py`(22 條) |
| §4.6 secret 走 env、不進 git | `.env.example` 只列變數名 | CI 的 git 紅線檢查 |
| §4.9 測試一律假帳號 | `tests/conftest.py` | CI |
| §3.3 SSO session 最長 36000s | `SESSION_MAX_AGE_SECONDS` | `test_config_and_logging.py` |

### 2.2 🔴 §4.10 同源環境的額外義務(v1.5 新增,對本專案有強制力)

因 MIS 只核可 `catsapp.sporton.com.tw` 一個名字,**IdP 與各 App 共用 origin**——
瀏覽器的同源政策不再替我們隔離。**我們出現 XSS 就等於全平台帳號淪陷。**

| 義務 | 現況 | 落實處 |
|---|---|---|
| session cookie `Path=/«PREFIX»/` + HttpOnly + SameSite=Lax | ✅ | `app/session.py`;`cookie_path` 由 `api_prefix` 導出 |
| 嚴格 CSP(至少 `default-src 'self'`,禁 unsafe-inline) | ✅ | `app/middleware.py`(T40 導入) |
| 前端 base path 設為自己的子路徑 | ✅ | `app/web_urls.py` 的 `web_url()`(T40) |
| 使用者輸入的輸出一律消毒 | ✅ | Jinja2 autoescape;模板禁用 `\|safe` |
| **不得**把 token 存進 `localStorage` | ✅ | 全站零 JS;token 只在 HttpOnly cookie |

> ⚠️ **最後一項在 T44 之後會變成活的約束**:上傳介面要導入外部 JS。
> T51 會把這五項逐條寫成測試,讓 T44 不可能無意間違反。

### 2.3 缺口(T51、T53 處理)

| # | 缺口 | 處置 |
|---|---|---|
| 1 | `.env.example` 的 `OIDC_ISSUER` 仍是舊的獨立網域形式 | ✅ T51 已改為 D2″ 的子路徑形式 |
| 2 | 導航列沒有指向 Account Console 的「帳號設定」連結(§2.1、§4.8) | ✅ T51 |
| 3 | §4.10 五項義務**沒有逐條測試**,只是「剛好有做」 | ✅ T51 已寫成測試 |
| 4 | 未登入開深層頁不 302(§7 冒煙第 1 項) | ✅ T53(深層頁 302、首頁留落地頁) |

**四項缺口已全數關閉(2026-07-28)。** 交付清單見 [待portal提供資訊.md](待portal提供資訊.md)。

### 2.4 已修正的缺陷

**T52 網頁 session 自動續期** ✅(2026-07-28)。
契約 §3.3 把 access token 壓到 **300 秒**,但本服務網頁零 JS,沒有人會打
`POST /auth/refresh`,session cookie 卻活 10 小時 → **登入 5 分鐘後全站靜默退回未登入**。
已改為伺服器端向 IdP 換發。詳見 `dev-logs/2026-07-28_T52_網頁session自動續期.md`。

---

## 3. 責任分界

| | IdP(portal 維護) | upload-program |
|---|---|---|
| 帳號/密碼/2FA、登入頁、改密碼 | ✅ 唯一保存處 | ❌ 禁自建(§4.8) |
| 簽發 token | ✅ | ❌ 禁自簽 |
| 驗 token | — | ✅ 每個 API 必驗 |
| 業務角色、開通 | ❌ | ✅ 自己後台(`/v1/admin/users`) |
| 個資 email/姓名 | ✅ 即時提供 | ❌ 不落地,只在記憶體傳遞供顯示 |
| 建 client、配發 secret | ✅ §5 流程 | 申請方 |
| gateway 路由 | ✅ cats-portal PR | 提出需求 |

---

## 4. 前置條件:需要平台方提供的清單

以下**全部**到齊才能進入 S2。此節可直接作為向 portal 提出的申請內容。

### 4.1 SSO client(契約 §5)

| 項目 | 我們的申請值 |
|---|---|
| `client_id` | `upload-program` |
| 型別 | **confidential**(本服務為伺服器端算繪,能保管 secret) |
| redirect URI | `https://catsapp.sporton.com.tw/«PREFIX»/oidc/callback/` |
| 需要的 claims | `email`、`email_verified`、`preferred_username` / `name`(**顯示用,不落地**) |
| 不需要的 | `groups`(業務授權以本地角色為準,§3.1 明載);S2S / client_credentials |
| 需要 refresh token | **是**——網頁 session 自動續期靠它(T52) |

> ⚠️ **redirect URI 含子路徑前綴**:契約 §4.1 寫的是
> `https://<你的 hostname>/oidc/callback/`,但 D2″ 之後所有 App 都掛在子路徑下。
> 登記表裡 compliance 的值是 `https://catsapp.sporton.com.tw/compliance/oidc/callback/`,
> 證實子路徑形式才是正確的。本專案的前綴待分配,取得後才能定案此值。

### 4.2 路徑前綴與 gateway 路由(T28)

| 項目 | 需求 |
|---|---|
| 路徑前綴 `«PREFIX»` | 暫定 `/upload/`,**待分配** |
| `client_max_body_size` | **≥ 128 MB**(單檔上限 100 MB + 餘裕) |
| 靜態檔 location | 採**剝前綴**寫法:`proxy_pass http://upload-program:8080/static/;` |

### 4.3 🔴 第一個管理員的 `sub`(最容易漏掉的一項)

**這是接入時最容易卡住的地方,必須在計畫裡先講明白。**

契約 §4.3 要求首登一律建**零角色**帳號 → 業務功能全部 403 待開通。
但**開通的人也要先登入**——於是第一個人登入後,沒有任何人有權開通他,
系統陷入「雞生蛋」。

本服務的解法是 `BOOTSTRAP_ADMIN_SUBS`:設定裡列出的 `sub` 首登時自動
`active` + `admin`。所以接入前必須:

1. 指定人選(預設:Benny)先到 `https://catsapp.sporton.com.tw/account` 登入一次
2. 從 IdP 取得其 **`sub`(UUID)**
3. 填進部署環境的 `BOOTSTRAP_ADMIN_SUBS`
4. 該人首登 upload-program 即為管理員,之後由他開通其他人

> 這個值**不是 secret**(只是一個 UUID),但仍走 env 不寫死在程式裡。

> **✅ 計畫更新(2026-07-28 06:40,T45 完成後回寫)**
>
> 上面第 2 步原本沒有落點——「從 IdP 取得 sub」在實務上要嘛請平台方到 Keycloak
> 後台查、要嘛自己解 token,兩者都不是接入當下做得順手的事。
>
> **T45 的 `/pending` 頁把它變成自助:** 該人直接登入 upload-program(會卡在待開通),
> 頁面上就印著他自己的 `sub`,複製即可。所以第 2 步改為:
>
> > 2. 該人登入 upload-program → 停在 `/«PREFIX»/pending` → **複製頁面上顯示的 `sub`**
>
> 填進 `BOOTSTRAP_ADMIN_SUBS` 並重啟後,他再登入一次即成為管理員。
> 這也意味著**「先部署、後拿 sub」是可行的順序**——原計畫把它排成 S2 的前置條件,
> 現在它可以在 S2 之中完成,不再是卡住整條路的外部相依。

---

## 5. 接入階段與相依

```
S0 可先做(不需外部)          S1 申請          S2 設定部署        S3 驗收        S4 登記
┌──────────────────┐      ┌────────┐      ┌──────────┐    ┌────────┐   ┌────────┐
│ T51 契約對齊      │      │ T26    │      │ T28 路由  │    │ T29    │   │ 契約 §9│
│ T53 深層頁 302    │─────▶│ client │─────▶│ .env 填值 │───▶│ 冒煙   │──▶│ 登記表 │
│ T52 續期 ✅       │      │ 前綴   │      │ bootstrap│    │ 六項   │   │        │
└──────────────────┘      └────────┘      └──────────┘    └────────┘   └────────┘
     我們可自主              卡平台方          需兩邊配合        我們執行      portal
```

| 階段 | 任務 | 誰做 | 出場條件 |
|---|---|---|---|
| **S0** | T51 ✅、T53 ✅ | 我們 | ✅ **已達成**(2026-07-28):測試 267 passed;§4.10 五項逐條有測試 |
| **S1** | T26 | **平台方** | 拿到 client_id/secret、路徑前綴 |
| **S2** | T28 | 兩邊 | `/«PREFIX»/` 通;`client_max_body_size` 生效;bootstrap sub 已填(**可由 `/pending` 自助取得**,見 §4.3) |
| **S3** | T29 | 我們 | §7 六項全綠(見 §6) |
| **S4** | — | portal | 契約 §9 登記表加上一列 |

---

## 6. 冒煙清單(契約 §7)逐項執行方式與預期證據

| # | 契約要求 | 我們的執行方式 | 預期證據 |
|---|---|---|---|
| 1 | 未登入開 App → 302 到登入頁 | ⚠️ **本專案採「深層頁 302、首頁留落地頁」**(2026-07-28 Benny 裁示,T53):`/projects/*` 等未登入時 302 並帶 `next` 導回;首頁保留「請先登入」的落地說明頁 | 對 `/«PREFIX»/projects/x` 的 302 記錄;**此解讀需與 portal 確認** |
| 2 | 登入 → 回跳 callback → 本地 user 出現,**DB 查只有 sub** | 真實帳號登入一次,再 `SELECT * FROM users` | 查詢輸出:只有 `sub`,無 email/姓名/密碼欄 |
| 3 | 無角色 → 業務 API 一律 403 待開通;後台派角色 → 即時通行 | 用第二個測試帳號登入 → 打 `/v1/projects` → 403;bootstrap 管理員在 `/admin/users` 一鍵開通後再打 → 200(T45 起不需手打 API) | 兩次回應的 status 與 `type` |
| 4 | 正確 token → 200;竄改/過期/別人的 aud → **401** | 已有 15 條自動化測試(真 RSA);接入後對**真實 IdP** 再跑一次 | 四種情境的 status |
| 5 | 登出 → IdP session 銷毀 → 再開任一 App 都要重登 | 登出後開 portal-landing 或 compliance | 需重新登入 |
| 6 | repo grep:零 Authentik、零 HS256、`.env` 不在 git | CI 已有此檢查 | CI 的 SSO 紅線 job |

> 第 1 項是**唯一與契約字面有出入**的一項,理由與取捨見 T53 的開發日誌;
> 若 portal 不接受,改為「全部 302」的成本很低(一個分支 + 改四條測試)。

---

## 7. 風險與回滾

| 風險 | 影響 | 因應 |
|---|---|---|
| 🔴 **同源之下我們的 XSS = 全平台帳號淪陷** | 極高 | §4.10 五項義務 + T51 逐條測試化;所有模板 autoescape、禁 `\|safe`;CSP `default-src 'self'` |
| redirect URI 登記錯(漏前綴) | 登入直接失敗 | 前綴到手後**先確認再登記**;`OIDC_REDIRECT_URI_OVERRIDE` 可不改程式即時修正 |
| bootstrap sub 沒先拿到 | **沒有人能開通任何人**,系統形同鎖死 | 列為 S2 的出場條件(§4.3);T45 後可由指定人選登入後從 `/pending` 自助複製,不需平台方協助 |
| access token 300 秒造成頻繁登出 | 使用者體驗 | T52 已修;冒煙時停留 >5 分鐘再操作以實測 |
| IdP 短暫不可用 | 無法登入 | `/health` 不查 IdP(不會被誤判死亡);`/ready` 會反映 |
| 前綴日後變更 | 所有連結與 cookie path | 全部由 `api_prefix` 導出,改 env 即可;**但 redirect URI 要同步回 portal 更新**(§8) |

**回滾:** 本服務在接入前後都不改資料模型,回滾只是把 `.env` 的 OIDC 設定移除
→ 服務會 fail-fast 拒絕啟動(這是刻意的,不要讓它用預設值頂替)。
**沒有資料需要回滾。**

---

## 8. 對現有資料的影響

**🟢 不動資料。** 接入不新增/修改任何欄位——`users.sub` 從 M2 起就存在。

唯一的「資料」變化是**開始有真實使用者的 `sub` 進來**。
因為契約 v1.6 開放了自助註冊(限 `@sporton.com.tw`),
我們會看到**沒見過的 `sub` 首次登入**,他們就該卡在待開通頁(§4.8 明載此為正常)。

---

## 9. 待裁示 / 已裁示

| # | 事項 | 狀態 |
|---|---|---|
| 1 | 未登入行為:302 vs 落地頁 | ✅ **已裁示(2026-07-28 Benny)**:深層頁 302、首頁留落地頁;需與 portal 確認冒煙第 1 項的解讀 |
| 2 | 第一個管理員人選(bootstrap sub) | ⏳ 待指定,預設 Benny |
| 3 | 路徑前綴 | ⏳ 待平台分配(暫定 `/upload/`) |
| 4 | repo 名 `upload-program-` 尾端多一個連字號,不符 `service-<name>` 規約 | ⏳ 是否改名由 Platform 決定,本專案不自行更名 |

---

## 10. 完成定義(本計畫結案條件)

- [ ] S0 兩項任務完成,測試全綠
- [ ] 契約 §9 登記表出現 upload-program 一列
- [ ] §7 冒煙六項全數通過,證據留存於 `dev-logs/`
- [ ] §4.10 五項同源義務**各有一條測試**
- [ ] 真實 IdP 下完成一次「登入 → 建專案 → 上傳 → 發布 → 下載 → 登出」
- [ ] M3 出場條件(冒煙前 5 項)標記達成

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.2 | 2026-07-28 06:40 | Claude(Benny 授權) | 依第二條 5 回寫:§4.3 第 2 步「從 IdP 取得 sub」原本沒有落點,**T45 的 `/pending` 頁把它變成自助**——指定人選登入後直接複製頁面上的 sub,不需平台方到 Keycloak 後台查。連帶讓「先部署、後拿 sub」成為可行順序,bootstrap sub 不再是卡住整條路的外部相依;冒煙第 3 項改用 `/admin/users` 一鍵開通 |
| v1.1 | 2026-07-28 04:05 | Claude(Benny 授權) | S0 兩項(T51、T53)完成,四項缺口全數關閉;狀態改為「等待平台方配發 client 與路徑前綴」;掛上 [待portal提供資訊.md](待portal提供資訊.md) 交付清單 |
| v1.0 | 2026-07-28 02:19 | Claude(Benny 授權) | 初版:對照契約 v1.7 的現況(已符合 12 項 / §4.10 五項 / 缺口 4 項 / 已修缺陷 1 項)、責任分界、**需平台方提供的清單**(client 申請值、路徑前綴、🔴 第一個管理員的 sub)、五階段相依圖、冒煙清單逐項執行方式與預期證據、風險與回滾、待裁示事項、結案條件 |
