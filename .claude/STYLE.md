## Стиль кода

- Не использовать `from __future__ import annotations` — проект на Python 3.13.
- Все комментарии, докстринги и тексты интерфейса — на русском.
- Аннотации проставляются везде, кроме возврата в тестах.
- Не использовать однобуквенные переменные, кроме общепринятых (x, y, i и т.п.).
- Все параметры передавать по ключу, кроме общепринятых (`max(a, b)`, `sum(c, d)`).
- Только полные названия: `request`, а не `req`; `callback`, а не `cb`.
- В функциях тестов (начинаются с `test_`) не аннотировать возврат.
- Для перечислений использовать `StrEnum` (Python 3.11+), не `(str, Enum)`:
  ```python
  # хорошо
  class AdminRole(StrEnum):
      SUPERADMIN = "superadmin"

  # плохо
  class AdminRole(str, Enum):
      SUPERADMIN = "superadmin"
  ```
- Использовать `StrEnum`-константы везде, где строки несут смысловую нагрузку — в том числе
  для аргументов внешних API, параметров конфигурации, типов очередей и т.п.:
  ```python
  # хорошо
  arguments={"x-queue-type": QueueType.QUORUM}

  # плохо
  arguments={"x-queue-type": "quorum"}
  ```
- Не использовать устаревшие аннотации из `typing`: `Union`, `Optional`, `List`, `Dict`, `Tuple`.
  Использовать встроенный синтаксис Python 3.10+:
  ```python
  # хорошо
  x: int | str | None
  items: list[int]
  mapping: dict[str, int]

  # плохо
  x: Union[int, str, None]
  items: List[int]
  ```

### Импорты

Импортировать **конечный объект**, а не namespace:

```python
# хорошо
from asyncpg import Pool, Record
from dishka import Provider, Scope, provide

pool: Pool
record: Record

# плохо
import asyncpg
pool: asyncpg.Pool
```

**Исключения** — namespace оставляет смысловой контекст:
- Коды статусов: `status.HTTP_404_NOT_FOUND` (понятно, что это HTTP-статус)
- Enum-скоупы: `Scope.APP`, `ParseMode.MARKDOWN_V2`
- Аналогичные паттерны `Module.CONSTANT_NAME`
