"""T59(契約 §4.2a L1):users 加顯示名稱快取欄位。

🟡 加欄位(nullable、無預設回填)——既有列一律 NULL,下次登入才由 token 覆寫。
backward = drop column,雙向皆不動其他資料。
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name_cache", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name_cache")
