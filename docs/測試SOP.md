# upload-program 測試 SOP

**建立日期:** 2026-08-11 23:10
**最後更新:** 2026-08-11 23:55
**版本:** v2.0
**對應任務:** T89
**適用對象:** 本專案所有開發與測試工作(含 AI 協作)

> **照著做**的文件(定位比照 runbook)。與憲法或 runbook 衝突時以它們為準。
> 每條規則盡量標注**出處**(哪個任務用什麼代價換來的)——沒有出處的規則,
> 懷疑它之前先查一下,通常是有的。

---

## 1. 測試長什麼樣、什麼時候跑

```
  換版冒煙(VM)   ── 四組檢查              ← 每次換版・人工
  CI 守門         ── pytest+ruff+掃描+Trivy ← 每次 push / PR
  整合測試(主體)── httpx 打完整 app       ← 開發中隨時
  純函式測試      ── markdown_lite 等       ← 開發中隨時
```
<!--SVG:test-layers-->

三件事記住就好:

1. **主體是整合測試**:httpx ASGITransport 打完整 app,SQLite + 替身,
   **不需要 Docker / PG / MinIO / Keycloak**——全套在任何乾淨機器上一分多鐘能跑完。
2. 為什麼是這個形狀:本專案的風險集中在**路由 × 權限 × 逸出 × 標頭的組合**,
   切太細會漏掉組合錯誤(T59:兩個迴圈各看都對,合起來就是漏了一半)。
3. **不追覆蓋率數字**。追兩件事:每條紅線有具名測試守著(§6),
   每個修過的 bug 有一條測試釘住根因(第五條 4)。現況 43 檔、506 條。

---

## 2. 環境準備

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # 內含 -r requirements.txt
```

- `pyproject.toml` 已設 `pythonpath = ["."]` 與 `asyncio_mode = "auto"`:
  `pytest` 與 `python -m pytest` 都能跑、測試直接寫 `async def`。
  (🐛 pythonpath 那行是 T12 的教訓:本機全綠、CI 一跑就找不到 `app`。)

---

## 3. 日常執行:三件套

| 目的 | 指令 |
|---|---|
| 全套 | `.venv/bin/python -m pytest -q` |
| lint | `.venv/bin/python -m ruff check .` |
| 文件同步 | `python tools/render_docs.py --check` |

**三個都乾淨才 push。**
🔴 「全綠」指**實際跑過並看到綠燈**(第三條 5)——dev-log 與 PR 裡的數字,必須是貼得出輸出的那一次。

---

## 4. TDD 流程(第三條)

```
①計畫 → ②紅測試(實際跑,記證據) → ③實作至綠 → ④全套+三件套 → ⑤日誌
                                          ↑ 既有測試轉紅 → 逐條歸類 ┘
