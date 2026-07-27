"""projects 加 quota_tier 欄位(F17 容量級距)

Revision ID: 0003_project_quota_tier
Revises: 0002_project_tags
Create Date: 2026-07-27

🟡 加欄位:`projects` 新增 `quota_tier`,**不動任何既有欄位**。
`server_default='standard'` 讓既有列自動補上預設值,不需要另外的資料回填。

⚠️ **與 0002(加表)的關鍵差別:`downgrade()` 會刪掉欄位,連同裡面的資料。**
回滾後所有專案的級距設定消失、一律退回標準級距;曾調成 extended 的專案
會立刻無法上傳新檔(既有檔案仍在,仍可下載)。這不是資料損毀,但**是可感知的功能倒退**。
回滾前若要保留,先撈一份:
    SELECT slug, quota_tier FROM projects WHERE quota_tier <> 'standard';

規約 §7.5:本 migration 已實際跑過 up → down → up 雙向演練,
並確認 downgrade 後 `projects` 的其他欄位與資料列數不變。

用 `batch_alter_table` 而非直接 add/drop column:SQLite(測試環境)對
ALTER TABLE 的支援有限,batch 模式會在需要時改用「建新表→搬資料→換名」的做法;
PostgreSQL 上則直接對應原生 ALTER,沒有額外成本。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_project_quota_tier"
down_revision: str | None = "0002_project_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "quota_tier",
                # native_enum=False → VARCHAR,與 models._enum 的做法一致:
                # PostgreSQL 與 SQLite 都能跑,日後加級距也不必動 PostgreSQL 的 ENUM 型別。
                sa.Enum("standard", "extended", name="quota_tier", native_enum=False),
                nullable=False,
                server_default="standard",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("quota_tier")
