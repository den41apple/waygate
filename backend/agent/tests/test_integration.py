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

from collections.abc import Iterator

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
    # `apply_custom_ipset` создаёт пару `<name>-v4`/`<name>-v6` (с 0.2.5
    # dual-family). Проверяем что v4-сет существует и содержит наши CIDR'ы.
    exit_code, output = agent_container.exec("ipset list it-test-idempotent-v4")
    assert exit_code == 0, output
    listing = output.decode()
    assert "10.99.99.0/24" in listing
    assert "192.168.99.0/24" in listing


# ############################################
# #  Routing apply
# ############################################


_RULE_IPSET = "it-routing-test"
_RULE_IFACE = "wg-it-test0"
_RULE_FWMARK = 42
_RULE_TABLE = 142


@pytest.fixture
def routing_dummy_iface(agent_container: AgentContainer) -> Iterator[str]:
    """Создаёт dummy-iface внутри agent-контейнера и поднимает up.

    `apply_rules` для scope=host вызывает `ip route replace default dev <iface>` —
    на отсутствующем iface'е ip route падает с `Cannot find device`. Dummy
    модуль доступен в --privileged-контейнере. Cleanup в finalizer'е, чтобы
    последующие тесты не наследовали iface (хотя имя уникально).
    """
    exit_code, output = agent_container.exec(f"ip link add {_RULE_IFACE} type dummy")
    assert exit_code == 0, f"link add: {output.decode(errors='replace')}"
    agent_container.exec(f"ip link set {_RULE_IFACE} up")
    yield _RULE_IFACE
    # Cleanup: после удаления iface'а оставшиеся `ip route table N default dev <iface>`
    # автоматически чистятся ядром (kernel removes routes via deleted netdev).
    agent_container.exec(f"ip link delete {_RULE_IFACE}")


def _flush_routing_state(agent_container: AgentContainer) -> None:
    """Убрать любые leftover'ы от предыдущих apply'ев — iptables MARK,
    ip rule fwmark, custom routing tables, self-bypass. Чтобы каждый
    routing-тест начинал с чистого листа независимо от порядка запуска.
    """
    for cmd in (
        "iptables -t mangle -F PREROUTING",
        "iptables -t mangle -F OUTPUT",
        "ip6tables -t mangle -F PREROUTING",
        "ip6tables -t mangle -F OUTPUT",
        # Снести fwmark'ные ip rule'и (любых приоритетов).
        "ip rule show | awk '/fwmark/{print $1}' | sed 's/://' | while read p; do ip rule del prio $p; done",
        f"ip route flush table {_RULE_TABLE}",
        f"ip -6 route flush table {_RULE_TABLE}",
    ):
        agent_container.exec(f"sh -c '{cmd} 2>/dev/null || true'")


def _apply_rule_payload(*, enabled: bool) -> dict[str, object]:
    return {
        "rules": [
            {
                "country": None,
                "ipset_name": _RULE_IPSET,
                "fwmark": _RULE_FWMARK,
                "table_id": _RULE_TABLE,
                "via_interface": _RULE_IFACE,
                "via_gateway": "10.99.99.1",
                "enabled": enabled,
                "scope": "host",
                "scope_target": None,
            },
        ],
    }


