"""T77:問題回報系統(第一期)——issues 與 issue_comments 兩張表。

🟡 加表,不動任何既有資料。
🔴 backward = DROP TABLE:**會刪掉使用者已經寫下的回報與討論串**,
   回滾前必須先備份(已列入 runbook 的不可逆清單)。

revision id 用**檔名全名**(v0.1.3 事故的結論:短 id 會讓 migration 鏈在
正式機上找不到 down_revision,症狀是換版當下整個服務起不來)。
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_issues"
down_revision = "0006_display_name_cache"
branch_labels = None
depends_on = None

# 與 models._enum() 一致:VARCHAR + CHECK,PostgreSQL 與 SQLite 都能跑,
# 日後加狀態不必動 PostgreSQL 的 ENUM 型別。
_STATUS = sa.Enum(
    "open",
    "in_progress",
    "resolved",
    "closed",
    "wontfix",
    name="issue_status",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "issues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "reporter_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("status", _STATUS, nullable=False, server_default="open"),
        sa.Column("page_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
        ),
    )
    op.create_index("ix_issues_reporter_id", "issues", ["reporter_id"])
    op.create_index("ix_issues_status_created", "issues", ["status", "created_at"])

    op.create_table(
        "issue_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "issue_id", sa.Uuid(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "author_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("is_staff_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_issue_comments_issue_id", "issue_comments", ["issue_id"])


def downgrade() -> None:
    # 🔴 不可逆:回報內容與討論串會一併消失,只能從備份還原。
    op.drop_index("ix_issue_comments_issue_id", table_name="issue_comments")
    op.drop_table("issue_comments")
    op.drop_index("ix_issues_status_created", table_name="issues")
    op.drop_index("ix_issues_reporter_id", table_name="issues")
    op.drop_table("issues")
