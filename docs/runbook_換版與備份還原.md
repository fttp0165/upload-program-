# upload-program 維運 runbook:換版、回滾、備份、還原演練

**建立日期:** 2026-07-29 04:10
**最後更新:** 2026-08-31 10:30
**版本:** v1.8
**對應任務:** T27
**適用環境:** Cats 共用 VM(單機 docker compose,gateway 由 portal 管理)

> 這份是**照著做**的文件,不是設計說明。每一步都假設執行者在壓力下、
> 半夜、對系統不熟——所以指令完整可貼、判斷點明確、該留的證據寫清楚。
>
> 🔴 三條不可逾越的底線,先放最前面:
> 1. **正式機不 build、不 `git pull`**——部署 = 改 tag 後 `pull`(平台鐵則)。
> 2. **production compose 禁 `latest` tag**。
> 3. **絕不啟動舊的 `nginx-gateway`**——它會搶 80/443,全平台一起掛。

---

## A0. 首次上線(只做一次;順序與直覺相反)

**🔴 是我方容器先上線,portal 的路由後啟用——不是反過來**(施工單 v1.2 §3.0)。

nginx 對寫死主機名的上游(`proxy_pass http://upload-program:8080/`)是在
**載入設定當下**解析的。容器不存在時不是回 502,而是 `[emerg]` 讓**整份設定
載入失敗、gateway 起不來**——而 portal-gateway 綁 80/443,它起不來等於
PLM 與 AES_KEY 正式站一起中斷。所以 portal 的 `/upload/` 路由**刻意保持註解**,
等我方容器上線後才解除。

| # | 步驟 | 誰做 |
|---|---|---|
| 1 | 從安全管道收取 `idp/.env.keycloak.upload-program`(client secret),填入部署 `.env`(chmod 600,不進 git)。⚠️ **取 `*CLIENT_SECRET=` 那個變數**(標準長度 32 字元)——2026-07-29 實測曾抓錯成另一個 64 字元變數,症狀是登入走到最後一步「授權碼交換失敗」401 | 我方 |
| 2 | `BOOTSTRAP_ADMIN_SUBS` 可先留空——上線後指定人選登入、從 `/upload/pending` 複製自己的 `sub` 再回填重啟(SSO 接入計畫 §4.3) | 我方 |
| 3 | 容器上線:`docker login ghcr.io`(image 是 private,需 `read:packages` PAT)→ `docker compose up -d`。compose 已含 `extra_hosts` hairpin 與內部 CA 掛載(2026-07-29 實測必需,理由見 compose 註釋) | 我方 |
| 4 | `docker compose exec svc alembic upgrade head` | 我方 |
| 5 | 確認 gateway 解析得到:`docker exec portal-gateway getent hosts upload-program` | 雙方 |
| 6 | portal 解除 `/upload/` 註解 → `nginx -t` → 低峰 reload | portal |
| 7 | 冒煙:§A.4 全套 + 既有系統零差異(`/`、`/plm/`、`/TMP_GEN/`、`/core/health`) | 雙方 |
| 8 | 掛 cron(§C.2)——備份與稽核清理**不掛就不存在** | 我方 |

之後的每次換版走下面的 §A;首次上線的特殊之處只有第 5、6 步。

## A. 換版(部署新版本)

### A.0 前置確認(第一次部署後,每次都一樣)

```bash
cd /opt/upload-program          # 部署目錄(VM 慣例:/opt/<服務名>,2026-07-29 實測定案)
docker compose config -q        # compose 語法與 .env 齊全性
```

### A.1 發版:打 tag,讓 CI 產 image

在開發機(不是 VM):

```bash
git tag v1.2.0 && git push origin v1.2.0
```

CI 只在 `v*` tag 推 GHCR(`ghcr.io/fttp0165/upload-program:v1.2.0`),
**不推 latest**。等 CI 全綠(含 Trivy)再進下一步——CI 紅著就部署,
等於把 CI 白裝了。**驗收點是 repo 側欄 Packages 出現該版 tag**,
Release 頁本來就只有 source zip,不會有 image。

