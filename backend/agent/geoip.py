import time

import aiohttp
from loguru import logger

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import GeoIpSyncResponse

_DOWNLOAD_TIMEOUT_SECONDS = 60


def _parse_zone_file(text: str) -> list[str]:
    """Парсит zone-файл (ipdeny/RIPE) — один CIDR/IP на строку, # — комментарии."""
    cidrs: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cidrs.append(line)
    return cidrs


async def _download_zone_file(*, url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as response:
        response.raise_for_status()
        return await response.text()


def _build_restore_input(*, set_name: str, cidrs: list[str]) -> bytes:
    """Билдит stdin для `ipset restore` — только `add`-строки.

    Раньше тут были `create ... -exist` + `flush`, но `ipset restore` парсит
    позиционные команды строго и игнорирует/ругается на флаг `-exist` в этом
    контексте. Поэтому create + flush делаем отдельными CLI-вызовами в
    `sync_list`, а restore используется только для атомарной массовой загрузки
    элементов.
    """
    lines = [f"add {set_name} {cidr}" for cidr in cidrs]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


async def _atomic_swap_ipset(*, name: str, family: str, cidrs: list[str]) -> None:
    """Atomic-swap для одного ipset'а. `family` = `inet` / `inet6`."""
    tmp_name = f"{name}_new"
    create_args = ["hash:net", "family", family, "hashsize", "4096", "maxelem", "1000000"]
    await run_command(["ipset", "create", "-exist", tmp_name, *create_args])
    await run_command(["ipset", "flush", tmp_name])
    if cidrs:
        restore_input = _build_restore_input(set_name=tmp_name, cidrs=cidrs)
        try:
            await run_command(["ipset", "restore"], stdin=restore_input)
        except CommandError as exc:
            await run_command(["ipset", "destroy", tmp_name], check=False)
            raise RuntimeError(f"ipset restore ({name}) не удался: {exc.stderr.strip()}") from exc
    await run_command(["ipset", "create", "-exist", name, *create_args])
    try:
        await run_command(["ipset", "swap", tmp_name, name])
    except CommandError as exc:
        await run_command(["ipset", "destroy", tmp_name], check=False)
        raise RuntimeError(f"ipset swap ({name}) не удался: {exc.stderr.strip()}") from exc
    await run_command(["ipset", "destroy", tmp_name], check=False)


async def sync_list(
    *,
    country: str,
    ipset_name: str,
    source_url: str,
    custom_cidrs: list[str],
) -> GeoIpSyncResponse:
    """Атомарно обновляет ipset'ы (-v4/-v6) из zone-файла.

    Имя `ipset_name` — логическое (например `geoip-ru`); физически создаются
    `<name>-v4` (заполненный IPv4-CIDR'ами из zone) и `<name>-v6` (пустой,
    но существует для consistency с `ip6tables --match-set <name>-v6`).
    Без пустого v6-set'а ip6tables-правило падало бы с "Set ... doesn't exist".
    """
    started_at = time.monotonic()

    text = await _download_zone_file(url=source_url)
    raw = _parse_zone_file(text) + list(custom_cidrs)
    # ipdeny zone-файлы IPv4-only, но user может в custom_cidrs дать IPv6 —
    # делаем split по `:` как в agent/ipset.py.
    v4_cidrs = [c for c in raw if ":" not in c]
    v6_cidrs = [c for c in raw if ":" in c]

    await _atomic_swap_ipset(name=f"{ipset_name}-v4", family="inet", cidrs=v4_cidrs)
    await _atomic_swap_ipset(name=f"{ipset_name}-v6", family="inet6", cidrs=v6_cidrs)

    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "geoip sync: {} → {} (v4={}, v6={} CIDR за {} мс)",
        country,
        ipset_name,
        len(v4_cidrs),
        len(v6_cidrs),
        duration_ms,
    )
    return GeoIpSyncResponse(
        cidrs_loaded=len(raw),
        ipset_name=ipset_name,
        duration_ms=duration_ms,
    )
