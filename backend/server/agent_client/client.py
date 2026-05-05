from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

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
    ContainerListResponse,
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

_P = ParamSpec("_P")
_R = TypeVar("_R")
_TModel = TypeVar("_TModel", bound=BaseModel)


class AgentClientError(RuntimeError):
    """Агент вернул ошибку (4xx/5xx) или прислал нечитаемое тело."""


class AgentUnreachable(AgentClientError):
    """Не удалось установить соединение с агентом."""


def _retry_unreachable() -> Callable[
    [Callable[_P, Awaitable[_R]]],
    Callable[_P, Awaitable[_R]],
]:
    """Декоратор: 3 попытки exponential-backoff на AgentUnreachable.

    Применяется только к read-only методам (GET status/metrics/tunnels/clients) —
    у них retry безопасен. POST'ы с побочными эффектами (apply_*) не ретраим,
    чтобы не дуплицировать iptables/ipset state на target'е.

    `ParamSpec`+`TypeVar` сохраняют типы декорируемой функции — без этого
    mypy получит `Any -> Any` и потеряет return-type на каждом методе клиента.
    """
    return retry(
        retry=retry_if_exception_type(AgentUnreachable),
        wait=wait_exponential(multiplier=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )


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

    async def _typed_get(self, *, path: str, response_model: type[_TModel]) -> _TModel:
        """GET + JSON-валидация в один шаг — убирает duplication по `model_validate`."""
        return response_model.model_validate(await self._get(path=path))

    async def _typed_post(
        self,
        *,
        path: str,
        payload: BaseModel,
        response_model: type[_TModel],
    ) -> _TModel:
        """POST с типизированным request+response."""
        return response_model.model_validate(await self._post(path=path, payload=payload))

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

    @_retry_unreachable()
    async def status(self) -> AgentStatus:
        # Прямой `Class.model_validate` (не через `_typed_get`): tenacity-retry
        # стирает Generic'и из TypeVar — без явного класса в return mypy
        # видит Any. Для не-retry методов это ниже не критично.
        return AgentStatus.model_validate(await self._get(path="/status"))

    @_retry_unreachable()
    async def metrics(self) -> MetricsSnapshot:
        return MetricsSnapshot.model_validate(await self._get(path="/metrics"))

    @_retry_unreachable()
    async def tunnels(self) -> TunnelsResponse:
        return TunnelsResponse.model_validate(await self._get(path="/tunnels"))

    async def list_containers(self) -> ContainerListResponse:
        return await self._typed_get(path="/containers", response_model=ContainerListResponse)

    async def apply_rules(self, *, request: ApplyRulesRequest) -> ApplyRulesResponse:
        return await self._typed_post(path="/rules/apply", payload=request, response_model=ApplyRulesResponse)

    async def sync_geoip(self, *, request: GeoIpSyncRequest) -> GeoIpSyncResponse:
        return await self._typed_post(path="/geoip/sync", payload=request, response_model=GeoIpSyncResponse)

    async def apply_custom_ipset(self, *, request: IpsetApplyRequest) -> IpsetApplyResponse:
        return await self._typed_post(path="/ipset/apply", payload=request, response_model=IpsetApplyResponse)

    async def apply_dns(self, *, request: ApplyDnsRequest) -> ApplyDnsResponse:
        return await self._typed_post(path="/dns/apply", payload=request, response_model=ApplyDnsResponse)

    async def apply_tls(self, *, config: TlsConfig) -> TlsApplyResponse:
        return await self._typed_post(path="/tls/apply", payload=config, response_model=TlsApplyResponse)

    async def update(self, *, request: UpdateRequest) -> UpdateResponse:
        return await self._typed_post(path="/update", payload=request, response_model=UpdateResponse)

    # ---------- AWG-клиенты (managed deployment) ----------

    async def create_client(
        self,
        *,
        request: CreateAwgClientRequest,
    ) -> CreateAwgClientResponse:
        return await self._typed_post(path="/clients", payload=request, response_model=CreateAwgClientResponse)

    @_retry_unreachable()
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