```
<!--SVG:tdd-flow-->

- **①** 計畫段的驗收標準要逐條可測(第二條)。
- **②** 紅的證據記下來(例:`5 failed, 3 passed`)。紅階段就綠的那幾條是**護欄**:
  釘住不該壞的東西,防實作做過頭——**不證明功能存在**(T81/T86/T87)。
- **④** 行為改變讓既有測試轉紅是常態(T81 一次 23 條)。逐條判斷「測試該改」還是
  「實作錯了」;改測試要在 docstring 記明哪個任務、為什麼。
- 純重構可不寫新測試,既有全綠當安全網(第三條 4)。

---

## 5. 撰寫規範

### 5.1 命名

檔名 `tests/test_主題.py`;測試名**用中文寫成一句行為描述**
(`test_匿名瀏覽器開首頁_302到登入頁`)——紅了的時候,測試名就是故障描述的第一句。
檔頭 docstring 寫「這個檔在守什麼、少了會出什麼事」,紅線標 🔴。

### 5.2 替身(conftest.py)

| 替身 | 換掉 | 一句話重點 |
|---|---|---|
| `FakeOidc` | Keycloak | `oidc.issue(sub, name=…, expired=…)`;🐛 T52:**替身比真實寬鬆,缺陷就藏在落差裡**(它曾永不過期,藏掉 300 秒過期的 bug) |
| `FakeStorage` | MinIO | 保留 magic bytes 檢查 |
| SQLite | PostgreSQL | 時區差異在**程式端**收掉(`_aware()`),不在測試端遷就 |
| `client` | 瀏覽器 | 瀏覽器視角自帶 `BROWSER` header;機器視角不帶——T81 起**兩種視角行為不同,是不同的測試** |

magic bytes 樣本用現成的 `ELF` / `SOURCE_ZIP` / `DOC_PDF`;`complete_kinds()` 一次補齊三類(T65)。
🔴 測試資料一律 fixture 現造,**嚴禁真實個資**(第三條 3)。

### 5.3 🔴 最重要的一條:狀態走真實路徑寫入

```
✗ 手動塞 DB → 被「每次登入覆寫」清掉 → 斷言「不含」→ 假綠
✓ 真實路徑寫入 → 前提斷言「存在」   → 斷言「不含」→ 可信
```
<!--SVG:false-green-->

兩條規則(T84 的代價):

1. 會被業務規則覆寫的狀態(快取、計數、狀態機),**用產生它的真實路徑造**
   ——名字走登入 claim,下載數真的打下載端點。
2. **反向斷言(「不得出現 X」)之前,必先有前提斷言(「X 存在於該在的地方」)**。
   否則 X 根本不存在時它照樣綠——假綠的安全測試比沒有更危險,它讓人以為紅線有人守。

### 5.4 斷言四規則

| 規則 | 出處 |
|---|---|
| **切區塊,不整頁比對**(先切出目標區塊再斷言,否則另一半內容替你假綠) | T85 / T59 |
| **斷言 invariant,不斷言字面**(搜「姓名」二字失效後,改以 `@` 掃全頁擋 email) | T85 |
| **載體會過期**(T81 後匿名版型斷言的載體從首頁改到 `/help`;改行為時檢查誰站在舊載體上) | T81 |
| **逸出斷言成對寫**:原文不得出現 **且** 逸出形必須出現,缺後者會被「整段被吞」騙過 | T77 / T84 |

### 5.5 量化紅線用機制驗,不抽查

- 查詢數固定(防 N+1):掛 `before_cursor_execute` **實際計數**,1 列與 10 列要相同(T84)。
- 檔案同一性比 **SHA-256**——「看起來很像」正是要防的事(T80)。
- 連結帶前綴:解析 HTML 逐一掃 `href/src/action`;白名單具名、且白名單自身有補償測試(T40/T67)。

---

## 6. 安全測試對照表(紅線 ↔ 誰在守)

改動觸及左欄任何一項,**先看右欄檔案的檔頭**再動手。

| 🔴 紅線 | 守護測試 |
|---|---|
| 上傳驗 magic bytes、HTML/SVG 拒收 | `test_filetypes` |
| 下載一律 attachment + nosniff | `test_upload`、`test_latest_release` |
| inline 圖片唯一例外的六條收窄 | `test_issue_attachments` |
| Markdown 逸出優先(`javascript:`/`data:` 永不成為 href/src) | `test_markdown_lite`(該模組唯一安全網,**不得刪改**) |
| 業務庫只存 sub;名字只上後台 HTML 不上 API | `test_audit_names`、`test_web_admin`、`test_display_name_cache` |
| JS 不碰 token、不進瀏覽器儲存 | `test_sso_contract`、`test_upload_cards`(字串掃描,連註解都不得出現) |
| CSP 禁 inline;禁自送 X-Frame-Options | `test_sso_contract` |
| 匿名不得漏出任何專案(302 與落地頁兩視角) | `test_web_home` |
| 轉址不得洩漏專案存在性 | `test_web_login_redirect` |
| 回報僅本人與管理員可見(404 不洩漏存在) | `test_issues` |
| log 不記 token / error_description | `test_oidc_error_logging` |
| 查詢數不隨列數成長 | `test_audit_names` |
| RS256-only、零 Authentik(repo 級掃描) | `test_sso_contract` + CI 紅線掃描 |

---

## 7. Migration 測試

1. 每支新 migration:本機 **up → down → up** 演練,結果進 dev-log(git 紅線)。
2. `alembic history` 驗鏈:單一 head、無分岔(第八條 6;0006 修過接錯)。
3. backward 會刪資料 → 檔頭標 🔴 + **同步補進 runbook §B**(T88 補過一次課)。
4. 不寫 migration 單元測試——正確性靠演練與 staging,不靠假裝。

---

## 8. CI 判定

| Job | 內容 | 紅了怎麼辦 |
|---|---|---|
| 測試 / lint / 文件同步 | ruff → pytest → `--check` → 禁入 git 檔案 → SSO 紅線掃描 | 本機重現修到綠;**不得註解測試換綠燈** |
| Build + Trivy | ≤300 MB → non-root → `/health` → Trivy | 🔴 CVE 用**加版本下限**修(T82),不用 `.trivyignore` |
| Push GHCR | 僅 `v*` tag,先驗「未發布過」 | 同版不重發(第八條 5) |

⚠️ Trivy 的 CVE 資料庫天天在長,**main 沒動也可能突然紅**(T82)——先讀報告再歸因,別反射性怪最後一個 commit。

---

## 9. 換版驗證(第八條 + runbook §A)

發版前:`alembic history` 驗鏈、`APP_VERSION` 已隨 PR 改好、runbook 該補的先補(**發版前,不是發版後**,T88)。

換版後四組冒煙,**全對才算完成**(指令見 runbook §A.4):

```
① 對外路徑(經 gateway)  ② 版本哨兵 /help 頁尾 vX.Y.Z
③ 容器內 /ready           ④ 既有系統零影響
```
<!--SVG:smoke-four-->

🔴 **「沒驗到」不得記成「驗過了」**——需要登入的驗證點,在登入不可用期間明列「未驗」,恢復後補驗。

---

## 10. 陷阱清單(全部真的踩過)

| # | 陷阱 | 事件 | 解法 |
|---|---|---|---|
| 1 | 反向安全測試在標的不存在時假綠 | T84 | §5.3:前提斷言 + 真實路徑寫入 |
| 2 | 整頁字串比對被另一半內容救活 | T85/T59 | 區塊斷言 |
| 3 | 字面斷言與 invariant 脫節 | T85 | 斷言 invariant(`@` 掃描) |
| 4 | 替身比真實系統寬鬆 | T52 | 替身要會過期、會失敗 |
| 5 | 斷言寫過嚴,把正確的安全行為當 bug | T77 | 紅了先想「規格說什麼」;修測試不修安全 |
| 6 | 載體過期(行為改了,測試站在舊頁面上) | T81 | 全套跑完逐條歸類,docstring 記改因 |
| 7 | 護欄被當成功能證明 | T87 | 紅證據分開陳述:N failed(功能)+ M passed(護欄) |
| 8 | 只驗容器內、不驗經 gateway | runbook §A.4 | 冒煙一律走對外路徑 |
| 9 | 拿首頁 200 當換版成功 | 2026-08-11 假換版 | 版本哨兵(§9) |
| 10 | CI 突然紅以為自己改壞 | T82 | 先讀 Trivy 報告再歸因 |

---

## 11. 本文的維護

新教訓 → §10 加一列 + 對應章節補規則,升次版號。憲法/runbook 修訂 → 回寫本文(第二條 5)。
每次修改依第七條更新「最後更新」與版本歷史。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v2.0 | 2026-08-11 23:55 | Claude(Benny:搭配 SVG、說明簡單一點) | 結構性改寫:全文精簡約四成,新增四張內嵌 SVG(測試層級、TDD 流程含護欄與轉紅迴圈、假綠對照、換版四組冒煙;light 配色,md 保留 ASCII 對應圖);規則與對照表內容不變,只改表達 |
| v1.0 | 2026-08-11 23:10 | Claude(Benny 指示) | 初版:把散落的測試紀律收攏為一份 SOP——策略形狀、環境、三件套、TDD 五步、撰寫規範、紅線對照表、migration 演練、CI 判定、換版四組冒煙、十條實踩陷阱;每條規則標注出處任務 |
