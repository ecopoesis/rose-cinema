from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rose_cinema.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """Dependency for FastAPI — yields an async session."""
    async with async_session() as session:
        yield session
