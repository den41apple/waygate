import asyncio

import pytest

from server.api import servers as servers_api
from server.tasks import update_runner as update_runner_module
from server.update_registry import get_update_registry
from server.ws.events import EventType, WsEvent
from shared.schemas import AgentStatus, UpdateResponse, UpdateStatus


@pytest.fixture(autouse=True)
def _clear_update_registry():
    """Registry — module-level singleton. Между тестами зачищаем, иначе job
    от предыдущего теста виден следующему (если server_id совпадает)."""
    get_update_registry()._jobs.clear()
    yield
    get_update_registry()._jobs.clear()


@pytest.fixture
async def server_id(client):
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.1", "port": 7743, "name": "edge", "token": "tok"},
    )
    return response.json()["id"]


async def test_update_starts_background_job_and_streams_done(
    client,
    server_id,
    monkeypatch,
    session_maker,
):
    """Happy-path: POST /update → 202 (job started) → SSE-стрим эмиттит progress
    + done; БД обновляется на подтверждённую версию; WS event broadcast'ится."""
    broadcasts: list[WsEvent] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.host = kwargs.get("host", "")
            self.port = kwargs.get("port", 0)

        async def update(self, *, request):
            return UpdateResponse(previous_version="0.1.0", status=UpdateStatus.RESTARTING)

        async def status(self):
            return AgentStatus(
                version="0.2.0",
                uptime_seconds=1,
                hostname="edge",
                awg_containers=[],
                rules_applied=0,
                tls_mode=None,
            )

    class FakeManager:
        async def broadcast(self, *, event):
            broadcasts.append(event)

    fake_manager = FakeManager()
    monkeypatch.setattr(update_runner_module, "AgentClient", FakeClient)
    monkeypatch.setattr(update_runner_module, "get_manager", lambda: fake_manager)
    monkeypatch.setattr(servers_api, "get_session_maker", lambda: session_maker)
    # Polling-интервал короче чтобы тест шёл быстро.
    monkeypatch.setattr(update_runner_module, "_RECONNECT_POLL_INTERVAL_SECONDS", 0.01)

    response = await client.post(
        f"/api/v1/servers/{server_id}/update",
        json={"version": "0.2.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["server_id"] == server_id
    assert body["target_version"] == "0.2.0"
    assert body["stream_url"] == f"/api/v1/servers/{server_id}/update/stream"

    # Дать background-task завершиться
    job = get_update_registry().get(server_id=server_id)
    assert job is not None and job.task is not None
    await asyncio.wait_for(job.task, timeout=2)

    # Стрим должен реплеить историю и завершиться "end"
    async with client.stream("GET", f"/api/v1/servers/{server_id}/update/stream") as stream:
        assert stream.status_code == 200
        text_chunks: list[str] = []
        async for line in stream.aiter_lines():
            text_chunks.append(line)
            if '"type": "end"' in line or '"type":"end"' in line:
                break

    full = "\n".join(text_chunks)
    assert "Запрашиваю агент" in full
    assert "Версия 0.2.0 подтверждена" in full
    assert "done" in full

    # Версия в БД и WS event
    response = await client.get(f"/api/v1/servers/{server_id}")
    assert response.json()["version"] == "0.2.0"
    assert any(event.type is EventType.SERVER_AGENT_UPDATED for event in broadcasts)


async def test_update_emits_error_when_agent_unreachable(
    client,
    server_id,
    monkeypatch,
    session_maker,
):
    """Если агент недоступен — POST всё равно вернёт 202 (job стартовал),
    но в стриме придёт error-event, БД не обновляется."""
    from server.agent_client import AgentUnreachable

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def update(self, *, request):
            raise AgentUnreachable("connection refused")

    monkeypatch.setattr(update_runner_module, "AgentClient", FakeClient)
    monkeypatch.setattr(servers_api, "get_session_maker", lambda: session_maker)

    response = await client.post(
        f"/api/v1/servers/{server_id}/update",
        json={"version": "0.2.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 202

    job = get_update_registry().get(server_id=server_id)
    assert job is not None and job.task is not None
    await asyncio.wait_for(job.task, timeout=2)

    async with client.stream("GET", f"/api/v1/servers/{server_id}/update/stream") as stream:
        body_text = ""
        async for line in stream.aiter_lines():
            body_text += line + "\n"
            if '"type": "end"' in line or '"type":"end"' in line:
                break

    assert "error" in body_text
    assert "connection refused" in body_text


async def test_update_404_when_server_missing(client):
    response = await client.post(
        "/api/v1/servers/9999/update",
        json={"version": "0.2.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 404


async def test_update_uses_ssh_flow_when_ssh_creds_saved(
    client,
    server_id,
    monkeypatch,
    session_maker,
):
    """Если у Server'а есть ssh_password_encrypted — update идёт через SSH (не self-update).

    Мокаем ssh_connect чтобы записать вызванные команды; проверяем что в SSE-логе
    появились шаги [ssh] (а не [self-update]).
    """
    import contextlib

    # Сохраняем ssh-пароль через PATCH (он сам зашифрует Fernet'ом).
    patch_response = await client.patch(
        f"/api/v1/servers/{server_id}",
        json={"ssh_password": "ssh-test-pass"},
    )
    assert patch_response.status_code == 200

    ssh_commands: list[str] = []

    class FakeSshSession:
        async def run(self, *, command: str, check: bool = True) -> object:
            ssh_commands.append(command)

            class _R:
                returncode = 0
                # `id -u` → "0" чтобы ensure_root_or_sudo не дёргал sudo-проверку.
                stdout = "0\n" if command == "id -u" else ""
                stderr = ""

            return _R()

        async def ensure_root_or_sudo(self) -> None:
            await self.run(command="id -u")

        def enable_sudo(self) -> None:
            pass

        async def upload_bytes(self, *, path: str, content: bytes) -> None:
            ssh_commands.append(f"sftp upload {path}")

    @contextlib.asynccontextmanager
    async def fake_ssh_connect(**kwargs):
        # Проверяем что SSH вызывается с расшифрованным паролем.
        assert kwargs["password"] == "ssh-test-pass"
        assert kwargs["host"] == "10.0.0.1"
        yield FakeSshSession()

    class FakeClient:
        """Используется для polling /v1/status после SSH-restart'а."""

        def __init__(self, **kwargs):
            pass

        async def status(self):
            return AgentStatus(
                version="0.3.0",
                uptime_seconds=1,
                hostname="edge",
                awg_containers=[],
                rules_applied=0,
                tls_mode=None,
            )

    broadcasts: list[WsEvent] = []

    class FakeManager:
        async def broadcast(self, *, event):
            broadcasts.append(event)

    monkeypatch.setattr(update_runner_module, "ssh_connect", fake_ssh_connect)
    monkeypatch.setattr(update_runner_module, "AgentClient", FakeClient)
    fake_manager_inst = FakeManager()
    monkeypatch.setattr(update_runner_module, "get_manager", lambda: fake_manager_inst)
    monkeypatch.setattr(servers_api, "get_session_maker", lambda: session_maker)
    monkeypatch.setattr(update_runner_module, "_RECONNECT_POLL_INTERVAL_SECONDS", 0.01)

    response = await client.post(
        f"/api/v1/servers/{server_id}/update",
        json={"version": "0.3.0", "wheel_url": "https://example.com/wheel.whl"},
    )
    assert response.status_code == 202

    job = get_update_registry().get(server_id=server_id)
    assert job is not None and job.task is not None
    await asyncio.wait_for(job.task, timeout=2)

    # SSH-команды реально вызывались
    assert any("curl -fsSL" in c for c in ssh_commands)
    assert any("python3 -m venv" in c for c in ssh_commands)
    assert any("systemctl restart waygate-agent" in c for c in ssh_commands)
    assert any("mv /opt/waygate-agent" in c for c in ssh_commands)

    # SSE содержит [ssh]-шаги
    async with client.stream("GET", f"/api/v1/servers/{server_id}/update/stream") as stream:
        text = ""
        async for line in stream.aiter_lines():
            text += line + "\n"
            if '"type": "end"' in line or '"type":"end"' in line:
                break

    assert "Restart waygate-agent" in text
    assert "Версия 0.3.0 подтверждена" in text


async def test_update_stream_404_when_no_active_job(client):
    """Stream 404 когда для server_id не было активных update-job'ов.
    Не используем фикстуру `server_id` — registry singleton общий между тестами,
    и более ранние тесты могли оставить запись по id=1."""
    response = await client.post(
        "/api/v1/servers",
        json={"host": "10.0.0.123", "port": 7743, "name": "fresh", "token": "tok"},
    )
    fresh_id = response.json()["id"]

    response = await client.get(f"/api/v1/servers/{fresh_id}/update/stream")
    assert response.status_code == 404
