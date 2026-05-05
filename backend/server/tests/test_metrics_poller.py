"""Tests for metrics_poller — parallel fetch (BACKLOG A2)."""

import asyncio
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.models import MetricsPoint, Server
from server.tasks import metrics_poller
from shared.schemas import MetricsSnapshot


class _SlowAgent:
    """Имитирует медленный /v1/metrics с искусственной задержкой."""

    def __init__(self, *, delay: float):
        self._delay = delay

    async def metrics(self) -> MetricsSnapshot:
        await asyncio.sleep(self._delay)
        return MetricsSnapshot(timestamp=datetime.now(tz=UTC), tunnels=[])


@pytest.fixture
def fake_broadcaster(monkeypatch):
    events: list[object] = []

    class _FakeManager:
        async def broadcast(self, *, event):
            events.append(event)

    fake = _FakeManager()
    monkeypatch.setattr(metrics_poller, "get_manager", lambda: fake)
    return events


async def _seed_servers(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    count: int,
) -> None:
    async with session_maker() as session:
        for index in range(count):
            session.add(
                Server(
                    host=f"10.0.0.{index + 1}",
                    port=7743,
                    name=f"edge-{index}",
                    token="tok",
                ),
            )
        await session.commit()


async def test_poll_once_runs_fetches_in_parallel(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
) -> None:
    """5 серверов с задержкой 0.2s — parallel должен уложиться в ~0.3s, не 1.0s.

    Регрессия #A2: раньше `for server in servers: await _poll_server(...)` —
    с 50 серверами и одним зависшим агентом весь цикл стоял на нём. После
    `asyncio.gather` per-server delays суммируются как max, а не как sum.
    """
    await _seed_servers(session_maker, count=5)
    delay = 0.2
    monkeypatch.setattr(metrics_poller, "AgentClient", lambda **_kw: _SlowAgent(delay=delay))

    started = time.monotonic()
    async with session_maker() as session:
        await metrics_poller.poll_once(session=session)
        await session.commit()
    elapsed = time.monotonic() - started

    # Sequential = 5 × 0.2s = 1.0s; parallel ~0.2s + overhead. Cutoff 0.6s — с
    # запасом на CI jitter, но всё ещё ловит регрессию (sequential = 1.0+).
    assert elapsed < 0.6, f"poll_once занял {elapsed:.2f}s — последовательный fetch?"

    async with session_maker() as session:
        rows = (await session.execute(select(MetricsPoint))).scalars().all()
        assert len(rows) == 5
    assert len(fake_broadcaster) == 5


async def test_poll_once_swallows_one_agent_failure(
    session_maker: async_sessionmaker[AsyncSession],
    fake_broadcaster,
    monkeypatch,
) -> None:
    """Один из агентов падает — остальные всё равно опрашиваются."""
    await _seed_servers(session_maker, count=3)

    call_count = 0

    class _FlakyAgent:
        async def metrics(self) -> MetricsSnapshot:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated agent crash")
            return MetricsSnapshot(timestamp=datetime.now(tz=UTC), tunnels=[])

    monkeypatch.setattr(metrics_poller, "AgentClient", lambda **_kw: _FlakyAgent())

    async with session_maker() as session:
        await metrics_poller.poll_once(session=session)
        await session.commit()

    # 3 агента, один упал → 2 точки в БД и 2 broadcast'а.
    async with session_maker() as session:
        rows = (await session.execute(select(MetricsPoint))).scalars().all()
        assert len(rows) == 2
    assert len(fake_broadcaster) == 2
