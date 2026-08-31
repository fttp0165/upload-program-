# 計畫:合併 PR #33(v0.3.0)——重編號 + migration 改鏈

**建立日期:** 2026-08-31 13:00
**最後更新:** 2026-08-31 13:00
**版本:** v1.0
**對應任務:** T110–T128(本計畫執行後的新編號)

> 第二條 3:大任務先立獨立計畫文件。本文件在動工前寫,執行中若與現實不符依第二條 5 回寫。

---

## 1. 現況(事實,已逐項核對)

| 項目 | 值 |
|---|---|
| PR #33 分支 | `claude/read-article-join-project-1op5nk`,head `856cb61` |
| 規模 | 29 commits / 106 檔 / +11655 −157 |
| 分岔點 | `46d265f`(= v0.2.3,**已含我方 T89–T96**) |
| `main` 現況 | v0.2.5 + 未發版的 T106–T109(在 `claude/upload-program-review-c23vpl`) |
| 合併狀態 | `mergeable_state: dirty` —— 8 檔內容衝突 |

### 1.1 兩個真正的問題(不是「解衝突」四個字)

**(A) 任務編號整段撞號:19 個。**

| 編號 | main(已發布) | PR #33(未合併) |
|---|---|---|
| T89 | 側欄入口連結移到最下面 | 測試 SOP |
| T90 | 建立版本頁顯示歷史版本號 | 測試項目清單 |
| T91 | 教學頁視覺化 SVG | 冒煙腳本 |
| T92 | 本機 WSL devserver | session 時序回覆與觀測補強 |
| T93 | base image util-linux CVE | 跨專案文件規約(憲法第九條) |
| T94 | 發版前置 0.2.2 | 中文文字檔判型修正 |
| T95 | 版號位置改到側欄底部 | `APP_VERSION` → 0.2.2 |
| T96 | 專案短名自動產生 | CI 基底映像快取致 Trivy 紅燈 |
| T97 | 發版前置 0.2.3 | 專案頁擁有者欄位 |
| T98 | CI 失效層快取自動重試 | 本地 WSL 開發與 CI |
| T99 | 回報通知信 + 後台入口 | 指令必須標明平台與路徑(憲法第十條) |
| T100 | 發布者設定查看權限 | 草稿版本回不去 |
| T101 | 專案標題與簡介可編輯 | 管理員指派入口 |
| T102 | base image 全量升級 | **發布審核制度** |
| T103 | 發版前置 0.2.4 | 專案留言板 |
| T104 | 發版補救 0.2.5 | 版本作者屬名 |
| T105 | CI 守門(⬜ 待辦) | `APP_VERSION` → 0.3.0 |
| T106 | 未上傳完成的檔案不顯示 | openssl CVE 擋下建置 |
| T107 | 被拒收的上傳不留殘骸 | 狀態總覽與 HTML 覆蓋守門 |

**(B) alembic 鏈撞號。**

```
main:      … → 0008_issue_attachments → 0009_notify_email(已上正式機)
PR #33:    … → 0008_issue_attachments → 0009_release_review → 0010_project_comments
```

🔴 兩個 `0009` 都以 `0008_issue_attachments` 為 `down_revision`,合併後 alembic 會看到
**同一個父節點的兩個分支**,`upgrade head` 直接報 "Multiple head revisions"。
而 `0009_notify_email` **已經在正式機上跑過**,所以讓號的一定是 #33 那兩支。

---

## 2. 決定

### 2.1 誰讓號:PR #33 讓

**規則:已發布的編號不動。** main 的 T89–T109 已寫進 Release 說明、commit message、
runbook、憲法引用與 GHCR image 的版本歷程;#33 尚未合併,它的編號還沒有任何外部引用。

**做法:整段平移 +21** —— T89→T110、T90→T111、……、T107→T128。
用單一位移而不是逐個挑號,理由是**可驗算**:任何人拿舊編號 +21 就知道它現在叫什麼,
不需要查對照表。對照表仍然留在本文件 §1.1 與各 dev-log 檔頭。

### 2.2 migration:#33 讓號並改鏈

```
0009_release_review   → 0010_release_review    (down_revision = "0009_notify_email")
0010_project_comments → 0011_project_comments  (down_revision = "0010_release_review")
```

⚠ **不動 `0009_notify_email` 一個字** —— 它已經在正式機上執行過,改它等於讓正式機的
`alembic_version` 指向一個不存在的 revision。

