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
from shared.awg_naming import (
    CONTAINER_NAME_PREFIX as _CONTAINER_NAME_PREFIX,
)
from shared.awg_naming import (
    container_name_for as _container_name,
)
from shared.awg_naming import (
    iface_name_for as _iface_name,
)
from shared.schemas import AwgClientInfo, AwgClientStatus

# docker label наших клиентов — детект и фильтрация ровно того, что мы развернули.
_CLIENT_ROLE_LABEL = "io.waygate.role=client"
_CLIENT_NAME_LABEL_KEY = "io.waygate.client-name"
_CLIENT_IFACE_LABEL_KEY = "io.waygate.client-iface"


class AwgClientError(RuntimeError):
    """Ошибка lifecycle клиента (parse, deploy, docker)."""


def _client_dir(*, name: str) -> Path:
    return Path(settings.clients_dir) / name


def _config_path(*, name: str) -> Path:
    """awg-quick парсит имя интерфейса из basename конфига — кладём как
    `<iface>.conf` чтобы интерфейс получил нужное имя."""
    return _client_dir(name=name) / f"{_iface_name(name=name)}.conf"


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
        interface_name=_iface_name(name=name),
        status=status,
        peer_endpoint=config.peer.endpoint,
        peer_pubkey=config.peer.public_key,
        interface_address=config.interface.address,
    )


