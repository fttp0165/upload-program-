"""T142:程式碼下載需作者核准 —— source_access_requests。

Revision ID: 0013_source_access
Revises: 0012_user_upload_limit

🟡 加表。既有專案、版本、檔案**一列都不用改**。

🔴 **但行為會變**:今天能下載 `source` 的**非成員**,升級後要先申請。
那是需求本身而不是副作用 —— 上線前要先跟使用者講,否則他們只會覺得壞了。

🔴 **downgrade 會失去所有核准紀錄**,而那不在任何其他地方。退版前先匯出:

    \\copy (SELECT p.slug, u.sub, r.status, r.decided_at
            FROM source_access_requests r
            JOIN projects p ON p.id = r.project_id
            JOIN users u ON u.id = r.user_id) TO '/tmp/source_access.csv' CSV HEADER;

⚠ 這句寫在這裡而不只寫在計畫書裡,是因為**執行 downgrade 的人讀的是這個檔案**。
"""

import sqlalchemy as sa

from alembic import op

revision = "0013_source_access"
down_revision = "0012_user_upload_limit"
branch_labels = None
depends_on = None

_STATUS = sa.Enum(
    "pending", "approved", "rejected", "revoked", name="source_access_status"
)


def upgrade() -> None:
    op.create_table(
        "source_access_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", _STATUS, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("decided_reason", sa.String(length=500), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("project_id", "user_id", name="uq_source_access_project_user"),
    )
    op.create_index(
        "ix_source_access_requests_project_id", "source_access_requests", ["project_id"]
    )
    op.create_index("ix_source_access_requests_user_id", "source_access_requests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_source_access_requests_user_id", table_name="source_access_requests")
    op.drop_index("ix_source_access_requests_project_id", table_name="source_access_requests")
    op.drop_table("source_access_requests")
    # 🔴 PostgreSQL 的 enum 型別不會隨表消失,要自己丟掉,
    # 否則再 upgrade 一次會撞「type already exists」——那個錯只在 down→up 時出現,
    # 而只做 up 的人永遠看不到它(所以本專案一律演練 up→down→up)。
    _STATUS.drop(op.get_bind(), checkfirst=True)
