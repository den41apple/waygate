from collections.abc import Iterable

import pytest

from agent import routing
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
    На каждый стек создаётся 2 mark-правила (FORWARD + OUTPUT) — PREROUTING
    больше не маркируется (с 0.2.26)."""
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
    # На каждый family — 2 mark-правила (FORWARD + OUTPUT).
    assert len(add_marks_v4) == 2
    chains_v4 = {call[4] for call in add_marks_v4}
    assert chains_v4 == {"FORWARD", "OUTPUT"}
    assert len(add_marks_v6) == 2
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
async def test_apply_rules_skipped_when_state_matches(monkeypatch, make_rule):
    """Если state уже совпадает в FORWARD+OUTPUT → skipped=2 (ни одной правки).
    PREROUTING пустой (с 0.2.26 туда не пишем)."""
    empty = "-P CHAIN ACCEPT\n"
    russia_v4_mark = "-m set --match-set russia-v4 dst -j MARK --set-xmark 0x100/0xffffffff"
    russia_v6_mark = "-m set --match-set russia-v6 dst -j MARK --set-xmark 0x100/0xffffffff"
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): empty,
            ("iptables", "-t", "mangle", "-S", "FORWARD"): f"-P FORWARD ACCEPT\n-A FORWARD {russia_v4_mark}\n",
            ("iptables", "-t", "mangle", "-S", "OUTPUT"): f"-P OUTPUT ACCEPT\n-A OUTPUT {russia_v4_mark}\n",
            ("ip", "rule", "show"): ("0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n"),
            ("ip", "route", "show", "table", "100"): "default dev awg0\n",
            ("ip6tables", "-t", "mangle", "-S", "PREROUTING"): empty,
            ("ip6tables", "-t", "mangle", "-S", "FORWARD"): f"-P FORWARD ACCEPT\n-A FORWARD {russia_v6_mark}\n",
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
    # Каждое family — 2 add (PREROUTING + OUTPUT chains).
    assert len(nsenter_iptables_v4) == 2
    assert len(nsenter_iptables_v6) == 2
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
    # Каждое правило × 2 chain (PREROUTING + OUTPUT) = 2 add per family.
    assert len(host_v4) == 2
    assert len(host_v6) == 2
    assert len(container_v4) == 2
    assert len(container_v6) == 2


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
async def test_apply_rules_installs_ssh_bypass(monkeypatch, make_rule):
    """SSH-bypass: PREROUTING `--dport 22 RETURN` и OUTPUT `--sport 22 RETURN`
    должны быть вставлены ДО любых match-set правил, чтобы при self-match'е
    серверного IP в ipset (geoip-ru на yandex VM в RU) SSH не отрубался."""
    runner = _FakeRunner(responses={})
    monkeypatch.setattr(routing, "run_command", runner)

    await routing.apply_rules(rules=[make_rule()])

    # iptables (v4) bypass — оба правила вставлены через `-I` в начало.
    v4_inserts = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-I"))
    v4_chains = {call[4] for call in v4_inserts if "waygate-self-bypass" in call}
    assert v4_chains == {"PREROUTING", "OUTPUT"}

    # ip6tables — то же самое для v6.
    v6_inserts = _calls_starting_with(runner.calls, ("ip6tables", "-t", "mangle", "-I"))
    v6_chains = {call[4] for call in v6_inserts if "waygate-self-bypass" in call}
    assert v6_chains == {"PREROUTING", "OUTPUT"}


@pytest.mark.asyncio
async def test_apply_rules_ssh_bypass_idempotent(monkeypatch, make_rule):
    """При повторном apply (когда bypass уже стоит для всех правил) — не дублируем."""
    existing = (
        "-P PREROUTING ACCEPT\n"
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

    bypass_inserts = [
        call
        for call in runner.calls
        if call[:4] == ("iptables", "-t", "mangle", "-I") and "waygate-self-bypass" in call
    ]
    assert bypass_inserts == []


@pytest.mark.asyncio
async def test_apply_rules_replaces_legacy_ssh_bypass(monkeypatch, make_rule):
    """0.2.13 ставил `waygate-ssh-bypass` (только порт 22). 0.2.14 заменяет на
    `waygate-self-bypass` с покрытием agent-port'а — старые правила должны
    удаляться, новые — вставляться."""
    existing = (
        "-P PREROUTING ACCEPT\n"
        "-A PREROUTING -p tcp -m tcp --dport 22 -m comment --comment waygate-ssh-bypass -j RETURN\n"
        "-A OUTPUT -p tcp -m tcp --sport 22 -m comment --comment waygate-ssh-bypass -j RETURN\n"
    )
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S"): existing,
            ("ip6tables", "-t", "mangle", "-S"): existing,
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    await routing.apply_rules(rules=[make_rule()])

    # Legacy-правила удалены через `-D`.
    legacy_deletes = [
        call for call in runner.calls if call[:4] == ("iptables", "-t", "mangle", "-D") and "waygate-ssh-bypass" in call
    ]
    assert len(legacy_deletes) == 2  # PREROUTING + OUTPUT
    # Новые правила вставлены.
    new_inserts = [
        call
        for call in runner.calls
        if call[:4] == ("iptables", "-t", "mangle", "-I") and "waygate-self-bypass" in call
    ]
    assert len(new_inserts) >= 4  # 2 порта × 2 цепи минимум
