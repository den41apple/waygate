import asyncio
import re
from pathlib import Path

import aiohttp
from loguru import logger

from agent import __version__
from shared.schemas import UpdateRequest, UpdateResponse, UpdateStatus

_DOWNLOAD_TIMEOUT_SECONDS = 120
_VERSION_RE = re.compile(r"^[0-9a-zA-Z_.\-+]+$")
_RESTART_TASKS: set[asyncio.Task[None]] = set()

# Пути на target. Live-venv — `_VENV_LIVE`, новый собирается в `_VENV_NEW`,
# старый перед swap'ом откладывается в `_VENV_BACKUP`. Так избегаем `pip install`
# поверх работающего процесса, который падал с EROFS на target'ах с
# overlay/squashfs или вылетающим linkat'ом на live-файлах.
_VENV_LIVE = "/opt/waygate-agent"
_VENV_NEW = "/opt/waygate-agent.new"
_VENV_BACKUP = "/opt/waygate-agent.bak"
_SWAP_SCRIPT_PATH = Path("/tmp/waygate-update-swap.sh")
_SWAP_LOG_PATH = Path("/var/log/waygate-update.log")
_RESTART_DELAY_SECONDS = 2  # дать UpdateResponse долететь до сервера


class UpdateError(RuntimeError):
    """Ошибка процедуры self-update."""


def _validate_version(*, version: str) -> None:
    if not _VERSION_RE.fullmatch(version):
        raise UpdateError(f"подозрительный version='{version}' — пропущен")


async def _download_wheel(*, url: str, target: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as response:
        response.raise_for_status()
        target.write_bytes(await response.read())


def _build_swap_script(*, wheel_path: Path) -> str:
    """Bash-скрипт: создать .new venv → установить wheel → swap dir'ов → restart.

    Весь stdout/stderr перенаправлен в /var/log/waygate-update.log, потому что
    основной процесс отвязан от родителя через setsid (агент в этот момент
    рестартует, его journal'у мы не доверяем). При неудаче этот лог — единственный
    источник диагностики; читать через `cat /var/log/waygate-update.log`.
    """
    return f"""#!/bin/bash
# Перенаправляем весь вывод в файл — иначе при упавшем шаге диагностики 0.
exec >>"{_SWAP_LOG_PATH}" 2>&1
echo
echo "===== $(date -Is) :: waygate-update-swap start ====="
set -ex

# Дать UpdateResponse долететь до control-plane'а до того как systemctl убьёт процесс.
sleep {_RESTART_DELAY_SECONDS}

OLD={_VENV_LIVE}
NEW={_VENV_NEW}
BAK={_VENV_BACKUP}

# 1. Снести следы предыдущих неудачных попыток.
rm -rf "$NEW" "$BAK"

# 2. Свежий venv. python3 -m venv доступен с момента онбординга.
python3 -m venv "$NEW"
"$NEW/bin/pip" install --upgrade --quiet pip
"$NEW/bin/pip" install --quiet "{wheel_path}"

# 3. Atomic swap. Между двумя `mv` процесс старого агента уже умирает (см. шаг 4),
# но даже если не умер — open fd'ы на старые файлы переживают rename.
mv "$OLD" "$BAK"
mv "$NEW" "$OLD"

# 4. Restart — systemd запустит новый процесс из $OLD/bin/granian (это уже новый venv).
systemctl restart waygate-agent

# 5. Cleanup. Делаем после restart, чтобы откат был возможен если systemctl упадёт.
rm -rf "$BAK"

echo "===== $(date -Is) :: waygate-update-swap done ====="
"""


async def _spawn_detached_swap(*, script_path: Path) -> None:
    """Запускает swap-скрипт отвязанно от текущего процесса.

    `start_new_session=True` создаёт новую сессию (POSIX setsid), процесс не
    погибнет вместе с агентом во время `systemctl restart`.
    """
    await asyncio.create_subprocess_exec(
        "/bin/bash",
        str(script_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    # НЕ ждём процесс — он переживёт нашу смерть, отчёт уйдёт в systemd-journal.


async def update_agent(*, request: UpdateRequest) -> UpdateResponse:
    """Скачивает новый wheel, готовит swap-скрипт, отвязывает его от процесса.

    Возвращает UpdateResponse{previous_version, status=restarting} ДО фактического
    swap'а и restart'а — сервер ловит ответ, потом ждёт reconnect через polling
    `/v1/status`.
    """
    _validate_version(version=request.version)
    previous_version = __version__
    # Имя wheel'а должно соответствовать PEP 427 ({name}-{version}-{python}-{abi}-{platform}.whl),
    # иначе pip падает на парсинге.
    wheel_path = Path("/tmp") / f"waygate_agent-{request.version}-py3-none-any.whl"

    logger.info("update: скачиваю {} → {}", request.wheel_url, wheel_path)
    try:
        await _download_wheel(url=request.wheel_url, target=wheel_path)
    except (aiohttp.ClientError, OSError) as exc:
        raise UpdateError(f"не удалось скачать wheel: {exc}") from exc

    # Готовим swap-скрипт, который соберёт новый venv и atomic-mv'нет вместо текущего.
    # Так избегаем `pip install --upgrade` в работающий venv (валится с EROFS на
    # некоторых target'ах при попытке перезаписать live-файлы агента).
    script = _build_swap_script(wheel_path=wheel_path)
    _SWAP_SCRIPT_PATH.write_text(script)
    _SWAP_SCRIPT_PATH.chmod(0o755)
    logger.info("update: записан swap-скрипт {}", _SWAP_SCRIPT_PATH)

    _RESTART_TASKS.add(
        task := asyncio.create_task(_spawn_detached_swap(script_path=_SWAP_SCRIPT_PATH)),
    )
    task.add_done_callback(_RESTART_TASKS.discard)

    return UpdateResponse(previous_version=previous_version, status=UpdateStatus.RESTARTING)
