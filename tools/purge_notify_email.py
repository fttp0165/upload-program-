"""清除通知信箱快取(T102 / 契約 §4.2b 第 7 條的清除義務)。

用法:
    docker compose exec svc python tools/purge_notify_email.py --all         # 整批清空
    docker compose exec svc python tools/purge_notify_email.py --sub <sub>  # 個別清除

不帶參數即拒絕執行——清除是有後果的動作,不給「不小心跑到」留路。

為什麼整批是「清空全部」而不是「挑孤兒清」:條文要求清除工具必須涵蓋
「IdP 已無此帳號」的孤兒快取,但本服務依 §4.2a 第 1 條不得呼叫 Admin API,
無從判斷誰是孤兒——整批清空是不需要任何 IdP 權限就必然涵蓋孤兒的做法
(與 tools/purge_name_cache.py 同一個論證)。仍在職的人下次登入自動回填,
成本只是他要重按一次訂閱前的等待;訂閱開關(review_email_opt_in)**不動**,
清的是投遞位址,不是使用者的意願。

`--sub` 供「個別使用者的刪除請求須能即時清除」(§4.2b 第 7 條後半)。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models import User  # noqa: E402


async def purge(session: AsyncSession, sub: str | None = None) -> int:
    """清除通知信箱快取。

    參數:session;sub 為 None 時整批清空,否則只清該使用者。
    回傳:實際清掉的列數(本來就是 NULL 的不算)。副作用:UPDATE users 並 commit。
    """
    stmt = update(User).where(User.notify_email_cache.is_not(None)).values(notify_email_cache=None)
    if sub is not None:
        stmt = stmt.where(User.sub == sub)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


async def main() -> None:
    parser = argparse.ArgumentParser(description="清除通知信箱快取(§4.2b 第 7 條)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="整批清空(必然涵蓋孤兒)")
    group.add_argument("--sub", help="只清這個 sub 的快取(個別刪除請求)")
    args = parser.parse_args()

    # 匯入放這裡:Settings() 讀 .env,測試直接 import purge() 時不需要環境變數。
    from app.config import Settings
    from app.db import create_engine, create_sessionmaker

    settings = Settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            cleared = await purge(session, sub=None if args.all else args.sub)
            print(f"已清除 {cleared} 筆通知信箱快取")
            if not args.all and cleared == 0:
                print("⚠ 該 sub 沒有快取(或不存在)——沒有東西被改動")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
