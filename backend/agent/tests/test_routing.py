from collections.abc import Iterable

import pytest

from agent import routing
from shared.schemas import RoutingRule


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
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "0:\tfrom all lookup local\n",
            ("ip", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule()])

    assert response.applied == 1
    assert response.skipped == 0
    assert response.errors == []

    add_marks = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A"))
    add_rules = _calls_starting_with(runner.calls, ("ip", "rule", "add"))
    replace_routes = _calls_starting_with(runner.calls, ("ip", "route", "replace"))
    assert len(add_marks) == 1
    assert len(add_rules) == 1
    assert len(replace_routes) == 1


@pytest.mark.asyncio
async def test_apply_rules_skipped_when_state_matches(monkeypatch, make_rule):
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): (
                "-P PREROUTING ACCEPT\n"
                "-A PREROUTING -m set --match-set russia dst -j MARK --set-xmark 0x100/0xffffffff\n"
            ),
            ("ip", "rule", "show"): ("0:\tfrom all lookup local\n1000:\tfrom all fwmark 0x100 lookup 100\n"),
            ("ip", "route", "show", "table", "100"): "default via 10.0.0.1 dev awg0\n",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(fwmark=0x100)])

    assert response.applied == 0
    assert response.skipped == 1
    assert response.errors == []
    assert _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A")) == []
    assert _calls_starting_with(runner.calls, ("ip", "rule", "add")) == []


@pytest.mark.asyncio
async def test_apply_rules_removes_orphans(monkeypatch, make_rule):
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): (
                "-P PREROUTING ACCEPT\n-A PREROUTING -m set --match-set belarus dst -j MARK --set-xmark 0x200\n"
            ),
            ("ip", "rule", "show"): "1000:\tfrom all fwmark 0x200 lookup 200\n",
            ("ip", "route", "show", "table", "100"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(ipset_name="russia", fwmark=0x100, table_id=100)])

    assert response.applied == 1
    deletes = _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-D"))
    rule_dels = _calls_starting_with(runner.calls, ("ip", "rule", "del"))
    assert any("belarus" in call for call in deletes)
    assert any("0x200" not in str(call) for call in rule_dels)  # удалили fwmark 512


@pytest.mark.asyncio
async def test_apply_rules_skips_disabled(monkeypatch, make_rule):
    runner = _FakeRunner(
        responses={
            ("iptables", "-t", "mangle", "-S", "PREROUTING"): "-P PREROUTING ACCEPT\n",
            ("ip", "rule", "show"): "",
        },
    )
    monkeypatch.setattr(routing, "run_command", runner)

    response = await routing.apply_rules(rules=[make_rule(enabled=False)])

    assert response.applied == 0
    assert response.skipped == 0
    assert _calls_starting_with(runner.calls, ("iptables", "-t", "mangle", "-A")) == []
