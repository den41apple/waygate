# 01 — Стек, layout и конвенции

## Стек (проверен в бою)

**Backend (один uv-workspace, flat layout):**
- Python **3.13** (без `from __future__ import annotations`).
- **FastAPI** + **uvicorn/granian** (ASGI). Granian-команда указывает на flat-модуль:
  `agent.main:app`, не `waygate_agent.main:app`.
- **SQLModel** + **alembic** + **Postgres** (async, asyncpg) на control-plane.
- **Pydantic** для схем (контракт agent↔server в `backend/shared/`).
- **aiohttp** + **tenacity** для исходящих вызовов агента.
- **APScheduler 4** (`AsyncScheduler`) для периодических задач в агенте.
- **asyncssh** для SSH-онбординга.
- **uv** как пакетmenеджер/раннер. Build-backend — **setuptools** (не hatchling), чтобы flat-layout
  с переименованием пакета работал.

**Frontend (отдельно от backend):**
- **Vite 5** + **React 18** + **TypeScript** (strict).
- **TanStack Query v5** (серверный стейт) + **Zustand v5** (клиентский/UI-стейт, с `persist`).
- Без Tailwind — обычный CSS с CSS-переменными (тема через `[data-theme]`). _(Не догма — но
  переносимый CSS сильно упрощает дизайн-синк, см. `11`.)_
- **Playwright** для e2e.

## Layout репозитория

```
backend/                     # один uv-workspace, корневого pyproject нет
├── pyproject.toml           # workspace + ruff + mypy + pytest
├── agent/                   # waygate-agent (FastAPI-демон на каждом хосте)
├── server/                  # waygate-server (control-plane)
│   ├── api/  models/  ws/  agent_client/  provisioner/  tasks/  auth/  scripts/  alembic/
└── shared/                  # Pydantic-контракт agent↔server + парсеры
frontend/                    # Vite/React, отдельный пакет
deploy/                      # docker-compose, nginx.conf, systemd unit
.github/workflows/           # ci.yml + release-*.yml (tag-per-component)
.claude/                     # SPEC, STYLE, ROUTING_ARCHITECTURE, PROJECT_STATE, foundation/
```

**Flat layout:** `backend/agent/main.py` без вложенного `waygate_agent/`, через
`setuptools.package-dir = {"agent" = "."}`. mypy/pytest-конфиги — в `backend/pyproject.toml`.

## Стиль кода (из `STYLE.md` донора — соблюдать)

- **Без** `from __future__ import annotations` (проект на 3.13).
- Современные аннотации: `int | str | None`, `list[int]`, `dict[str, int]` — **не** `Optional/List/Dict/Union`.
- **`StrEnum`** (не `(str, Enum)`) для всех строк со смыслом — статусы, типы событий, параметры
  внешних API, имена очередей и т.п.
- **Все ключевые аргументы — keyword-only**: `def foo(*, x, y)`. Исключения — общепринятое (`max(a, b)`).
- **Импортируем конечный объект**, не namespace: `from sqlmodel import Field`, не `import sqlmodel`.
  Исключения — где namespace несёт контекст: `status.HTTP_404_NOT_FOUND`, `Scope.APP`.
- Полные имена: `request`, не `req`; `callback`, не `cb`. Однобуквенные — только `x/y/i`.
- **Комментарии и docstring'и — на русском.**
- В тестах (`test_*`) возврат не аннотируем; параметры — аннотируем (mypy `check_untyped_defs=true`).
- `ruff format` включён; вертикальное выравнивание из SPEC не сохраняем (форматтер важнее).

## Команды тулинга

```bash
# Backend: lint + типы + тесты
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q

# Frontend
cd frontend && npm run typecheck && npm run build && npm run test:e2e

# Регенерация TS-типов после правок Pydantic-схем (см. 07)
cd backend && uv run python -m server.scripts.dump_openapi > server/openapi.json
cd ../frontend && npm run generate-types
```

## ⚠️ Toolchain-first (урок этой сессии — не пропускать)

**Перед первой строкой кода проверь, что тулчейн реально установлен и виден:**

```bash
node -v && npm -v      # на чистой Windows-машине Node может ОТСУТСТВОВАТЬ (был только адобовский node.exe)
uv --version
```

- На Windows ставить Node: `winget install OpenJS.NodeJS.LTS`, затем **новый терминал** (PATH).
- Если работаешь через PowerShell-инструмент и PATH не подхватился — префиксь:
  `$env:Path = "$env:ProgramFiles\nodejs;$env:Path"`.
- Браузерные раннеры (Playwright): `npx playwright install chromium` — **дать докачать до конца**,
  не убивать процессы (прерванная закачка оставляет битую папку → `Executable doesn't exist`).

Полный разбор этих граблей — в `11-WAR-STORIES-AND-METHOD`.