### 2.3 🔴 為什麼不能用整檔 sed(這是本計畫最容易出事的地方)

#33 是從 **v0.2.3** 切出去的,所以**它的分支上同時存在**:
- 我方已發布的 T89–T96(來自 main)
- 它自己的 T89–T107

整檔 sed 會把**我方已發布的編號一起改掉**,而那是 Release 說明與正式機版本歷程
引用的東西。

**做法:只改「#33 新增的那些行」。** 具體:
1. 取 `git diff main...pr33` 的**新增行集合**;
2. 合併之後,只對「內容出現在該集合裡」的行套用位移;
3. #33 新增的整份檔案(19 篇 dev-log、計畫書、測試檔)可整檔處理 —— 那些檔案裡的
   每一行都是它的。
4. 套用後**逐一人工複核** `git diff` 裡每一處編號變更。

實測範圍:程式碼(`app/` `tests/` `tools/` `alembic/`)的新增行共 **83 處**編號引用。

### 2.4 合併方向:#33 併進 `claude/upload-program-review-c23vpl`

而不是我去改 #33 的分支。理由:
1. 本 session 的指定開發分支就是前者,不得推別人的分支;
2. 我方分支上還有未合併的 T106–T109,兩邊終究要在一起;
3. 結果相同,而且只需要一個 PR。

⚠ **PR #33 會因此變成「已被取代」** —— 合併後要在 #33 留言說明並關閉,
不能讓它孤零零地開著(下一個人會以為那還是待辦事項)。

---

## 3. 影響範圍

| 類別 | 內容 |
|---|---|
| 衝突需人工解 | `.github/workflows/ci.yml`、`CLAUDE.md`、`README.md`、`app/routers/projects.py`、`app/routers/web.py`、`app/version.py`、`docs/任務表.md` + `.html` |
| 重編號 | 19 篇 dev-log(檔名 + 內容)、1 份施工計畫書(檔名含 T101-T104)、任務表、83 處程式碼引用 |
| migration | 2 支改名 + 改鏈 |
| 版本號 | `APP_VERSION` 衝突:#33 要 0.3.0、main 是 0.2.5 → **取 0.3.0**(見 §4) |

**對現有資料的影響:🟡 兩個 migration(加欄位 / 加表),都不 UPDATE 既有資料。**
🔴 `0010_release_review` 的 backward 會失去「待審」狀態與退回理由;
`0011_project_comments` 的 backward 會**刪掉全部留言且無法重建** ——
兩者都要進 runbook §B 不可逆清單(#33 已寫,合併後確認還在)。

## 4. 版本號:發 0.3.0

#33 改變了既有行為(`POST /v1/releases/{id}/publish` 從「發布」變成「送審」),
那是 minor 版的定義。而 main 目前是 0.2.5。
**合併後 `APP_VERSION` = 0.3.0**,一版含 T106–T109(我方)+ T110–T128(#33)。

⚠ 依第八條 4,常數在打 tag 之前改好;依第八條 5,`v0.2.4` 作廢那件事不重來。

## 5. 驗收標準

1. `git merge` 後 8 檔衝突全部解掉,`git diff --check` 乾淨。
2. **全套測試綠**(我方 593 + #33 的新測試,預期 ~640)、`ruff` 乾淨。
3. `alembic history` 是**一條線**、`heads` 只有一個(`0011_project_comments`)。
4. 🔴 `grep -rn "T89\|T90\|…\|T107"` 的每一處都能說出它指的是哪一邊 —— 沒有殘留的舊編號。
5. `render_docs.py --check` 通過(md/HTML 同步)。
6. 🔴 **正式機的 `alembic_version` 目前是 `0009_notify_email`**,合併後從它往前跑
   `0010` → `0011` 必須成立(以 `alembic history` 驗證接點,不靠猜)。

## 6. 回滾方式

合併尚未推出前:`git merge --abort`。
已推出但尚未合併進 main:重開分支即可,`main` 一個字都沒動。
🔴 已合併並上正式機之後:退版需 `alembic downgrade 0009_notify_email`,
而那會**刪掉全部留言**(§3 的紅字)—— 所以退版前必須先備份並當場驗讀得回來。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-31 13:00 | Claude(Benny:「依你推薦執行」) | 初版:盤點 19 個撞號與 alembic 雙 head,訂「已發布的不動、#33 整段 +21」與 migration 讓號改鏈;載明不可用整檔 sed 的理由(#33 從 v0.2.3 切出,分支上同時有兩邊的 T89–T96) |
