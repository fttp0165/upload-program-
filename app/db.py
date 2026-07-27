"""資料庫:非同步 SQLAlchemy;連線字串走 DATABASE_URL,連線池上限 20(平台規約)。"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import Settings


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict = {"echo": False, "pool_pre_ping": True}
    # SQLite(測試用)不支援連線池參數。
    if not settings.database_url.startswith("sqlite"):
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
    return create_async_engine(settings.database_url, **kwargs)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 相依:每個請求一個 session,結束自動關閉。

    🐛 根本原因(T50):`request` 原本沒有型別註解,FastAPI 便把它當成**查詢參數**,
    導致所有需要 DB 的端點一律回 422「query.request Field required」。
    註解不是可有可無的風格問題——FastAPI 靠它決定參數要從哪裡取。
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with factory() as session:
        yield session
