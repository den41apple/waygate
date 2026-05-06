from collections.abc import Iterable

import pytest

from agent import routing
from agent.subprocess_runner import CommandError
from shared.schemas import RoutingRule, RoutingScope


class _FakeRunner:
    """Замена subprocess_runner.run_command, отдаёт заранее заготовленные ответы."""

    def __init__(self, *, responses: dict[tuple[str, ...], str]):
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []
        self.unmatched_returns: str = ""

    async def __call__(
        self,
        command: list[str],
        *,
        stdin: bytes | None = None,
        check: bool = True,
    ) -> str:
        key = tuple(command)
        self.calls.append(key)
        for stored_key, response in self.responses.items():
            if _matches(stored_key=stored_key, actual=key):
                return response
        return self.unmatched_returns


def _matches(*, stored_key: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    if len(stored_key) > len(actual):
        return False
    return all(stored == got for stored, got in zip(stored_key, actual, strict=False))


def _calls_starting_with(calls: Iterable[tuple[str, ...]], prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [call for call in calls if _matches(stored_key=prefix, actual=call)]


@pytest.fixture(autouse=True)
def _assume_iface_has_v6(monkeypatch):
    """В большинстве тестов считаем что awg-iface имеет GUA-IPv6 → v6-стек
    применяется. Тесты которые проверяют именно skip-логику могут override
    через monkeypatch уже после fixture."""

    async def fake(*, ctx, iface):
        return True

    monkeypatch.setattr(routing, "_iface_has_global_ipv6", fake)


@pytest.fixture
def make_rule():
    def _make(
        *,
        ipset_name: str = "russia",
        fwmark: int = 256,
        table_id: int = 100,
        via_interface: str = "awg0",
        via_gateway: str = "10.0.0.1",
        enabled: bool = True,
    ) -> RoutingRule:
        return RoutingRule(
            country="RU",
            ipset_name=ipset_name,
            fwmark=fwmark,
            table_id=table_id,
            via_interface=via_interface,
            via_gateway=via_gateway,
            enabled=enabled,
        )

    return _make


@pytest.mark.asyncio
async def test_apply_rules_adds_missing_components(monkeypatch, make_rule):
    """Каждое RoutingRule применяется в двух стеках (V4 + V6) → applied=2.
    На каждый стек создаётся 2 mark-правила (PREROUTING + OUTPUT)."""
    empty_chain = "-P CHAIN ACCEPT\n"
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule()])

    assert response.applied == 2  # V4 + V6
    assert response.skipped == 0
    assert response.errors == []

    add_marks_v4 = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A"))
    add_marks_v6 = _calls_starting_with(runner.calls, ("ip6tables", "-t", "mangle", "-A"))
    add_rules_v4 = _calls_starting_with(runner.calls, ("ip", "rule", "add"))
    add_rules_v6 = _calls_starting_with(runner.calls, ("ip", "-6", "rule", "add"))
    replace_routes_v4 = _calls_starting_with(runner.calls, ("ip", "route", "replace"))
    replace_routes_v6 = _calls_starting_with(runner.calls, ("ip", "-6", "route", "replace"))
    # 0.2.28: только PREROUTING (OUTPUT убрали — local-curl с mark-routing хрупок).
    assert len(add_marks_v4) == 1
    chains_v4 = {call[4] for call in add_marks_v4}
    assert chains_v4 == {"PREROUTING"}
    assert len(add_marks_v6) == 1
    assert len(add_rules_v4) == 1
    assert len(add_rules_v6) == 1
    assert len(replace_routes_v4) == 1
    assert len(replace_routes_v6) == 1
    assert any("russia-v4" in str(call) for call in add_marks_v4)
    assert any("russia-v6" in str(call) for call in add_marks_v6)
    v6_replace = replace_routes_v6[0]
    assert "via" not in v6_replace
    assert "onlink" not in v6_replace


