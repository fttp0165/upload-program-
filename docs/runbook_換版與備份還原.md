# upload-program 維運 runbook:換版、回滾、備份、還原演練

**建立日期:** 2026-07-29 04:10
**最後更新:** 2026-07-29 04:25
**版本:** v1.0
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

## A. 換版(部署新版本)

### A.0 前置確認(第一次部署後,每次都一樣)

```bash
cd /srv/upload-program          # 部署目錄(實際路徑以 VM 為準)
docker compose config -q        # compose 語法與 .env 齊全性
```

### A.1 發版:打 tag,讓 CI 產 image

在開發機(不是 VM):

```bash
git tag v1.2.0 && git push origin v1.2.0
```

CI 只在 `v*` tag 推 GHCR(`ghcr.io/fttp0165/upload-program:v1.2.0`),
**不推 latest**。等 CI 全綠(含 Trivy)再進下一步——CI 紅著就部署,
等於把 CI 白裝了。

### A.2 VM 上換版

```bash
cd /srv/upload-program
# 1) 🔴 換版前備份(見 §C;有 migration 的版本**必做**,沒 migration 的版本也建議做)
./backup.sh   # 即 §C.1 的指令組

# 2) 改 tag:編輯 docker-compose.yml 的 image 版號(唯一要改的東西)
#    image: ghcr.io/fttp0165/upload-program:v1.2.0

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
# 容器內 readiness(查 DB / MinIO / JWKS)
docker compose exec svc python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/ready').status)"
# 🔴 既有系統零影響(每次動 gateway 相關的東西都要驗)
for p in / /plm/ /TMP_GEN/ /core/health; do
  curl -sk -o /dev/null -w "$p = %{http_code}\n" "https://catsapp.sporton.com.tw$p"
done
```

四組都對才算換版完成。**只驗容器內、不驗經 gateway 的路徑,驗到的是另一個東西。**

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

要保留就照各檔頭的 SQL 先撈一份再 downgrade。

---

## C. 備份

### C.1 備份指令(`backup.sh` 的內容)

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST=/srv/backups/upload-program/$STAMP    # ⏳ 最終目的地待定(Q12 一併確認);先本機
mkdir -p "$DEST"

# 🔴 順序固定:先物件、後資料庫。
# DB 的 storage_key 指向 MinIO 物件:若 DB 快照比物件新,還原後會出現
# 「有 metadata 沒檔案」的下載 500;反向頂多多出孤兒物件,佔空間不壞功能。
# 寧可孤兒,不可懸空。
docker compose exec -T minio sh -c 'tar cf - /data' > "$DEST/minio-data.tar"
docker compose exec -T db pg_dump -U upload_program_user -d upload_program_db -Fc \
  > "$DEST/pg.dump"

# 完整性驗證:不驗的備份 = 薛丁格的備份
pg_restore --list "$DEST/pg.dump" > /dev/null        # dump 可解析
tar tf "$DEST/minio-data.tar" > /dev/null            # tar 可列出
sha256sum "$DEST"/* > "$DEST/SHA256SUMS"
echo "OK: $DEST"
```

- 🔴 備份檔含正式資料與物件,**絕不進 git**(`/srv/backups` 不在 repo 內)
- 頻率:**每日一次** + **每次換版前一次**(§A.2 第 1 步)
- 保留:每日備份留 14 天、換版前備份留到下下個版本穩定
- ⏳ 異地(NAS / 另一台 VM)同步待定,與 Q12(磁碟空間)一併跟平台確認

### C.2 cron(部署當天照抄——設定值不會自己生效)

```cron
# 每日備份(02:30)
30 2 * * *  cd /srv/upload-program && ./backup.sh >> /var/log/upload-backup.log 2>&1
# 稽核紀錄保留期清理(04:00;AUDIT_RETENTION_DAYS 只是給這支腳本讀的,不掛 cron 就是無限成長的個資表)
0 4 * * *   cd /srv/upload-program && docker compose exec -T svc python tools/purge_audit.py --apply >> /var/log/upload-audit-purge.log 2>&1
```

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
| v1.0 | 2026-07-29 04:25 | Claude(Benny 授權) | 初版:A 換版(含 🔴 通知 portal reload 的明文約定與「經 gateway 冒煙才算數」)、B 回滾(含兩個不可逆 migration 的集中清單)、C 備份(先物件後資料庫的順序論證、完整性驗證、cron 含稽核清理)、D 還原演練證據表(毀掉再還原、SHA-256 驗功能、實測 RTO);演練未執行,T27 維持 🔵 |
