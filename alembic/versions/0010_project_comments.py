"""T103:專案留言板——project_comments。

🟡 **加一張表,不動既有任何一列。**

🔴 backward = DROP TABLE:**全部留言會消失且無法重建**,與 `0007_issues` 同一類。
留言是使用者累積的內容,不是可以重算的衍生資料。已寫進 runbook §B。

revision id 用檔名全名(v0.1.3 事故的結論)。
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_project_comments"
down_revision = "0009_release_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # CASCADE:專案刪掉時留言一起走,不留孤兒。
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT:與既有慣例一致——留言還在就不讓使用者被硬刪。
        sa.Column(
            "author_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_comments_project_id", "project_comments", ["project_id"])
    # 留言一律「依專案取、依時間排」,複合索引一次涵蓋。
    op.create_index(
        "ix_project_comments_project_created", "project_comments", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_comments_project_created", table_name="project_comments")
    op.drop_index("ix_project_comments_project_id", table_name="project_comments")
    op.drop_table("project_comments")
