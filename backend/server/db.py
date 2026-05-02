from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.config import settings


def make_engine(*, db_url: str | None = None) -> AsyncEngine:
    """Создаёт async-engine. Допускает override URL для тестов / альтернативных BD."""
    return create_async_engine(db_url or settings.db_url, echo=False, future=True)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Production-engine, ленивый singleton. В тестах не вызывается — там dependency_overrides."""
    return make_engine()


@lru_cache(maxsize=1)
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: открывает сессию на запрос, коммитит при успехе."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