> 🔎 **發版前一分鐘檢查(v0.1.3 事故後新增):**
> `alembic history | head -3` 驗 migration 鏈(revision id 慣例是**檔名全名**);
> 有 migration 的版本至少本機 up→down→up 演練過。
>
> ⚠ **CI 發版後、VM pull 前要重新 `docker login ghcr.io`**:publish job 的
> login-action 清理步驟會登出 ghcr,而 runner 與 deploy 共用 docker 憑證檔
> (根治=runner 設獨立 DOCKER_CONFIG,完成後刪本註記)。
>
> 🩺 **CI job 幾秒內失敗、沒有任何步驟輸出、`runner_id: 0`**(2026-07-29 v0.1.2 實例):
> 這不是程式紅燈,是**帳號層分不到 runner**——先查
> Settings → Billing 的 Actions 分鐘額度(私有 repo 免費 2000 分/月,
> docker build + Trivy 很吃),再查 githubstatus.com。程式問題的紅燈
> 一定看得到是哪個步驟死的;什麼都沒有的紅燈去查帳,不要改程式。

### A.2 VM 上換版

```bash
cd /opt/upload-program
# 1) 🔴 換版前備份(見 §C;有 migration 的版本**必做**,沒 migration 的版本也建議做)
./backup.sh   # 即 §C.1 的指令組

# 2) 改 tag:編輯 docker-compose.yml 的 image 版號
#    image: ghcr.io/fttp0165/upload-program:v1.2.0
#    ⚠ 版本若新增/變更 .env 變數(看該版 release note 的「部署」段),
#      在 pull 之前一併補進 /opt/upload-program/.env——漏了通常是啟動即
#      fail-fast(缺必要變數),或功能退回舊行為(可選變數)。

# 3) 拉新版並重建(只重建 svc,不動 db/minio)
docker compose pull svc
docker compose up -d svc

# 4) 跑 migration(若本版有新增;查 alembic/versions/ 或版本 release note)
docker compose exec svc alembic upgrade head
```

> Migration **不會**在啟動時自動跑——這是刻意的:schema 變更是部署動作,
> 不是應用程式的副作用,要在人看著的時候發生。

### A.3 🔴 通知 portal reload gateway(不做 = 靜默 502)

**nginx 只在啟動/reload 當下解析一次上游 IP。** 容器重建後 IP 已變,
gateway 仍指向舊 IP——症狀最惡劣:`docker ps` healthy、健康檢查全綠,
**只有真正的使用者拿到 502**(portal 施工單 §3.2,他們自己 2026-07-28 踩過)。

```
通知 portal 執行:docker exec portal-gateway nginx -s reload
```

這是與 portal 的**明文約定**,不是禮貌。portal 把「執行期動態解析上游」
列入改進項,屆時本步驟可移除(移除時本文件要同步升版)。

### A.4 換版冒煙(reload 之後才算數)

```bash
# 對外路徑(經 gateway)——這才是使用者的視角
curl -sk -o /dev/null -w '/upload/        = %{http_code}\n' https://catsapp.sporton.com.tw/upload/
curl -sk -o /dev/null -w '/upload/static/ = %{http_code}\n' https://catsapp.sporton.com.tw/upload/static/app.css
# 🎯 版本哨兵:挑一個「只有新版才有」的靜態檔驗——200 才證明跑的真的是新版。
#    首頁 200 不代表換版成功(舊容器照樣 200)。哨兵檔隨版本挑選,
#    例:v0.1.2 用 /upload/static/vendor/bootstrap.min.css(該版新增的 vendor)。
curl -sk -o /dev/null -w '哨兵檔          = %{http_code}\n' https://catsapp.sporton.com.tw/upload/static/vendor/bootstrap.min.css
# 容器內 readiness(查 DB / MinIO / JWKS)
docker compose exec svc python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/ready').status)"
# 🔴 既有系統零影響(每次動 gateway 相關的東西都要驗)
for p in / /plm/ /TMP_GEN/ /core/health; do
  curl -sk -o /dev/null -w "$p = %{http_code}\n" "https://catsapp.sporton.com.tw$p"
done
```

