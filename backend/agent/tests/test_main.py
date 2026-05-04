import os
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

assert os.environ.get("TOKEN"), "conftest должен подкинуть TOKEN до импорта приложения"

from agent import main as agent_main  # noqa: E402
from agent import tunnels  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.main import app  # noqa: E402
from shared.schemas import (  # noqa: E402
    ApplyRulesResponse,
    AwgContainerInfo,
    MetricsSnapshot,
    TunnelInfo,
    TunnelsResponse,
    TunnelStatus,
)


@pytest.fixture(autouse=True)
def stub_tunnels(monkeypatch):
    """Не уходим в реальный docker — отдаём заранее заготовленные данные."""

    async def fake_list_awg_containers() -> list[AwgContainerInfo]:
        return [AwgContainerInfo(name="awg-ru", interface="awg0")]

    async def fake_list_tunnels() -> TunnelsResponse:
        return TunnelsResponse(
            tunnels=[
                TunnelInfo(
                    container_name="awg-ru",
                    interface="awg0",
                    peers=[],
                    status=TunnelStatus.DOWN,
                ),
            ],
        )

    async def fake_collect_metrics_snapshot() -> MetricsSnapshot:
        return MetricsSnapshot(timestamp=datetime.now(tz=UTC), tunnels=[])

    monkeypatch.setattr(tunnels, "list_awg_containers", fake_list_awg_containers)
    monkeypatch.setattr(tunnels, "list_tunnels", fake_list_tunnels)
    monkeypatch.setattr(tunnels, "collect_metrics_snapshot", fake_collect_metrics_snapshot)
    # Также подменяем имена, импортированные в agent.main модулем напрямую.
    monkeypatch.setattr(agent_main, "list_awg_containers", fake_list_awg_containers)
    monkeypatch.setattr(agent_main, "list_tunnels", fake_list_tunnels)
    monkeypatch.setattr(agent_main, "collect_metrics_snapshot", fake_collect_metrics_snapshot)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {settings.token}"}


@pytest.fixture
async def client():
    # APScheduler в lifespan не дружит с тестовым event loop'ом (TaskGroup умирает на
    # __aexit__). Поэтому AgentState инициализируем руками без scheduler-а.
    from agent.main import AgentState

    app.state.agent = AgentState()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://agent") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_status_requires_auth(client):
    response = await client.get("/v1/status")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_rejects_wrong_token(client):
    response = await client.get("/v1/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_returns_payload(client, auth_headers):
    response = await client.get("/v1/status", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"]
    assert payload["hostname"]
    assert payload["awg_containers"] == [
        {"name": "awg-ru", "interface": "awg0", "role": "external"},
    ]


@pytest.mark.asyncio
async def test_tunnels_endpoint(client, auth_headers):
    response = await client.get("/v1/tunnels", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["tunnels"][0]["container_name"] == "awg-ru"


@pytest.mark.asyncio
async def test_containers_endpoint(client, auth_headers, monkeypatch):
    """Endpoint /v1/containers возвращает все docker-контейнеры с пометкой
    is_waygate_managed. Используется UI-модалкой Direction'а для dropdown
    `scope_target` — оператор не вводит имя руками."""
    sample = (
        '{"Names":"waygate-amnezia-client-firstbyte","State":"running","Image":"waygate-awg-client:latest"}\n'
        '{"Names":"my-awg-server","State":"running","Image":"amneziavpn/amnezia-server:latest"}\n'
        '{"Names":"old-stopped","State":"exited","Image":"alpine"}\n'
    )

    async def fake_run(command, *, stdin=None, check=True):
        assert command[:3] == ["docker", "ps", "--all"]
        return sample

    from agent import containers as containers_module

    monkeypatch.setattr(containers_module, "run_command", fake_run)

    response = await client.get("/v1/containers", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    by_name = {c["name"]: c for c in body["containers"]}
    assert by_name["waygate-amnezia-client-firstbyte"]["is_waygate_managed"] is True
    assert by_name["waygate-amnezia-client-firstbyte"]["status"] == "running"
    assert by_name["my-awg-server"]["is_waygate_managed"] is False
    assert by_name["my-awg-server"]["status"] == "running"
    assert by_name["old-stopped"]["status"] == "stopped"


@pytest.mark.asyncio
async def test_metrics_endpoint(client, auth_headers):
    response = await client.get("/v1/metrics", headers=auth_headers)
    assert response.status_code == 200
    assert "timestamp" in response.json()


@pytest.mark.asyncio
async def test_rules_apply_endpoint(client, auth_headers, monkeypatch):
    async def fake_apply(*, rules):
        return ApplyRulesResponse(applied=len(rules), skipped=0, errors=[])

    monkeypatch.setattr(agent_main, "routing_apply_rules", fake_apply)

    payload = {
        "rules": [
            {
                "country": "RU",
                "ipset_name": "russia",
                "fwmark": 256,
                "table_id": 100,
                "via_interface": "awg0",
                "via_gateway": "10.0.0.1",
                "enabled": True,
            },
        ],
    }
    response = await client.post("/v1/rules/apply", headers=auth_headers, json=payload)
    assert response.status_code == 200
    assert response.json()["applied"] == 1
