import asyncio

from fastapi import WebSocket
from loguru import logger

from server.ws.events import WsEvent


class ConnectionManager:
    """Хранит активные WebSocket-подключения и рассылает на них события.

    Singleton доступен через get_manager(). Не используем app.state — нужно эмитить
    из background-тасок, у которых нет ссылки на app.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, *, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.debug("ws: подключён клиент, всего={}", len(self._connections))

    async def disconnect(self, *, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.debug("ws: отключён клиент, всего={}", len(self._connections))

    async def broadcast(self, *, event: WsEvent) -> None:
        async with self._lock:
            connections = list(self._connections)
        if not connections:
            return
        payload = event.model_dump(mode="json")
        dead: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception as exc:
                logger.debug("ws: send_json упал, помечаю на удаление: {}", exc)
                dead.append(websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._connections.discard(websocket)


_manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    return _manager
