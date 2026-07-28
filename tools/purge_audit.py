#!/usr/bin/env python
"""清除超過保留期的稽核紀錄(F54 / T38)。

🔴 **這支工具存在的理由是 T37 開的支票。** T37(下載次數統計)刻意不做下載事件表,
當時寫下的理由是「誰下載了什麼」屬於稽核,而稽核「有自己的保存期限與存取權限」。
沒有清除手段的話,那句話就只是把一張無限長大的個資表推給未來。

⚠️ **本服務沒有排程器**,這支工具不會自己跑。部署時掛進 VM 的 cron,例如每日一次:

    0 4 * * *  cd /srv/upload-program && .venv/bin/python tools/purge_audit.py --apply

用法:

    python tools/purge_audit.py            # dry-run:只印出會刪幾筆,不刪
    python tools/purge_audit.py --apply    # 真的刪
    python tools/purge_audit.py --days 90  # 覆寫保留天數(預設讀 AUDIT_RETENTION_DAYS)

🔴 **預設是 dry-run。** 刪稽核紀錄不可逆,而且刪掉的正是「誰做了什麼」——
預設值不該是刪。要真的刪必須明確加上 `--apply`。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit import purge_expired  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402


async def _main(days: int | None, apply: bool) -> int:
    """回傳筆數。副作用:`--apply` 時對 `audit_events` 執行 DELETE。"""
    settings = Settings()
    retention = days if days is not None else settings.audit_retention_days

    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            count = await purge_expired(session, retention, dry_run=not apply)
    finally:
        await engine.dispose()

    verb = "已刪除" if apply else "將會刪除(dry-run,未實際刪除)"
    print(f"保留 {retention} 天:{verb} {count} 筆稽核紀錄")
    if not apply and count:
        print("要真的刪除請加上 --apply")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清除超過保留期的稽核紀錄")
    parser.add_argument("--days", type=int, default=None, help="保留天數(預設讀設定值)")
    parser.add_argument("--apply", action="store_true", help="真的刪除;不加則只試算")
    args = parser.parse_args()
    asyncio.run(_main(args.days, args.apply))