@pytest.mark.asyncio
async def test_apply_rules_default_egress_uses_unconditional_mark(monkeypatch, make_rule):
    """#0b: catch-all rule (`is_default_egress=True`) ставит `-m mark --mark 0`,
    а не `-m set --match-set`. ipset_name'а не имеет."""
    empty_chain = "-P CHAIN ACCEPT\n"
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    rule = RoutingRule(
        country=None,
        ipset_name=None,
        fwmark=256,
        table_id=100,
        via_interface="awg0",
        via_gateway="10.0.0.1",
        enabled=True,
        scope=RoutingScope.HOST,
        scope_target=None,
        is_default_egress=True,
    )
    response = await routing.apply_rules(rules=[rule])

    assert response.errors == []
    assert response.applied == 2  # V4 + V6 — оба стека получают unconditional MARK

    add_marks_v4 = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A"))
    assert len(add_marks_v4) == 1
    cmd = add_marks_v4[0]
    assert "--match-set" not in cmd, cmd
    assert "--mark" in cmd and "0" in cmd, cmd
    assert "MARK" in cmd and "--set-mark" in cmd, cmd


@pytest.mark.asyncio
async def test_apply_rules_default_egress_skipped_when_already_set(monkeypatch, make_rule):
    """Idempotency: повторный apply catch-all rule с тем же fwmark → skipped=2."""
    rule_v4 = "-m mark --mark 0 -j MARK --set-xmark 0x100/0xffffffff"
    rule_v6 = rule_v4
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): f"-P PREROUTING ACCEPT\n-A PREROUTING {rule_v4}\n",
            ("iptables", "-t", "mangle", "-S", "FORWARD"): "-P FORWARD ACCEPT\n",
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): "-P OUTPUT ACCEPT\n",
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): f"-P PREROUTING ACCEPT\n-A PREROUTING {rule_v6}\n",
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): "-P FORWARD ACCEPT\n",
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): "-P OUTPUT ACCEPT\n",
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n",
            ("ip", "route", "show", "table", "100"): "default dev awg0\n",
            ("ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n",
            ("ip", "-6", "route", "show", "table", "100"): "default dev awg0\n",
            ("iptables", "-t", "nat", "-S", "POSTROUTING"): "-A POSTROUTING -o awg0 -j MASQUERADE\n",
            ("ip6tables", "-t", "nat", "-S", "POSTROUTING"): "-A POSTROUTING -o awg0 -j MASQUERADE\n",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    rule = RoutingRule(
        country=None,
        ipset_name=None,
        fwmark=0x100,
        table_id=100,
        via_interface="awg0",
        via_gateway="10.0.0.1",
        enabled=True,
        scope=RoutingScope.HOST,
        scope_target=None,
        is_default_egress=True,
    )
    response = await routing.apply_rules(rules=[rule])

    assert response.applied == 0
    assert response.skipped == 2
    add_marks_v4 = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A"))
    assert add_marks_v4 == []


@pytest.mark.asyncio
async def test_apply_rules_skips_v6_when_iface_has_no_global_ipv6(monkeypatch, make_rule):
    """#21a: на awg-iface без GUA-IPv6 v6-стек должен быть пропущен.

    Регрессия: AmneziaWG-туннели у большинства провайдеров без v6 → AAAA-резолв
    клиента уходит в `<name>-v6` ipset, iptables v6 mark → ip -6 rule → table N
    → `default dev awg-X` без link-peer'а → silent drop. Лучше отдать AAAA на
    дефолтный маршрут (eth0), чем отправить в чёрную дыру.
    """

    async def fake_no_v6(*, ctx, iface):
        return False

    monkeypatch.setattr(routing, "_iface_has_global_ipv6", fake_no_v6)

    empty_chain = "-P CHAIN ACCEPT\n"
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule()])

    # V4 применилось, V6 — скипнуто.
    assert response.applied == 1
    assert response.errors == []
    add_marks_v4 = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A"))
    add_marks_v6 = _calls_starting_with(runner.calls, ("ip6tables", "-t", "mangle", "-A"))
    assert len(add_marks_v4) == 1
    assert add_marks_v6 == []  # ни одного MARK в v6
    add_rules_v6 = _calls_starting_with(runner.calls, ("ip", "-6", "rule", "add"))
    assert add_rules_v6 == []
    replace_routes_v6 = _calls_starting_with(runner.calls, ("ip", "-6", "route", "replace"))
    assert replace_routes_v6 == []


