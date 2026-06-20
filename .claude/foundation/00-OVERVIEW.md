# Foundation — техническая база для нового проекта

Этот набор документов — **выжимка принципов, паттернов и дорого-добытых уроков** из проекта
**Waygate** (control-plane + managed-агенты + React-фронт для GeoIP/VPN-роутинга). Waygate как
продукт хоронится, но его архитектура выверена в бою и служит фундаментом нового проекта.

**Домен нового проекта — тот же** (VPN/роутинг control-plane), но **акцент смещается с
маршрутизации трафика → на деплой и провижининг самих сервисов**: онбординг хостов, lifecycle
агентов, релизы, ops. Поэтому при чтении делай упор на `04`, `05`, `09` — а `10` (netfilter)
держи как доменный reference.

## Как пользоваться

1. Скопируй папку `.claude/foundation/` в `.claude/` нового репозитория.
2. Прочти этот overview → `01-STACK-AND-CONVENTIONS` (что и как ставим, в каком стиле пишем).
3. Дальше — по слою, который трогаешь. Каждый документ самодостаточен.
4. Каждый принцип помечен ссылкой «**в доноре Waygate: `<path>`**» — там можно подсмотреть
   реальную реализацию. Донор — это исходный Waygate-репозиторий.

## Карта архитектуры (что за система)

```
            ┌─────────────────────── control-plane (1 шт) ───────────────────────┐
            │  FastAPI + SQLModel + alembic + Postgres                            │
  React     │  • REST /api/v1/* (под global auth)                                 │
  фронт ◄──►│  • WebSocket /ws/events (реал-тайм)         ──── agent_client ────► │ ──┐
  (Vite)    │  • SSE для длинных операций (онбординг)         (aiohttp+tenacity)  │   │ authed HTTP
            │  • SSH-провижионер (asyncssh) ── онбординг ──►                      │   │ (Bearer)
            └────────────────────────────────────────────────────────────────────┘   │
                                                                                       ▼
                              ┌──── managed-агент (на каждом хосте, N шт) ────┐
                              │  FastAPI-демон (waygate-agent), root/systemd  │
                              │  • идемпотентный apply на состояние хоста     │
                              │  • self-update (wheel + systemctl restart)    │
                              │  • APScheduler (метрики/healthcheck)          │
                              └───────────────────────────────────────────────┘
```

## Сквозные принципы (повторяются во всех слоях)

- **Schema as source of truth.** Pydantic-схемы и Python-`EnumЫ` — единственный источник; TS-типы
  и WS-event-типы **генерируются** из них, дрейф ловится в CI. См. `07`.
- **Идемпотентность.** Любой `apply` = «прочитать текущее состояние → diff → снести orphan'ы →
  добавить недостающее». Повторный вызов безопасен. Честный счётчик `applied/skipped/errors`. См. `04`.
- **Fail-fast на конфиге.** Нет dev-fallback'а для критичного (`SECRET_KEY` роняет старт). См. `02`.
- **Реал-тайм через инвалидацию, а не через стейт.** Бэкенд шлёт типизированное событие → фронт
  инвалидирует нужный queryKey → TanStack Query сам перезагружает. WS — это «шина», TanStack Query —
  «кэш-стейт». См. `03`, `06`.
- **Best-effort побочки.** Audit/логирование пишутся в try/except и **никогда** не ломают ответ. См. `02`.
- **Single source of truth для доков.** Архитектурные решения и грабли живут в `.claude/*.md`, а не
  в головах. Этот набор — продолжение той же дисциплины.

## Состав набора

| Файл | О чём |
|---|---|
| `00-OVERVIEW` | (этот файл) карта и как пользоваться |
| `01-STACK-AND-CONVENTIONS` | стек, версии, layout, стиль кода, тулинг, toolchain-first |
| `02-CONTROL-PLANE` | FastAPI control-plane: auth, config, SQLModel/alembic, agent_client, tasks, audit |
| `03-REALTIME-WEBSOCKETS` | **ключевое** — WS-петля event→broadcast→invalidate + SSE |
| `04-AGENT-DAEMON-AND-LIFECYCLE` | managed-агент: idempotent apply, self-update, subprocess, scheduler |
| `05-PROVISIONING-AND-ONBOARDING` | SSH-онбординг (asyncssh), provisioner-шаги, SSE-job |
| `06-FRONTEND-PATTERNS` | api-обёртка, TanStack Query, Zustand persist, modals, SSE-consume |
| `07-TYPE-SAFETY-AND-CODEGEN` | openapi.json + ws-event-codegen + CI drift |
| `08-TESTING` | unit/integration/e2e стратегия + CI-цепочка |
| `09-DEPLOY-AND-OPS` | compose, nginx-edge, systemd-hardening, релизные пайплайны |
| `10-ROUTING-DOMAIN` | доменный netfilter-reference (сжатый) |
| `11-WAR-STORIES-AND-METHOD` | дорогие грабли + метод работы (читать до старта!) |

> Совет: перед первым кодом нового проекта прочитай `11-WAR-STORIES-AND-METHOD` — он экономит дни.
