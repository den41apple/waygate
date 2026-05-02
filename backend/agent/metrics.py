from collections import deque
from threading import Lock

from shared.schemas import MetricsSnapshot


class MetricsBuffer:
    """In-memory ring buffer последних N снимков метрик.

    Сервер опрашивает GET /v1/metrics каждые 30 сек и сохраняет точку в свою БД,
    но между опросами агент тоже хранит историю в памяти на случай сбоя сервера.
    """

    def __init__(self, *, max_size: int) -> None:
        self._buffer: deque[MetricsSnapshot] = deque(maxlen=max_size)
        self._lock = Lock()

    def append(self, *, snapshot: MetricsSnapshot) -> None:
        with self._lock:
            self._buffer.append(snapshot)

    def latest(self) -> MetricsSnapshot | None:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def all(self) -> list[MetricsSnapshot]:
        with self._lock:
            return list(self._buffer)
