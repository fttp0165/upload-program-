"""程式碼(source)下載的門禁(T142;計畫書《設計_程式碼下載需作者核准》)。

**判準只寫在這裡一份**,兩條下載路徑共用 —— 那段程式碼的檔頭早就寫著同一個
道理(「安全標頭絕不能因為換了一條路徑就鬆掉」),門禁沿用它。

🔴 判準:`kind == source` 的下載,只有四種人可以 ——
① 平台管理員 ② 專案成員(owner / maintainer / viewer)
③ 已被作者核准的申請人 ④ **沒有第四種**。

⚠ `viewer` 也能下載程式碼:那是既有語意(能讀專案就能取得其內容),
本任務**不改它**。要更嚴屬另一個決定,不夾帶。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ArtifactKind,
    Project,
    ProjectRole,
    SourceAccessRequest,
    SourceAccessStatus,
    User,
)


def is_locked_kind(kind: ArtifactKind) -> bool:
    """這一類檔案要不要門禁。

    🔴 只有 `source`(2026-09-08 裁示)。說明與執行檔維持現狀 ——
    鎖到說明文件會讓人連「這是什麼」都看不到,通常直接讓人放棄。
    """
    return kind is ArtifactKind.source


async def has_source_grant(session: AsyncSession, project: Project, user: User) -> bool:
    """這個人有沒有「已核准」的程式碼下載授權。副作用:無(唯讀)。"""
    row = (
        await session.execute(
            select(SourceAccessRequest.status).where(
                SourceAccessRequest.project_id == project.id,
                SourceAccessRequest.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    return row is SourceAccessStatus.approved


async def my_request(
    session: AsyncSession, project: Project, user: User
) -> SourceAccessRequest | None:
    """這個人對這個專案的申請(最多一筆 —— 唯一約束保證)。副作用:無。"""
    return (
        await session.execute(
            select(SourceAccessRequest).where(
                SourceAccessRequest.project_id == project.id,
                SourceAccessRequest.user_id == user.id,
            )
        )
    ).scalar_one_or_none()


async def may_download(
    session: AsyncSession,
    project: Project,
    user: User,
    kind: ArtifactKind,
    role: ProjectRole | None,
) -> bool:
    """能不能下載這一個檔案。

    參數:project、user 當事人、kind 檔案類別、role 當事人在該專案的角色
    (`None` = 非成員;由呼叫端以 `project_role()` 備好,本函式不重查)。
    回傳:能否下載。副作用:唯讀(可能查一次授權表)。

    🔴 **順序刻意是「先放行再查表」**:管理員與成員不必付一次查詢的代價,
    而他們是絕大多數的下載來源。
    """
    if not is_locked_kind(kind):
        return True
    if user.is_admin or role is not None:
        return True
    return await has_source_grant(session, project, user)


async def pending_count(session: AsyncSession, project: Project) -> int:
    """這個專案有幾筆待處理的下載申請(給專案頁的提示用)。

    🔴 **為什麼要在專案頁顯示**:平台沒有 email 也沒有推播(既有營運限制),
    作者不會被通知。少了這個數字,申請會安靜地躺在資料庫裡,
    而申請人會以為系統壞了 —— 與 T79 / T123 完全同一個理由。
    """
    from sqlalchemy import func

    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(SourceAccessRequest)
                .where(
                    SourceAccessRequest.project_id == project.id,
                    SourceAccessRequest.status == SourceAccessStatus.pending,
                )
            )
        ).scalar()
        or 0
    )
