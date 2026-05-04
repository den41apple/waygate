import asyncio
import time
from importlib import resources

from loguru import logger

from server.agent_client import AgentClient, AgentClientError, AgentUnreachable
from server.provisioner.ssh import SshError, SshSession
from server.provisioner.steps_types import ProgressEmitter
from server.provisioner.wheel_fetch import fetch_wheel_to_target
from shared.schemas import AgentStatus


class StepError(RuntimeError):
    """Ошибка на одном из шагов онбординга."""


# Re-export для обратной совместимости — раньше `from steps import ProgressEmitter`.
__all__ = ["ProgressEmitter", "StepError", "fetch_wheel_to_target"]


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

    # Все следующие шаги (apt-get, chattr, systemctl, /etc/...) требуют root.
    # Если SSH-юзер не root, проверяем что доступен NOPASSWD-sudo и включаем
    # auto-sudo-режим у SshSession — последующие команды оборачиваются в `sudo`.
    await emit("Проверяю root-доступ…")
    try:
        uid_result = await ssh.run(command="id -u")
    except SshError as exc:
        raise StepError(f"Не удалось проверить uid SSH-юзера: {exc}") from exc
    if uid_result.stdout.strip() == "0":
        await emit("root-доступ есть")
        return
    # Не root — пробуем NOPASSWD-sudo. `-n` = non-interactive (если требует
    # пароль — мгновенный fail вместо тимаута на чтении пароля).
    sudo_check = await ssh.run(command="sudo -n true", check=False)
    if sudo_check.returncode != 0:
        raise StepError(
            "SSH-юзер не root, и NOPASSWD-sudo не настроен. Варианты: "
            "1) подключайся как root@<host>; "
            "2) настрой sudoers на target-хосте — "
            "echo '$USER ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/waygate-nopasswd "
            "&& sudo chmod 440 /etc/sudoers.d/waygate-nopasswd",
        )
    ssh.enable_sudo()
    await emit("NOPASSWD-sudo есть — оборачиваю команды в sudo")


async def install_deps(*, ssh: SshSession, emit: ProgressEmitter) -> None:
    await emit("apt-get update…")
    await ssh.run(command="DEBIAN_FRONTEND=noninteractive apt-get update -qq")
    await emit("apt-get install ipset iptables iproute2 dnsmasq curl openssl python3-venv conntrack…")
    await ssh.run(
        command=(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "ipset iptables iproute2 dnsmasq curl openssl python3-venv wireguard-tools conntrack"
        ),
    )
    # AmneziaWG kernel-модуль — без него awg-quick откатывается на userspace
    # `amneziawg-go`, который НЕ поддерживает obfuscation-параметры I1-I5
    # (`Line unrecognized: I2=`). Ставим из официального PPA. На некоторых cloud-VM
    # без linux-headers DKMS-build падает — это не критично, awg-quick просто
    # фолбекнется на userspace для конфигов без I*-параметров.
    await emit("Установка amneziawg kernel-модуля (PPA)…")
    await ssh.run(
        command=(
            "(add-apt-repository -y ppa:amnezia/ppa "
            "&& DEBIAN_FRONTEND=noninteractive apt-get update -qq "
            "&& DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "linux-headers-$(uname -r) amneziawg amneziawg-dkms "
            "&& modprobe amneziawg) || true"
        ),
        check=False,
    )

    # Python 3.13 — текущий целевой версии waygate-agent (см. backend/agent/pyproject.toml
    # `requires-python = ">=3.11"`, но мы хотим 3.13 для consistency с dev-средой
    # и future-proof). На Ubuntu 22.04 default 3.10, на 24.04 — 3.12. Если уже
    # 3.13 — пропускаем. Если нет — пробуем deadsnakes PPA. Опционально: в сборке
    # упадёт — оставим default python3 как fallback.
    await emit("Проверка/установка Python 3.13…")
    await ssh.run(
        command=(
            "if ! command -v python3.13 >/dev/null 2>&1; then "
            "  (add-apt-repository -y ppa:deadsnakes/ppa "
            "  && DEBIAN_FRONTEND=noninteractive apt-get update -qq "
            "  && DEBIAN_FRONTEND=noninteractive apt-get install -y python3.13 python3.13-venv) "
            "  || true; "
            "fi"
        ),
        check=False,
    )

    await emit("Зависимости установлены")