async def _wait_for_iface(*, iface: str, timeout_s: float, interval_s: float = 0.2) -> None:
    """Поллит `ip link show <iface>` пока iface не появится в host netns или не истечёт timeout.

    Закрывает race с `docker run -d`: команда возвращается мгновенно, но awg-quick
    внутри контейнера ещё инициализирует туннель ~0.5-2 сек. Без ожидания
    последующий `ip rule add ... iif awg-X` падает с "Cannot find device".
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        try:
            await run_command(["ip", "-o", "link", "show", iface])
        except CommandError:
            pass
        else:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise AwgClientError(
                f"iface {iface} не появился в host netns за {timeout_s}s после `docker run`",
            )
        await asyncio.sleep(interval_s)


async def deploy_client(*, name: str, config_text: str, network_mode: str = "host") -> AwgClientInfo:
    """Парсит .conf, сохраняет на диск, разворачивает docker-контейнер.

    `network_mode` — что передаётся в `docker run --network`:
    - `"host"` (default) — iface awg-<name> появляется в host netns, использовать
      со scope=host direction'ами.
    - `"container:<name>"` — контейнер использует netns другого контейнера, iface
      появляется ВНУТРИ его netns. Нужно для scope=container — чтобы iptables
      mangle и ip route внутри netns target'а могли роутить через awg-iface.

    Кидает AwgClientError на парсинге или docker-команде.
    """
    try:
        config = parse_awg_config(config_text)
    except ValueError as exc:
        raise AwgClientError(f"невалидный .conf: {exc}") from exc

    await _ensure_docker()

    iface = _iface_name(name=name)
    container = _container_name(name=name)

    # Снести следы предыдущего клиента с тем же именем — для идемпотентности.
    await run_command(["docker", "rm", "-f", container], check=False)
    # И netdev в host-netns (он переживает docker rm). Без этого `awg-quick up`
    # упадёт с "Address already in use" / "Interface exists".
    await run_command(["ip", "link", "delete", iface], check=False)

    # Записать конфиг (нормализованный, не plaintext-as-is — выкинули unknown keys).
    # Имя файла = имя интерфейса (`awg-<name>[:11]`.conf), awg-quick его подхватит.
    client_dir = _client_dir(name=name)
    client_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path(name=name)
    config_path.write_text(serialize_awg_config(config))
    config_path.chmod(0o600)

    # `docker pull` перед run обязателен для image'ей с тегом `:latest` —
    # без него docker возьмёт закешированную локально версию и пропустит
    # обновлённый CMD/слой. `check=False`: оффлайн-машина без registry должна
    # запустить ранее закешированный image, а не падать.
    await run_command(["docker", "pull", settings.awg_client_image], check=False)

    # `--network host` — netdev клиента появляется напрямую на хосте, можно
    # роутить host-трафик через него (`ip route ... dev awg-<name>`).
    # NET_ADMIN + /dev/net/tun обязательны для wg-quick. ENV IFACE говорит
    # awg-quick'у какой `.conf` поднимать (см. CMD в awg-client.Dockerfile).
    try:
        await run_command(
            [
                "docker",
                "run",
                "-d",
                "--restart",
                "unless-stopped",
                "--network",
                network_mode,
                # `--privileged` нужен для записи sysctl `net.ipv4.conf.all.src_valid_mark=1`,
                # которую awg-quick делает в /proc/sys (read-only с обычными cap'ами при
                # `--network host`). Безопасно ТОЛЬКО в паре с `Table = off` в конфиге
                # (выставляется в shared/awg_config.py::serialize_awg_config) — иначе
                # awg-quick hijack'нет default-route хоста и обрубит SSH.
                "--privileged",
                "--cap-add",
                "NET_ADMIN",
                "--device",
                "/dev/net/tun:/dev/net/tun",
                "--volume",
                f"{client_dir}:/etc/amnezia",
                "--env",
                f"IFACE={iface}",
                "--label",
                _CLIENT_ROLE_LABEL,
                "--label",
                f"{_CLIENT_NAME_LABEL_KEY}={name}",
                "--label",
                f"{_CLIENT_IFACE_LABEL_KEY}={iface}",
                "--name",
                container,
                settings.awg_client_image,
            ],
        )
    except CommandError as exc:
        raise AwgClientError(f"docker run упал: {exc.stderr.strip()}") from exc

    # Дожидаемся пока awg-quick внутри контейнера успеет создать iface. Только для
    # `network_mode=host` — в container-mode iface уезжает в чужую netns и из
    # host'а не виден, проверять надо в той netns (за это отвечает caller).
    if network_mode == "host":
        await _wait_for_iface(iface=iface, timeout_s=settings.awg_iface_wait_seconds)

    return _info_from_config(name=name, config=config, status=AwgClientStatus.RUNNING)


async def get_network_mode(*, name: str) -> str | None:
    """Возвращает текущий `--network` mode awg-client-контейнера или None.

    Формат соответствует docker'овскому: `"host"`, `"bridge"`, `"none"`,
    `"container:<id-or-name>"`. None — контейнера нет.
    """
    container = _container_name(name=name)
    try:
        output = await run_command(
            ["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", container],
        )
    except CommandError:
        return None
    mode = output.strip()
    return mode or None


async def find_client_by_iface(*, iface: str) -> str | None:
    """Поиск client.name по имени iface (awg-X). Используется routing.py чтобы
    переключать AWG-client'ов в нужную netns для scope=container."""
    output = await run_command(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={_CLIENT_IFACE_LABEL_KEY}={iface}",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    for line in output.splitlines():
        full_name = line.strip()
        if full_name.startswith(_CONTAINER_NAME_PREFIX):
            return full_name.removeprefix(_CONTAINER_NAME_PREFIX)
    return None


async def redeploy_with_network_mode(*, name: str, network_mode: str) -> None:
    """Перезапускает существующего client'а с другим `--network` mode без потери .conf.

    Идемпотентно — если текущий mode уже совпадает с желаемым, no-op.
    Используется в routing.py при scope=container: agent видит что awg-client
    запущен с `--network host`, но direction требует `--network container:<X>`,
    и переключает.
    """
    current = await get_network_mode(name=name)
    if current == network_mode:
        return
    config_path = _config_path(name=name)
    if not config_path.exists():
        # Backward-compat: ранние клиенты сохраняли как `awg0.conf`.
        legacy = _client_dir(name=name) / "awg0.conf"
        if legacy.exists():
            config_path = legacy
        else:
            raise AwgClientError(
                f"конфиг для client'а {name} не найден на диске — не могу пересоздать в новом netns",
            )
    config_text = config_path.read_text()
    logger.info(
        "awg-clients: redeploy {} с {} → {}",
        name,
        current or "<отсутствует>",
        network_mode,
    )
    await deploy_client(name=name, config_text=config_text, network_mode=network_mode)


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

        config_path = _config_path(name=name)
        # Backward-compat: ранние клиенты сохраняли конфиг как `awg0.conf`.
        if not config_path.exists():
            legacy = _client_dir(name=name) / "awg0.conf"
            if legacy.exists():
                config_path = legacy
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
                interface_name=_iface_name(name=name),
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


async def _delete_iface_in_current_netns(*, name: str) -> None:
    """Сносит netdev `awg-<name>` ВЕЗДЕ где он мог зависнуть.

    `docker rm -f` посылает SIGKILL — graceful shutdown awg-quick'а внутри
    контейнера не успевает; netdev остаётся orphan'ом. Куда именно он попал
    зависит от NetworkMode'а:
    - `--network host` → iface в host netns. Удаляем `ip link delete`.
    - `--network container:X` → iface в netns контейнера X. Если X ещё жив,
      удаляем через `nsenter -t <pidX> -n ip link delete`. Если X тоже мёртв,
      iface ушёл вместе с его netns'ом — ничего делать не надо.

    Зовётся ДО `docker rm` чтобы успеть прочитать NetworkMode'а.
    """
    iface = _iface_name(name=name)
    network_mode = await get_network_mode(name=name)
    if network_mode and network_mode.startswith("container:"):
        target = network_mode.removeprefix("container:")
        try:
            pid_output = await run_command(
                ["docker", "inspect", "-f", "{{.State.Pid}}", target],
                check=False,
            )
            pid = int(pid_output.strip() or "0")
        except (CommandError, ValueError):
            pid = 0
        if pid > 0:
            await run_command(
                ["nsenter", "-t", str(pid), "-n", "ip", "link", "delete", iface],
                check=False,
            )
            return
        # netns target'а уже исчез вместе с iface'ом — ничего не делаем
        logger.info("awg-clients: target netns {} уже удалён, iface {} ушёл с ним", target, iface)
        return
    # NetworkMode == "host" или None (контейнер уже удалён) — пробуем host netns
    await run_command(["ip", "link", "delete", iface], check=False)


async def delete_client(*, name: str) -> None:
    """Сносит контейнер (force), netdev в любой использовавшейся netns'е и папку с конфигом."""
    container = _container_name(name=name)
    # ВАЖНО: ip link delete ДО docker rm — иначе NetworkMode уже не прочитать.
    await _delete_iface_in_current_netns(name=name)
    await run_command(["docker", "rm", "-f", container], check=False)
    client_dir = _client_dir(name=name)
    if client_dir.exists():
        shutil.rmtree(client_dir, ignore_errors=True)


def _resolve_existing_config(*, name: str) -> Path:
    """Возвращает путь к конфигу — `<iface>.conf` (новый формат) или
    легаси `awg0.conf`, какой существует. Кидает AwgClientError если нет."""
    primary = _config_path(name=name)
    if primary.exists():
        return primary
    legacy = _client_dir(name=name) / "awg0.conf"
    if legacy.exists():
        return legacy
    raise AwgClientError(f"конфиг для клиента {name!r} не найден")


async def generate_qr(*, name: str) -> bytes:
    """Возвращает PNG-байты QR-кода .conf-файла для импорта в мобильный AmneziaWG.

    Использует `qrencode -o - -t PNG` — ставится из apt-пакета `qrencode`.
    """
    text = _resolve_existing_config(name=name).read_text()

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
    return _resolve_existing_config(name=name).read_text()
