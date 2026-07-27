"""新增 project_tags(F42 標籤分類)

Revision ID: 0002_project_tags
Revises: 0001_initial
Create Date: 2026-07-27

🟡 加表:只新增 `project_tags`,**不動任何既有表的欄位**。
`downgrade()` 直接 drop table——既有資料不受影響,回滾唯一的損失是標籤本身,
而標籤就是這個功能的全部內容。

規約 §7.5:本 migration 已實際跑過 up → down → up 雙向演練。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_project_tags"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 32 字元上限與 schema 層的驗證一致;存的一律是正規化後(小寫、去空白)的值
        sa.Column("tag", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "tag", name="uq_project_tag"),
    )
    op.create_index("ix_project_tags_project_id", "project_tags", ["project_id"])
    # 依標籤篩選與 SELECT DISTINCT tag 都靠這個索引
    op.create_index("ix_project_tags_tag", "project_tags", ["tag"])


def downgrade() -> None:
    op.drop_index("ix_project_tags_tag", table_name="project_tags")
    op.drop_index("ix_project_tags_project_id", table_name="project_tags")
    op.drop_table("project_tags")
