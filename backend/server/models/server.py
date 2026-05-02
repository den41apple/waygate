from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class ServerStatus(StrEnum):
    ONLINE = "online"  # агент отвечает на /v1/status
    OFFLINE = "offline"  # последний healthcheck провалился
    DEGRADED = "degraded"  # агент отвечает, но часть туннелей лежит
    PROVISIONING = "provisioning"  # идёт онбординг через SSH
    ERROR = "error"  # онбординг провалился


class Server(SQLModel, table=True):
    """Управляемый сервер с агентом."""

    __tablename__ = "server"

    id: int | None = Field(default=None, primary_key=True)
    host: str
    port: int = Field(default=7743)
    name: str
    token: str  # Bearer-токен агента
    version: str = Field(default="")
    status: str = Field(default=ServerStatus.OFFLINE.value)
    region: str | None = Field(default=None)
    awg_containers: list[str] = Field(sa_type=JSON, default_factory=list)
    added_at: datetime = Field(default_factory=datetime.now)
    last_seen_at: datetime | None = Field(default=None)
