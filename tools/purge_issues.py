#!/usr/bin/env python3
"""清除已關閉滿保存期的問題回報(T79)。

比照 `tools/purge_audit.py`:**工具 + cron,不自動跑**——刪資料的東西
不該在應用程式裡自己啟動,那會讓「什麼時候刪了什麼」變成沒人說得清的事。

🔴 預設 **dry-run**:不加 `--yes` 只印出會刪什麼,不動任何資料。
   刪掉的回報**無法回滾**(migration 的 downgrade 救不了已刪的列),只能從備份還原。

用法:
    python tools/purge_issues.py                 # 只看會刪什麼
    python tools/purge_issues.py --yes           # 真的刪
    python tools/purge_issues.py --days 180 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models import Issue, IssueStatus  # noqa: E402

DEFAULT_RETENTION_DAYS = 365

# 只有這兩種狀態算「結案」。resolved 不算——使用者還有機會說「還是不行」。
CLOSED_STATES = (IssueStatus.closed, IssueStatus.wontfix)


async def purge_closed_issues(session, storage, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """刪除已關閉超過保存期的回報(含討論串與附件物件)。

    參數:session 資料庫連線、storage 物件儲存、retention_days 保存天數。
    回傳:刪除的回報件數。
    副作用:🔴 **刪資料庫列與物件儲存內容**;呼叫端負責 commit。
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    rows = (
        (
            await session.execute(
                select(Issue).where(Issue.status.in_(CLOSED_STATES), Issue.closed_at <= cutoff)
            )
        )
        .scalars()
        .all()
    )

    for issue in rows:
        # 物件先刪:資料庫列刪掉後就沒有 storage_key 可查了,順序反過來會留下孤兒物件。
        if storage is not None:
            await storage.delete_prefix(f"issues/{issue.id}/")
        # 討論串與附件紀錄由 CASCADE 一併移除(見 models 的 relationship 設定)。
        await session.delete(issue)

    return len(rows)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="清除已關閉滿保存期的問題回報")
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS, help="保存天數")
    parser.add_argument("--yes", action="store_true", help="真的刪除(否則只是預演)")
    args = parser.parse_args()

    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.storage import ObjectStorage

    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    storage = ObjectStorage(settings) if args.yes else None

    async with sessionmaker() as session:
        if not args.yes:
            cutoff = datetime.now(UTC) - timedelta(days=args.days)
            rows = (
                (
                    await session.execute(
                        select(Issue).where(
                            Issue.status.in_(CLOSED_STATES), Issue.closed_at <= cutoff
                        )
                    )
                )
                .scalars()
                .all()
            )
            print(f"[預演] 會刪除 {len(rows)} 件已關閉超過 {args.days} 天的回報。")
            for issue in rows:
                print(f"  - {issue.id} {issue.title[:40]}(關閉於 {issue.closed_at})")
            print("加上 --yes 才會真的刪除。")
            return 0

        removed = await purge_closed_issues(session, storage, retention_days=args.days)
        await session.commit()
        print(f"已刪除 {removed} 件回報(含討論串與附件)。")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
