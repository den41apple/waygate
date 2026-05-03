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
    """Регрессионный тест: sync_list должен делать
    `create -exist tmp` → `flush tmp` → `restore` → `create -exist target` → `swap` → `destroy tmp`.
    Если порядок нарушен, atomic-swap ломается."""
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
        ipset_name="waygate-ru-v4",
        source_url="https://example.invalid/ru.zone",
        custom_cidrs=[],
    )

    # Извлекаем первое слово каждой команды для краткости проверки порядка.
    op_sequence = [(call[0], call[1]) for call in calls if call[0] == "ipset"]
    assert op_sequence[0] == ("ipset", "create"), "первым должен быть create tmp"
    assert "-exist" in calls[0] and "waygate-ru-v4_new" in calls[0]
    assert op_sequence[1] == ("ipset", "flush")
    assert calls[1] == ("ipset", "flush", "waygate-ru-v4_new")
    assert op_sequence[2] == ("ipset", "restore")
    assert op_sequence[3] == ("ipset", "create"), "затем create целевого"
    assert "-exist" in calls[3] and "waygate-ru-v4" in calls[3]
    assert op_sequence[4] == ("ipset", "swap")
    assert calls[4] == ("ipset", "swap", "waygate-ru-v4_new", "waygate-ru-v4")
    assert op_sequence[5] == ("ipset", "destroy")

    assert response.cidrs_loaded == 2
    assert response.ipset_name == "waygate-ru-v4"
