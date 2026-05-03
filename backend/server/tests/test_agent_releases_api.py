"""Тесты для GET /api/v1/agent-releases."""

import pytest
from httpx import AsyncClient

from server.api import agent_releases as agent_releases_api
from shared.schemas import AgentRelease, AgentReleasesResponse


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Каждый тест начинается с пустого кеша — иначе прогоны протекают."""
    agent_releases_api._cache.clear()


async def test_returns_filtered_and_sorted_releases(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(*, repo: str) -> AgentReleasesResponse:
        return AgentReleasesResponse(
            releases=[
                AgentRelease(
                    tag="agent-v0.2.0",
                    version="0.2.0",
                    name="agent v0.2.0",
                    published_at="2026-05-03T12:00:00Z",
                    wheel_url="https://example.com/v0.2.0/waygate_agent-py3-none-any.whl",
                ),
                AgentRelease(
                    tag="agent-v0.1.17",
                    version="0.1.17",
                    name="agent v0.1.17",
                    published_at="2026-04-01T12:00:00Z",
                    wheel_url="https://example.com/v0.1.17/waygate_agent-py3-none-any.whl",
                ),
            ],
        )

    monkeypatch.setattr(agent_releases_api, "_fetch_from_github", fake_fetch)

    response = await client.get("/api/v1/agent-releases")
    assert response.status_code == 200
    body = response.json()
    assert len(body["releases"]) == 2
    assert body["releases"][0]["tag"] == "agent-v0.2.0"
    assert body["releases"][0]["version"] == "0.2.0"


async def test_caches_for_5_minutes(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Второй запрос подряд не должен дёргать GitHub."""
    call_count = {"n": 0}

    async def fake_fetch(*, repo: str) -> AgentReleasesResponse:
        call_count["n"] += 1
        return AgentReleasesResponse(releases=[])

    monkeypatch.setattr(agent_releases_api, "_fetch_from_github", fake_fetch)

    await client.get("/api/v1/agent-releases")
    await client.get("/api/v1/agent-releases")
    assert call_count["n"] == 1


async def test_returns_502_if_github_down_and_no_cache(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    async def fake_fetch(*, repo: str) -> AgentReleasesResponse:
        raise aiohttp.ClientError("connection refused")

    monkeypatch.setattr(agent_releases_api, "_fetch_from_github", fake_fetch)

    response = await client.get("/api/v1/agent-releases")
    assert response.status_code == 502
