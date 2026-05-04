"""Тесты agent/ipset.py — split CIDR'ов по family + atomic-swap для двух ipset'ов."""

from collections.abc import Iterable

import pytest

from agent import ipset as ipset_module
from shared.schemas import IpsetApplyRequest


class _FakeRunner:
    """run_command-replacement: записывает все вызовы, отдаёт пустой stdout."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[bytes | None] = []

    async def __call__(
        self,
        command: Iterable[str],
        *,
        stdin: bytes | None = None,
        check: bool = True,
    ) -> str:
        self.calls.append(list(command))
        self.stdins.append(stdin)
        return ""


def test_split_by_family_separates_v4_and_v6() -> None:
    v4, v6 = ipset_module._split_by_family(
        ["10.0.0.0/8", "2001:db8::/32", "1.2.3.4", "fe80::/64"],
    )
    assert v4 == ["10.0.0.0/8", "1.2.3.4"]
    assert v6 == ["2001:db8::/32", "fe80::/64"]


@pytest.mark.asyncio
async def test_apply_custom_ipset_creates_both_families(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _FakeRunner()
    monkeypatch.setattr(ipset_module, "run_command", runner)

    request = IpsetApplyRequest(name="myset", cidrs=["1.2.3.0/24", "2001:db8::/32"])
    response = await ipset_module.apply_custom_ipset(request=request)

    assert response.name == "myset"
    assert response.cidrs_loaded == 2

    create_calls = [call for call in runner.calls if call[:3] == ["ipset", "create", "-exist"]]
    set_names = {call[3] for call in create_calls}
    # Оба `-v4`/`-v6` set'а + tmp-копии для atomic swap.
    assert "myset-v4" in set_names
    assert "myset-v6" in set_names

    # restore для v4 содержит IPv4 CIDR, restore для v6 — IPv6.
    restore_inputs = [
        stdin
        for command, stdin in zip(runner.calls, runner.stdins, strict=False)
        if command[:2] == ["ipset", "restore"]
    ]
    decoded = [s.decode() if s else "" for s in restore_inputs]
    assert any("1.2.3.0/24" in text for text in decoded)
    assert any("2001:db8::/32" in text for text in decoded)
    # IPv4 не попадает в v6-restore и наоборот.
    for text in decoded:
        if "1.2.3.0/24" in text:
            assert "2001:db8::/32" not in text


@pytest.mark.asyncio
async def test_apply_custom_ipset_empty_v6_creates_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Только IPv4 CIDR'ы: v6-set всё равно создаётся пустым (iptables match-set не должен падать)."""
    runner = _FakeRunner()
    monkeypatch.setattr(ipset_module, "run_command", runner)

    request = IpsetApplyRequest(name="onlyv4", cidrs=["10.0.0.0/8"])
    await ipset_module.apply_custom_ipset(request=request)

    create_calls = [call for call in runner.calls if call[:3] == ["ipset", "create", "-exist"]]
    set_names = {call[3] for call in create_calls}
    assert "onlyv4-v4" in set_names
    assert "onlyv4-v6" in set_names
