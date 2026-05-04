import platform
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import Response
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from agent import __version__, awg_clients, containers
from agent.auth import verify_bearer_token
from agent.config import settings
from agent.dns import apply_dns as dns_apply
from agent.geoip import sync_list as geoip_sync_list
from agent.ipset import apply_custom_ipset
from agent.metrics import MetricsBuffer
from agent.routing import apply_rules as routing_apply_rules
from agent.scheduler import AgentScheduler
from agent.tls import TlsApplyError
from agent.tls import apply_tls as tls_apply
from agent.token_rotate import RotateError, rotate_agent_token
from agent.tunnels import (
    collect_metrics_snapshot,
    list_awg_containers,
    list_tunnels,
)
from agent.updater import UpdateError, update_agent
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


class AgentState:
    """Состояние процесса агента, доступное через app.state."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.metrics_buffer = MetricsBuffer(max_size=settings.metrics_buffer_size)
        self.last_applied_rules_count: int = 0
        self.tls_mode: str | None = None
        self.scheduler: AgentScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = AgentState()
    app.state.agent = state
    state.scheduler = AgentScheduler(metrics_buffer=state.metrics_buffer)
    await state.scheduler.start()
    logger.info("Агент Waygate v{} запущен на порту {}", __version__, settings.port)
    try:
        yield
    finally:
        if state.scheduler is not None:
            await state.scheduler.stop()
        logger.info("Агент остановлен")


app = FastAPI(
    title="Waygate Agent",
    version=__version__,
    lifespan=lifespan,
)
# /metrics на агенте — открыт без auth (Prometheus-скраппер не носит JWT). В проде
# доступ ограничивается iptables-правилом «только с control-plane IP».
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app, endpoint="/metrics")


def _state(app: FastAPI) -> AgentState:
    return app.state.agent  # type: ignore[no-any-return]


# ############################################
# #  /v1/status
# ############################################


@app.get("/v1/status", response_model=AgentStatus, dependencies=[Depends(verify_bearer_token)])
async def get_status() -> AgentStatus:
    state = _state(app)
    awg_containers = await list_awg_containers()
    return AgentStatus(
        version=__version__,
        uptime_seconds=int(time.monotonic() - state.started_at),
        hostname=platform.node(),
        awg_containers=awg_containers,
        rules_applied=state.last_applied_rules_count,
        tls_mode=state.tls_mode,
    )


# ############################################
# #  /v1/metrics
# ############################################


@app.get("/v1/metrics", response_model=MetricsSnapshot, dependencies=[Depends(verify_bearer_token)])
async def get_metrics() -> MetricsSnapshot:
    latest = _state(app).metrics_buffer.latest()
    if latest is not None:
        return latest
    return await collect_metrics_snapshot()


# ############################################
# #  /v1/tunnels
# ############################################


@app.get("/v1/tunnels", response_model=TunnelsResponse, dependencies=[Depends(verify_bearer_token)])
async def get_tunnels() -> TunnelsResponse:
    return await list_tunnels()


# ############################################
# #  /v1/containers (список docker-контейнеров для UI scope_target dropdown)
# ############################################


@app.get(
    "/v1/containers",
    response_model=ContainerListResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def get_containers() -> ContainerListResponse:
    return ContainerListResponse(containers=await containers.list_all_containers())


# ############################################
# #  /v1/rules/apply
# ############################################


@app.post(
    "/v1/rules/apply",
    response_model=ApplyRulesResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_rules_apply(request: ApplyRulesRequest) -> ApplyRulesResponse:
    response = await routing_apply_rules(rules=request.rules)
    _state(app).last_applied_rules_count = sum(1 for rule in request.rules if rule.enabled)
    return response


# ############################################
# #  /v1/geoip/sync
# ############################################


@app.post(
    "/v1/geoip/sync",
    response_model=GeoIpSyncResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_geoip_sync(request: GeoIpSyncRequest) -> GeoIpSyncResponse:
    try:
        return await geoip_sync_list(
            country=request.country,
            ipset_name=request.ipset_name,
            source_url=request.source_url,
            custom_cidrs=request.custom_cidrs,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


# ############################################
# #  /v1/ipset/apply (custom ipset из CIDR'ов)
# ############################################


@app.post(
    "/v1/ipset/apply",
    response_model=IpsetApplyResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_ipset_apply(request: IpsetApplyRequest) -> IpsetApplyResponse:
    try:
        return await apply_custom_ipset(request=request)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


# ############################################
# #  /v1/dns/apply
# ############################################


@app.post(
    "/v1/dns/apply",
    response_model=ApplyDnsResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_dns_apply(request: ApplyDnsRequest) -> ApplyDnsResponse:
    return await dns_apply(rules=request.rules)


# ############################################
# #  /v1/token/rotate
# ############################################


@app.post(
    "/v1/token/rotate",
    response_model=TokenRotateResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_token_rotate() -> TokenRotateResponse:
    try:
        new_token = await rotate_agent_token()
    except RotateError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return TokenRotateResponse(token=new_token)


# ############################################
# #  /v1/tls/apply
# ############################################


@app.post(
    "/v1/tls/apply",
    response_model=TlsApplyResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_tls_apply(config: TlsConfig) -> TlsApplyResponse:
    try:
        response = await tls_apply(config=config)
    except TlsApplyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    _state(app).tls_mode = config.mode.value
    return response


# ############################################
# #  /v1/update
# ############################################


@app.post(
    "/v1/update",
    response_model=UpdateResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_update(request: UpdateRequest) -> UpdateResponse:
    try:
        return await update_agent(request=request)
    except UpdateError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


# ############################################
# #  /v1/clients (AmneziaWG-клиенты — managed deployment)
# ############################################


def _client_error(*, exc: awg_clients.AwgClientError, http_status: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=http_status, detail=str(exc))


@app.post(
    "/v1/clients",
    response_model=CreateAwgClientResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_clients(request: CreateAwgClientRequest) -> CreateAwgClientResponse:
    try:
        info = await awg_clients.deploy_client(name=request.name, config_text=request.config_text)
    except awg_clients.AwgClientError as exc:
        raise _client_error(exc=exc) from exc
    return CreateAwgClientResponse(client=info)


@app.get(
    "/v1/clients",
    response_model=ListAwgClientsResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def get_clients() -> ListAwgClientsResponse:
    clients = await awg_clients.list_managed_clients()
    return ListAwgClientsResponse(clients=clients)


@app.post(
    "/v1/clients/{name}/start",
    response_model=AwgClientActionResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_client_start(name: str) -> AwgClientActionResponse:
    try:
        new_status = await awg_clients.start_client(name=name)
    except awg_clients.AwgClientError as exc:
        raise _client_error(exc=exc, http_status=status.HTTP_502_BAD_GATEWAY) from exc
    return AwgClientActionResponse(name=name, status=new_status)


@app.post(
    "/v1/clients/{name}/stop",
    response_model=AwgClientActionResponse,
    dependencies=[Depends(verify_bearer_token)],
)
async def post_client_stop(name: str) -> AwgClientActionResponse:
    try:
        new_status = await awg_clients.stop_client(name=name)
    except awg_clients.AwgClientError as exc:
        raise _client_error(exc=exc, http_status=status.HTTP_502_BAD_GATEWAY) from exc
    return AwgClientActionResponse(name=name, status=new_status)


@app.delete(
    "/v1/clients/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_bearer_token)],
)
async def delete_client_endpoint(name: str) -> Response:
    await awg_clients.delete_client(name=name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/v1/clients/{name}/qr",
    dependencies=[Depends(verify_bearer_token)],
)
async def get_client_qr(name: str) -> Response:
    try:
        png = await awg_clients.generate_qr(name=name)
    except awg_clients.AwgClientError as exc:
        raise _client_error(exc=exc, http_status=status.HTTP_404_NOT_FOUND) from exc
    return Response(content=png, media_type="image/png")


@app.get(
    "/v1/clients/{name}/config",
    dependencies=[Depends(verify_bearer_token)],
)
async def get_client_config_endpoint(name: str) -> Response:
    try:
        text = await awg_clients.get_client_config(name=name)
    except awg_clients.AwgClientError as exc:
        raise _client_error(exc=exc, http_status=status.HTTP_404_NOT_FOUND) from exc
    return Response(content=text, media_type="text/plain; charset=utf-8")
