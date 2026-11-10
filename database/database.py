from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseManager:
    """إدارة اتصال قاعدة البيانات غير المتزامن مع تفعيل SQLite WAL Mode."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=False,
            future=True,
            connect_args={"timeout": 30} if "sqlite" in database_url else {},
        )

        if "sqlite" in database_url:
            @event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.close()

        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """توفير جلسة قاعدة بيانات مع إدارة المعاملات الذرية (Commit/Rollback)."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """إغلاق محرك قاعدة البيانات بأمان عند إيقاف النظام."""
        await self.engine.dispose()
