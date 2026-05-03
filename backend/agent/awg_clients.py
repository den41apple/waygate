"""Lifecycle managed AmneziaWG-клиентов на target-машине.

Каждый клиент — отдельный docker-контейнер с docker-label `io.waygate.role=client`,
именем `waygate-amnezia-client-<name>`, конфигом в `/etc/waygate/clients/<name>/awg0.conf`.
Образ клиента — наш собственный `waygate-awg-client` с `amneziawg-tools/-go` внутри.
"""

import asyncio
import json
import shutil
from pathlib import Path

from loguru import logger

from agent.config import settings
from agent.subprocess_runner import CommandError, run_command
from shared.awg_config import AwgFullConfig, parse_awg_config, serialize_awg_config
from shared.schemas import AwgClientInfo, AwgClientStatus

# docker label наших клиентов — детект и фильтрация ровно того, что мы развернули.
_CLIENT_ROLE_LABEL = "io.waygate.role=client"
_CLIENT_NAME_LABEL_KEY = "io.waygate.client-name"
_CONTAINER_NAME_PREFIX = "waygate-amnezia-client-"


class AwgClientError(RuntimeError):
    """Ошибка lifecycle клиента (parse, deploy, docker)."""


def _container_name(*, name: str) -> str:
    return f"{_CONTAINER_NAME_PREFIX}{name}"


def _client_dir(*, name: str) -> Path:
    return Path(settings.clients_dir) / name


async def _ensure_docker() -> None:
    """Если на target нет docker — ставим его официальным скриптом get.docker.com.

    Скрипт работает на всех типичных дистрибутивах (Debian/Ubuntu/Fedora/Alpine).
    На уже установленной системе noop'ится (повторный запуск идемпотентен).
    """
    try:
        await run_command(["docker", "--version"])
    except (CommandError, FileNotFoundError):
        logger.info("awg-clients: docker не найден, ставлю через get.docker.com")
    else:
        return
    # `curl ... | sh` — да, выглядит небезопасно, но это официальный путь Docker'а.
    # Альтернатива — apt-get install docker.io, но это нестабильно по дистрибутивам.
    await run_command(["sh", "-c", "curl -fsSL https://get.docker.com | sh"])
    await run_command(["systemctl", "enable", "--now", "docker"], check=False)


def _docker_status_to_state(*, status: str) -> AwgClientStatus:
    """Маппит `docker ps` State (`running`/`exited`/...) в наш enum."""
    state = status.lower().split()[0] if status else ""
    if state == "running":
        return AwgClientStatus.RUNNING
    if state in {"exited", "created", "paused", "dead"}:
        return AwgClientStatus.STOPPED
    return AwgClientStatus.PENDING


def _info_from_config(*, name: str, config: AwgFullConfig, status: AwgClientStatus) -> AwgClientInfo:
    return AwgClientInfo(
        name=name,
        container_name=_container_name(name=name),
        status=status,
        peer_endpoint=config.peer.endpoint,
        peer_pubkey=config.peer.public_key,
        interface_address=config.interface.address,
    )


async def deploy_client(*, name: str, config_text: str) -> AwgClientInfo:
    """Парсит .conf, сохраняет на диск, разворачивает docker-контейнер.

    Кидает AwgClientError на парсинге или docker-команде.
    """
    try:
        config = parse_awg_config(config_text)
    except ValueError as exc:
        raise AwgClientError(f"невалидный .conf: {exc}") from exc

    await _ensure_docker()

    # Снести следы предыдущего клиента с тем же именем — для идемпотентности.
    container = _container_name(name=name)
    await run_command(["docker", "rm", "-f", container], check=False)

    # Записать конфиг (нормализованный, не plaintext-as-is — выкинули unknown keys).
    client_dir = _client_dir(name=name)
    client_dir.mkdir(parents=True, exist_ok=True)
    config_path = client_dir / "awg0.conf"
    config_path.write_text(serialize_awg_config(config))
    config_path.chmod(0o600)

    # Запустить контейнер. NET_ADMIN + /dev/net/tun обязательны для wg-quick.
    try:
        await run_command(
            [
                "docker",
                "run",
                "-d",
                "--restart",
                "unless-stopped",
                "--cap-add",
                "NET_ADMIN",
                "--device",
                "/dev/net/tun:/dev/net/tun",
                "--volume",
                f"{client_dir}:/etc/amnezia",
                "--label",
                _CLIENT_ROLE_LABEL,
                "--label",
                f"{_CLIENT_NAME_LABEL_KEY}={name}",
                "--name",
                container,
                settings.awg_client_image,
            ],
        )
    except CommandError as exc:
        raise AwgClientError(f"docker run упал: {exc.stderr.strip()}") from exc

    return _info_from_config(name=name, config=config, status=AwgClientStatus.RUNNING)


