"""Идемпотентное применение пользовательского ipset из явного списка CIDR'ов.

Используется как третья сущность для маршрутизации (наряду с GeoIP-зонами и
DNS-правилами): пользователь указывает CIDR'ы напрямую, агент собирает их в
ipset с заданным именем. atomic-swap pattern такой же как в `agent/geoip.py`,
но без download'а zone-файла.

Создаются ДВА ipset'а на каждый custom-список: `<name>-v4` (hash:net family inet)
и `<name>-v6` (hash:net family inet6). Это нужно потому что `routing.py`
матчит `iptables --match-set <name>-v4` и `ip6tables --match-set <name>-v6`
независимо. Пользователь в textarea может указать как IPv4-CIDR'ы (`10.0.0.0/8`),
так и IPv6 (`2001:db8::/32`) — agent split'ит по наличию `:` в строке.
"""

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import IpsetApplyRequest, IpsetApplyResponse


def _split_by_family(cidrs: list[str]) -> tuple[list[str], list[str]]:
    """`:` в CIDR → IPv6, иначе IPv4. Парсим грубо — `ipset restore` потом
    проверит формат каждой строки и упадёт со внятным error message."""
    v4: list[str] = []
    v6: list[str] = []
    for cidr in cidrs:
        if ":" in cidr:
            v6.append(cidr)
        else:
            v4.append(cidr)
    return v4, v6


async def _apply_one_family(*, name: str, family: str, cidrs: list[str]) -> None:
    """Atomic-swap для одного ipset'а конкретной family.

    `family` — `inet` для IPv4, `inet6` для IPv6. Имя `name` уже содержит
    суффикс (`-v4`/`-v6`).
    """
    tmp_name = f"{name}_new"

    # Параметры create должны быть одинаковыми и у tmp, и у target'а — после
    # swap target наследует конфиг tmp, и при повторном вызове `-exist` падает
    # с "Set cannot be created" если параметры расходятся (ipset >= 7.x строже).
    create_args = [
        "hash:net",
        "family",
        family,
        "hashsize",
        "4096",
        "maxelem",
        "1000000",
    ]

    # 1. Свежий tmp ipset.
    await run_command(["ipset", "create", "-exist", tmp_name, *create_args])
    await run_command(["ipset", "flush", tmp_name])

    # 2. Массово заливаем CIDR'ы через restore.
    if cidrs:
        restore_lines = [f"add {tmp_name} {cidr}" for cidr in cidrs]
        restore_input = ("\n".join(restore_lines) + "\n").encode("utf-8")
        try:
            await run_command(["ipset", "restore"], stdin=restore_input)
        except CommandError as exc:
            await run_command(["ipset", "destroy", tmp_name], check=False)
            raise RuntimeError(f"ipset restore ({name}) не удался: {exc.stderr.strip()}") from exc

    # 3. Целевой сет + atomic swap.
    await run_command(["ipset", "create", "-exist", name, *create_args])
    try:
        await run_command(["ipset", "swap", tmp_name, name])
    except CommandError as exc:
        await run_command(["ipset", "destroy", tmp_name], check=False)
        raise RuntimeError(f"ipset swap ({name}) не удался: {exc.stderr.strip()}") from exc

    await run_command(["ipset", "destroy", tmp_name], check=False)


async def apply_custom_ipset(*, request: IpsetApplyRequest) -> IpsetApplyResponse:
    """Создаёт `<name>-v4` и `<name>-v6` ipset'ы из request.cidrs.

    Поделяет cidrs на v4/v6 по наличию `:` в строке. Каждый ipset обновляется
    через atomic-swap независимо. Если одна family пуста — её ipset создаётся
    пустым (это нужно чтобы iptables `--match-set` для этой family не падал).
    """
    base = request.name
    cidrs = list(request.cidrs)
    v4_cidrs, v6_cidrs = _split_by_family(cidrs)

    await _apply_one_family(name=f"{base}-v4", family="inet", cidrs=v4_cidrs)
    await _apply_one_family(name=f"{base}-v6", family="inet6", cidrs=v6_cidrs)

    return IpsetApplyResponse(name=base, cidrs_loaded=len(cidrs))
