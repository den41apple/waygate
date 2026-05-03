"""In-memory registry активных self-update job'ов агентов.

Структура и интерфейс — копия `server/provisioner/registry.py` (там тот же
паттерн «job-on-demand с pluggable subscribers»). Не наследуем общую базу:
классы маленькие, проще иметь две независимых копии.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel


class UpdateEventType(StrEnum):
    PROGRESS = "progress"
    DONE = "done"
    ERROR = "error"


class UpdateEvent(BaseModel):
    type: UpdateEventType
    message: str
    timestamp: datetime


class UpdateJob:
    """Состояние одного активного self-update.

    Хранит всю историю событий, чтобы поздний SSE-подписчик увидел старт.
    Несколько подписчиков работают независимо через свои очереди.
    """

    def __init__(self, *, server_id: int) -> None:
        self.server_id = server_id
        self._events: list[UpdateEvent] = []
        self._subscribers: list[asyncio.Queue[UpdateEvent | None]] = []
        self._lock = asyncio.Lock()
        self.completed = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def emit(self, *, type: UpdateEventType, message: str) -> None:
        event = UpdateEvent(type=type, message=message, timestamp=datetime.now(tz=UTC))
        async with self._lock:
            self._events.append(event)
            for queue in self._subscribers:
                queue.put_nowait(event)

    async def finish(self) -> None:
        async with self._lock:
            self.completed.set()
            for queue in self._subscribers:
                queue.put_nowait(None)
            self._subscribers.clear()

    async def subscribe(self) -> AsyncIterator[UpdateEvent]:
        """Отдаёт всю накопленную историю + новые события до finish()."""
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with self._lock:
            for event in self._events:
                queue.put_nowait(event)
            if self.completed.is_set():
                queue.put_nowait(None)
            else:
                self._subscribers.append(queue)
        try:
            while True:
                next_event: UpdateEvent | None = await queue.get()
                if next_event is None:
                    return
                yield next_event
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)


class UpdateRegistry:
    """Singleton: server_id → UpdateJob. Хранится в памяти процесса."""

    def __init__(self) -> None:
        self._jobs: dict[int, UpdateJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, server_id: int) -> UpdateJob:
        async with self._lock:
            existing = self._jobs.get(server_id)
            if existing is not None and not existing.completed.is_set():
                raise RuntimeError(f"update для server_id={server_id} уже идёт")
            job = UpdateJob(server_id=server_id)
            self._jobs[server_id] = job
            return job

    def get(self, *, server_id: int) -> UpdateJob | None:
        return self._jobs.get(server_id)


_registry = UpdateRegistry()


def get_update_registry() -> UpdateRegistry:
    return _registry
