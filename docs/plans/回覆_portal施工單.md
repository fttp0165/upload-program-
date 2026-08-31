# 回覆:portal SSO 接入施工單(upload-program)

**專案:** upload-program
**發文專案:** upload-program
**受文專案:** cats-portal
**發文時間:** 2026-07-29 03:20
**建立日期:** 2026-07-29 03:20
**最後更新:** 2026-08-12 17:20
**版本:** v1.2
**回覆對象:** portal 統一入口平台(cats-portal)
**對應文件:** `upload-program SSO 接入施工單(技術規格)` v1.0(2026-07-29)§9
**狀態:** 📤 回覆完畢;client 已建立,等 secret 安全管道交付後我方部署容器

> 這份文件回答施工單 §9「需要貴方回覆的事項」五項。逐項核對現有實作後,
> 兩項要修的地方已修完(T54,見 [開發日誌](../dev-logs/2026-07-29_T54_portal施工單技術對齊.md)),
> 一項確認即可,一項待與 Platform 確認,一項無異議。

---

## 逐項回覆

### 1. 🔴 路徑前綴:接受 `/upload/`

**接受貴方的建議,維持 `/upload/`,不要求改為 `/upload-program/`。**

理由與貴方施工單 §1 相同:我方尚未部署,`/upload/` 較短可讀,且與貴方既有
路由慣例(`/plm/`、`/core/`)一致。感謝在核准之後仍誠實回報與規約命名表的
字面差異——這種「已經核准但發現問題還是講」的態度,比默默放過更有價值。

我方所有網址、cookie path、CSP、`root_path` 皆由 `API_PREFIX=/upload` 這一個
設定值**直接導出**,無任何硬寫死的路徑,已有測試釘住
(`tests/test_web_layout.py::test_web_url在無前綴時不產生雙斜線`、
`tests/test_sso_contract.py` 的 cookie Path 測試)。此項無待辦。

### 2. 🔴 compose 是否符合 §3(容器名 / port / cats-edge)

**確認符合,並已補上一項求字面精確一致。**

| 約束 | 現況 |
|---|---|
| 容器內監聽 8080、不對主機 publish | ✅ `Dockerfile` `EXPOSE 8080`;compose 的 `svc` 無 `ports:` |
| 加入 external `cats-edge` | ✅ `networks.cats-edge.external: true` + `name: cats-edge` |
| gateway 以 `upload-program` 解析上游 | ✅ 原先以 `cats-edge` 網路上的 `aliases: [upload-program]` 達成;**T54 已補上 `container_name: upload-program`**,求字面與施工單完全一致,不留模糊空間 |
| DB / MinIO 不上 `cats-edge`、不曝 port | ✅ 只在 `backend` 網路,無 `ports:` |

### 3. 🔴 cookie Path 在剝前綴形態下是否仍為 `/upload/`

**確認是,而且這件事在我方的設計上不受「剝前綴」影響。**

`cookie_path` 是從設定值 `api_prefix` **直接推導**(`app/config.py`
`Settings.cookie_path`),不是從當下請求的路徑推導。剝前綴之後 App 認知的
路徑固然是 `/`,但我方從一開始就沒有依賴這個路徑去算 cookie Path——
這正是為了避開貴方施工單點出的那個坑。已有自動化測試釘住實際送出的
`Set-Cookie` 標頭確實含 `Path=/upload/`(`tests/test_sso_contract.py`),
非本次施工單來函後才補。

### 4. 上傳頁的外部 JS 是否為自行託管

**確認是。** `app/static/upload.js` 是純內部靜態檔,經 gateway 的
`/upload/static/` location 提供,原始碼中沒有任何外部網址或 CDN 引用
(已檢索確認)。這是 T44 開發上傳頁時就定下的原則——同源之下引入第三方腳本
等於讓第三方腳本能觸及 IdP,契約 §4.10 的精神不允許這件事。

### 5. repo 改名

**與貴方裁決函 §4、施工單 §6.1 的立場一致:我方不擅自處理,待與 Platform 確認。**

已把貴方給出的目標命名(`service-upload-program`)記入我方的
[開發計畫書.md](../開發計畫書.md) §6 待處理清單,作為與 Platform 討論時的
明確參照值,而不是停在「不知道規約要求叫什麼」。**改名前不會動 repo**,
因為那會連動 GHCR image 路徑與現有的 PR/連結,方向錯了是白做。

---

## 順帶處理的兩項(施工單 §5.2、§6 的直接後果)

雖然施工單 §9 沒有把這兩項列進「需要回覆」,但它們是 §5.2、§6 的字面
要求,核對後發現與現況不完全一致,已一併修正(T54):

| 項目 | 修正前 | 修正後 | 理由 |
|---|---|---|---|
| v1.2 | 2026-08-12 17:20 | Claude(T93 第九條) | 依憲法第九條補上抬頭四欄(專案 / 發文專案 / 受文專案 / 發文時間);內容未變動 |
| `X-Frame-Options` | App 自行送 `DENY` | **App 不送**,交給 gateway | 施工單 §5.2:語意上不支援多值合併,兩邊都送在今天恰好無害,但一旦任一邊日後改值就會製造「行為未定義」的衝突,責任本來就只該有一邊 |
| DB 名 / 使用者 | `upload_db` / `upload_user` | `upload_program_db` / `upload_program_user` | 施工單 §6 的平台規約命名表;尚未上線、無正式資料,現在改不動任何既有資料列,比上線後改便宜 |

---

## 尚未能驗證的項目(待 Keycloak 部署後)

施工單 §7.2 新增的三項冒煙(cookie Path 實測、重導網址帶前綴實測、
~90MB 大檔上傳),與契約 §7 原有六項,皆需要真實 gateway + 真實 Keycloak
的部署形態才驗得到——這點與貴方原文一致:「在本機開發(無 gateway 剝前綴)
一定是綠的,只有經過 gateway 才會現形」。我方會在 T29(接入冒煙清單)
執行時逐項留存證據。

~~**目前唯一的阻擋項與貴方施工單 §8 一致:Keycloak 正式環境部署(Platform P0-A)。**~~

> **更正(2026-07-29,依施工單 v1.1/v1.2)**:上句所依據的施工單 v1.0 有事實錯誤,
> 貴方已自行更正——**IdP 自 2026-07-27 起即已上線**,本案沒有外部阻擋。
> 且施工單 v1.2 §3.0 明確了施工順序:**我方容器先上線,貴方才解除 `/upload/` 註解並
> reload**。我方已據此把首次上線流程寫進 [runbook §A0](../runbook_換版與備份還原.md),
> 並確認貴方已執行 client 建置腳本(回讀驗證通過)——等 secret 以安全管道交付即可部署。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.1 | 2026-07-29 07:30 | Claude(Benny 授權) | 依施工單 v1.1/v1.2 補記更正:IdP 早已上線(P0-A 阻擋不成立)、施工順序反轉(我方容器先上線);記錄 client 建置腳本已執行、回讀驗證通過 |
| v1.0 | 2026-07-29 03:20 | Claude(Benny 授權) | 初版:回覆施工單 §9 五項(前綴接受、compose 確認並補 `container_name`、cookie Path 確認由設定值直接導出不受剝前綴影響、JS 自行託管確認、repo 改名待 Platform);另記錄兩項順帶修正(不自送 X-Frame-Options、DB 命名對齊規約)與尚待 Keycloak 部署後驗證的冒煙項目 |
