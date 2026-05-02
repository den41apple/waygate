from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SERVER_STATUS_CHANGED = "server.status_changed"  # online ↔ offline ↔ degraded
    SERVER_METRICS = "server.metrics"  # новая точка rx/tx
    SERVER_AGENT_UPDATED = "server.agent_updated"  # сменилась version или awg_containers
    RULE_APPLIED = "rule.applied"  # применено правило маршрутизации
    DNS_APPLIED = "dns.applied"  # применены DNS-правила
    GEOIP_SYNCED = "geoip.synced"  # GeoIP-список синхронизирован на агенте
    TLS_APPLIED = "tls.applied"  # применена TLS-конфигурация
    PROVISION_PROGRESS = "provision.progress"  # шаг онбординга


class WsEvent(BaseModel):
    type: EventType = Field(description="Тип события")
    payload: dict[str, Any] = Field(default_factory=dict, description="Полезная нагрузка события")
    server_id: int | None = Field(default=None, description="К какому серверу относится событие")
    timestamp: datetime = Field(description="Когда событие произошло")
