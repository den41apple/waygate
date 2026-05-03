"""Идемпотентное применение пользовательского ipset из явного списка CIDR'ов.

Используется как третья сущность для маршрутизации (наряду с GeoIP-зонами и
DNS-правилами): пользователь указывает CIDR'ы напрямую, агент собирает их в
ipset с заданным именем. atomic-swap pattern такой же как в `agent/geoip.py`,
но без download'а zone-файла.
"""

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import IpsetApplyRequest, IpsetApplyResponse


async def apply_custom_ipset(*, request: IpsetApplyRequest) -> IpsetApplyResponse:
    name = request.name
    cidrs = list(request.cidrs)
    tmp_name = f"{name}_new"

    # 1. Свежий tmp ipset (-exist чтобы не падать если уже есть от прошлого).
    await run_command(
        [
            "ipset",
            "create",
            "-exist",
            tmp_name,
            "hash:net",
            "family",
            "inet",
            "hashsize",
            "4096",
            "maxelem",
            "1000000",
        ],
    )
    await run_command(["ipset", "flush", tmp_name])

    # 2. Массово заливаем CIDR'ы через restore.
    if cidrs:
        restore_lines = [f"add {tmp_name} {cidr}" for cidr in cidrs]
        restore_input = ("\n".join(restore_lines) + "\n").encode("utf-8")
        try:
            await run_command(["ipset", "restore"], stdin=restore_input)
        except CommandError as exc:
            await run_command(["ipset", "destroy", tmp_name], check=False)
            raise RuntimeError(f"ipset restore не удался: {exc.stderr.strip()}") from exc

    # 3. Целевой сет + atomic swap.
    await run_command(["ipset", "create", "-exist", name, "hash:net", "family", "inet"])
    try:
        await run_command(["ipset", "swap", tmp_name, name])
    except CommandError as exc:
        await run_command(["ipset", "destroy", tmp_name], check=False)
        raise RuntimeError(f"ipset swap не удался: {exc.stderr.strip()}") from exc

    await run_command(["ipset", "destroy", tmp_name], check=False)
    return IpsetApplyResponse(name=name, cidrs_loaded=len(cidrs))
