import asyncio
import re
import sys
from pathlib import Path

import aiohttp
from loguru import logger

from agent import __version__
from shared.schemas import UpdateRequest, UpdateResponse, UpdateStatus

_DOWNLOAD_TIMEOUT_SECONDS = 120
_RESTART_DELAY_SECONDS = 2  # дать HTTP-ответу долететь до сервера до systemctl restart
_VERSION_RE = re.compile(r"^[0-9a-zA-Z_.\-+]+$")
_RESTART_TASKS: set[asyncio.Task[None]] = set()


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


def _pip_executable() -> str:
    """Путь к pip из текущего интерпретатора. В проде это /opt/waygate-agent/bin/pip."""
    return str(Path(sys.executable).parent / "pip")


async def _run(*, command: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise UpdateError(
            f"команда {' '.join(command)} вернула {process.returncode}: {stderr.decode(errors='replace').strip()}",
        )
    logger.debug("update: команда {} ok ({} байт stdout)", command[0], len(stdout))


async def _delayed_restart() -> None:
    """Запускается фоном — даёт UpdateResponse долететь до сервера, затем systemctl restart."""
    await asyncio.sleep(_RESTART_DELAY_SECONDS)
    try:
        await _run(command=["systemctl", "restart", "waygate-agent"])
    except UpdateError as exc:
        logger.error("update: systemctl restart упал: {}", exc)


async def update_agent(*, request: UpdateRequest) -> UpdateResponse:
    """Скачивает новый wheel, ставит, шедулит restart.

    Возвращает UpdateResponse{previous_version, status=restarting} ДО фактического restart —
    сервер ловит ответ и потом ждёт reconnect.
    """
    _validate_version(version=request.version)
    previous_version = __version__
    # Имя файла должно соответствовать PEP 427 ({name}-{version}-{python}-{abi}-{platform}.whl),
    # иначе pip падает на парсинге. Подставляем реальную версию из request.
    wheel_path = Path("/tmp") / f"waygate_agent-{request.version}-py3-none-any.whl"

    logger.info("update: скачиваю {} → {}", request.wheel_url, wheel_path)
    try:
        await _download_wheel(url=request.wheel_url, target=wheel_path)
    except (aiohttp.ClientError, OSError) as exc:
        raise UpdateError(f"не удалось скачать wheel: {exc}") from exc

    logger.info("update: pip install {}", wheel_path)
    await _run(command=[_pip_executable(), "install", "--upgrade", "--quiet", str(wheel_path)])

    # Сохраняем ссылку на задачу — иначе GC может убить её до systemctl restart.
    _RESTART_TASKS.add(task := asyncio.create_task(_delayed_restart()))
    task.add_done_callback(_RESTART_TASKS.discard)

    return UpdateResponse(previous_version=previous_version, status=UpdateStatus.RESTARTING)
