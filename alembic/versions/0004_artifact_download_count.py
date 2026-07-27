"""artifacts 加 download_count 欄位(F43 下載次數統計)

Revision ID: 0004_artifact_download_count
Revises: 0003_project_quota_tier
Create Date: 2026-07-27

🟡 加欄位:`artifacts` 新增 `download_count`,**不動任何既有欄位**。
`server_default='0'` 讓既有列自動補 0,不需要資料回填。

⚠️ **`downgrade()` 會刪掉欄位連同累計值,而且不可重建**——
本專案刻意不做下載事件表(那是稽核 T38/F54 的職責,見 models.Artifact 的註釋),
所以沒有原始資料可以重算。回滾前若要保留,先撈一份:

    SELECT id, filename, download_count FROM artifacts WHERE download_count > 0;

規約 §7.5:本 migration 已實際跑過 up → down → up 雙向演練,
並確認 downgrade 後 `artifacts` 的其他欄位與資料列數不變。

`batch_alter_table` 的理由同 0003:SQLite(測試環境)對 ALTER TABLE 的支援有限。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_artifact_download_count"
down_revision: str | None = "0003_project_quota_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(
            sa.Column("download_count", sa.BigInteger(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("download_count")
