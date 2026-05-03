import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from server.api import audit as audit_router
from server.api import auth as auth_router
from server.api import clients as clients_router
from server.api import directions as directions_router
from server.api import dns as dns_router
from server.api import geoip as geoip_router
from server.api import ipset_groups as ipset_groups_router
from server.api import metrics as metrics_router
from server.api import provision as provision_router
from server.api import rules as rules_router
from server.api import servers as servers_router
from server.api import tls as tls_router
from server.api import tunnels as tunnels_router
from server.audit import AuditMiddleware
from server.auth.bootstrap import bootstrap_admin_from_env
from server.auth.dependencies import require_user
from server.config import settings
from server.db import get_session_maker
from server.tasks.healthcheck import healthcheck_loop
from server.tasks.metrics_poller import metrics_poller_loop
from server.ws import router as ws_router


class ServerState:
    def __init__(self) -> None:
        self.poller_task: asyncio.Task[None] | None = None
        self.healthcheck_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = ServerState()
    app.state.server = state
    session_maker = get_session_maker()
    # Bootstrap админа из ENV до запуска фоновых тасок — иначе healthcheck
    # успеет начать опрос пустого парка.
    await bootstrap_admin_from_env(session_maker=session_maker)
    state.poller_task = asyncio.create_task(metrics_poller_loop(session_maker=session_maker))
    state.healthcheck_task = asyncio.create_task(healthcheck_loop(session_maker=session_maker))
    logger.info("Waygate control-plane запущен на порту {}", settings.port)
    try:
        yield
    finally:
        for task in (state.poller_task, state.healthcheck_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("Waygate control-plane остановлен")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Waygate Control Plane",
        version="0.1.0",
        lifespan=lifespan,
        # Не редиректим trailing-slash формы — лишние 307/301 шумят в логах nginx,
        # клиенты должны идти на каноническую форму без trailing slash.
        redirect_slashes=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Audit-middleware пишет mutations в audit_entries. Стоит ПЕРЕД роутерами,
    # чтобы видеть и тело запроса, и финальный status_code.
    app.add_middleware(AuditMiddleware, session_maker=get_session_maker())

    # /auth/login — публичный (без require_user), всё остальное — закрыто.
    app.include_router(auth_router.router, prefix="/api/v1")

    protected = [Depends(require_user)]
    app.include_router(servers_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(provision_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(rules_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(dns_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(geoip_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(metrics_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(tls_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(tunnels_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(audit_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(clients_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(ipset_groups_router.router, prefix="/api/v1", dependencies=protected)
    app.include_router(directions_router.router, prefix="/api/v1", dependencies=protected)
    # WS-роутер сам прописывает свои пути — без общего prefix
    app.include_router(ws_router.router)

    # Prometheus /metrics — без префикса. Не регистрируем эндпоинт `/metrics` на 8000
    # за пределами /api, чтобы он не конфликтовал с фронтовыми правилами nginx.
    Instrumentator(excluded_handlers=["/metrics", "/ws/.*"]).instrument(app).expose(app, endpoint="/metrics")

    return app


app = create_app()
