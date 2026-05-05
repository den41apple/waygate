"""Tests for app lifespan — bootstrap admin from ENV + background tasks (BACKLOG C2).

Регрессия: lifespan вызывается до того как фронт подключится. Если
`bootstrap_admin_from_env` падает или поллер не стартует — silent failure
в проде до первого 500 (или до полной потери метрик).
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.auth.bootstrap import bootstrap_admin_from_env
from server.models import User


async def test_bootstrap_creates_admin_from_env(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    """ENV задан, в БД пусто → bootstrap должен создать User."""
    from server.config import settings

    monkeypatch.setattr(settings, "admin_username", "lifespan-admin")
    monkeypatch.setattr(settings, "admin_password", "lifespan-pass-123")

    await bootstrap_admin_from_env(session_maker=session_maker)

    async with session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1
    assert users[0].username == "lifespan-admin"
    assert users[0].is_admin is True
    assert users[0].password_hash != "lifespan-pass-123"  # bcrypt-хеш, не plaintext


async def test_bootstrap_idempotent(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    """Повторный запуск bootstrap'а с тем же ENV не дублирует юзера."""
    from server.config import settings

    monkeypatch.setattr(settings, "admin_username", "ide-admin")
    monkeypatch.setattr(settings, "admin_password", "ide-pass-123")

    await bootstrap_admin_from_env(session_maker=session_maker)
    await bootstrap_admin_from_env(session_maker=session_maker)

    async with session_maker() as session:
        result = await session.execute(select(User).where(User.username == "ide-admin"))
        users = result.scalars().all()
    assert len(users) == 1


async def test_bootstrap_skips_when_env_missing_and_users_exist(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
    admin_user: User,  # фикстура из conftest добавляет одного юзера
) -> None:
    """Если в БД уже есть юзеры и ENV пусто — bootstrap не падает и не
    создаёт ничего. Самый частый случай в проде после первого старта."""
    from server.config import settings

    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")

    await bootstrap_admin_from_env(session_maker=session_maker)
    # admin_user уже создан фикстурой — должен остаться единственным.
    async with session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1


async def test_lifespan_starts_and_stops_background_tasks(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    """lifespan создаёт poller_task и healthcheck_task, и cancel'ит их при выходе.

    Без этого теста легко регрессить: убрать `await task` в finally → задача
    остаётся висеть, при следующем старте появляется зомби-копия.
    """
    from server.main import create_app
    from server.tasks import healthcheck, metrics_poller

    # Останавливаем реальные циклы — нам нужно проверить только lifecycle.
    started: list[str] = []
    stopped: list[str] = []

    async def fake_poller_loop(*, session_maker, interval_seconds=None):
        started.append("poller")
        try:
            # Используем Event чтобы не дёргать sleep'ы — task снимется через
            # task.cancel() в lifespan finally, ASYNC110 от ruff успокаивается.
            await asyncio.Event().wait()
        finally:
            stopped.append("poller")

    async def fake_healthcheck_loop(*, session_maker, interval_seconds=None):
        started.append("healthcheck")
        try:
            # Используем Event чтобы не дёргать sleep'ы — task снимется через
            # task.cancel() в lifespan finally, ASYNC110 от ruff успокаивается.
            await asyncio.Event().wait()
        finally:
            stopped.append("healthcheck")

    monkeypatch.setattr(metrics_poller, "metrics_poller_loop", fake_poller_loop)
    monkeypatch.setattr(healthcheck, "healthcheck_loop", fake_healthcheck_loop)
    # Также подменяем имена, импортированные напрямую в server.main.
    import server.main

    monkeypatch.setattr(server.main, "metrics_poller_loop", fake_poller_loop)
    monkeypatch.setattr(server.main, "healthcheck_loop", fake_healthcheck_loop)
    # БД в conftest подменена на in-memory — bootstrap не нужен в проверке.
    monkeypatch.setattr(server.main, "bootstrap_admin_from_env", lambda **_kw: asyncio.sleep(0))
    monkeypatch.setattr(server.main, "get_session_maker", lambda: session_maker)

    app = create_app()
    async with app.router.lifespan_context(app):
        # Дать таскам стартовать.
        await asyncio.sleep(0.05)
        assert "poller" in started
        assert "healthcheck" in started
        # Таски ещё бегут.
        assert app.state.server.poller_task is not None
        assert not app.state.server.poller_task.done()
        assert not app.state.server.healthcheck_task.done()

    # После выхода из lifespan — обе таски должны быть отменены.
    assert "poller" in stopped
    assert "healthcheck" in stopped
    assert app.state.server.poller_task.done()
    assert app.state.server.healthcheck_task.done()