四組都對才算換版完成。**只驗容器內、不驗經 gateway 的路徑,驗到的是另一個東西。**

> 附註:若冒煙裡有 `/upload/auth/login`,**302 是正常**(登入本來就轉址去 IdP);
> 判讀標準是「非 5xx、非 404」,不是一律 200。

---

## B. 回滾

### B.1 只退程式(本版沒有 migration)

```bash
# compose 的 image 改回上一版 tag
docker compose pull svc && docker compose up -d svc
# 🔴 仍要通知 portal reload(§A.3)+ 冒煙(§A.4)——回滾也是換版
```

### B.2 退程式 + 退 schema(本版有 migration)

```bash
# 1) 🔴 先備份(回滾也會動資料——downgrade 是 UPDATE/DROP,不是「回到安全」)
./backup.sh
# 2) 先退 schema、再退程式(順序:舊程式看不懂新 schema 的機率,遠低於新程式看不懂舊 schema)
docker compose exec svc alembic downgrade -1
# 3) compose 改回上一版 tag → pull → up -d svc → reload → 冒煙
```

⚠️ **已知的不可逆回滾**(檔頭都有註記,這裡集中列一次):

| migration | downgrade 會失去 | 可否重建 |
|---|---|---|
| `0004_artifact_download_count` | 全部下載累計數 | ❌(無事件表可重算) |
| `0005_audit_events` | 整張稽核表 | ❌(stdout log 會輪替) |
| `0007_issues` | **全部問題回報與討論串** | ❌ |
| `0008_issue_attachments` | **全部回報附件的 DB 紀錄** | ❌(MinIO 物件會變成孤兒,佔空間且無從對應) |

要保留就照各檔頭的 SQL 先撈一份再 downgrade。

🔴 **`0007` / `0008` 的 backward 是 `DROP TABLE`,銷毀的是使用者親手送出的東西。**
與前兩列不同:下載數與稽核是系統產生的紀錄,回報則是別人花時間寫的內容,
刪掉沒有任何補救管道,連「他寫過什麼」都無從得知。
**退這兩支之前一定要先備份(§C),而且要當場驗備份讀得回來**,不是確認檔案存在就算。

---

## C. 備份

### C.1 備份指令(repo 內 `tools/backup.sh`,scp 到部署目錄即可)

🔴 正式機不 git pull,所以這支腳本要隨 compose 一起 **scp 到部署目錄**;
內容如下(權威版本在 repo 的 `tools/backup.sh`,兩處若有歧異以 repo 為準):

**腳本全文不再內嵌於本文件**——2026-07-29 實測後升 v2(minio 映像檔無 `tar` →
改 `docker cp`;主機不需裝 postgresql-client → 驗證改用 db 容器內的 `pg_restore`),
內嵌副本當天就過時了,正好證明「兩份會漂移」。**唯一權威:repo 的 `tools/backup.sh`**,
部署時隨 compose 一起 scp、要看內容直接開檔案。

- 🔴 備份檔含正式資料與物件,**絕不進 git**(`/srv/backups` 不在 repo 內)
- 頻率:**每日一次** + **每次換版前一次**(§A.2 第 1 步)
- 保留:每日備份留 14 天、換版前備份留到下下個版本穩定
- ⏳ 異地(NAS / 另一台 VM)同步待定,與 Q12(磁碟空間)一併跟平台確認

### C.2 cron(部署當天照抄——設定值不會自己生效)

