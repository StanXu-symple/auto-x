from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.mysql_dsn,
    pool_pre_ping=True,
    pool_recycle=settings.mysql_pool_recycle_seconds,
    pool_size=settings.mysql_pool_size,
    max_overflow=settings.mysql_max_overflow,
    echo=settings.debug,
)
AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
