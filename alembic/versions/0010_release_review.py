"""T123:發布審核——releases 加審核欄位,並放寬 status 的長度。

🟡 **加欄位 + 放寬一個既有欄位的長度。不 UPDATE 任何一列。**

為什麼不用改既有資料:`published` 的語意刻意維持「可下載」,新的送審走
`pending_review`。既有已發布的版本因此原地不動,自然滿足裁示
「既有已發布視為已核准」——這是設計換來的,不是省略。

🔴 **`status` 的長度必須放寬,而這件事 SQLite 測不出來。**
`_enum(..., native_enum=False)` 存成 VARCHAR,長度取**最長值**:
原本只有 `draft`(5)與 `published`(9)→ VARCHAR(9)。
新值 `pending_review` 是 **14 個字元**,PostgreSQL 會直接拒絕寫入,
而 SQLite 不檢查 VARCHAR 長度、測試會全綠。
本欄改為 VARCHAR(20),為日後再加狀態留一點餘裕。

🔴 backward 的資料代價:downgrade 會把 `pending_review` 的版本一律當回
`draft`(它們確實還沒被核准過,回到「未送審」是唯一說得通的狀態),
並刪掉三個審核欄位——**退回理由會一起消失**。
退版前應先把待審的版本處置掉(核准或退回)。已寫進 runbook §B。

revision id 用檔名全名(v0.1.3 事故的結論)。
"""

import sqlalchemy as sa

from alembic import op

# 🐛 up→down→up 演練當場抓到:**SQLite 不支援 `ALTER COLUMN ... TYPE`**,
# 直接 `op.alter_column` 會在 SQLite 上以語法錯誤炸掉(PostgreSQL 沒事)。
# 測試套件不跑 migration(用 create_all),所以這個錯**測試全綠也照樣存在**——
# 只有真的演練一次才看得到。改用 `op.batch_alter_table`:SQLite 走「建新表→搬資料
# →換名」的重建路徑,PostgreSQL 則原樣轉成一般 ALTER。

# 🔴 2026-08-31 合併 PR #33 時讓號:原本是 `0009_release_review`,down_revision 指向
#    `0008_issue_attachments`。而 main 上已有 `0009_notify_email`(T99)**且已在正式機
#    執行過** —— 兩個 0009 接同一個父節點會讓 alembic 看到兩個 head,`upgrade head` 直接失敗。
#    讓號的一定是還沒上線的這一支;`0009_notify_email` 一個字都不能動
#    (改它等於讓正式機的 alembic_version 指向不存在的 revision)。
revision = "0010_release_review"
down_revision = "0009_notify_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("releases") as batch:
        # 🔴 先放寬長度,否則之後任何一筆 pending_review 都寫不進 PostgreSQL。
        batch.alter_column(
            "status",
            existing_type=sa.String(9),
            type_=sa.String(20),
            existing_nullable=False,
        )
        # 退回理由。裁示要求必填——沒有理由的退回,作者只能猜,然後重傳一模一樣的東西。
        batch.add_column(
            sa.Column("review_note", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            # 🔴 batch 模式下外鍵**必須具名**:SQLite 走建新表→搬資料的重建路徑,
            # 匿名約束搬不過去(alembic 直接拒絕:Constraint must have a name)。
            sa.Column(
                "reviewed_by_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_releases_reviewed_by_id"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # 待審的版本還沒被核准過,回到「未送審」是唯一說得通的狀態。
    # (不能留著 pending_review——欄位縮回 VARCHAR(9) 後那個值放不下。)
    op.execute("UPDATE releases SET status = 'draft' WHERE status = 'pending_review'")
    with op.batch_alter_table("releases") as batch:
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by_id")
        batch.drop_column("review_note")
        batch.alter_column(
            "status",
            existing_type=sa.String(20),
            type_=sa.String(9),
            existing_nullable=False,
        )
