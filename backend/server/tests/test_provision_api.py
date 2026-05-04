import asyncio
from contextlib import asynccontextmanager

import pytest

from server.provisioner import service as service_module
from server.provisioner.registry import ProvisionEventType, get_registry
from server.provisioner.ssh import CommandResult
from shared.schemas import AgentStatus


class _FakeSshSession:
    """Минимальная реализация SshSession для интеграционного теста."""

    def __init__(self, *, responses: dict[str, CommandResult] | None = None):
        self.responses = responses or {}
        self.calls: list[str] = []
        self.files_written: list[tuple[str, str, str]] = []

    async def run(self, *, command: str, check: bool = True) -> CommandResult:
        self.calls.append(command)
        for prefix, response in self.responses.items():
            if command.startswith(prefix):
                return response
        return CommandResult(returncode=0, stdout="", stderr="")

    async def write_file(self, *, path: str, content: str, mode: str = "0644") -> None:
        self.files_written.append((path, content, mode))


@pytest.fixture
def stub_provisioning(monkeypatch):
    """Делает run_provision детерминированной без реальных SSH/HTTP вызовов."""

    async def fake_run_provision(*, job, server_id, **_kwargs):
        await job.emit(type=ProvisionEventType.PROGRESS, message="connect ok")
        await job.emit(type=ProvisionEventType.PROGRESS, message="install deps ok")
        await job.emit(type=ProvisionEventType.DONE, message=f"server id={server_id} готов")
        await job.finish()

    from server.api import provision as provision_api

    monkeypatch.setattr(service_module, "run_provision", fake_run_provision)
    monkeypatch.setattr(provision_api, "run_provision", fake_run_provision)


async def test_provision_creates_server_and_emits_events(client, stub_provisioning):
    response = await client.post(
        "/api/v1/servers/provision",
        json={
            "host": "10.0.0.1",
            "ssh_user": "root",
            "ssh_password": "fake-pass",
            "name": "edge-eu",
            "region": "EU",
        },
    )
    assert response.status_code == 202
    body = response.json()
    server_id = body["id"]
    assert body["status"] == "provisioning"

    # Дать фоновой fake_run_provision прогнаться до конца
    await asyncio.sleep(0.05)

    # Стрим должен реплеить историю и закрыться "end"
    async with client.stream("GET", f"/api/v1/servers/{server_id}/provision/stream") as response:
        assert response.status_code == 200
        text_chunks: list[str] = []
        async for line in response.aiter_lines():
            text_chunks.append(line)
            if '"type": "end"' in line or '"type":"end"' in line:
                break

    full = "\n".join(text_chunks)
    assert "connect ok" in full
    assert "install deps ok" in full
    assert "done" in full


async def test_provision_stream_404_when_no_job(client):
    response = await client.get("/api/v1/servers/9999/provision/stream")
    assert response.status_code == 404


async def test_provision_reuses_existing_record_for_same_host(client, stub_provisioning):
    """Повторный provision на тот же host обновляет существующий Server-record,
    не плодит дубликаты — иначе старая запись висела бы в БД с устаревшим токеном
    и metrics_poller бил бы по ней с 401."""
    payload = {
        "host": "10.99.0.1",
        "ssh_user": "root",
        "ssh_password": "fake-pass",
        "name": "edge-rs",
    }
    first = await client.post("/api/v1/servers/provision", json=payload)
    assert first.status_code == 202
    first_id = first.json()["id"]

    await asyncio.sleep(0.05)

    # Имитируем «снёс агент на target и заново запустил онбординг с другим именем»
    second = await client.post(
        "/api/v1/servers/provision",
        json={**payload, "name": "edge-rs-renamed"},
    )
    assert second.status_code == 202
    second_id = second.json()["id"]

    assert first_id == second_id, "ожидался upsert по host, а не новый Server-record"
    assert second.json()["name"] == "edge-rs-renamed"
    assert second.json()["status"] == "provisioning"

    # И в БД должна быть ровно одна запись
    listing = await client.get("/api/v1/servers")
    same_host = [server for server in listing.json()["servers"] if server["host"] == "10.99.0.1"]
    assert len(same_host) == 1


async def test_provision_handles_existing_duplicates_by_host(
    client,
    session_maker,
    stub_provisioning,
):
    """Регрессия на `MultipleResultsFound`: до фикса в БД могли накопиться
    дубликаты Server'ов с одним и тем же host (старый баг — provision всегда
    создавал новую запись). После фикса upsert делает `.first()` с order_by
    desc(id) — берёт самую свежую и продолжает работать, не падает с 500."""
    from server.models import Server, ServerStatus

    # Имитируем накопленный мусор: две записи с одинаковым host.
    async with session_maker() as session:
        for index in range(2):
            session.add(
                Server(
                    host="10.99.0.42",
                    port=7743,
                    name=f"old-record-{index}",
                    token=f"stale-token-{index}",
                    status=ServerStatus.ERROR.value,
                ),
            )
        await session.commit()

    # POST /provision на тот же host не падает, переиспользует запись.
    response = await client.post(
        "/api/v1/servers/provision",
        json={
            "host": "10.99.0.42",
            "ssh_user": "root",
            "ssh_password": "fake",
            "name": "edge-fresh",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["name"] == "edge-fresh"


async def test_provision_requires_password_or_key(client):
    response = await client.post(
        "/api/v1/servers/provision",
        json={"host": "10.0.0.1", "name": "edge", "ssh_user": "root"},
    )
    assert response.status_code == 422


async def test_real_orchestrator_writes_token_and_status(client, session_maker, monkeypatch):
    """Проверяет реальный run_provision при подменённых ssh_connect/wait_for_agent."""

    @asynccontextmanager
    async def fake_ssh_connect(**_kwargs):
        yield _FakeSshSession(
            responses={
                "cat /etc/os-release": CommandResult(
                    returncode=0,
                    stdout='ID=ubuntu\nVERSION_ID="22.04"\n',
                    stderr="",
                ),
                "id -u": CommandResult(returncode=0, stdout="0\n", stderr=""),
                "docker ps": CommandResult(returncode=0, stdout="", stderr=""),
            },
        )

    async def fake_wait_for_agent(*, host, port, token, timeout_seconds, emit):
        await emit("(stub) agent up")
        return AgentStatus(
            version="0.1.0",
            uptime_seconds=1,
            hostname="edge",
            awg_containers=[],
            rules_applied=0,
            tls_mode=None,
        )

    from server.api import provision as provision_api

    monkeypatch.setattr(service_module, "ssh_connect", fake_ssh_connect)
    monkeypatch.setattr(service_module, "wait_for_agent", fake_wait_for_agent)
    # run_provision получает session_maker через get_session_maker() из api/provision —
    # подмена нужна, иначе фоновая таска уйдёт в production-engine без таблиц
    monkeypatch.setattr(provision_api, "get_session_maker", lambda: session_maker)

    response = await client.post(
        "/api/v1/servers/provision",
        json={
            "host": "10.0.0.2",
            "ssh_user": "root",
            "ssh_password": "fake",
            "name": "edge-us",
        },
    )
    assert response.status_code == 202
    server_id = response.json()["id"]

    job = get_registry().get(server_id=server_id)
    assert job is not None
    assert job.task is not None
    await job.task

    response = await client.get(f"/api/v1/servers/{server_id}")
    body = response.json()
    assert body["status"] == "online"
    assert body["version"] == "0.1.0"