async def _create_rule_ipset(
    *,
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """Создаём v4+v6 ipset'ы под rule.ipset_name через публичный /v1/ipset/apply."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{agent_container.base_url}/v1/ipset/apply",
            headers=_auth(integration_agent_token),
            json={"name": _RULE_IPSET, "cidrs": ["10.99.99.0/24"]},
        )
    assert response.status_code == 200, response.text


async def test_apply_rules_writes_real_state(
    agent_container: AgentContainer,
    integration_agent_token: str,
    routing_dummy_iface: str,
) -> None:
    """`/v1/rules/apply` создаёт реальные iptables/ip-rule/ip-route на target.

    Это base-line проверка: моки в test_routing.py верят что run_command
    дошел, но не проверяют что ipset/iptables в действительности приняли
    команду без ошибок. `--match-set` против несуществующего ipset'а или
    неправильное имя iface'а здесь упадёт явно.
    """
    _flush_routing_state(agent_container)
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] == [], body
    assert body["applied"] >= 1

    exit_code, mangle_output = agent_container.exec("iptables -t mangle -S PREROUTING")
    assert exit_code == 0, mangle_output
    mangle_text = mangle_output.decode()
    assert f"--match-set {_RULE_IPSET}-v4 dst" in mangle_text, mangle_text
    assert f"--set-xmark 0x{_RULE_FWMARK:x}" in mangle_text or f"0x{_RULE_FWMARK:X}" in mangle_text, mangle_text

    exit_code, rule_output = agent_container.exec("ip rule show")
    assert exit_code == 0, rule_output
    rule_text = rule_output.decode()
    # Хешный fwmark в `ip rule show` — `0x2a` для 42.
    assert f"fwmark 0x{_RULE_FWMARK:x}" in rule_text, rule_text
    assert f"lookup {_RULE_TABLE}" in rule_text, rule_text

    exit_code, route_output = agent_container.exec(f"ip route show table {_RULE_TABLE}")
    assert exit_code == 0, route_output
    route_text = route_output.decode()
    assert "default" in route_text, route_text
    assert f"dev {_RULE_IFACE}" in route_text, route_text


async def test_apply_rules_inserts_self_bypass(
    agent_container: AgentContainer,
    integration_agent_token: str,
    routing_dummy_iface: str,
) -> None:
    """`apply_rules` всегда вешает self-bypass на agent-port + SSH в PREROUTING.

    Регрессия: до 0.2.x в OUTPUT уходили SSH-ответы агента → match-set RU →
    оператор терял SSH. Эти RETURN-правила должны существовать ВСЕГДА после
    любого apply (даже с пустым desired, даже с одним правилом).
    """
    _flush_routing_state(agent_container)
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
    assert response.status_code == 200, response.text

    _, output = agent_container.exec("iptables -t mangle -S PREROUTING")
    text = output.decode()
    assert "waygate-self-bypass" in text, text
    assert "--dport 22" in text, text
    # Agent-port (env PORT в conftest = 7743) тоже защищён.
    assert "--dport 7743" in text, text
    # ESTABLISHED/RELATED bypass в начале цепи — для возвратных пакетов.
    assert "ESTABLISHED" in text, text


async def test_apply_rules_idempotent(
    agent_container: AgentContainer,
    integration_agent_token: str,
    routing_dummy_iface: str,
) -> None:
    """Повторный apply с теми же правилами не плодит дубликатов в iptables.

    Регрессия: ранние версии `_ensure_mark` без diff'а добавляли -A MARK
    каждый раз, и спустя 5 apply'ев в PREROUTING висело 5 одинаковых rule.
    """
    _flush_routing_state(agent_container)
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    payload = _apply_rule_payload(enabled=True)
    async with httpx.AsyncClient(timeout=15.0) as client:
        first = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=payload,
        )
        second = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=payload,
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # Второй apply не должен ничего добавлять.
    assert second.json()["applied"] == 0, second.json()

    _, output = agent_container.exec("iptables -t mangle -S PREROUTING")
    text = output.decode()
    # Ровно один MARK для нашего ipset'а.
    mark_lines = [
        line for line in text.splitlines() if f"--match-set {_RULE_IPSET}-v4 dst" in line and "-j MARK" in line
    ]
    assert len(mark_lines) == 1, f"ожидался 1 MARK, найдено {len(mark_lines)}: {mark_lines}"


async def test_apply_rules_disabled_cleans_up(
    agent_container: AgentContainer,
    integration_agent_token: str,
    routing_dummy_iface: str,
) -> None:
    """`enabled=false` (или пустой rules) удаляет MARK/ip rule/ip route.

    Регрессия: toggle direction → off в UI должен полностью убирать наши
    iptables-следы, иначе orphan'ы продолжают маркировать пакеты.
    """
    _flush_routing_state(agent_container)
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        on = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
        assert on.status_code == 200, on.text
        off = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=False),
        )
        assert off.status_code == 200, off.text

    _, mangle_output = agent_container.exec("iptables -t mangle -S PREROUTING")
    mangle_text = mangle_output.decode()
    assert f"--match-set {_RULE_IPSET}-v4 dst" not in mangle_text, mangle_text

    _, rule_output = agent_container.exec("ip rule show")
    rule_text = rule_output.decode()
    assert f"fwmark 0x{_RULE_FWMARK:x}" not in rule_text, rule_text

    _, route_output = agent_container.exec(f"ip route show table {_RULE_TABLE}")
    assert route_output.decode().strip() == "", route_output
