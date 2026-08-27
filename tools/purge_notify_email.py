"""整批清除通知用信箱快取(T99 / 契約 §4.2a L1b 沿用 L1 第 7 條的清除義務)。

用法:docker compose exec svc python tools/purge_notify_email.py

為什麼是「整批清空」而不是「挑孤兒清」:理由與 `purge_name_cache.py` 逐字相同 ——
條文要求清除工具必須涵蓋「IdP 已無此帳號」的孤兒快取,而本服務沒有(也不該有)
查詢 IdP 全帳號的權限;整批清空是**不需要任何 IdP 權限就必然涵蓋孤兒**的做法。
仍在職的人下次登入自動回填,成本只是那段期間不會收到通知信。

🔴 這支工具的存在本身就是 L1b 可以落地的條件之一,不是附加功能。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.models import User  # noqa: E402


async def purge_notify_email(session: AsyncSession) -> int:
    """把所有 `notify_email` 清成 NULL;回傳受影響的列數。

    參數:session。回傳:清掉幾列。副作用:UPDATE users(只動這一欄)。
    """
    result = await session.execute(
        update(User).where(User.notify_email.is_not(None)).values(notify_email=None)
    )
    await session.commit()
    return result.rowcount or 0


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as session:
        cleared = await purge_notify_email(session)
    await engine.dispose()
    print(f"已清除 {cleared} 筆通知用信箱快取(仍在職者下次登入自動回填)")


if __name__ == "__main__":
    asyncio.run(main())
