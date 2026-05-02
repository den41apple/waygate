import asyncio

import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import create_app
from server.ws.auth import WsTokenError, sign_ws_token, verify_ws_token
from server.ws.events import EventType, WsEvent
from server.ws.manager import ConnectionManager


class _FakeWebSocket:
    """Имитирует то, что нужно ConnectionManager: accept/send_json + флаг dead для имитации обрыва."""

    def __init__(self, *, dead: bool = False):
        self.accepted = False
        self.sent: list[dict[str, object]] = []
        self.dead = dead

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self.dead:
            raise ConnectionError("simulated dead client")
        self.sent.append(payload)


async def test_jwt_sign_and_verify_roundtrip():
    token = sign_ws_token(ttl_seconds=60)
    verify_ws_token(token=token)


async def test_jwt_rejects_garbage():
    with pytest.raises(WsTokenError):
        verify_ws_token(token="not-a-jwt")


async def test_jwt_rejects_wrong_scope():
    import jwt as pyjwt

    bad = pyjwt.encode({"exp": 9999999999, "scope": "other"}, settings.secret_key, algorithm="HS256")
    with pytest.raises(WsTokenError):
        verify_ws_token(token=bad)


async def test_manager_broadcast_to_all_clients():
    manager = ConnectionManager()
    client_a = _FakeWebSocket()
    client_b = _FakeWebSocket()
    await manager.connect(websocket=client_a)
    await manager.connect(websocket=client_b)

    from datetime import UTC, datetime

    event = WsEvent(
        type=EventType.RULE_APPLIED,
        server_id=1,
        payload={"applied": 2},
        timestamp=datetime.now(tz=UTC),
    )
    await manager.broadcast(event=event)

    assert client_a.sent == client_b.sent
    assert client_a.sent[0]["type"] == "rule.applied"
    assert client_a.sent[0]["server_id"] == 1


async def test_manager_drops_dead_clients():
    manager = ConnectionManager()
    alive = _FakeWebSocket()
    dead = _FakeWebSocket(dead=True)
    await manager.connect(websocket=alive)
    await manager.connect(websocket=dead)

    from datetime import UTC, datetime

    event = WsEvent(
        type=EventType.SERVER_METRICS,
        server_id=1,
        payload={},
        timestamp=datetime.now(tz=UTC),
    )
    await manager.broadcast(event=event)

    # dead клиент должен быть удалён
    await manager.broadcast(event=event)
    assert len(alive.sent) == 2  # alive получил оба broadcast
    assert dead.sent == []


def test_ws_endpoint_rejects_without_token():
    """Через sync TestClient — websockets лучше всего тестировать им."""
    app = create_app()
    with (
        TestClient(app) as test_client,
        pytest.raises(Exception),  # noqa: B017
        test_client.websocket_connect("/ws/events"),
    ):
        pass


def test_ws_endpoint_accepts_valid_token_and_streams_event():
    app = create_app()
    token = sign_ws_token(ttl_seconds=60)
    received: list[dict[str, object]] = []

    from datetime import UTC, datetime

    from server.ws.manager import get_manager

    async def emit_event():
        await asyncio.sleep(0.05)
        await get_manager().broadcast(
            event=WsEvent(
                type=EventType.RULE_APPLIED,
                server_id=42,
                payload={"applied": 5},
                timestamp=datetime.now(tz=UTC),
            ),
        )

    with (
        TestClient(app) as test_client,
        test_client.websocket_connect(f"/ws/events?token={token}") as websocket,
    ):
        test_client.portal.start_task_soon(emit_event)
        received.append(websocket.receive_json())

    assert received[0]["type"] == "rule.applied"
    assert received[0]["server_id"] == 42
