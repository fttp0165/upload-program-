"""初始 schema:users / projects / project_members / releases / artifacts

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-25

🔴 users 表刻意沒有 email / 姓名 / 密碼欄位——個資不落地(SSO 契約 §4.2)。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sub", sa.String(255), nullable=False),
        sa.Column("status", _enum("user_status", "pending", "active", "disabled"), nullable=False),
        sa.Column("platform_role", _enum("platform_role", "member", "admin"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("sub", name="uq_users_sub"),
    )
    op.create_index("ix_users_sub", "users", ["sub"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("visibility", _enum("visibility", "internal", "private"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"])

    op.create_table(
        "project_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", _enum("project_role", "owner", "maintainer", "viewer"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    op.create_table(
        "releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", _enum("release_status", "draft", "published"), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_release_version"),
    )
    op.create_index("ix_releases_project_id", "releases", ["project_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "release_id",
            sa.Uuid(),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", _enum("artifact_kind", "source", "binary", "doc"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("hash_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "upload_status", _enum("upload_status", "pending", "ready", "failed"), nullable=False
        ),
        sa.Column(
            "scan_status",
            _enum("scan_status", "not_scanned", "clean", "infected"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("release_id", "filename", name="uq_artifact_filename"),
    )
    op.create_index("ix_artifacts_release_id", "artifacts", ["release_id"])
    op.create_index("ix_artifact_sha256", "artifacts", ["sha256"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("releases")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
