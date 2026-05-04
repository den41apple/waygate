import secrets
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.config import settings
from server.models import Server, ServerStatus
from server.provisioner.registry import ProvisionEventType, ProvisionJob
from server.provisioner.ssh import SshError, ssh_connect
from server.provisioner.steps import (
    StepError,
    configure_dns_resolver,
    deploy_agent,
    detect_awg_containers,
    install_deps,
    verify_os,
    wait_for_agent,
)


async def _update_server_after_success(
    *,
    server_id: int,
    token: str,
    version: str,
    awg_container_names: list[str],
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        server = await session.get(Server, server_id)
        if server is None:
            logger.warning("provision: server id={} исчез из БД до завершения", server_id)
            return
        server.token = token
        server.version = version
        server.awg_containers = awg_container_names
        server.status = ServerStatus.ONLINE.value
        server.last_seen_at = datetime.now()
        await session.commit()


async def _mark_server_error(*, server_id: int, session_maker: async_sessionmaker[AsyncSession]) -> None:
    async with session_maker() as session:
        server = await session.get(Server, server_id)
        if server is None:
            return
        server.status = ServerStatus.ERROR.value
        await session.commit()


async def run_provision(
    *,
    job: ProvisionJob,
    server_id: int,
    host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_password: str | None,
    ssh_private_key: str | None,
    agent_port: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Background-таска: SSH → шаги → запись результата в БД → emit done/error."""

    async def emit(message: str) -> None:
        await job.emit(type=ProvisionEventType.PROGRESS, message=message)

    try:
        await emit(f"Подключаюсь по SSH к {ssh_user}@{host}:{ssh_port}…")
        async with ssh_connect(
            host=host,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            private_key=ssh_private_key,
        ) as ssh:
            await emit("SSH-соединение установлено")
            await verify_os(ssh=ssh, emit=emit)
            await install_deps(ssh=ssh, emit=emit)
            await configure_dns_resolver(ssh=ssh, emit=emit)
            awg_names = await detect_awg_containers(ssh=ssh, emit=emit)
            token = secrets.token_urlsafe(48)
            await deploy_agent(
                ssh=ssh,
                token=token,
                wheel_url=settings.agent_wheel_url,
                agent_port=agent_port,
                emit=emit,
            )

        agent_status = await wait_for_agent(
            host=host,
            port=agent_port,
            token=token,
            timeout_seconds=settings.provision_healthcheck_timeout_seconds,
            emit=emit,
        )
    except (SshError, StepError) as exc:
        logger.warning("provision: server_id={} ошибка: {}", server_id, exc)
        await _mark_server_error(server_id=server_id, session_maker=session_maker)
        await job.emit(type=ProvisionEventType.ERROR, message=str(exc))
        await job.finish()
        return
    except Exception as exc:
        logger.exception("provision: server_id={} непредвиденная ошибка", server_id)
        await _mark_server_error(server_id=server_id, session_maker=session_maker)
        await job.emit(type=ProvisionEventType.ERROR, message=f"внутренняя ошибка: {exc}")
        await job.finish()
        return

    container_names = [container.name for container in agent_status.awg_containers] or awg_names
    await _update_server_after_success(
        server_id=server_id,
        token=token,
        version=agent_status.version,
        awg_container_names=container_names,
        session_maker=session_maker,
    )
    await job.emit(type=ProvisionEventType.DONE, message=f"Сервер id={server_id} готов")
    await job.finish()
