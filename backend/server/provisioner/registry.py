import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from server.ws.events import EventType, WsEvent
from server.ws.manager import get_manager


class ProvisionEventType(StrEnum):
    PROGRESS = "progress"  # очередное сообщение шага
    DONE = "done"  # провижионинг успешно завершён
    ERROR = "error"  # провижионинг провалился


class ProvisionEvent(BaseModel):
    type: ProvisionEventType
    message: str
    timestamp: datetime


class ProvisionJob:
    """Состояние одного активного провижионинга.

    Хранит всю историю событий, чтобы поздний SSE-подписчик увидел старт.
    Несколько подписчиков работают независимо через свои очереди.
    """

    def __init__(self, *, server_id: int) -> None:
        self.server_id = server_id
        self._events: list[ProvisionEvent] = []
        self._subscribers: list[asyncio.Queue[ProvisionEvent | None]] = []
        self._lock = asyncio.Lock()
        self.completed = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def emit(self, *, type: ProvisionEventType, message: str) -> None:
        event = ProvisionEvent(type=type, message=message, timestamp=datetime.now(tz=UTC))
        async with self._lock:
            self._events.append(event)
            for queue in self._subscribers:
                queue.put_nowait(event)
        # Параллельно с SSE-стримом дублируем в общий WS-канал — фронт может слушать
        # один общий /ws/events вместо отдельного SSE на каждый онбординг.
        await get_manager().broadcast(
            event=WsEvent(
                type=EventType.PROVISION_PROGRESS,
                server_id=self.server_id,
                payload={"step_type": type.value, "message": message},
                timestamp=event.timestamp,
            ),
        )

    async def finish(self) -> None:
        async with self._lock:
            self.completed.set()
            for queue in self._subscribers:
                queue.put_nowait(None)
            self._subscribers.clear()

    async def subscribe(self) -> AsyncIterator[ProvisionEvent]:
        """Отдаёт всю накопленную историю + новые события до finish()."""
        queue: asyncio.Queue[ProvisionEvent | None] = asyncio.Queue()
        async with self._lock:
            for event in self._events:
                queue.put_nowait(event)
            if self.completed.is_set():
                queue.put_nowait(None)
            else:
                self._subscribers.append(queue)
        try:
            while True:
                next_event: ProvisionEvent | None = await queue.get()
                if next_event is None:
                    return
                yield next_event
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)


class ProvisionRegistry:
    """Singleton: server_id → ProvisionJob. Хранится в памяти процесса."""

    def __init__(self) -> None:
        self._jobs: dict[int, ProvisionJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, server_id: int) -> ProvisionJob:
        async with self._lock:
            existing = self._jobs.get(server_id)
            if existing is not None and not existing.completed.is_set():
                raise RuntimeError(f"провижионинг для server_id={server_id} уже идёт")
            job = ProvisionJob(server_id=server_id)
            self._jobs[server_id] = job
            return job

    def get(self, *, server_id: int) -> ProvisionJob | None:
        return self._jobs.get(server_id)


_registry = ProvisionRegistry()


def get_registry() -> ProvisionRegistry:
    return _registry
