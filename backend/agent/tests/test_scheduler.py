"""Tests for agent scheduler — metrics + cert expiry jobs (BACKLOG C3).

Регрессия: scheduler-jobs тихо ловят Exception и возвращают None. Если
`collect_metrics_snapshot` начнёт падать (новая версия kernel'а, изменился
формат /proc/net/dev), buffer останется пустым, control-plane не получит
точек, dashboard'ы пустые. Тесты ловят:
1. Happy-path — успешный snapshot оседает в buffer.
2. Exception в snapshot — buffer не растёт, исключение не пробрасывается.
3. Cert expiry warning срабатывает на close-to-expiry, тихо логируется на
   далёкой дате, и пропускается если cert'а нет.
"""

from datetime import UTC, datetime, timedelta

from agent import scheduler
from agent.metrics import MetricsBuffer
from shared.schemas import MetricsSnapshot


async def test_collect_metrics_job_appends_snapshot(monkeypatch) -> None:
    """Successful snapshot → попадает в buffer."""
    buffer = MetricsBuffer(max_size=10)
    fake_snapshot = MetricsSnapshot(timestamp=datetime.now(tz=UTC), tunnels=[])

    async def fake_collect() -> MetricsSnapshot:
        return fake_snapshot

    monkeypatch.setattr(scheduler, "collect_metrics_snapshot", fake_collect)
    await scheduler.run_metrics_job_once(buffer=buffer)

    points = buffer.all()
    assert len(points) == 1
    assert points[0] is fake_snapshot


async def test_collect_metrics_job_swallows_exception(monkeypatch) -> None:
    """Падение `collect_metrics_snapshot` не должно ронять scheduler."""
    buffer = MetricsBuffer(max_size=10)

    async def fake_failing() -> MetricsSnapshot:
        raise RuntimeError("simulated /proc/net/dev parse error")

    monkeypatch.setattr(scheduler, "collect_metrics_snapshot", fake_failing)

    # Не должно raise — scheduler продолжит работать на следующем тике.
    await scheduler.run_metrics_job_once(buffer=buffer)

    assert buffer.all() == []


async def test_check_cert_expiry_warns_when_close(monkeypatch, caplog) -> None:
    """Сертификат истекает <30 дней → WARNING лог."""
    expires = datetime.now(tz=UTC) + timedelta(days=10)
    monkeypatch.setattr(scheduler, "read_current_cert_metadata", lambda: (expires, ["panel.example.com"]))
    # loguru пишет через stderr, не в caplog — используем capsys-style через
    # перехват loguru-handler'а. Проще проверить, что функция не падает и
    # отрабатывает по веткам — ловим лог через monkeypatch logger.warning.
    captured: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(scheduler.logger, "warning", lambda msg, *args: captured.append((msg, args)))  # type: ignore[attr-defined]
    monkeypatch.setattr(scheduler.logger, "debug", lambda *_a, **_k: None)  # type: ignore[attr-defined]

    await scheduler._check_cert_expiry_job()

    assert any("истекает через" in msg for msg, _ in captured)


async def test_check_cert_expiry_silent_when_not_close(monkeypatch) -> None:
    """Сертификат живёт больше 30 дней — никакого WARNING'а."""
    expires = datetime.now(tz=UTC) + timedelta(days=180)
    monkeypatch.setattr(scheduler, "read_current_cert_metadata", lambda: (expires, ["panel.example.com"]))

    warnings: list[str] = []
    monkeypatch.setattr(scheduler.logger, "warning", lambda msg, *args: warnings.append(msg))  # type: ignore[attr-defined]
    monkeypatch.setattr(scheduler.logger, "debug", lambda *_a, **_k: None)  # type: ignore[attr-defined]

    await scheduler._check_cert_expiry_job()
    assert warnings == []


async def test_check_cert_expiry_no_cert(monkeypatch) -> None:
    """Сертификата нет (None) → no-op, без ошибок."""
    monkeypatch.setattr(scheduler, "read_current_cert_metadata", lambda: None)
    # Просто не должно raise.
    await scheduler._check_cert_expiry_job()


def test_calculate_days_until_expiry_basic() -> None:
    """Hot-path вычисление — 30 дней назад → -1, через 30 дней → 29 (округление вниз)."""
    future = datetime.now(tz=UTC) + timedelta(days=30)
    assert scheduler.calculate_days_until_expiry(expires_at=future) == 29

    past = datetime.now(tz=UTC) - timedelta(days=1)
    assert scheduler.calculate_days_until_expiry(expires_at=past) < 0
