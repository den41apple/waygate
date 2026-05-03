from typing import Any

import aiohttp
from loguru import logger
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from server.config import settings
from shared.schemas import (
    AgentStatus,
    ApplyDnsRequest,
    ApplyDnsResponse,
    ApplyRulesRequest,
    ApplyRulesResponse,
    AwgClientActionResponse,
    CreateAwgClientRequest,
    CreateAwgClientResponse,
    GeoIpSyncRequest,
    GeoIpSyncResponse,
    IpsetApplyRequest,
    IpsetApplyResponse,
    ListAwgClientsResponse,
    MetricsSnapshot,
    TlsApplyResponse,
    TlsConfig,
    TokenRotateResponse,
    TunnelsResponse,
    UpdateRequest,
    UpdateResponse,
)


class AgentClientError(RuntimeError):
    """Агент вернул ошибку (4xx/5xx) или прислал нечитаемое тело."""


class AgentUnreachable(AgentClientError):
    """Не удалось установить соединение с агентом."""


class AgentClient:
    """Типизированный HTTP-клиент к агенту.

    GET-запросы выполняются с tenacity-retry (3 попытки, exponential backoff).
    POST-запросы (apply_*) — без retry, чтобы не дуплицировать побочные эффекты.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        scheme: str = "https",
        verify_tls: bool = False,
    ) -> None:
        self._base_url = f"{scheme}://{host}:{port}/v1"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._verify_tls = verify_tls
        self._timeout = aiohttp.ClientTimeout(
            connect=settings.agent_connect_timeout_seconds,
            total=settings.agent_request_timeout_seconds,
        )

    def _ssl_param(self) -> bool:
        # aiohttp: ssl=True — дефолтный SSL-контекст с верификацией; ssl=False — без верификации.
        return self._verify_tls

    async def _get(self, *, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.get(url, headers=self._headers, ssl=self._ssl_param()) as response,
            ):
                if response.status >= 400:
                    body = await response.text()
                    raise AgentClientError(f"GET {path} → HTTP {response.status}: {body}")
                return await _read_json(response=response)
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"GET {path}: {exc}") from exc

    async def _post(self, *, path: str, payload: BaseModel) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = payload.model_dump(mode="json")
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    url,
                    headers={**self._headers, "Content-Type": "application/json"},
                    json=body,
                    ssl=self._ssl_param(),
                ) as response,
            ):
                if response.status >= 400:
                    text = await response.text()
                    raise AgentClientError(f"POST {path} → HTTP {response.status}: {text}")
                return await _read_json(response=response)
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"POST {path}: {exc}") from exc

    @retry(
        retry=retry_if_exception_type(AgentUnreachable),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def status(self) -> AgentStatus:
        return AgentStatus.model_validate(await self._get(path="/status"))

    @retry(
        retry=retry_if_exception_type(AgentUnreachable),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def metrics(self) -> MetricsSnapshot:
        return MetricsSnapshot.model_validate(await self._get(path="/metrics"))

    @retry(
        retry=retry_if_exception_type(AgentUnreachable),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def tunnels(self) -> TunnelsResponse:
        return TunnelsResponse.model_validate(await self._get(path="/tunnels"))

    async def apply_rules(self, *, request: ApplyRulesRequest) -> ApplyRulesResponse:
        return ApplyRulesResponse.model_validate(await self._post(path="/rules/apply", payload=request))

    async def sync_geoip(self, *, request: GeoIpSyncRequest) -> GeoIpSyncResponse:
        return GeoIpSyncResponse.model_validate(await self._post(path="/geoip/sync", payload=request))

    async def apply_custom_ipset(self, *, request: IpsetApplyRequest) -> IpsetApplyResponse:
        return IpsetApplyResponse.model_validate(await self._post(path="/ipset/apply", payload=request))

    async def apply_dns(self, *, request: ApplyDnsRequest) -> ApplyDnsResponse:
        return ApplyDnsResponse.model_validate(await self._post(path="/dns/apply", payload=request))

    async def apply_tls(self, *, config: TlsConfig) -> TlsApplyResponse:
        return TlsApplyResponse.model_validate(await self._post(path="/tls/apply", payload=config))

    async def update(self, *, request: UpdateRequest) -> UpdateResponse:
        return UpdateResponse.model_validate(await self._post(path="/update", payload=request))

    # ---------- AWG-клиенты (managed deployment) ----------

    async def create_client(
        self,
        *,
        request: CreateAwgClientRequest,
    ) -> CreateAwgClientResponse:
        return CreateAwgClientResponse.model_validate(
            await self._post(path="/clients", payload=request),
        )

    @retry(
        retry=retry_if_exception_type(AgentUnreachable),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def list_clients(self) -> ListAwgClientsResponse:
        return ListAwgClientsResponse.model_validate(await self._get(path="/clients"))

    async def start_client(self, *, name: str) -> AwgClientActionResponse:
        return AwgClientActionResponse.model_validate(
            await self._post_empty(path=f"/clients/{name}/start"),
        )

    async def stop_client(self, *, name: str) -> AwgClientActionResponse:
        return AwgClientActionResponse.model_validate(
            await self._post_empty(path=f"/clients/{name}/stop"),
        )

    async def delete_client(self, *, name: str) -> None:
        url = f"{self._base_url}/clients/{name}"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.delete(url, headers=self._headers, ssl=self._ssl_param()) as response,
            ):
                if response.status >= 400 and response.status != 404:
                    text = await response.text()
                    raise AgentClientError(f"DELETE /clients/{name} → HTTP {response.status}: {text}")
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"DELETE /clients/{name}: {exc}") from exc

    async def get_client_qr(self, *, name: str) -> bytes:
        return await self._get_bytes(path=f"/clients/{name}/qr")

    async def get_client_config(self, *, name: str) -> str:
        text_bytes = await self._get_bytes(path=f"/clients/{name}/config")
        return text_bytes.decode("utf-8")

    async def _post_empty(self, *, path: str) -> dict[str, Any]:
        """POST без body — для /start, /stop endpoints без request-схемы."""
        url = f"{self._base_url}{path}"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    url,
                    headers={**self._headers, "Content-Type": "application/json"},
                    json={},
                    ssl=self._ssl_param(),
                ) as response,
            ):
                if response.status >= 400:
                    text = await response.text()
                    raise AgentClientError(f"POST {path} → HTTP {response.status}: {text}")
                return await _read_json(response=response)
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"POST {path}: {exc}") from exc

    async def _get_bytes(self, *, path: str) -> bytes:
        """GET для бинарных ответов (image/png, text/plain) — без JSON-парсинга."""
        url = f"{self._base_url}{path}"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.get(url, headers=self._headers, ssl=self._ssl_param()) as response,
            ):
                if response.status >= 400:
                    text = await response.text()
                    raise AgentClientError(f"GET {path} → HTTP {response.status}: {text}")
                return await response.read()
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"GET {path}: {exc}") from exc

    async def rotate_token(self) -> TokenRotateResponse:
        # POST без тела — посылаем явно через aiohttp в обход _post (он требует BaseModel).
        url = f"{self._base_url}/token/rotate"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    url,
                    headers={**self._headers, "Content-Type": "application/json"},
                    json={},
                    ssl=self._ssl_param(),
                ) as response,
            ):
                if response.status >= 400:
                    text = await response.text()
                    raise AgentClientError(f"POST /token/rotate → HTTP {response.status}: {text}")
                payload = await _read_json(response=response)
        except (TimeoutError, aiohttp.ClientConnectionError) as exc:
            raise AgentUnreachable(f"POST /token/rotate: {exc}") from exc
        return TokenRotateResponse.model_validate(payload)


async def _read_json(*, response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        return await response.json()  # type: ignore[no-any-return]
    except (aiohttp.ContentTypeError, ValueError) as exc:
        text = await response.text()
        logger.warning("agent: ответ не-JSON: {}", text[:200])
        raise AgentClientError(f"ответ агента не JSON: {exc}") from exc
