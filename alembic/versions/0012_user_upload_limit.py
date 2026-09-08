"""T141:users 加「這個帳號的單檔上限」欄位。

Revision ID: 0012_user_upload_limit
Revises: 0011_project_comments

🟡 加欄位。nullable、無預設值、不動任何既有資料列 ——
既有帳號一律 NULL,而 NULL 的語意是「沿用全站上限」,故**行為逐字不變**。

🔴 **可回滾,但與 0009 那種「快取欄位」不同**:這一欄不是快取,
它是**管理員的決定**。down 之後那些決定就消失了,而它們不在任何其他地方 ——
所以退版前應先記錄哪些帳號有個別上限:

    SELECT sub, max_artifact_bytes FROM users WHERE max_artifact_bytes IS NOT NULL;

⚠ 這句話寫在這裡而不只寫在計畫書裡,是因為**執行 downgrade 的人讀的是這個檔案**。
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_user_upload_limit"
down_revision = "0011_project_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("max_artifact_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "max_artifact_bytes")
