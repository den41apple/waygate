import json
from datetime import UTC, datetime

from loguru import logger

from agent.subprocess_runner import CommandError, run_command
from shared.schemas import (
    AwgContainerInfo,
    MetricsSnapshot,
    PeerInfo,
    TunnelInfo,
    TunnelMetrics,
    TunnelsResponse,
    TunnelStatus,
)

_AWG_IMAGE_HINTS = ("amneziawg", "amnezia-wg", "amnezia/amneziawg")
_HANDSHAKE_DEGRADED_THRESHOLD_SECONDS = 180


async def _list_running_containers() -> list[dict[str, str]]:
    """Возвращает список запущенных Docker-контейнеров с базовыми полями."""
    output = await run_command(
        ["docker", "ps", "--format", "{{json .}}"],
        check=False,
    )
    containers: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("docker ps: не парсится строка: {} ({})", line, exc)
    return containers


def _is_awg_container(*, image: str, names: str) -> bool:
    haystack = f"{image} {names}".lower()
    return any(hint in haystack for hint in _AWG_IMAGE_HINTS)


async def _detect_interface(*, container: str) -> str:
    """Достаёт имя WG-интерфейса внутри контейнера через `wg show interfaces`."""
    try:
        output = await run_command(["docker", "exec", container, "wg", "show", "interfaces"])
    except CommandError:
        return ""
    interfaces = output.strip().split()
    return interfaces[0] if interfaces else ""


async def list_awg_containers() -> list[AwgContainerInfo]:
    containers = await _list_running_containers()
    result: list[AwgContainerInfo] = []
    for container in containers:
        image = container.get("Image", "")
        names = container.get("Names", "")
        if not _is_awg_container(image=image, names=names):
            continue
        interface = await _detect_interface(container=names)
        result.append(AwgContainerInfo(name=names, interface=interface or "awg0"))
    return result


def _parse_wg_dump(text: str) -> list[PeerInfo]:
    """Парсит вывод `wg show <iface> dump`.

    Формат после первой строки (private-key, ...):
      <public_key>\\t<preshared_key>\\t<endpoint>\\t<allowed_ips>\\t<latest_handshake>\\t<rx>\\t<tx>\\t<keepalive>
    """
    peers: list[PeerInfo] = []
    lines = text.splitlines()
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        public_key, _preshared, endpoint, _allowed, handshake, rx, tx, _keepalive = parts[:8]
        endpoint_value: str | None = endpoint if endpoint and endpoint != "(none)" else None
        handshake_ts = int(handshake)
        last_handshake = datetime.fromtimestamp(handshake_ts, tz=UTC) if handshake_ts > 0 else None
        peers.append(
            PeerInfo(
                public_key=public_key,
                endpoint=endpoint_value,
                last_handshake=last_handshake,
                rx_bytes=int(rx),
                tx_bytes=int(tx),
            ),
        )
    return peers


async def _read_wg_peers(*, container: str, interface: str) -> list[PeerInfo]:
    output = await run_command(
        ["docker", "exec", container, "wg", "show", interface, "dump"],
        check=False,
    )
    if not output.strip():
        return []
    return _parse_wg_dump(output)


def _evaluate_status(*, peers: list[PeerInfo]) -> TunnelStatus:
    if not peers:
        return TunnelStatus.DOWN
    now = datetime.now(tz=UTC)
    healthy = 0
    for peer in peers:
        if peer.last_handshake is None:
            continue
        age = (now - peer.last_handshake).total_seconds()
        if age <= _HANDSHAKE_DEGRADED_THRESHOLD_SECONDS:
            healthy += 1
    if healthy == len(peers):
        return TunnelStatus.UP
    if healthy == 0:
        return TunnelStatus.DOWN
    return TunnelStatus.DEGRADED


async def list_tunnels() -> TunnelsResponse:
    """Собирает информацию по всем AWG-контейнерам и их пирам."""
    containers = await list_awg_containers()
    tunnels: list[TunnelInfo] = []
    for container in containers:
        peers = await _read_wg_peers(container=container.name, interface=container.interface)
        tunnels.append(
            TunnelInfo(
                container_name=container.name,
                interface=container.interface,
                peers=peers,
                status=_evaluate_status(peers=peers),
            ),
        )
    return TunnelsResponse(tunnels=tunnels)


async def collect_metrics_snapshot() -> MetricsSnapshot:
    """Снимает rx/tx по всем пирам всех туннелей."""
    response = await list_tunnels()
    metrics: list[TunnelMetrics] = []
    for tunnel in response.tunnels:
        for peer in tunnel.peers:
            metrics.append(
                TunnelMetrics(
                    peer=peer.public_key,
                    endpoint=peer.endpoint,
                    rx_bytes=peer.rx_bytes,
                    tx_bytes=peer.tx_bytes,
                ),
            )
    return MetricsSnapshot(timestamp=datetime.now(tz=UTC), tunnels=metrics)
