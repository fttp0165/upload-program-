# upload-program 測試 SOP

**建立日期:** 2026-08-11 23:10
**最後更新:** 2026-08-11 23:10
**版本:** v1.0
**對應任務:** T89
**適用對象:** 本專案所有開發與測試工作(含 AI 協作)

> 這份是**照著做**的文件(定位比照 runbook)。它把散在憲法第三條、conftest、
> 43 個測試檔檔頭與各篇 dev-log 裡的測試紀律收攏成一份;**與憲法或 runbook
> 衝突時以它們為準**,並回頭修正本文。
>
> 本文的每一條規則都盡量標注**它是用什麼代價換來的**(任務編號/事件)。
> 沒有出處的規則,懷疑它之前先查一下——通常是有的,只是這裡漏標了。

---

## 1. 測試策略:這個專案的測試長什麼樣

```
                ┌────────────────────────────┐
   冒煙(VM)   │ 換版四組冒煙(runbook §A.4)│ ← 每次換版,人工執行
                ├────────────────────────────┤
   CI 守門      │ pytest + ruff + 文件同步 +  │ ← 每次 push / PR
                │ 紅線掃描 + Trivy            │
                ├────────────────────────────┤
   整合測試     │ httpx ASGITransport 打整個  │ ← 主體(~9 成)
   (本機)     │ FastAPI app;SQLite + 替身  │
                ├────────────────────────────┤
   純函式測試   │ markdown_lite、dashboard、  │ ← 邏輯獨立處
                │ oidc 錯誤解析…             │
                └────────────────────────────┘
```

- **主體是整合式測試**:用 `httpx.ASGITransport` 打完整的 app,不 mock 路由層。
  資料庫用 SQLite(`tmp_path` 下的檔案),外部相依用替身(見 §5)。
  選這個形狀的理由:本專案的風險集中在**路由 × 權限 × 逸出 × 標頭**的組合,
  單元測試切太細會漏掉組合錯誤(T59 只改了一半的模板,就是單看各半都對)。
- **不追求覆蓋率數字**。追求的是:每條紅線至少有一條具名測試守著(§6 的對照表),
  每個修過的 bug 有一條測試釘住根因(第五條 4)。
- 現況:**43 個測試檔、506 條,全套約 60–90 秒**。

---

