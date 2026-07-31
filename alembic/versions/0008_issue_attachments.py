"""T78:問題回報的附件(第二期)——issue_attachments。

🟡 加表,不動既有資料。物件本身存在 MinIO 的 `issues/` 前綴下。
🔴 backward = DROP TABLE:附件**紀錄**會消失,但 MinIO 裡的物件不會隨之刪除
   ——回滾後若要清乾淨,需以 `issues/` 前綴手動清理(已寫進 T78 日誌的回滾段)。

revision id 用檔名全名(v0.1.3 事故的結論)。
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_issue_attachments"
down_revision = "0007_issues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "issue_id", sa.Uuid(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        # 判定出來的型別,不是使用者宣稱的(inline 顯示時當 Content-Type 用)。
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "uploaded_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_issue_attachments_issue_id", "issue_attachments", ["issue_id"])


def downgrade() -> None:
    op.drop_index("ix_issue_attachments_issue_id", table_name="issue_attachments")
    op.drop_table("issue_attachments")
