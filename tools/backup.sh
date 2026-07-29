#!/usr/bin/env bash
# upload-program 備份腳本(T27 / runbook §C.1)
#
# v2(2026-07-29 首次上線實測修正):
#   🐛 minio 映像檔(UBI micro)內沒有 tar → 改用 `docker cp`(由 Docker daemon
#      執行,不依賴容器內任何工具),主機側再打包。
#   🐛 完整性驗證原用主機的 pg_restore → 主機未必裝 postgresql-client →
#      改用 db 容器內的 pg_restore 讀 stdin。
#
# 🔴 順序固定:先物件、後資料庫。
# DB 的 storage_key 指向 MinIO 物件:若 DB 快照比物件新,還原後會出現
# 「有 metadata 沒檔案」的下載 500;反向頂多多出孤兒物件,佔空間不壞功能。
# 寧可孤兒,不可懸空。
#
# 用法:在部署目錄(有 docker-compose.yml 與 .env 的地方)執行:
#   ./backup.sh                                   # 目的地預設 /srv/backups/upload-program
#   BACKUP_ROOT=/home/deploy/upload-backups ./backup.sh   # 或用環境變數指定
# cron(見 runbook §C.2;部署目錄依 VM 慣例為 /opt/upload-program):
#   30 2 * * *  cd /opt/upload-program && BACKUP_ROOT=/home/deploy/upload-backups ./backup.sh >> /var/log/upload-backup.log 2>&1
#
# 🔴 備份檔含正式資料與物件,絕不進 git;備份目錄不在 repo 內。
set -euo pipefail

STAMP=$(date +%Y%m%d-%H%M%S)
DEST=${BACKUP_ROOT:-/srv/backups/upload-program}/$STAMP
mkdir -p "$DEST"

# 1) 物件:docker cp 由 daemon 執行,容器內不需要 tar
docker cp "$(docker compose ps -q minio)":/data "$DEST/minio-data"
tar cf "$DEST/minio-data.tar" -C "$DEST" minio-data
rm -rf "$DEST/minio-data"

# 2) 資料庫(自訂格式,pg_restore 可選擇性還原)
docker compose exec -T db pg_dump -U upload_program_user -d upload_program_db -Fc \
  > "$DEST/pg.dump"

# 3) 完整性驗證:不驗的備份 = 薛丁格的備份(pg_restore 用 db 容器內的)
docker compose exec -T db pg_restore --list < "$DEST/pg.dump" > /dev/null
tar tf "$DEST/minio-data.tar" > /dev/null
( cd "$DEST" && sha256sum ./* > SHA256SUMS )

# 4) 保留期:每日備份留 14 天(換版前手動備份不受此限,自行清理)
find "${BACKUP_ROOT:-/srv/backups/upload-program}" -maxdepth 1 -mindepth 1 -type d \
  -mtime +14 -exec rm -rf {} + 2>/dev/null || true

echo "OK: $DEST"
