"""Integration-тесты против реального docker-контейнера с агентом.

Скип если docker daemon недоступен. Проверяют те классы багов, которые
in-process unit-тесты с моками ловить не могут:
- ipset с одинаковыми create-параметрами (idempotency, баг до 0.1.18).
- dns-apply пишет в `/etc/dnsmasq.d/` под ProtectSystem (тут не используется,
  но тест pertinent если в будущем юнит изменится).

Для запуска нужен docker daemon на хосте. На macOS Docker Desktop работает.
В CI потребуется `services: docker` или равноценный setup.

По умолчанию integration-тесты выключены (см. `addopts` в pyproject.toml).
Запуск: `uv run pytest -m integration`.
"""

import httpx
import pytest

from agent.tests.conftest import AgentContainer

pytestmark = pytest.mark.integration


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_smoke_status_endpoint(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """Smoke: контейнер поднялся, granian отвечает на /v1/status с валидным Bearer."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{agent_container.base_url}/v1/status",
            headers=_auth(integration_agent_token),
        )
    assert response.status_code == 200
    body = response.json()
    assert "version" in body


async def test_apply_dns_writes_config(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """`/v1/dns/apply` создаёт `/etc/dnsmasq.d/waygate.conf` внутри контейнера."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{agent_container.base_url}/v1/dns/apply",
            headers=_auth(integration_agent_token),
            json={
                "rules": [
                    {
                        "name": "test-streaming",
                        "domains": ["example.test", "*.example.test"],
                        "ipset_name": "dns-test-streaming",
                    },
                ],
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1
    # Конфиг написан — читаем через docker exec.
    exit_code, output = agent_container.exec("cat /etc/dnsmasq.d/waygate.conf")
    assert exit_code == 0, output
    text = output.decode()
    assert "dns-test-streaming" in text
    assert "example.test" in text
    assert "Сгенерировано Waygate" in text


async def test_apply_custom_ipset_idempotent(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """Повторный apply с теми же параметрами — не падает (баг до 0.1.18)."""
    payload = {
        "name": "it-test-idempotent",
        "cidrs": ["10.99.99.0/24", "192.168.99.0/24"],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        first = await client.post(
            f"{agent_container.base_url}/v1/ipset/apply",
            headers=_auth(integration_agent_token),
            json=payload,
        )
        assert first.status_code == 200, first.text
        # Второй раз с тем же name+cidrs — должен пройти без 500.
        second = await client.post(
            f"{agent_container.base_url}/v1/ipset/apply",
            headers=_auth(integration_agent_token),
            json=payload,
        )
        assert second.status_code == 200, second.text
    # Проверяем что ipset реально существует и содержит CIDR'ы.
    exit_code, output = agent_container.exec("ipset list it-test-idempotent")
    assert exit_code == 0, output
    listing = output.decode()
    assert "10.99.99.0/24" in listing
    assert "192.168.99.0/24" in listing
