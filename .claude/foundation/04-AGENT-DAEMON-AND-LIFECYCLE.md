# 04 — Managed-агент: демон, идемпотентность, self-update

Паттерн «агент на каждом хосте, которым рулит control-plane по authed-HTTP». Донор: `backend/agent/`.
Для нового проекта (фокус на деплое сервисов) — **это центральный документ**.

## Структура демона + тестируемый state

**в доноре: `backend/agent/main.py`, `tests/test_main.py`**

- FastAPI-app + `lifespan` (async context manager). Lifespan управляет **только жизненным циклом
  планировщика** (start/stop), а не построением состояния.
- Всё мутабельное — в классе `AgentState` (ring-buffer метрик, scheduler, счётчики), кладётся в
  `app.state.agent`.
- **State строится ОТДЕЛЬНО от async-контекста.** Поэтому юнит-тесты **не входят в lifespan**
  (APScheduler/TaskGroup не дружат с pytest-asyncio fixture'ами): просто `app.state.agent =
  AgentState()` руками и `ASGITransport`.

**Reuse-урок:** разведи «построение state» и «запуск async-машинерии» — тесты станут быстрыми и
детерминированными.

## Auth + config агента

**в доноре: `backend/agent/auth.py`, `config.py`**

- Bearer-токен, единая FastAPI-зависимость. Сравнение через **`secrets.compare_digest`** (timing-safe).
- Если токен в ENV не задан → **`503` «не сконфигурирован»**, а не `401`. Различай «сервис не настроен»
  и «токен неверный».
- Config — простой класс на `envparse`, значения читаются на импорте, один глобальный `settings`.
  Без async-загрузки конфига, без фабрик — масштабируется на 20+ настроек.

## Идемпотентный apply (главный паттерн)

**в доноре: `backend/agent/routing.py::apply_rules`** (но паттерн доменно-нейтрален)

Любое приведение хоста к желаемому состоянию = **read → diff → remove-orphans → add-missing**:

1. **Прочитать текущее состояние** ядра/системы (отдельно по scope и по семейству). Ошибка чтения
   не блокирует apply — логируется, состояние считается пустым.
2. **Вычислить желаемое** из входных правил (с фильтрами — напр. скип v6 если у iface нет global IPv6).
3. **Сначала снести orphan'ы** (всё, чего нет в desired) — чистый старт исключает интерференцию.
4. **Добавить/обновить недостающее.** Каждый `_ensure_*()` возвращает `bool` «изменилось ли» —
   **честный счётчик `applied` считает реально изменённое, а не «сколько включено»**.
5. **Persist `last-apply.json`** (`{ts, rule_count, applied, errors, succeeded}`) в `/var/lib/...` —
   control-plane по нему решает, нужен ли retry (healthcheck перезапускает apply, если были errors).

Доп. приёмы:
- **Retry x2 + backoff** для операций, чувствительных к гонке (напр. switch в netns после
  `docker run -d`, пока iface ещё не готов). Вызывающему прозрачно.
- **Persistent «touched» state** (что трогали в прошлый раз) — чтобы вычистить orphan'ы там, где
  объект уже удалён из desired (иначе хвосты в чужих netns).

**Reuse-урок:** идемпотентность + честная отчётность + персист последнего применения = система,
которая сама восстанавливается, и которую можно повторно «пнуть» без страха.

## subprocess_runner — единый тип ошибки

**в доноре: `backend/agent/subprocess_runner.py`**

- `async def run_command(command: list[str], *, stdin=None, check=True) -> str` поверх
  `asyncio.create_subprocess_exec`.
- **`FileNotFoundError` оборачивается в `CommandError(returncode=127)`.** На минималистичных хостах
  (LXC, slim-контейнеры) может не быть `systemctl`/`ipset`/`nft` — вызывающим достаточно **одного
  `except CommandError`**, без отдельного `except FileNotFoundError`.
- stderr всегда захватывается и кладётся в текст исключения (для дебага). `decode(errors="replace")`.
- `check=False` → даже 127 возвращает `""` (для идемпотентных проверок «есть ли оно»).

## Атомарные swap'ы

**в доноре: `backend/agent/geoip.py`, `ipset.py`**

Обновление набора без обрыва обслуживания: создать `<name>_new` теми же параметрами → залить
bulk'ом (`ipset restore` через stdin) → **`ipset swap` (атомарно)** → удалить tmp. Правила
матчатся по логическому имени и не замечают подмены. На ошибке заливки — destroy tmp + raise (rollback).
_Тот же приём применим к любому «пересобрать таблицу/набор целиком и подменить атомарно»._

## Self-update (критичный для парка агентов)

**в доноре: `backend/agent/updater.py`**

1. **Скачать wheel** новой версии.
2. Собрать **detached bash-скрипт**, который: подождёт пару секунд (чтобы ответ дошёл до
   control-plane) → `python -m venv /opt/agent.new` → `pip install <wheel>` → **атомарный swap**
   (`mv /opt/agent /opt/agent.bak && mv .new /opt/agent`) → `systemctl restart` → cleanup.
3. Запустить скрипт **detached** (`start_new_session=True` / setsid) — он переживёт рестарт самого
   агента systemd'ом.
4. Сразу вернуть `status=RESTARTING`.

**Пути — критично (грабли systemd-sandbox'а):**
- Все артефакты в **`/var/lib/<agent>/`** (`update.log`, swap-скрипт). **Не `/tmp`** (его убивает
  `PrivateTmp=true` при рестарте), **не `/var/log`** (его не даёт писать `ProtectSystem=strict`).
- Никогда не `pip install --upgrade` в живой venv — **только сборка нового + атомарный swap**.

## Recovery / throttle

**в доноре: `backend/agent/routing.py`, `awg_clients.py`**

- **Авто-восстановление при «incompatible»**: если системная команда падает с «table incompatible»
  (смешение iptables-nft и native nft) — авто-`flush` + повтор apply (rules идемпотентны).
- **Дедуп дублей** (правило могло задвоиться при переключении legacy↔nft backend): посчитать,
  лишние удалить, не блокируя apply.
- **Enforce-параметры на startup** (напр. MTU существующих iface'ов, задеплоенных до изменения
  дефолта) — без рестарта контейнера. Non-fatal: warning + продолжаем.

## Планировщик

**в доноре: `backend/agent/scheduler.py`**

APScheduler 4 `AsyncScheduler` (полностью async). Lifecycle — явный `start()`/`stop()` (а не
контекст-менеджер у вызывающего), `add_schedule(callable, IntervalTrigger(seconds=N))` +
`start_in_background()`. Job — обычная async-функция. Создание планировщика отделено от запуска →
тесты его пропускают.
