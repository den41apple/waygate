from datetime import datetime

from sqlmodel import Field, SQLModel


class MetricsPoint(SQLModel, table=True):
    """Точка временного ряда rx/tx по всему серверу. Агрегируется из всех туннелей."""

    __tablename__ = "metrics_points"

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    timestamp: datetime = Field(index=True)
    rx_bytes: int
    tx_bytes: int
