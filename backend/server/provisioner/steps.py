import asyncio
import time
from collections.abc import Awaitable, Callable
from importlib import resources

from loguru import logger

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.provisioner.ssh import SshError, SshSession
from shared.schemas import AgentStatus


class StepError(RuntimeError):
    """Ошибка на одном из шагов онбординга."""


# Тип эмиттера прогресса. Передаётся снаружи (из ProvisionJob), упрощает тесты.
ProgressEmitter = Callable[[str], Awaitable[None]]


def _load_systemd_unit() -> str:
    """Читает agent.service из package-data — единственный источник правды.

    Тот же файл лежит в deploy/agent.service для ops-конвенции (можно скопировать
    руками при ручной установке), но провижионер всегда берёт версию из wheel'а.
    """
    return resources.files("server.provisioner").joinpath("agent.service").read_text(encoding="utf-8")


SYSTEMD_UNIT_TEMPLATE = _load_systemd_unit()


def _agent_env_content(*, token: str, port: int) -> str:
    return f"PORT={port}\nTOKEN={token}\nLOG_LEVEL=INFO\nTLS_DIR=/etc/waygate/tls\nDATA_DIR=/var/lib/waygate-agent\n"


async def verify_os(*, ssh: SshSession, emit: ProgressEmitter) -> None:
    await emit("Проверяю ОС…")
    try:
        result = await ssh.run(command="cat /etc/os-release")
    except SshError as exc:
        raise StepError(f"Не удалось прочитать /etc/os-release: {exc}") from exc
    lower = result.stdout.lower()
    if "ubuntu" not in lower and "debian" not in lower:
        raise StepError("Поддерживаются только Ubuntu и Debian")
    await emit("ОС подходит")


async def install_deps(*, ssh: SshSession, emit: ProgressEmitter) -> None:
    await emit("apt-get update…")
    await ssh.run(command="DEBIAN_FRONTEND=noninteractive apt-get update -qq")
    await emit("apt-get install ipset iptables iproute2 dnsmasq curl openssl python3-venv…")
    await ssh.run(
        command=(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "ipset iptables iproute2 dnsmasq curl openssl python3-venv wireguard-tools"
        ),
    )
    await emit("Зависимости установлены")


async def detect_awg_containers(*, ssh: SshSession, emit: ProgressEmitter) -> list[str]:
    await emit("Проверяю AmneziaWG-контейнеры…")
    result = await ssh.run(
        command="docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'amnezia|awg' || true",
        check=False,
    )
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if names:
        await emit(f"Найдены AWG-контейнеры: {', '.join(names)}")
    else:
        await emit("AWG-контейнеры не найдены — это нормально для свежего сервера")
    return names


async def deploy_agent(
    *,
    ssh: SshSession,
    token: str,
    wheel_url: str,
    agent_port: int,
    emit: ProgressEmitter,
) -> None:
    await emit("Создаю /etc/waygate, /var/lib/waygate-agent, /etc/waygate/tls…")
    await ssh.run(command="mkdir -p /etc/waygate /etc/waygate/tls /var/lib/waygate-agent")

    await emit("Генерирую самоподписанный TLS-сертификат…")
    await ssh.run(
        command=(
            "test -f /etc/waygate/tls/cert.pem || openssl req -x509 -newkey rsa:2048 -nodes "
            "-days 365 -subj '/CN=waygate-agent' "
            "-keyout /etc/waygate/tls/key.pem -out /etc/waygate/tls/cert.pem"
        ),
    )
    await ssh.run(command="chmod 600 /etc/waygate/tls/key.pem")

    await emit(f"Скачиваю wheel: {wheel_url}")
    # Pip парсит имя wheel'а строго по PEP 427 ({name}-{version}-{python}-{abi}-{platform}.whl).
    # Versionless имя `waygate_agent-py3-none-any.whl` из /latest/download он отвергает,
    # поэтому сохраняем под валидным шаблонным именем — реальная версия читается
    # из METADATA внутри архива при install.
    wheel_path = "/tmp/waygate_agent-0.0.0-py3-none-any.whl"
    await ssh.run(command=f"curl -fsSL '{wheel_url}' -o {wheel_path}")

    await emit("Создаю virtualenv в /opt/waygate-agent…")
    await ssh.run(command="rm -rf /opt/waygate-agent && python3 -m venv /opt/waygate-agent")
    await ssh.run(command="/opt/waygate-agent/bin/pip install --upgrade pip --quiet")

    await emit("Устанавливаю agent-wheel в venv…")
    await ssh.run(command=f"/opt/waygate-agent/bin/pip install --quiet {wheel_path}")

    await emit("Записываю /etc/waygate/agent.env…")
    await ssh.write_file(
        path="/etc/waygate/agent.env",
        content=_agent_env_content(token=token, port=agent_port),
        mode="0600",
    )

    await emit("Записываю systemd unit /etc/systemd/system/waygate-agent.service…")
    await ssh.write_file(
        path="/etc/systemd/system/waygate-agent.service",
        content=SYSTEMD_UNIT_TEMPLATE,
        mode="0644",
    )

    await emit("systemctl daemon-reload && enable --now waygate-agent…")
    await ssh.run(command="systemctl daemon-reload")
    await ssh.run(command="systemctl enable --now waygate-agent")


async def wait_for_agent(
    *,
    host: str,
    port: int,
    token: str,
    timeout_seconds: int,
    emit: ProgressEmitter,
) -> AgentStatus:
    """Опрашивает /v1/status пока не получит 200 либо не истечёт таймаут."""
    deadline = time.monotonic() + timeout_seconds
    client = AgentClient(host=host, port=port, token=token)
    last_error: str = ""
    await emit(f"Жду пока агент поднимется на {host}:{port}…")
    while time.monotonic() < deadline:
        try:
            status = await client.status()
        except (AgentUnreachable, AgentClientError) as exc:
            last_error = str(exc)
            logger.debug("healthcheck pending: {}", exc)
            await asyncio.sleep(2)
            continue
        await emit(f"Агент отвечает: версия {status.version}, hostname {status.hostname}")
        return status
    raise StepError(f"Агент не отвечает за {timeout_seconds}с: {last_error}")
