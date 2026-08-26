"""T102 發布審核:releases 加審核欄位、users 加通知信箱快取與訂閱開關。

🟡 加欄位,**不 UPDATE 任何既有列**(Benny 裁示:既有已發布版本視為已核准)——
`status` 欄是 VARCHAR(native_enum=False、無 CHECK),新值 `in_review` 不需改 schema;
既有 published 列原樣保留,其 reviewed_* 留 NULL(當年沒人審過,不偽造審核紀錄)。

backward = 先把 in_review 改回 draft(舊程式不認識這個值,直接 drop 會留下
一批舊版程式讀不懂的狀態),再 drop 六個新欄位。🔴 downgrade 會刪掉審核紀錄
與通知信箱快取,回滾前必備份。
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_release_review"
down_revision = "0008_issue_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 審核軌跡:送審時間、核准/退回者與時刻、退回理由(給作者看的自由文字)。
    # batch 模式:SQLite(演練環境)的 ALTER 不支援加外鍵欄位,batch 以重建表達成;
    # PostgreSQL(正式)下退化為一般 ALTER,行為不變。downgrade 同理。
    with op.batch_alter_table("releases") as batch:
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "reviewed_by_id",
                sa.Uuid(),
                # batch(重建表)模式要求約束具名,匿名 FK 會直接 ValueError。
                sa.ForeignKey("users.id", name="fk_releases_reviewed_by_users"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("review_note", sa.Text(), nullable=False, server_default=""))
    # 契約 §4.2b:通知信箱快取——🔴 刻意無 unique、無 index(第 9 條:不得作查詢鍵)。
    op.add_column("users", sa.Column("notify_email_cache", sa.String(255), nullable=True))
    # §4.2b 第 4/8 條:明示訂閱 + 退訂,預設關。
    op.add_column(
        "users",
        sa.Column(
            "review_email_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    # 🔴 先把待審列退回 draft:回滾後的程式只認識 draft/published,
    # 留著 in_review 會讓那些版本在舊程式眼裡「不 draft 也不 published」,
    # 行為未定義。退回 draft 是對作者最無害的解讀(東西還在,重走舊發布流程)。
    op.execute("UPDATE releases SET status = 'draft' WHERE status = 'in_review'")
    with op.batch_alter_table("releases") as batch:
        batch.drop_column("review_note")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by_id")
        batch.drop_column("submitted_at")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("review_email_opt_in")
        batch.drop_column("notify_email_cache")
