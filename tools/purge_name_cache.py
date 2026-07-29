"""整批清除顯示名稱快取(T59 / 契約 §4.2a 的清除義務)。

用法:docker compose exec svc python tools/purge_name_cache.py

為什麼是「整批清空」而不是「挑孤兒清」:條文要求清除工具必須涵蓋
「IdP 已無此帳號」的孤兒快取,但本服務沒有(也不該有)查詢 IdP 全帳號的權限
——整批清空是不需要任何 IdP 權限就必然涵蓋孤兒的做法;
仍在職的人下次登入會自動回填,成本只是畫面短暫顯示 sub。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.models import User  # noqa: E402


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            result = await session.execute(
                update(User)
                .where(User.display_name_cache.is_not(None))
                .values(display_name_cache=None)
            )
            await session.commit()
            print(f"已清除 {result.rowcount} 筆顯示名稱快取")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
