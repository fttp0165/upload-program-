"""T99(契約 §4.2a L1b):users 加通知用信箱快取欄位。

Revision ID: 0009_notify_email
Revises: 0008_issue_attachments

🟡 加欄位。nullable、無預設值、不動任何既有資料列。

🔴 **可回滾**(git 紅線):down 只 drop 這一欄。失去的是「快取」,
不是事實來源 —— 真相在 IdP,仍在職的人下次登入自動回填。
這與 0004/0005/0007/0008 那些「失去就補不回來」的 migration 性質完全不同,
所以退版前**不需要**額外備份這一欄。
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_notify_email"
down_revision = "0008_issue_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notify_email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "notify_email")
