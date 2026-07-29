#!/usr/bin/env bash
# upload-program 備份腳本(T27 / runbook §C.1)
#
# 🔴 順序固定:先物件、後資料庫。
# DB 的 storage_key 指向 MinIO 物件:若 DB 快照比物件新,還原後會出現
# 「有 metadata 沒檔案」的下載 500;反向頂多多出孤兒物件,佔空間不壞功能。
# 寧可孤兒,不可懸空。
#
# 用法:在部署目錄(有 docker-compose.yml 與 .env 的地方)執行:
#   ./backup.sh
# cron(每日 02:30,見 runbook §C.2):
#   30 2 * * *  cd /srv/upload-program && ./backup.sh >> /var/log/upload-backup.log 2>&1
#
# 🔴 備份檔含正式資料與物件,絕不進 git;/srv/backups 不在 repo 內。
set -euo pipefail

STAMP=$(date +%Y%m%d-%H%M%S)
DEST=${BACKUP_ROOT:-/srv/backups/upload-program}/$STAMP   # ⏳ 異地目的地待定(Q12)
mkdir -p "$DEST"

# 1) 物件(MinIO named volume 內容)
docker compose exec -T minio sh -c 'tar cf - /data' > "$DEST/minio-data.tar"

# 2) 資料庫(自訂格式,pg_restore 可選擇性還原)
docker compose exec -T db pg_dump -U upload_program_user -d upload_program_db -Fc \
  > "$DEST/pg.dump"

# 3) 完整性驗證:不驗的備份 = 薛丁格的備份
pg_restore --list "$DEST/pg.dump" > /dev/null
tar tf "$DEST/minio-data.tar" > /dev/null
( cd "$DEST" && sha256sum ./* > SHA256SUMS )

# 4) 保留期:每日備份留 14 天(換版前手動備份不受此限,自行清理)
find "${BACKUP_ROOT:-/srv/backups/upload-program}" -maxdepth 1 -mindepth 1 -type d \
  -mtime +14 -exec rm -rf {} + 2>/dev/null || true

echo "OK: $DEST"
