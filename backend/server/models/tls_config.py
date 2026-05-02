from datetime import datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class TlsConfigRow(SQLModel, table=True):
    """Текущая TLS-конфигурация для сервера. Один сервер — одна строка."""

    __tablename__ = "tls_config"

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", unique=True)
    config: dict[str, Any] = Field(sa_type=JSON)  # сериализованный TlsConfig
    expires_at: datetime | None = Field(default=None)