@pytest.mark.asyncio
async def test_apply_rules_skipped_when_state_matches(monkeypatch, make_rule):
    """Если state уже совпадает в PREROUTING+OUTPUT → skipped=2 (ни одной правки)."""
    empty = "-P CHAIN ACCEPT\n"
    russia_v4_mark = "-m set --match-set russia-v4 dst -j MARK --set-xmark 0x100/0xffffffff"
    russia_v6_mark = "-m set --match-set russia-v6 dst -j MARK --set-xmark 0x100/0xffffffff"
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): f"-P PREROUTING ACCEPT\n-A PREROUTING {russia_v4_mark}\n",
            ("iptables", "-t", "mangle", "-S", "FORWARD"): empty,
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): f"-P OUTPUT ACCEPT\n-A OUTPUT {russia_v4_mark}\n",
            ("ip", "rule", "show"): ("0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n"),
            ("ip", "route", "show", "table", "100"): "default dev awg0\n",
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): (
                f"-P PREROUTING ACCEPT\n-A PREROUTING {russia_v6_mark}\n"
            ),
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): empty,
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): f"-P OUTPUT ACCEPT\n-A OUTPUT {russia_v6_mark}\n",
            ("ip", "-6", "rule", "show"): ("0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n"),
            ("ip", "-6", "route", "show", "table", "100"): "default dev awg0\n",
            ("iptables", "-t", "nat", "-S", "POSTROUTING"): "-A POSTROUTING -o awg0 -j MASQUERADE\n",
            ("ip6tables", "-t", "nat", "-S", "POSTROUTING"): "-A POSTROUTING -o awg0 -j MASQUERADE\n",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(fwmark=0x100)])

    assert response.applied == 0
    assert response.skipped == 2
    assert response.errors == []
    assert _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A")) == []
    assert _calls_starting_with(runner.calls, ("ip6tables", "-t", "mangle", "-A")) == []


@pytest.mark.asyncio
async def test_apply_rules_removes_orphans(monkeypatch, make_rule):
    """Orphan-mark/rule в V4 удаляется, V6 пустой — в обоих появляется новое правило."""
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): (
                "-P PREROUTING ACCEPT\n-A PREROUTING -m set --match-set belarus-v4 dst -j MARK --set-xmark 0x200\n"
            ),
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "1000:\tfrom all fwmark 0x200 lookup 200\n",
            ("ip", "-6", "rule", "show"): "",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(ipset_name="russia", fwmark=0x100, table_id=100)])

    assert response.applied == 2  # V4 + V6
    v4_deletes = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-D"))
    assert any("belarus-v4" in call for call in v4_deletes)


