"""清掉「上傳沒成功」的殘骸列(T107 的補救工具)。

用法(🔴 先備份):
    docker compose exec svc python tools/purge_failed_artifacts.py            # 只列出,不刪
    docker compose exec svc python tools/purge_failed_artifacts.py --apply    # 真的刪

為什麼需要它:T107 之前,被 magic bytes 擋下或中途失敗的上傳會留下一列
`upload_status != ready`(0 bytes、SHA-256 空白)。T107 之後不會再產生新的,
但**既有的殘骸不會自己消失** —— 而 T106 讓它們在檢視面隱形之後,
更沒有人會發現它們還在。

🔴 預設 dry-run 而不是預設刪除:這支工具會刪正式資料的列。
「跑了才發現刪錯」在這裡沒有復原路徑(那些列不在任何備份的差異裡,
除非整份備份還原)。**要人多打一個 `--apply`,是刻意的阻力。**

⚠ 一律不碰 `ready` 的列 —— 那是真的檔案。判斷條件寫死在查詢裡,
不接受任何參數放寬。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.models import Artifact, UploadStatus  # noqa: E402
from app.storage import ObjectStorage  # noqa: E402


async def find_residue(session: AsyncSession) -> list[Artifact]:
    """找出所有 `upload_status != ready` 的 artifact 列。

    參數:session。回傳:Artifact 清單(含 release 以便印出人看得懂的位置)。
    副作用:無(唯讀)。
    """
    rows = (
        await session.execute(
            select(Artifact)
            .options(selectinload(Artifact.release))
            .where(Artifact.upload_status != UploadStatus.ready)
            .order_by(Artifact.created_at)
        )
    ).scalars().all()
    return list(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="清掉上傳沒成功的 artifact 殘骸列")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真的刪除(不加這個旗標只會列出,什麼都不動)",
    )
    args = parser.parse_args()

    settings = Settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    storage = ObjectStorage(settings)

    async with sessionmaker() as session:
        residue = await find_residue(session)
        if not residue:
            print("沒有殘骸列,不需要清理。")
            await engine.dispose()
            return

        # 先印出來讓執行的人看得到自己在刪什麼 —— 只印數字的工具沒有人敢按下去。
        print(f"找到 {len(residue)} 列上傳未完成的檔案:")
        for a in residue:
            print(
                f"  {a.filename:40s} {a.upload_status.value:8s} "
                f"{a.size_bytes:>10d} bytes  release={a.release_id}"
            )

        if not args.apply:
            print("\n(dry-run:什麼都沒有刪。確認無誤後加 --apply 再跑一次)")
            await engine.dispose()
            return

        removed = 0
        for a in residue:
            if a.storage_key:
                # best-effort:物件可能根本沒寫成功,刪不掉不該中斷整批清理。
                try:
                    await storage.delete(a.storage_key)
                except Exception:  # noqa: BLE001
                    print(f"  ⚠ 物件刪除失敗(略過):{a.storage_key}")
            await session.delete(a)
            removed += 1
        await session.commit()
        print(f"\n已刪除 {removed} 列。ready 的檔案一列都沒有動。")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
