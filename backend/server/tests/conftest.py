from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from server import models  # noqa: F401  — регистрирует таблицы в SQLModel.metadata
from server.auth.passwords import hash_password
from server.auth.session import sign_session_token
from server.db import get_session
from server.main import create_app
from server.models import User

_TEST_ADMIN_USERNAME = "test-admin"
_TEST_ADMIN_PASSWORD = "test-admin-pass-123"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite на каждый тест.

    StaticPool обязателен: иначе разные коннекты разделяют разные in-memory БД,
    и таблицы созданные в одной не видны из другой.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def admin_user(session_maker: async_sessionmaker[AsyncSession]) -> User:
    """Создаёт тестового админа в БД и возвращает его."""
    async with session_maker() as session:
        user = User(
            username=_TEST_ADMIN_USERNAME,
            password_hash=hash_password(plaintext=_TEST_ADMIN_PASSWORD),
            is_admin=True,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture
async def client(
    session_maker: async_sessionmaker[AsyncSession],
    admin_user: User,
) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент к приложению с переопределённым get_session и Authorization-header.

    По умолчанию все запросы идут от лица тестового админа. Если тесту нужна
    проверка 401 — снимать Authorization header вручную в самом тесте.
    Lifespan не запускаем — фоновому poller'у в тестах делать нечего.
    """
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session

    token = sign_session_token(user=admin_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as async_client:
        yield async_client


@pytest.fixture
async def anon_client(session_maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    """Клиент БЕЗ Authorization-header — для тестов 401 на закрытых эндпоинтах."""
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