```cron
# 每日備份(02:30;BACKUP_ROOT 依 2026-07-29 實測定案)
30 2 * * *  cd /opt/upload-program && BACKUP_ROOT=/home/deploy/upload-backups ./backup.sh >> /var/log/upload-backup.log 2>&1
# 稽核紀錄保留期清理(04:00;AUDIT_RETENTION_DAYS 只是給這支腳本讀的,不掛 cron 就是無限成長的個資表)
0 4 * * *   cd /opt/upload-program && docker compose exec -T svc python tools/purge_audit.py --apply >> /var/log/upload-audit-purge.log 2>&1
# 已關閉滿 365 天的問題回報清除(04:30;含 MinIO 附件物件)
# ⚠️ 旗標是 `--yes`,**不是** purge_audit 的 `--apply`——兩支腳本不同,抄錯就是「每天空跑」
#    而且不會有任何錯誤訊息(預設 dry-run 會正常結束、正常寫 log)。
30 4 * * *  cd /opt/upload-program && docker compose exec -T svc python tools/purge_issues.py --yes >> /var/log/upload-issue-purge.log 2>&1
```

### C.3 一次性:清掉上傳沒成功的殘骸(T107)

🔴 **這一支不掛 cron。** 它刪的是 artifact 列,而 T107 之後**不會再產生新的殘骸**
——掛成排程只會讓一支「平常什麼都不做、哪天出事就大量刪東西」的腳本在夜裡跑,
那是最難察覺的風險形狀。既有殘骸清一次就結束。

```bash
# 【CATS VM(Ubuntu)】【/opt/upload-program】【upload-program】
# 0) 🔴 先備份(§C.1)——這支會刪列,沒有單獨的復原路徑
./backup.sh

# 1) 先看會刪什麼(預設 dry-run,什麼都不動)
docker compose exec -T svc python tools/purge_failed_artifacts.py

# 2) 確認清單無誤,才真的刪
docker compose exec -T svc python tools/purge_failed_artifacts.py --apply
```

⚠ **旗標是 `--apply`**(同 `purge_audit.py`,**不是** `purge_issues.py` 的 `--yes`)。
不加旗標會正常結束、正常寫 log,但什麼都沒刪 —— 這是刻意的阻力,不是 bug。

它只碰 `upload_status != ready` 的列,`ready` 的一列都不動(條件寫死在查詢裡,
不接受參數放寬)。

---

## D. 還原演練(T27 的驗收本體;在 staging 執行)

> 目的不是「把備份倒回一個空庫」——那只驗證檔案格式。
> 是**把一個有資料的系統毀掉,再還原到指定時點**——那才是災難當天要做的事。
> 每步指定要留存的證據;全表回填後 T27 才轉 ✅。

| # | 步驟 | 指令/動作 | 要留存的證據 |
|---|---|---|---|
| 1 | 造資料 | 建 2 專案、發 2 版本、上傳 3 檔、下載數次、開通 1 使用者 | `SELECT count(*)` 各表列數;任一檔案的 SHA-256 |
| 2 | 全量備份 | `./backup.sh` | `SHA256SUMS`;備份目錄清單 |
| 3 | 繼續寫入 | 再建 1 專案(它**不在**備份裡,還原後應消失) | 該專案 slug |
| 4 | **毀掉** | `docker compose down`;刪除 `pgdata`、`minio-data` 兩個 volume | `docker volume ls` 前後對照 |
| 5 | 還原物件 | 起 minio → 把 `minio-data.tar` 倒回 volume | `mc ls` 或容器內 `ls /data` 物件數 |
| 6 | 還原 DB | 起 db → `pg_restore -U upload_program_user -d upload_program_db --clean --if-exists pg.dump` | restore 輸出無 error |
| 7 | 起服務 | `up -d svc` → `alembic current` 確認 schema 版本吻合 | `alembic current` 輸出 |
| 8 | 驗資料 | 步驟 1 的列數全部吻合;步驟 3 的專案**不存在** | 列數對照表 |
| 9 | 🔴 驗功能 | 下載步驟 1 的檔案,SHA-256 與備份前一致;再上傳一個新檔成功 | 兩個 SHA-256;上傳回應 201 |
| 10 | 驗 migration 雙向 | `downgrade -1` → `upgrade head`(在還原後的庫上) | 兩次輸出 |
| 11 | 記錄耗時 | 從步驟 4 到步驟 9 的總時長 = **實測 RTO** | 時間數字 |

