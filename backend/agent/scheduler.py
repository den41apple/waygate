from datetime import datetime, timedelta

from apscheduler import AsyncScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from agent.config import settings
from agent.metrics import MetricsBuffer
from agent.tls import read_current_cert_metadata
from agent.tunnels import collect_metrics_snapshot

_CERT_RENEWAL_THRESHOLD_DAYS = 30


async def _collect_metrics_job(*, buffer: MetricsBuffer) -> None:
    try:
        snapshot = await collect_metrics_snapshot()
    except Exception as exc:
        logger.warning("scheduler.metrics: {}", exc)
        return
    buffer.append(snapshot=snapshot)


async def _check_cert_expiry_job() -> None:
    """Проверяет cert.pem — если до истечения <30 дней, надо renewal.

    Сейчас, без полноценного ACME, мы только логируем. В Фазе 6+ здесь будет
    тригер ACME-renewal или эмиссия события, чтобы control-plane инициировал re-apply.
    """
    metadata = read_current_cert_metadata()
    if metadata is None:
        return
    expires_at, domains = metadata
    days_left = (expires_at - datetime.now(tz=expires_at.tzinfo)).days
    if days_left < _CERT_RENEWAL_THRESHOLD_DAYS:
        logger.warning(
            "scheduler.cert: сертификат для {} истекает через {} дней — нужен renewal",
            domains,
            days_left,
        )
    else:
        logger.debug("scheduler.cert: до истечения {} дней", days_left)


class AgentScheduler:
    """Обёртка над APScheduler 4. Управляется из FastAPI lifespan."""

    def __init__(self, *, metrics_buffer: MetricsBuffer) -> None:
        self._metrics_buffer = metrics_buffer
        self._scheduler = AsyncScheduler()
        self._started = False

    async def start(self) -> None:
        await self._scheduler.__aenter__()
        await self._scheduler.add_schedule(
            self._metrics_callable,
            trigger=IntervalTrigger(seconds=settings.metrics_interval_seconds),
            id="collect_metrics",
        )
        await self._scheduler.add_schedule(
            _check_cert_expiry_job,
            trigger=IntervalTrigger(hours=24),
            id="check_cert_expiry",
        )
        await self._scheduler.start_in_background()
        self._started = True
        logger.info("scheduler: запущен (метрики каждые {} сек)", settings.metrics_interval_seconds)

    async def stop(self) -> None:
        if not self._started:
            return
        await self._scheduler.stop()
        await self._scheduler.__aexit__(None, None, None)
        self._started = False
        logger.info("scheduler: остановлен")

    async def _metrics_callable(self) -> None:
        await _collect_metrics_job(buffer=self._metrics_buffer)


# Используется только тестами — позволяет вызвать job-функцию напрямую без scheduler-а.
async def run_metrics_job_once(*, buffer: MetricsBuffer) -> None:
    await _collect_metrics_job(buffer=buffer)


def calculate_days_until_expiry(*, expires_at: datetime) -> int:
    return (expires_at - datetime.now(tz=expires_at.tzinfo) - timedelta(seconds=1)).days
