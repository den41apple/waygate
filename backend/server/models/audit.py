from datetime import datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class AuditEntry(SQLModel, table=True):
    """Запись аудита: кто/что/когда сделал mutation на API.

    user пока не привязан к auth-системе (её нет в MVP) — поле зарезервировано
    под будущий username/email после внедрения OIDC.
    """

    __tablename__ = "audit_entries"

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, index=True)
    method: str = Field(description="HTTP-метод: POST/PATCH/DELETE")
    path: str = Field(description="URL-путь без query-string")
    server_id: int | None = Field(default=None, foreign_key="server.id", index=True)
    status_code: int = Field(description="HTTP-код ответа")
    user: str | None = Field(default=None, description="username (зарезервировано под OIDC)")
    ip: str | None = Field(default=None, description="X-Real-IP клиента, если nginx прокидывает")
    payload: dict[str, Any] | None = Field(default=None, sa_type=JSON, description="Тело запроса (без секретов)")