async def list_managed_clients() -> list[AwgClientInfo]:
    """Возвращает список наших клиентов через `docker ps --filter label=...`.

    Метаданные [Peer] подтягиваем из сохранённого .conf на диске (а не из docker-labels —
    PrivateKey мы не хотим в labels палить, а PublicKey peer там и не нужен).
    """
    output = await run_command(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label={_CLIENT_ROLE_LABEL}",
            "--format",
            "{{json .}}",
        ],
        check=False,
    )
    result: list[AwgClientInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("awg-clients: пропускаю невалидную строку docker ps: {}", line)
            continue
        full_name = container.get("Names", "")
        if not full_name.startswith(_CONTAINER_NAME_PREFIX):
            continue
        name = full_name.removeprefix(_CONTAINER_NAME_PREFIX)
        status = _docker_status_to_state(status=container.get("State", "") or container.get("Status", ""))

        config_path = _client_dir(name=name) / "awg0.conf"
        if config_path.exists():
            try:
                config = parse_awg_config(config_path.read_text())
                result.append(_info_from_config(name=name, config=config, status=status))
                continue
            except (OSError, ValueError) as exc:
                logger.warning("awg-clients: не смог прочитать config для {}: {}", name, exc)

        # Конфиг утратили — отдадим минимальную инфу.
        result.append(
            AwgClientInfo(
                name=name,
                container_name=full_name,
                status=status,
                peer_endpoint=None,
                peer_pubkey=None,
                interface_address=None,
            ),
        )
    return result


async def start_client(*, name: str) -> AwgClientStatus:
    container = _container_name(name=name)
    try:
        await run_command(["docker", "start", container])
    except CommandError as exc:
        raise AwgClientError(f"docker start: {exc.stderr.strip()}") from exc
    return AwgClientStatus.RUNNING


async def stop_client(*, name: str) -> AwgClientStatus:
    container = _container_name(name=name)
    try:
        await run_command(["docker", "stop", container])
    except CommandError as exc:
        raise AwgClientError(f"docker stop: {exc.stderr.strip()}") from exc
    return AwgClientStatus.STOPPED


async def delete_client(*, name: str) -> None:
    """Сносит контейнер (force) и удаляет папку с конфигом."""
    container = _container_name(name=name)
    await run_command(["docker", "rm", "-f", container], check=False)
    client_dir = _client_dir(name=name)
    if client_dir.exists():
        shutil.rmtree(client_dir, ignore_errors=True)


async def generate_qr(*, name: str) -> bytes:
    """Возвращает PNG-байты QR-кода .conf-файла для импорта в мобильный AmneziaWG.

    Использует `qrencode -o - -t PNG` — ставится из apt-пакета `qrencode`.
    """
    config_path = _client_dir(name=name) / "awg0.conf"
    if not config_path.exists():
        raise AwgClientError(f"конфиг для клиента {name!r} не найден")
    text = config_path.read_text()

    process = await asyncio.create_subprocess_exec(
        "qrencode",
        "-o",
        "-",
        "-t",
        "PNG",
        "-l",
        "L",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate(text.encode("utf-8"))
    if process.returncode != 0:
        raise AwgClientError(f"qrencode упал: {stderr_bytes.decode('utf-8', errors='replace').strip()}")
    return stdout_bytes


async def get_client_config(*, name: str) -> str:
    """Возвращает plaintext .conf клиента (для скачивания файла из UI)."""
    config_path = _client_dir(name=name) / "awg0.conf"
    if not config_path.exists():
        raise AwgClientError(f"конфиг для клиента {name!r} не найден")
    return config_path.read_text()
