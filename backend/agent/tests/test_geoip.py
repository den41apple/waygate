from collections.abc import Iterable

import pytest

from agent import geoip as geoip_module
from agent.geoip import _build_restore_input, _parse_zone_file


def test_parse_zone_file_skips_comments_and_blank_lines() -> None:
    text = """# header comment
# another
1.2.3.0/24

  4.5.6.0/24
# trailing
7.8.9.0/24
"""
    assert _parse_zone_file(text) == ["1.2.3.0/24", "4.5.6.0/24", "7.8.9.0/24"]


def test_build_restore_input_contains_only_add_lines() -> None:
    """`ipset restore` парсит позиционно и не понимает `-exist` в этом контексте.
    Раньше мы передавали `create … -exist` + `flush` через restore — на проде это
    валилось с `Set cannot be created`. Теперь create/flush делаются отдельными
    CLI-командами, restore содержит ТОЛЬКО `add`."""
    payload = _build_restore_input(set_name="waygate-ru-v4_new", cidrs=["1.2.3.0/24", "4.5.0.0/16"])
    text = payload.decode("utf-8")

    assert "create" not in text
    assert "flush" not in text
    assert "-exist" not in text

    lines = text.strip().split("\n")
    assert lines == [
        "add waygate-ru-v4_new 1.2.3.0/24",
        "add waygate-ru-v4_new 4.5.0.0/16",
    ]


def test_build_restore_input_handles_empty_cidrs() -> None:
    """Пустой список → пустой stdin (sync_list пропустит сам restore-команду)."""
    assert _build_restore_input(set_name="empty-set", cidrs=[]) == b""


@pytest.mark.asyncio
async def test_sync_list_workflow_calls_ipset_in_correct_order(monkeypatch) -> None:
    """Регрессионный тест: sync_list создаёт ДВА ipset'а — `<name>-v4` (с CIDR'ами
    из zone) и `<name>-v6` (пустой, для consistency с ip6tables --match-set).
    Каждый делает atomic-swap: create tmp → flush → [restore если cidrs] → create target → swap → destroy.
    """
    calls: list[tuple[str, ...]] = []

    async def fake_run_command(
        command: Iterable[str],
        *,
        stdin: bytes | None = None,
        check: bool = True,
    ) -> str:
        calls.append(tuple(command))
        return ""

    async def fake_download(*, url: str) -> str:
        return "1.2.3.0/24\n4.5.0.0/16\n"

    monkeypatch.setattr(geoip_module, "run_command", fake_run_command)
    monkeypatch.setattr(geoip_module, "_download_zone_file", fake_download)

    response = await geoip_module.sync_list(
        country="RU",
        ipset_name="geoip-ru",
        source_url="https://example.invalid/ru.zone",
        custom_cidrs=[],
    )

    # v4-блок: 6 команд (create tmp, flush, restore, create target, swap, destroy).
    v4_block = calls[:6]
    assert v4_block[0][:3] == ("ipset", "create", "-exist") and "geoip-ru-v4_new" in v4_block[0]
    assert "inet" in v4_block[0]
    assert v4_block[1] == ("ipset", "flush", "geoip-ru-v4_new")
    assert v4_block[2] == ("ipset", "restore")
    assert v4_block[3][:3] == ("ipset", "create", "-exist") and "geoip-ru-v4" in v4_block[3]
    assert v4_block[4] == ("ipset", "swap", "geoip-ru-v4_new", "geoip-ru-v4")
    assert v4_block[5][:2] == ("ipset", "destroy")

    # v6-блок: cidrs пуст → restore пропускается (5 команд).
    v6_block = calls[6:]
    assert v6_block[0][:3] == ("ipset", "create", "-exist") and "geoip-ru-v6_new" in v6_block[0]
    assert "inet6" in v6_block[0]
    assert v6_block[1] == ("ipset", "flush", "geoip-ru-v6_new")
    assert v6_block[2][:3] == ("ipset", "create", "-exist") and "geoip-ru-v6" in v6_block[2]
    assert v6_block[3] == ("ipset", "swap", "geoip-ru-v6_new", "geoip-ru-v6")
    assert v6_block[4][:2] == ("ipset", "destroy")

    assert response.cidrs_loaded == 2
    assert response.ipset_name == "geoip-ru"
