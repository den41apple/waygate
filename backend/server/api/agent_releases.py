"""Прокси к GitHub Releases API для UpdateAgentModal.

Зачем прокси, а не fetch из браузера: api.github.com не отдаёт CORS-заголовки
для 3rd-party сайтов, и unauthenticated запросы лимитированы 60/час на IP —
из браузера это легко выбить если пользователей много. Server-side кеш на 5
минут плюс централизованный rate-limit одного IP control-plane'а решают и то,
и другое.
"""

import asyncio
import ssl
import time

import aiohttp
import certifi
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from server.config import settings
from shared.schemas import AgentRelease, AgentReleasesResponse

router = APIRouter(prefix="/agent-releases", tags=["agent-releases"])


def _semver_key(version: str) -> tuple[int, ...]:
    """Семвер-сортировка: `0.2.11` > `0.2.9` > `0.2.10`-нет такого.
    GitHub API возвращает релизы по `published_at`, а это не всегда совпадает
    с возрастанием версии (особенно если workflow для нового тега подвисал).
    Парсим кортеж `int`'ов; нечисловые компоненты (rc/beta) трактуем как `-1`,
    чтобы они уезжали в конец списка.
    """
    parts: list[int] = []
    for token in version.split("."):
        try:
            parts.append(int(token))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


_TAG_PREFIX = "agent-v"
_WHEEL_ASSET_NAME = "waygate_agent-py3-none-any.whl"
# 60 секунд — баланс между свежестью списка после нового release'а и нагрузкой
# на GitHub API. С 5-минутным кешем юзер видел свежий тег с задержкой до 5 мин.
_CACHE_TTL_SECONDS = 60
_REQUEST_TIMEOUT_SECONDS = 10
# Кеш — простой in-memory, переживает между запросами но не между рестартами
# control-plane'а (что нормально). Lock — чтобы не делать N параллельных
# fetch'ей если кеш протух одновременно для нескольких клиентов.
_cache: dict[str, tuple[float, AgentReleasesResponse]] = {}
_cache_lock = asyncio.Lock()


async def _fetch_from_github(*, repo: str) -> AgentReleasesResponse:
    """Качает /releases у GitHub, фильтрует по `agent-v*`-тегам."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "waygate-control-plane"}
    # python:slim не всегда корректно настраивает system trust store
    # (`update-ca-certificates` под `--no-install-recommends` иногда не запускается),
    # поэтому явно используем bundle от `certifi` — это работает в любом окружении.
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with (
        aiohttp.ClientSession(timeout=timeout, connector=connector) as session,
        session.get(url, headers=headers) as response,
    ):
        if response.status != 200:
            body = await response.text()
            logger.warning("agent-releases: GitHub HTTP {} → {}", response.status, body[:200])
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"GitHub API HTTP {response.status}",
            )
        payload: list[dict[str, object]] = await response.json()

    releases: list[AgentRelease] = []
    for entry in payload:
        tag = str(entry.get("tag_name", ""))
        if not tag.startswith(_TAG_PREFIX):
            continue
        if entry.get("draft") or entry.get("prerelease"):
            continue
        # `wheel_url` — versionless-копия из release-workflow. Если её нет в
        # ассетах — пропускаем релиз (старые ручные релизы могли не иметь её).
        assets = entry.get("assets") or []
        if not isinstance(assets, list):
            continue
        wheel_url: str | None = None
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == _WHEEL_ASSET_NAME:
                wheel_url = str(asset.get("browser_download_url", ""))
                break
        if not wheel_url:
            continue
        releases.append(
            AgentRelease(
                tag=tag,
                version=tag.removeprefix(_TAG_PREFIX),
                name=str(entry.get("name") or tag),
                published_at=str(entry.get("published_at") or entry.get("created_at") or ""),
                wheel_url=wheel_url,
            ),
        )
    return AgentReleasesResponse(releases=releases)


@router.get("", response_model=AgentReleasesResponse)
async def list_agent_releases() -> AgentReleasesResponse:
    """Список релизов с GitHub (cached 5 мин)."""
    repo = settings.agent_releases_repo
    now = time.monotonic()
    cached = _cache.get(repo)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with _cache_lock:
        # double-check после lock'а — пока ждали, кто-то мог обновить
        cached = _cache.get(repo)
        if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        try:
            response = await _fetch_from_github(repo=repo)
        except aiohttp.ClientError as exc:
            logger.error("agent-releases: GitHub недоступен: {}", exc)
            # Если кеш есть (пусть и протухший) — лучше отдать его чем 502.
            if cached is not None:
                logger.info("agent-releases: отдаю stale-кеш")
                return cached[1]
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"GitHub недоступен: {exc}",
            ) from exc
        sorted_releases = sorted(response.releases, key=lambda r: _semver_key(r.version), reverse=True)
        response = AgentReleasesResponse(releases=sorted_releases)
        _cache[repo] = (time.monotonic(), response)
        return response