_DNSMASQ_UPSTREAM_CONF = """\
# Сгенерировано Waygate-провижионером. Не менять вручную.
listen-address=127.0.0.1
bind-interfaces
no-resolv
server=1.1.1.1
server=8.8.8.8
"""


async def configure_dns_resolver(*, ssh: SshSession, emit: ProgressEmitter) -> None:
    """Настраивает системный DNS-резолвер на локальный dnsmasq.

    Без этого DNS-запросы с хоста минуют наш dnsmasq, ipset не наполняется
    через `ipset=/domain/setname`-директивы → iptables `--match-set` ничего
    не находит → routing-rule безмолвно не работает.

    Шаги:
    1. Отключить systemd-resolved если активен (он держит :53).
    2. Положить /etc/dnsmasq.d/upstream.conf — слушать только на 127.0.0.1,
       upstream — Cloudflare/Google.
    3. Зафиксировать /etc/resolv.conf на nameserver=127.0.0.1 + chattr +i
       чтобы NetworkManager/networkd не перезаписали.
    4. systemctl restart dnsmasq + sanity-check через dig.
    """
    await emit("Отключаю systemd-resolved (если активен)…")
    await ssh.run(
        command="systemctl disable --now systemd-resolved 2>/dev/null || true",
        check=False,
    )

    await emit("Записываю /etc/dnsmasq.d/upstream.conf (listen-address=127.0.0.1)…")
    await ssh.write_file(
        path="/etc/dnsmasq.d/upstream.conf",
        content=_DNSMASQ_UPSTREAM_CONF,
        mode="0644",
    )

    await emit("Фиксирую /etc/resolv.conf на nameserver=127.0.0.1…")
    # chattr -i может фейлиться (если иммутабельность не выставлена) — это норма.
    await ssh.run(command="chattr -i /etc/resolv.conf 2>/dev/null || true", check=False)
    await ssh.run(command="rm -f /etc/resolv.conf")
    await ssh.write_file(path="/etc/resolv.conf", content="nameserver 127.0.0.1\n", mode="0644")
    # +i — networkd/NetworkManager не смогут перезаписать. Если нужно поменять — chattr -i.
    await ssh.run(command="chattr +i /etc/resolv.conf", check=False)

    await emit("systemctl restart dnsmasq…")
    await ssh.run(command="systemctl restart dnsmasq")

    await emit("Проверяю что dnsmasq отвечает на 127.0.0.1…")
    # dig может быть не установлен; fallback на nslookup или просто curl с DNS-проверкой.
    # В install_deps мы ставим `dnsutils`? Нет, не ставим. Проверим через getent — он
    # тоже использует resolv.conf, и если dnsmasq отвечает — getent сработает.
    sanity = await ssh.run(command="getent hosts example.com 2>&1 | head -1", check=False)
    if "example.com" not in sanity.stdout.lower():
        # Не critical: dnsmasq может ещё не успел подняться, getent ловит timeout.
        # Логируем warning, но не падаем — следующий шаг wait_for_agent поймает реальную проблему.
        await emit(f"⚠ getent example.com не отдал A-record (stdout={sanity.stdout!r})")
    await emit("DNS-резолвер настроен: запросы → 127.0.0.1:53 → dnsmasq")


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
    # Сначала пробуем curl на target. На RU-серверах GitHub может быть заблокирован /
    # DNS не резолвиться → fallback на download via control-plane + SFTP upload.
    await fetch_wheel_to_target(ssh=ssh, wheel_url=wheel_url, wheel_path=wheel_path, emit=emit)

    await emit("Создаю virtualenv в /opt/waygate-agent…")
    # Используем python3.13 если доступен (см. install_deps), иначе fallback на python3
    # (default ОС). 3.13 — консистентность с dev-средой.
    await ssh.run(
        command=(
            "rm -rf /opt/waygate-agent && "
            "PY=$(command -v python3.13 || command -v python3) && "
            "$PY -m venv /opt/waygate-agent"
        ),
    )
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
