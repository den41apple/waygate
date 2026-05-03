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
    # `create ... -exist` — не падаем если сет с таким именем уже есть
    # (например, _new остался от прошлой попытки, и destroy не сработал из-за
    # ссылки из iptables). `flush` после — гарантия что внутри пусто перед add'ами,
    # иначе старые элементы могли бы накопиться.
    lines = [
        f"create {set_name} hash:net family inet hashsize 4096 maxelem 1000000 -exist",
        f"flush {set_name}",
    ]
    lines.extend(f"add {set_name} {cidr}" for cidr in cidrs)
    return ("\n".join(lines) + "\n").encode("utf-8")


async def sync_list(
    *,
    country: str,
    ipset_name: str,
    source_url: str,
    custom_cidrs: list[str],
) -> GeoIpSyncResponse:
    """Атомарно обновляет ipset из zone-файла.

    1. Скачиваем zone-файл по source_url.
    2. Парсим CIDR'ы и добавляем custom_cidrs.
    3. Загружаем в _new ipset через `ipset restore`.
    4. Создаём целевой ipset (если ещё нет) — `ipset create -exist`.
    5. `ipset swap` — мгновенный switchover, трафик не прерывается.
    6. Удаляем _new (теперь содержит старые данные).
    """
    started_at = time.monotonic()

    text = await _download_zone_file(url=source_url)
    cidrs = _parse_zone_file(text) + list(custom_cidrs)

    tmp_name = f"{ipset_name}_new"

    # Если _new остался от прошлой неудачной попытки — снести
    await run_command(["ipset", "destroy", tmp_name], check=False)

    restore_input = _build_restore_input(set_name=tmp_name, cidrs=cidrs)
    try:
        await run_command(["ipset", "restore"], stdin=restore_input)
    except CommandError as exc:
        await run_command(["ipset", "destroy", tmp_name], check=False)
        raise RuntimeError(f"ipset restore не удался: {exc.stderr.strip()}") from exc

    await run_command(
        ["ipset", "create", "-exist", ipset_name, "hash:net", "family", "inet"],
    )

    try:
        await run_command(["ipset", "swap", tmp_name, ipset_name])
    except CommandError as exc:
        await run_command(["ipset", "destroy", tmp_name], check=False)
        raise RuntimeError(f"ipset swap не удался: {exc.stderr.strip()}") from exc

    await run_command(["ipset", "destroy", tmp_name], check=False)

    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "geoip sync: {} → {} ({} CIDR за {} мс)",
        country,
        ipset_name,
        len(cidrs),
        duration_ms,
    )
    return GeoIpSyncResponse(
        cidrs_loaded=len(cidrs),
        ipset_name=ipset_name,
        duration_ms=duration_ms,
    )
