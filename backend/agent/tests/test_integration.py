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
        "iptables -t nat -F POSTROUTING",
        "ip6tables -t mangle -F PREROUTING",
        "ip6tables -t mangle -F OUTPUT",
        "ip6tables -t nat -F POSTROUTING",
        # Снести fwmark'ные ip rule'и (любых приоритетов).
        "ip rule show | awk '/fwmark/{print $1}' | sed 's/://' | while read p; do ip rule del prio $p; done",
        f"ip route flush table {_RULE_TABLE}",
        f"ip -6 route flush table {_RULE_TABLE}",
    ):
        agent_container.exec(f"sh -c '{cmd} 2>/dev/null || true'")


def _nft_dump(agent_container: AgentContainer, *, table: str, chain: str) -> str:
    """Дамп конкретной nft-chain в текстовом виде.

    На Ubuntu 24.04 + Docker 28+ агент пишет правила через iptables-nft compat,
    а реальный netfilter обрабатывает их через nft hook'и. `iptables -L` может
    показывать shadow-chain (отдельную таблицу от iptables-nft) которая визуально
    содержит правила, но реально пакеты идут через chain в той же priority,
    которую держит другая компонента (Docker). `nft list` показывает **реальное
    netfilter state** независимо от того, кто его создал.

    Заметка по семантике: `iptables -m set` рендерится в nft как opaque
    `xt match "set"` (без имени set'а в дампе) — для match-set'ов тесты
    ассертят `xt match "set"` + сопутствующий `meta mark set 0x...`.
    """
    exit_code, output = agent_container.exec(f"nft list chain ip {table} {chain}")
    assert exit_code == 0, f"nft list ip {table} {chain}: {output.decode(errors='replace')}"
    return output.decode()


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

    # Реальное netfilter state через nft (а не shadow-chain через iptables -L).
    mangle_text = _nft_dump(agent_container, table="mangle", chain="PREROUTING")
    # `iptables -m set` рендерится как opaque `xt match "set"` — имени ipset'а в
    # дампе нет, но соседний `meta mark set` отдельно подтверждает нашу метку.
    assert 'xt match "set"' in mangle_text, mangle_text
    assert f"meta mark set 0x{_RULE_FWMARK:x}" in mangle_text, mangle_text

    # NAT POSTROUTING: MASQUERADE на наш iface — отдельно проверяем что писалось
    # в реальную nat hook'у (closing NFT-1 — на Ubuntu 24.04+Docker28 эта проверка
    # ловит shadow-chain bug когда rule visible в iptables -L но не в реальной chain).
    nat_text = _nft_dump(agent_container, table="nat", chain="POSTROUTING")
    assert f'oifname "{_RULE_IFACE}"' in nat_text, nat_text
    assert "masquerade" in nat_text, nat_text

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
    """`apply_rules` всегда вешает minimum self-bypass set в PREROUTING (mangle).

    Минимум (с 0.2.32, после 0a-future cleanup):
    - `addrtype --dst-type LOCAL → RETURN` (в nft: `fib daddr type local return`):
      покрывает все incoming на local-IP (SSH:22, agent:7743, AWG-handshake'и).
    - `ct state RELATED,ESTABLISHED → RETURN`: для forwarded reply packets
      чтобы mac_IP в RU-set не попадал на возвратном пути.

    Регрессия: до 0.2.x в OUTPUT уходили SSH-ответы агента → match-set RU →
    оператор терял SSH. С 0.2.28 mark ставится только в PREROUTING; OUTPUT
    bypass в 0.2.32 убран как избыточный.
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

    text = _nft_dump(agent_container, table="mangle", chain="PREROUTING")
    # `addrtype --dst-type LOCAL` в nft рендерится как `fib daddr type local return`.
    assert "fib daddr type local" in text, text
    # ESTABLISHED/RELATED bypass: nft пишет `ct state established,related ... return`.
    assert "ct state" in text and "established" in text, text
    # Регрессия: port-specific bypass'ы (`tcp dport 22`, `tcp dport <agent>`)
    # больше НЕ должны быть — addrtype LOCAL их покрывает (NFT-21).
    assert "tcp dport 22" not in text, f"port-specific bypass должен быть removed: {text}"


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

    text = _nft_dump(agent_container, table="mangle", chain="PREROUTING")
    # Ровно одна match-set + MARK rule для нашего fwmark — иначе reconcile сломан
    # и каждый apply плодит дубликат.
    mark_lines = [line for line in text.splitlines() if f"meta mark set 0x{_RULE_FWMARK:x}" in line]
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

    mangle_text = _nft_dump(agent_container, table="mangle", chain="PREROUTING")
    # После disable/empty apply наш MARK должен быть удалён из реальной chain.
    assert f"meta mark set 0x{_RULE_FWMARK:x}" not in mangle_text, mangle_text

    nat_text = _nft_dump(agent_container, table="nat", chain="POSTROUTING")
    # Симметрично — MASQUERADE на наш iface удаляется реконсилером.
    assert f'oifname "{_RULE_IFACE}"' not in nat_text, nat_text

    _, rule_output = agent_container.exec("ip rule show")
    rule_text = rule_output.decode()
    assert f"fwmark 0x{_RULE_FWMARK:x}" not in rule_text, rule_text

    _, route_output = agent_container.exec(f"ip route show table {_RULE_TABLE}")
    assert route_output.decode().strip() == "", route_output


# ############################################
# #  Recovery / partial-failure (SESSION_2026_05_06)
# ############################################


async def test_apply_recovers_after_iface_appears_late(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """Закрывает SESSION_2026_05_06: первый apply падает с "Cannot find device"
    (iface ещё не поднят), iface появляется позже, повторный apply докатывает
    остаток state'а до консистентного.

    Не использует `routing_dummy_iface` фикстуру — iface создаётся вручную
    после первого apply'я (иначе race не воспроизвести).
    """
    _flush_routing_state(agent_container)
    # На всякий случай — iface'а быть не должно даже от предыдущих тестов.
    agent_container.exec(f"ip link delete {_RULE_IFACE} 2>/dev/null || true")
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Первый apply: iface отсутствует → reconcile падает на ip-rule/route.
        first = await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["errors"], "ожидали что первый apply упадёт без iface"

        # 2. Создаём iface руками (имитируем awg-quick поднявший туннель спустя
        #    некоторое время — например после redeploy_with_network_mode).
        exit_code, output = agent_container.exec(f"ip link add {_RULE_IFACE} type dummy")
        assert exit_code == 0, output.decode(errors="replace")
        agent_container.exec(f"ip link set {_RULE_IFACE} up")

        try:
            # 3. Повторный apply: должен догнать state до конца.
            second = await client.post(
                f"{agent_container.base_url}/v1/rules/apply",
                headers=_auth(integration_agent_token),
                json=_apply_rule_payload(enabled=True),
            )
            assert second.status_code == 200, second.text
            body = second.json()
            assert body["errors"] == [], body
            assert body["applied"] >= 1

            # 4. Реальное netfilter state — всё на месте.
            mangle_text = _nft_dump(agent_container, table="mangle", chain="PREROUTING")
            assert f"meta mark set 0x{_RULE_FWMARK:x}" in mangle_text, mangle_text
            nat_text = _nft_dump(agent_container, table="nat", chain="POSTROUTING")
            assert f'oifname "{_RULE_IFACE}"' in nat_text, nat_text
            _, rule_output = agent_container.exec("ip rule show")
            assert f"fwmark 0x{_RULE_FWMARK:x}" in rule_output.decode()
        finally:
            agent_container.exec(f"ip link delete {_RULE_IFACE}")


async def test_status_reports_apply_errors(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """После failed apply `/v1/status` показывает `last_apply_succeeded=False`
    и `last_apply_errors!=[]` — server использует это для auto-reapply при
    OFFLINE→ONLINE (см. backend/server/tasks/healthcheck.py)."""
    _flush_routing_state(agent_container)
    agent_container.exec(f"ip link delete {_RULE_IFACE} 2>/dev/null || true")
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
        status_resp = await client.get(
            f"{agent_container.base_url}/v1/status",
            headers=_auth(integration_agent_token),
        )

    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["last_apply_succeeded"] is False, body
    assert body["last_apply_errors"], body


async def test_no_orphan_ip_rule_after_failed_apply_then_empty(
    agent_container: AgentContainer,
    integration_agent_token: str,
) -> None:
    """После failed apply (iface отсутствует) → empty apply должен очистить ip rule.

    Регрессия из сегодняшнего state'а на yandex VM — там после неудачного apply
    остался `from all fwmark 0x3 lookup 102` без соответствующего match-set
    rule'а (orphan). Reconcile должен убирать это даже когда apply падал
    в середине.
    """
    _flush_routing_state(agent_container)
    agent_container.exec(f"ip link delete {_RULE_IFACE} 2>/dev/null || true")
    await _create_rule_ipset(
        agent_container=agent_container,
        integration_agent_token=integration_agent_token,
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Failed apply.
        await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json=_apply_rule_payload(enabled=True),
        )
        # Empty apply — должен убрать всё что относилось к нашему правилу,
        # независимо от того что предыдущий apply фейлился.
        await client.post(
            f"{agent_container.base_url}/v1/rules/apply",
            headers=_auth(integration_agent_token),
            json={"rules": []},
        )

    _, rule_output = agent_container.exec("ip rule show")
    assert f"fwmark 0x{_RULE_FWMARK:x}" not in rule_output.decode(), rule_output
