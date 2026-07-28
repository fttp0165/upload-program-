"""新增 audit_events 稽核事件表(F54 稽核紀錄)

Revision ID: 0005_audit_events
Revises: 0004_artifact_download_count
Create Date: 2026-07-28

🟡 加表:只新增 `audit_events`,**不修改任何既有表的欄位**,既有資料不動。

🔴 個資邊界(F54 定案時釘住):本表只存 `actor_id`(本地 user id,可回查 `sub`),
**沒有 email / 姓名欄位**。稽核不是繞過個資紅線的後門。

🔴 `action` 刻意是 `String(64)` 而不是帶 CHECK 的 enum:稽核動作的字彙會隨功能
持續成長,把它釘進 schema 等於「每加一個被稽核的動作就要一次 migration」——
而阻力會讓人選擇不記,那是稽核最糟的失效方式(表存在、看起來正常,但漏掉的
動作不會留下任何跡象)。字彙的守門由 `app/audit.py::AuditAction` 與 test_audit.py 負責。

🔴 `actor_id` 用 **RESTRICT** 而非 CASCADE:稽核紀錄不得因為使用者被刪除而消失。
換句話說,有稽核紀錄的使用者就刪不掉——這是刻意的。
`target_id` **不是外鍵**:目標(專案/版本/檔案)可能已被刪除,而「誰刪掉了什麼」
正是稽核最重要的用途;人可讀的定位靠同列的 `target_label` 快照保存。

⚠️ **`downgrade()` 會刪掉整張表連同所有稽核紀錄,而且不可重建**——
沒有任何其他地方保存這些事件(stdout log 會輪替)。回滾前若要保留,先撈一份:

    SELECT * FROM audit_events ORDER BY occurred_at;

規約 §7.5:本 migration 已實際跑過 up → down → up 雙向演練,
並確認 downgrade 後其他表的欄位與資料列數不變。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_audit_events"
down_revision: str | None = "0004_artifact_download_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("target_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_action", "audit_events", ["action"])
    op.create_index("ix_audit_target", "audit_events", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_target", table_name="audit_events")
    op.drop_index("ix_audit_action", table_name="audit_events")
    op.drop_index("ix_audit_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_table("audit_events")