第 9 步是整個演練的靈魂:**列數對了只代表 metadata 回來了;
檔案抓得回來、雜湊對得上,才代表系統活著。**

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.8 | 2026-08-31 10:30 | Claude(Benny:「沒有上傳成功的 就不用顯示了」) | **新增 §C.3:一次性清掉上傳沒成功的殘骸**(T107 的 `tools/purge_failed_artifacts.py`)。🔴 明寫**不掛 cron** —— T107 之後不再產生新殘骸,把它排程化只會讓一支「平常什麼都不做、哪天出事就大量刪東西」的腳本在夜裡跑,那是最難察覺的風險形狀。⚠ 旗標是 `--apply`(同 `purge_audit.py`,不是 `purge_issues.py` 的 `--yes`),不加只會 dry-run 且不會有任何錯誤訊息;執行前必須先備份 |
| v1.2 | 2026-07-29 07:50 | Claude(Benny 授權) | §C.1 的 backup.sh 落成 repo 檔案 `tools/backup.sh`(含每日備份 14 天保留期的自動清理;正式機不 git pull,需隨 compose 一起 scp);runbook 標明權威版本在 repo,歧異以 repo 為準 |
| v1.7 | 2026-08-11 11:40 | Claude(Benny 指示) | **T88 發版前補完**:§B 不可逆清單補 `0007_issues` / `0008_issue_attachments`(backward 是 `DROP TABLE`,銷毀的是**使用者親手送出的內容**,與前兩列的系統紀錄性質不同,故要求退版前備份並**當場驗讀得回來**);§C.2 cron 補 `purge_issues.py`,並明寫 ⚠️ 旗標是 `--yes` 不是 `--apply`——抄錯會每天空跑且**不會有任何錯誤訊息** |
| v1.6 | 2026-07-30 23:00 | Claude(Benny 授權) | v0.1.3→v0.1.4 事故回寫:§A.1 加「發版前 alembic history 驗鏈 + migration 演練」與「CI 發版後 VM pull 前重新 docker login(runner 憑證互洗)」 |
| v1.5 | 2026-07-29 16:30 | Claude(Benny 授權) | §A.2 補「版本新增 .env 變數要照 release note 先補再 pull」(T60 的三個 OIDC 內部端點覆寫即首例) |
| v1.4 | 2026-07-29 15:30 | Claude(Benny 授權) | v0.1.2 發版中斷的兩課回寫:§A.1 補「CI 秒殺無 runner=帳號層(Billing/事故),不是程式紅燈」診斷與「驗收點是 Packages 不是 Release 頁」;§A.4 冒煙加**版本哨兵檔**(只有新版才有的靜態檔,200 才證明換到新版)與「302 判讀」附註 |
| v1.3 | 2026-07-29 09:40 | Claude(Benny 授權) | **依首次上線實測回寫**:部署目錄定案 `/opt/upload-program`(VM 慣例);§A0 補 GHCR login 與「secret 變數要抓 32 字元那個」的教訓;compose 已內建 extra_hosts hairpin + 內部 CA;§C.1 的 backup.sh 升 v2(minio 無 tar → docker cp;主機不需 pg_restore);cron 帶 BACKUP_ROOT |
| v1.1 | 2026-07-29 07:30 | Claude(Benny 授權) | 新增 **§A0 首次上線**(施工單 v1.2 §3.0:我方容器**先**上線,portal 的 `/upload/` 路由保持註解等我方——nginx 對不存在的上游是 `[emerg]` 整份設定載入失敗,不是 502;順序弄反會讓全平台一起中斷);含 secret 收取、bootstrap sub 可後填、cron 掛載等八步 |
| v1.0 | 2026-07-29 04:25 | Claude(Benny 授權) | 初版:A 換版(含 🔴 通知 portal reload 的明文約定與「經 gateway 冒煙才算數」)、B 回滾(含兩個不可逆 migration 的集中清單)、C 備份(先物件後資料庫的順序論證、完整性驗證、cron 含稽核清理)、D 還原演練證據表(毀掉再還原、SHA-256 驗功能、實測 RTO);演練未執行,T27 維持 🔵 |