## 2. 環境準備

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # 內含 -r requirements.txt
```

- 測試**不需要** Docker、PostgreSQL、MinIO、Keycloak——SQLite + 替身就能跑全套。
  這是刻意的:全套要在任何一台乾淨機器上一分鐘內能動,否則沒有人會跑(第三條 5 的前提)。
- `pyproject.toml` 已設 `pythonpath = ["."]`:`pytest` 與 `python -m pytest`
  兩種呼叫都能用。🐛 這行是 T12 的教訓——`python -m pytest` 會把 cwd 加進
  `sys.path` 而 `pytest` 執行檔不會,當年本機全綠、CI 一跑就
  `ModuleNotFoundError: No module named 'app'`。
- `asyncio_mode = "auto"`:測試函式直接寫 `async def`,不用掛裝飾器。

---

## 3. 日常執行

| 目的 | 指令 |
|---|---|
| 全套 | `.venv/bin/python -m pytest -q` |
| 單檔(開發中) | `.venv/bin/python -m pytest tests/test_xxx.py -q` |
| lint | `.venv/bin/python -m ruff check .` |
| 文件 md/HTML 同步 | `python tools/render_docs.py --check` |

**提交前三件套:全套 pytest + ruff + `--check`,三個都乾淨才 push。**

🔴 「測試全綠」指**實際跑過並看到綠燈**(第三條 5)。沒跑過的不得宣稱通過;
dev-log 與 PR 描述裡寫的測試數字,必須是貼得出輸出的那一次。

---

## 4. TDD 標準流程(第三條)

每個任務照這個順序走,**順序本身就是規矩**:

1. **先有計畫**(第二條):dev-log 的計畫段寫妥「目標、影響範圍、驗收標準、回滾方式」,
   驗收標準逐條可測。
2. **先寫紅測試**:把預期行為釘住,**實際執行**並把紅的證據記下來
   (例:`5 failed, 3 passed`)。
   - 紅階段就綠的那幾條是**護欄**——它們釘的是「不該因為這次改動而壞掉的東西」
     (T81:非瀏覽器維持 200;T86:已發布版本無上傳入口)。護欄在紅階段是空的,
     實作後才真正生效——它們防的是實作**做過頭**,不證明功能存在(T87 的觀察)。
3. **實作至綠**:目標測試綠了之後,**跑全套**。行為改變會讓既有測試轉紅是常態
   (T81 一次轉紅 23 條)——逐條判斷是「測試該改」還是「實作錯了」,
   改測試時在 docstring 記明**哪個任務、為什麼**改掉它原本的斷言。
4. **三件套全過** → commit。訊息裡帶紅→綠證據與測試總數。
5. **dev-log 補結果段**:紅的證據、最終數字、取捨、遺留。
6. 純重構可不寫新測試,但既有測試**必須維持全綠**當安全網(第三條 4)。

---

## 5. 撰寫規範

### 5.1 命名與結構

- 檔名 `tests/test_主題.py`;測試名**用中文描述行為**,讀起來是一句話:
  `test_匿名瀏覽器開首頁_302到登入頁`、`test_沒有名稱快取時顯示UUID`。
  紅了的時候,測試名就是故障描述的第一句。
- 檔頭 docstring 寫**這個檔在守什麼、少了會出什麼事**;紅線標 🔴。
  這不是裝飾——T81 改行為時,就是靠各檔頭判斷哪些斷言可以改、哪些絕對不能動。

### 5.2 替身(conftest.py)

| 替身 | 換掉什麼 | 要注意 |
|---|---|---|
| `FakeOidc` | Keycloak | `oidc.issue(sub, name=..., expired=...)` 發 token;🐛 T52 教訓:**替身與真實行為的落差本身就是風險**——它原本永不過期,於是「300 秒過期沒人續」藏過 240 條測試 |
| `FakeStorage` | MinIO | 保留 magic bytes 檢查(`magic_sniff_bytes`) |
| SQLite(`tmp_path`) | PostgreSQL | 不保存時區——naive/aware 差異要在**程式端**收掉(`dashboard._aware()`),不是在測試端遷就 |
| `client` fixture | 瀏覽器 | 預帶探測 cookie(T64 遺產);瀏覽器視角要自帶 `BROWSER` header,機器視角不帶——**兩種視角是不同的測試**(T81 起首頁對兩者行為不同) |

magic bytes 樣本用 conftest 現成的:`ELF`(binary)、`SOURCE_ZIP`、`DOC_PDF`;
`complete_kinds()` 一次補齊三類(T65 之後發布需三類齊備,建 release 的測試都會用到)。

### 5.3 測試資料

- 🔴 一律 fixture 現造假資料;**嚴禁任何真實個資、正式資料匯出檔**(第三條 3)。
- sub 用 `sub-任務代號-用途` 形態的假值,測試之間不共用帳號。

### 5.4 🔴 狀態要走真實路徑寫入,不得手動塞 DB

T84 的假綠教訓,本 SOP 最重要的一條:

> `display_name_cache` 被手動 `session.merge()` 塞值後,測試用不帶 `name` 的
> token 登入——**§4.2a 的「每次登入覆寫」把值清成 NULL**,於是
> 「API 不含名字」這條反向安全測試在**根本沒有名字**的情況下通過了。

規則:

1. 會被業務規則覆寫的狀態(快取、計數、狀態機),**用產生它的真實路徑造**
   (名字→`oidc.issue(sub, name=...)` 登入寫入;下載數→真的打下載端點)。
2. **反向斷言(「不得出現 X」)必須先有前提斷言(「X 確實存在於該在的地方」)**,
   否則 X 根本不存在時它會假綠——一條假綠的安全測試比沒有更危險,
   它讓人以為紅線有人守。

### 5.5 斷言的粒度

- **切區塊,不整頁比對**。同一頁有多個區塊時,先切出目標區塊再斷言
  (T85 的 `_pending_block()`)——否則另一半的內容會替你假綠,
  而 T59 恰恰就是「兩個迴圈只改了一個」。
- **斷言 invariant,不斷言字面**。`test_管理後台不顯示個資欄位` 曾整頁搜「姓名」
  二字,T59 之後名字本來就會顯示,它還綠只是文案剛好沒用那兩個字;
  改成以 `@` 掃全頁斷言「不得出現 email」——標籤可以改名,email 不會沒有 `@`(T85)。
- **載體會過期**。匿名版型斷言的載體從首頁改到 `/help`(T81 後首頁對匿名 302);
  改行為時要順手檢查「還有哪些測試站在舊載體上」。
- 逸出斷言成對寫:原文不得出現 + 逸出形必須出現
  (`"<script>" not in main` **且** `"&lt;script&gt;" in main`),缺後者會被「整段被吞掉」騙過。

### 5.6 量化的紅線用機制驗,不用抽查

- 查詢數固定(防 N+1):掛 `before_cursor_execute` 事件**實際計數**,
  斷言 1 列與 10 列相同(T84)。
- 檔案同一性:比 **SHA-256**,不比「有一張圖」——「看起來很像」正是要防的事(T80)。
- 全站連結帶前綴:解析 HTML 逐一掃 `href/src/action`,白名單具名且白名單本身有補償測試
  (T40/T67 的 `PLATFORM_URLS` + `test_portal_link`)。

---

## 6. 安全測試對照表(紅線 ↔ 誰在守)

改動觸及左欄任何一項,**先去右欄的檔案看檔頭**再動手。

| 🔴 紅線 | 守護測試 |
|---|---|
| 上傳驗 magic bytes、HTML/SVG 拒收 | `test_filetypes` |
| 下載一律 attachment + nosniff | `test_upload`、`test_latest_release` |
| inline 圖片唯一例外的六條收窄 | `test_issue_attachments` |
| Markdown 逸出優先(`javascript:`/`data:` 永不成為 href/src) | `test_markdown_lite`(該模組唯一的安全網,**不得刪改**) |
| 業務庫只存 sub;名字只上後台 HTML 不上 API | `test_audit_names`、`test_web_admin`(`@` 掃全頁)、`test_display_name_cache` |
| JS 不碰 token、不進瀏覽器儲存 | `test_sso_contract`、`test_upload_cards`(字串掃描——JS 檔連註解都不得出現那兩個 API 名) |
| CSP 禁 inline / unsafe-eval;禁自送 X-Frame-Options | `test_sso_contract` |
| 匿名不得漏出任何專案(302 與落地頁兩視角) | `test_web_home` |
| 轉址不得洩漏專案存在性 | `test_web_login_redirect` |
| 回報僅本人與管理員可見(404 不洩漏存在) | `test_issues` |
| log 不記 token / error_description | `test_oidc_error_logging` |
| 查詢數不隨列數成長 | `test_audit_names` |
| RS256-only、零 Authentik(repo 級掃描) | `test_sso_contract` + CI 紅線掃描步驟 |

---

## 7. Migration 測試

1. 每支新 migration,本機 **up → down → up 雙向演練**,結果記進 dev-log(git 紅線)。
2. `alembic history` 驗鏈:單一 head、無分岔(第八條 6;0006 曾修過 down_revision 接錯)。
3. backward 會刪資料的,檔頭標 🔴 並**同步補進 runbook §B 的不可逆清單**
   (T88 補過一次課:0007/0008 晚了九天才入表)。
4. 測試端不寫 migration 測試——schema 由 `Base.metadata.create_all` 直建,
   migration 的正確性靠演練與 staging,不靠單元測試假裝。

---

## 8. CI 判定(每次 push / PR)

| Job | 內容 | 紅了怎麼辦 |
|---|---|---|
| 測試 / lint / 文件同步 | ruff → pytest → `render_docs --check` → 不該進 git 的檔案 → SSO 紅線掃描(零 Authentik、零 HS256) | 本機重現、修到綠;**不得註解掉測試換綠燈** |
| Build image + Trivy | 大小 ≤300 MB → non-root → `/health` 冒煙 → Trivy | 🔴 CVE 用**加版本下限**修(T82),不用 `.trivyignore`、不放寬 severity——本服務散布可執行檔,掃描是自我加嚴的一環 |
| Push GHCR | 僅 `v*` tag;先「確認此版本尚未發布過」 | 同版不重發(第八條 5),發錯發下一個 patch |

- runner 是 self-hosted(`cats-platform`);CI 秒殺且無 runner ≈ 帳號層問題,不是程式紅燈(runbook §A.1)。
- ⚠️ Trivy 的 CVE 資料庫每天在長,**main 沒動也可能突然紅**(T82 就是這樣來的)——
  先看報告裡的套件與 CVE 再判斷,不要反射性地怪最後一個 commit。

---

## 9. 發版與換版驗證(第八條 + runbook §A)

發版前:

- [ ] `alembic history` 驗鏈;有 migration 的版本本機演練過
- [ ] `APP_VERSION` 已隨 PR 改成該版(tag 前先改——不一致 = 首頁對使用者說謊)
- [ ] runbook 該補的先補(不可逆清單、cron)——**發版前,不是發版後**(T88)

換版後四組冒煙(**全對才算換版完成**;詳細指令見 runbook §A.4):

1. 對外路徑經 gateway(`/upload/`、`/upload/static/app.css`)
2. 🎯 **版本哨兵**:`/help` 頁尾的 `vX.Y.Z`——匿名可讀,**登入壞掉也驗得到**。
   首頁 200 證明不了任何事;2026-08-11 那次「假換版」(compose tag 沒改到、
   容器 `Running` 而非 `Started`)就是哨兵當場抓到的
3. 容器內 `/ready`(DB / MinIO / JWKS)——容器重建過就要**重驗**,舊容器的 200 不算數
4. 既有系統零影響(`/`、`/plm/`、`/TMP_GEN/`、`/core/health`)

🔴 **「沒驗到」不得記成「驗過了」。** 需要登入的驗證點在登入不可用期間要明列
「未驗」,等恢復後補驗(v0.2.1 的管理頁與上傳頁即為此狀態)。

---

## 10. 陷阱清單(全部真的踩過)

| # | 陷阱 | 事件 | 解法 |
|---|---|---|---|
| 1 | 反向安全測試在標的不存在時假綠 | T84 | 前提斷言 + 狀態走真實路徑寫入(§5.4) |
| 2 | 整頁字串比對被另一半內容救活 | T85/T59 | 區塊斷言(§5.5) |
| 3 | 字面斷言與 invariant 脫節,守的東西名存實亡 | T85 | 斷言 invariant(`@` 掃描) |
| 4 | 替身比真實系統寬鬆,缺陷藏在落差裡 | T52 | 替身要會過期/會失敗;落差本身就是風險 |
| 5 | 測試斷言寫過嚴,把正確的安全行為當 bug | T77(`javascript:` 應成為**無害純文字**而非消失) | 紅了先想「規格說什麼」,修測試不修安全 |
| 6 | 載體過期(行為改了,測試站在舊頁面上) | T81(23 條) | 改行為時全套跑完逐條歸類,docstring 記明改因 |
| 7 | 「先綠後真」的護欄被當成功能證明 | T87 | 紅證據裡分開陳述:N failed(功能)+ M passed(護欄) |
| 8 | 只驗容器內、不驗經 gateway 的路徑 | runbook §A.4 | 「驗到的是另一個東西」——冒煙一律走對外路徑 |
| 9 | 換版驗證拿首頁 200 當成功 | 2026-08-11 假換版 | 版本哨兵(§9) |
| 10 | CI 突然紅以為是自己改壞 | T82(CVE 資料庫更新) | 先讀 Trivy 報告再歸因 |

---

## 11. 本文的維護

- 新的測試教訓(踩到新坑、立了新規)→ **§10 加一列 + 對應章節補規則**,升次版號。
- 憲法第三條或 runbook 修訂 → 本文同步回寫(第二條 5)。
- 每次修改依第七條更新「最後更新」與版本歷史。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-11 23:10 | Claude(Benny 指示) | 初版:把散落在憲法第三條、conftest、43 個測試檔檔頭與 dev-log 的測試紀律收攏為一份 SOP——策略形狀、環境、三件套、TDD 五步、撰寫規範(真實路徑寫入/區塊斷言/invariant/成對逸出斷言)、紅線對照表、migration 演練、CI 判定、換版四組冒煙、十條實踩陷阱;每條規則標注出處任務 |