@pytest.mark.asyncio
async def test_apply_rules_in_container_uses_nsenter(monkeypatch, make_rule):
    """scope=container: agent резолвит PID через docker inspect и префиксует
    все iptables/ip-команды через `nsenter -t <pid> -n`. Группа host'а исполняется
    отдельно (без prefix), даже если присутствует в том же batch."""
    runner = _FakeRunner(
        responses={
            ("docker", "inspect", "-f"): "12345\n",
            ("nsenter", "-t", "12345", "-n", "iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("nsenter", "-t", "12345", "-n", "ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("nsenter", "-t", "12345", "-n", "ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("nsenter", "-t", "12345", "-n", "ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n",
            ("nsenter", "-t", "12345", "-n", "ip", "route", "show", "table", "100"): "",
            ("nsenter", "-t", "12345", "-n", "ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    rule = RoutingRule(
        country="RU",
        ipset_name="russia",
        fwmark=256,
        table_id=100,
        via_interface="awg0",
        via_gateway="10.0.0.1",
        enabled=True,
        scope=RoutingScope.CONTAINER,
        scope_target="amnezia-awg2",
    )
    response = await routing.apply_rules(rules=[rule])

    assert response.applied == 2  # V4 + V6
    assert response.errors == []
    # IPv4-стек через nsenter
    nsenter_iptables_v4 = _calls_starting_with(
        runner.calls,
        ("nsenter", "-t", "12345", "-n", "iptables", "-t", "mangle", "-A"),
    )
    nsenter_iptables_v6 = _calls_starting_with(
        runner.calls,
        ("nsenter", "-t", "12345", "-n", "ip6tables", "-t", "mangle", "-A"),
    )
    nsenter_ip_rule_v4 = _calls_starting_with(
        runner.calls,
        ("nsenter", "-t", "12345", "-n", "ip", "rule", "add"),
    )
    nsenter_ip_rule_v6 = _calls_starting_with(
        runner.calls,
        ("nsenter", "-t", "12345", "-n", "ip", "-6", "rule", "add"),
    )
    # 0.2.28: только PREROUTING (OUTPUT убрали).
    assert len(nsenter_iptables_v4) == 1
    assert len(nsenter_iptables_v6) == 1
    assert len(nsenter_ip_rule_v4) == 1
    assert len(nsenter_ip_rule_v6) == 1
    # И что docker inspect был
    assert _calls_starting_with(runner.calls, ("docker", "inspect"))


@pytest.mark.asyncio
async def test_apply_rules_isolates_host_and_container_scopes(monkeypatch, make_rule):
    """Two rules: одна host, одна container — каждая в своём netns, не пересекаются."""
    runner = _FakeRunner(
        responses={
            ("docker", "inspect", "-f"): "9999\n",
            # host-сторона
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "",
            ("ip", "-6", "rule", "show"): "",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
            # container-сторона
            ("nsenter", "-t", "9999", "-n", "iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("nsenter", "-t", "9999", "-n", "ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("nsenter", "-t", "9999", "-n", "ip", "rule", "show"): "",
            ("nsenter", "-t", "9999", "-n", "ip", "-6", "rule", "show"): "",
            ("nsenter", "-t", "9999", "-n", "ip", "route", "show", "table", "200"): "",
            ("nsenter", "-t", "9999", "-n", "ip", "-6", "route", "show", "table", "200"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    host_rule = make_rule(ipset_name="host-ipset", fwmark=0x100, table_id=100)
    container_rule = RoutingRule(
        country="DE",
        ipset_name="container-ipset",
        fwmark=0x200,
        table_id=200,
        via_interface="awg0",
        via_gateway="10.66.66.1",
        enabled=True,
        scope=RoutingScope.CONTAINER,
        scope_target="amnezia-awg2",
    )
    response = await routing.apply_rules(rules=[host_rule, container_rule])

    assert response.applied == 4  # 2 rules × 2 families
    # Host-команды БЕЗ nsenter prefix, обе семьи (v4 + v6)
    host_v4 = [call for call in runner.calls if call[:1] == ("iptables",) and "-A" in call and "host-ipset-v4" in call]
    host_v6 = [call for call in runner.calls if call[:1] == ("ip6tables",) and "-A" in call and "host-ipset-v6" in call]
    # Container-команды С nsenter prefix, обе семьи
    container_v4 = [
        call
        for call in runner.calls
        if call[:4] == ("nsenter", "-t", "9999", "-n")
        and call[4] == "iptables"
        and "-A" in call
        and "container-ipset-v4" in call
    ]
    container_v6 = [
        call
        for call in runner.calls
        if call[:4] == ("nsenter", "-t", "9999", "-n")
        and call[4] == "ip6tables"
        and "-A" in call
        and "container-ipset-v6" in call
    ]
    # 0.2.28: только PREROUTING (OUTPUT убрали) → 1 add per family.
    assert len(host_v4) == 1
    assert len(host_v6) == 1
    assert len(container_v4) == 1
    assert len(container_v6) == 1


@pytest.mark.asyncio
async def test_apply_rules_empty_desired_cleans_host_orphans(monkeypatch):
    """`apply_rules(rules=[])` (или все enabled=False) должен удалять orphan'ы
    в host-scope. До фикса пустой desired не доходил до reconciler'а и
    iptables-правила оставались после toggle direction → off → Apply.
    """
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): (
                "-P PREROUTING ACCEPT\n-A PREROUTING -m set --match-set legacy dst -j MARK --set-xmark 0x1\n"
            ),
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "32765:\tfrom all fwmark 0x1 lookup 100\n",
            ("ip", "-6", "rule", "show"): "",
            ("ip", "route", "show", "table", "100"): "default dev awg0\n",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[])

    assert response.errors == []
    iptables_dels = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-D"))
    assert any("legacy" in call for call in iptables_dels)
    ip_rule_dels = _calls_starting_with(runner.calls, ("ip", "rule", "del"))
    assert len(ip_rule_dels) == 1


@pytest.mark.asyncio
async def test_apply_rules_skips_disabled(monkeypatch, make_rule):
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "",
            ("ip", "-6", "rule", "show"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(enabled=False)])

    assert response.applied == 0
    assert response.skipped == 0
    assert _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A")) == []


@pytest.mark.asyncio
async def test_apply_rules_installs_self_bypass_minimal(monkeypatch, make_rule):
    """Минимальный self-bypass set (0.2.32+ после 0a-future cleanup):
    `addrtype LOCAL` (покрывает SSH/agent/handshake'и) + `RELATED,ESTABLISHED`
    (для forwarded reply-packets). Только в PREROUTING — OUTPUT mark был
    убран в 0.2.28."""
    runner = _FakeRunner(responses={})
    monkeypatch.setattr(routing, "run_command", runner)

    await routing.apply_rules(rules=[make_rule()])

    # iptables (v4) self-bypass — оба правила в PREROUTING (только!).
    v4_inserts = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-I"))
    bypass_inserts_v4 = [call for call in v4_inserts if "waygate-self-bypass" in call]
    chains_v4 = {call[4] for call in bypass_inserts_v4}
    assert chains_v4 == {"PREROUTING"}, f"OUTPUT bypass должен быть удалён, найдено: {chains_v4}"
    # Должно быть ровно 2 inserts на каждом стеке: addrtype LOCAL + ESTABLISHED.
    assert len(bypass_inserts_v4) == 2, f"ожидался minimal-set 2 rules, найдено {len(bypass_inserts_v4)}"
    # `call` — tuple, проверяем substring в любом element.
    types = [
        "addrtype"
        if any("addrtype" in x for x in call)
        else "established"
        if any("ESTABLISHED" in x for x in call)
        else "unknown"
        for call in bypass_inserts_v4
    ]
    assert set(types) == {"addrtype", "established"}

    # ip6tables — то же.
    v6_inserts = _calls_starting_with(runner.calls, ("ip6tables", "-t", "mangle", "-I"))
    bypass_inserts_v6 = [call for call in v6_inserts if "waygate-self-bypass" in call]
    assert {call[4] for call in bypass_inserts_v6} == {"PREROUTING"}
    assert len(bypass_inserts_v6) == 2


@pytest.mark.asyncio
async def test_apply_rules_self_bypass_cleans_up_legacy_ports_and_output(monkeypatch, make_rule):
    """0.2.13 (`waygate-ssh-bypass`) + 0.2.14-0.2.31 (port-specific и OUTPUT
    bypass'ы) → 0.2.32 (минимум: addrtype LOCAL + ESTABLISHED в PREROUTING).
    Reconcile должен удалить ВСЕ legacy/port/OUTPUT rules независимо от их
    comment'а — и legacy `waygate-ssh-bypass`, и старый `waygate-self-bypass`
    с port-specifier'ами."""
    existing = (
        "-P PREROUTING ACCEPT\n"
        # Старые legacy:
        "-A PREROUTING -p tcp -m tcp --dport 22 -m comment --comment waygate-ssh-bypass -j RETURN\n"
        "-A OUTPUT -p tcp -m tcp --sport 22 -m comment --comment waygate-ssh-bypass -j RETURN\n"
        # 0.2.14-0.2.31 set:
        "-A PREROUTING -p tcp -m tcp --dport 22 -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A OUTPUT -p tcp -m tcp --sport 22 -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A PREROUTING -p tcp -m tcp --dport 7743 -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A OUTPUT -p tcp -m tcp --sport 7743 -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A PREROUTING -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment waygate-self-bypass -j RETURN\n"
        "-A PREROUTING -m addrtype --dst-type LOCAL -m comment --comment waygate-self-bypass -j RETURN\n"
    )
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S"): existing,
            ("ip6tables", "-t", "mangle", "-S"): existing,
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    await routing.apply_rules(rules=[make_rule()])

    # Все legacy/old rules удалены через `-D` (всем 9 listed выше).
    deletes = [
        call
        for call in runner.calls
        if call[:4] == ("iptables", "-t", "mangle", "-D")
        and ("waygate-self-bypass" in call or "waygate-ssh-bypass" in call)
    ]
    assert len(deletes) == 9, f"ожидалось 9 deletes (все legacy+old), найдено {len(deletes)}: {deletes}"

    # И minimal-set вставлен (2 rule в PREROUTING — addrtype + ESTABLISHED).
    inserts = [
        call
        for call in runner.calls
        if call[:4] == ("iptables", "-t", "mangle", "-I") and "waygate-self-bypass" in call
    ]
    assert len(inserts) == 2  # addrtype LOCAL + ESTABLISHED, оба в PREROUTING
    assert all(call[4] == "PREROUTING" for call in inserts)


# ############################################
# #  NFT-6: detect+recover mangle incompatibility (2026-05-06 incident)
# ############################################


@pytest.mark.asyncio
async def test_apply_rules_dedupes_masquerade_duplicates(monkeypatch, make_rule):
    """NFT-4 (2026-05-06): если в nat POSTROUTING накопилось несколько одинаковых
    `MASQUERADE -o awg-X` rule'ов (из-за прошлых apply при iptables=legacy → nft
    alternative switch — iptables-S падал с 'incompatible' → counts пуст → каждый
    apply добавлял дубликат), reconcile должен оставить ровно одно правило."""
    empty_chain = "-P CHAIN ACCEPT\n"
    # Симулируем 3 одинаковых MASQUERADE rule в nat POSTROUTING — agent должен
    # удалить 2 лишних и оставить одно.
    masq_lines = "\n".join(
        [
            "-P POSTROUTING ACCEPT",
            "-A POSTROUTING -o awg0 -j MASQUERADE",
            "-A POSTROUTING -o awg0 -j MASQUERADE",
            "-A POSTROUTING -o awg0 -j MASQUERADE",
        ],
    )
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("iptables", "-t", "nat", "-S", "POSTROUTING"): masq_lines,
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): empty_chain,
            ("ip6tables", "-t", "mangle", "-S", "OUTPUT"): empty_chain,
            ("ip6tables", "-t", "nat", "-S", "POSTROUTING"): empty_chain,
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "-6", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "route", "show", "table", "100"): "",
            ("ip", "-6", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule()])

    assert response.errors == []
    # Должно быть 2 dedupe-remove'а для awg0 (3 → 1).
    delete_masq_calls = _calls_starting_with(runner.calls, ("iptables", "-t", "nat", "-D", "POSTROUTING"))
    awg0_deletes = [call for call in delete_masq_calls if "awg0" in call]
    assert len(awg0_deletes) == 2, f"ожидалось 2 dedupe-delete для awg0, найдено {len(awg0_deletes)}: {awg0_deletes}"


@pytest.mark.asyncio
async def test_count_masquerades_falls_back_to_nft_when_iptables_empty(monkeypatch):
    """NFT-4: на Ubuntu 24.04+Docker28 iptables -S может вернуть пустой вывод
    (или incompatible-warning без rules), но реально в kernel nat-table есть
    rules через native nft. _count_masquerades_per_iface должен fallback на
    `nft list chain` чтобы corretly увидеть существующие rules."""
    nft_listing = (
        "table ip nat {\n"
        "    chain POSTROUTING {\n"
        "        type nat hook postrouting priority srcnat; policy accept;\n"
        '        oifname "awg-eurohoster" counter packets 10 bytes 100 masquerade\n'
        '        oifname "awg-eurohoster" counter packets 5 bytes 50 masquerade\n'
        "    }\n"
        "}\n"
    )
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "nat", "-S", "POSTROUTING"): "",  # пустой вывод
            ("nft", "list", "chain", "ip", "nat", "POSTROUTING"): nft_listing,
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    ctx = routing._ScopeContext(name="host", command_prefix=[], container_pid=None)
    counts = await routing._count_masquerades_per_iface(ctx=ctx, family=routing._FAMILY_V4)

    # Fallback на nft увидел 2 rule для awg-eurohoster.
    assert counts == {"awg-eurohoster": 2}


@pytest.mark.asyncio
async def test_recover_mangle_flushes_when_incompatible(monkeypatch):
    """Если первый probe iptables -t mangle падает с 'incompatible' — agent
    должен сделать nft flush table ip mangle и не записать в errors."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(command, *, stdin=None, check=True):
        calls.append(tuple(command))
        if tuple(command) == ("iptables", "-t", "mangle", "-S", "PREROUTING"):
            raise CommandError(
                command=command,
                returncode=1,
                stderr="iptables v1.8.10 (nf_tables): table 'mangle' is incompatible, use 'nft' tool.",
            )
        return ""

    monkeypatch.setattr(routing, "run_command", fake_run)

    errors: list[str] = []
    await routing._recover_mangle_if_incompatible(errors=errors)

    assert errors == []
    assert ("nft", "flush", "table", "ip", "mangle") in calls


@pytest.mark.asyncio
async def test_recover_mangle_noop_when_iptables_works(monkeypatch):
    """Если iptables -t mangle отработал OK (mangle не incompatible) — никакого
    nft flush делать не должно, errors пустой."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(command, *, stdin=None, check=True):
        calls.append(tuple(command))
        return ""

    monkeypatch.setattr(routing, "run_command", fake_run)

    errors: list[str] = []
    await routing._recover_mangle_if_incompatible(errors=errors)

    assert errors == []
    # Только probe-call, без nft flush.
    assert calls == [("iptables", "-t", "mangle", "-S", "PREROUTING")]


@pytest.mark.asyncio
async def test_recover_mangle_noop_for_unrelated_iptables_failure(monkeypatch):
    """Если iptables упал с другой ошибкой (не incompatible) — recovery не
    срабатывает, ошибка propagate'ится через downstream-код apply_rules."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(command, *, stdin=None, check=True):
        calls.append(tuple(command))
        if tuple(command) == ("iptables", "-t", "mangle", "-S", "PREROUTING"):
            raise CommandError(
                command=command,
                returncode=127,
                stderr="iptables: command not found",
            )
        return ""

    monkeypatch.setattr(routing, "run_command", fake_run)

    errors: list[str] = []
    await routing._recover_mangle_if_incompatible(errors=errors)

    # nft flush не вызывался — ошибка не та, что лечится recovery.
    assert all(call[0] != "nft" for call in calls)
    assert errors == []


@pytest.mark.asyncio
async def test_recover_mangle_records_nft_flush_failure(monkeypatch):
    """Если nft flush упал (например, нет nft бинаря) — ошибка попадает в
    response.errors, чтобы оператор/server мог увидеть проблему."""

    async def fake_run(command, *, stdin=None, check=True):
        if tuple(command) == ("iptables", "-t", "mangle", "-S", "PREROUTING"):
            raise CommandError(
                command=command,
                returncode=1,
                stderr="table 'mangle' is incompatible, use 'nft' tool",
            )
        if command[0] == "nft":
            raise CommandError(
                command=command,
                returncode=127,
                stderr="nft: command not found",
            )
        return ""

    monkeypatch.setattr(routing, "run_command", fake_run)

    errors: list[str] = []
    await routing._recover_mangle_if_incompatible(errors=errors)

    assert any("mangle recovery via nft flush" in e for e in errors)
