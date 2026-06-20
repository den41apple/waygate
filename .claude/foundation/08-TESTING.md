# 08 — Стратегия тестирования

Три уровня: backend-unit (быстрые, моки) → integration (реальный контейнер, gated) → e2e (Playwright).
Донор: `backend/{agent,server}/tests/`, `frontend/` Playwright, `.github/workflows/ci.yml`.

## Backend unit

**в доноре: `backend/agent/tests/`, `backend/server/tests/`, `conftest.py`**

- **Не входить в lifespan** для агента: `app.state.agent = AgentState()` руками + `ASGITransport`
  (APScheduler/TaskGroup не дружат с pytest-asyncio). См. `04`.
- **Fake subprocess по prefix-match:** `_FakeRunner` хранит `командный-префикс → ответ`. `_matches()`
  матчит по началу аргументов (`("iptables","-t","mangle")` ловит любой такой вызов). Позволяет
  мокать целые подсистемы, не описывая каждую команду. _Важно: моки делаются через
  `monkeypatch.setattr(module, "run_command", fake)` — все вызовы должны идти через одно
  module-level имя (это ограничивает «распил» модуля, см. примечание ниже)._
- **Control-plane:** `app.dependency_overrides[get_session]` для подмены БД-сессии; `conftest`
  выставляет `SECRET_KEY` **до** импорта `server.*`.
- `SECRET_KEY` обязателен и в тестах — `conftest.py` подкидывает значение.

> Примечание про распил модулей: если тесты патчат `module.run_command`, нельзя бездумно дробить
> модуль на подмодули, которые импортируют `run_command` напрямую — monkeypatch перестанет влиять.
> Сначала переводить тесты на patch конкретного источника, потом дробить.

## Integration (реальный контейнер, по требованию)

**в доноре: `backend/agent/tests/test_integration.py`, маркер `@pytest.mark.integration`**

- Поднимает **реальный `--privileged` docker-контейнер** с агентом (ipset/iptables/**nftables**/
  dnsmasq) + Ubuntu sshd-контейнер для SSH-flow. Гоняет HTTP к живому процессу и SSH против реального
  OpenSSH.
- **По умолчанию ВЫКЛючены** через `addopts = "-m 'not integration'"`. Запуск явно:
  `uv run pytest -m integration` (~60-90с). В CI — отдельным job'ом на release-тегах.
- **Читать реальный netfilter через `nft list chain ip <table> <chain>`, а НЕ `iptables -L`.**
  На Ubuntu 24.04 + Docker 28+ `iptables -L` показывает **shadow-chain** (iptables-nft compat), а не
  реальное состояние ядра. Тест, читающий `iptables -L`, **прошёл бы на сломанном состоянии**. Это
  ловит самый дорогой класс багов (см. `10`, `11`).
- Ловят то, чего не ловят моки: read-only filesystem, отсутствие бинарей, реальные параметры ipset,
  гонки apply, orphan-cleanup.

## e2e (Playwright)

**в доноре: `frontend/` Playwright-конфиг + specs**

- Реальный backend на **sqlite** (мигрируется при старте) + реальный frontend. `webServer` в конфиге
  **поднимает оба автоматически**.
- ⚠️ **`SECRET_KEY` надо передать в `webServer.env`** — иначе backend упадёт на старте (fail-fast).
- Покрывают auth, CRUD, онбординг-error+retry, и т.п. Прогон ~20с.

## CI-цепочка

**в доноре: `.github/workflows/ci.yml`**

```
backend (ruff + ruff format --check + mypy + pytest + openapi drift-check, артефакт openapi.json)
   └─needs─► frontend (npm ci + generate-types drift-check + typecheck + build, артефакт dist)
                 └─needs─► e2e (Playwright chromium)
```

- Джобы связаны через `needs:` (параллелизм + artifact-pass codegen-выхлопов).
- **Drift-checks** (`openapi.json`, `openapi.ts`) — падают, если схема разошлась (см. `07`).
- Integration-тесты — отдельный job на тегах (не на каждый PR: build+run контейнера долгий, а моки
  логику уже покрывают).

## Reuse-уроки

- Юнит — быстрые, через подмену subprocess/сессии; integration — редкие, но на реальном состоянии
  системы (и читать состояние «как ядро видит», а не через compat-view).
- Тесты, читающие «view» вместо реального состояния, дают ложное зелёное — проверяй то, что реально
  применилось.
- `webServer` Playwright'а + ENV (SECRET_KEY) = воспроизводимый e2e без ручного поднятия стека.
