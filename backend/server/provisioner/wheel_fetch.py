"""Скачивание agent-wheel'а на target — с fallback'ом через control-plane.

На RU-серверах прямой `curl` к GitHub часто блокируется (DNS не резолвится /
connection refused / timeout). В таком случае control-plane качает wheel у
себя и заливает на target по SFTP через ту же SSH-сессию, что и онбординг.
"""

import ssl

import aiohttp
import certifi
from loguru import logger

from server.provisioner.ssh import SshError, SshSession
from server.provisioner.steps_types import ProgressEmitter

_DOWNLOAD_TIMEOUT_SECONDS = 60


async def _download_via_control_plane(*, wheel_url: str) -> bytes:
    """Скачивает wheel у control-plane'а — для случая когда target не имеет
    доступа к GitHub (RU-блокировки, broken DNS на target и т.п.)."""
    timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT_SECONDS)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with (
        aiohttp.ClientSession(timeout=timeout, connector=connector) as session,
        session.get(wheel_url) as response,
    ):
        response.raise_for_status()
        return await response.read()


async def fetch_wheel_to_target(
    *,
    ssh: SshSession,
    wheel_url: str,
    wheel_path: str,
    emit: ProgressEmitter,
) -> None:
    """Скачивает wheel на target. Сначала пробует `curl` (быстрый путь — трафик
    не идёт через control-plane). Если упал по сетевой причине (DNS, connect
    refused, и т.п.) — fallback: качаем у control-plane и заливаем по SFTP.
    """
    try:
        await ssh.run(command=f"curl -fsSL '{wheel_url}' -o '{wheel_path}'")
    except SshError as exc:
        # Любой curl-фейл на target — fallback через control-plane:
        # 6=resolve, 7=connect, 18=partial transfer, 22=HTTP error, 23=write,
        # 28=timeout, 35=ssl, 52=empty reply, 56=recv. Все они означают что
        # target по сети не дотянулся до GitHub (RU-блок / DPI / DNS / cert).
        # Дешевле всегда падать в SFTP-fallback чем перечислять все коды.
        logger.info(
            "agent-update: curl на target упал ({}); качаю у control-plane и заливаю по SFTP",
            exc,
        )
        await emit("Curl на target не достучался — качаю у control-plane и заливаю по SFTP...")
        try:
            wheel_bytes = await _download_via_control_plane(wheel_url=wheel_url)
        except Exception as download_exc:
            # Если и control-plane не смог — отдаём оригинальную SSH-ошибку
            # (она ближе к user'у и говорит про target), плюс упоминаем причину.
            raise SshError(
                f"curl на target упал, и control-plane тоже не смог скачать ({download_exc}). Original: {exc}",
            ) from download_exc
        await ssh.upload_bytes(path=wheel_path, content=wheel_bytes)
