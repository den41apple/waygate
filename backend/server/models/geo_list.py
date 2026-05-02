from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class GeoListStatus(StrEnum):
    SYNCED = "synced"  # последняя синхронизация прошла успешно
    STALE = "stale"  # данных давно не было или ещё ни разу не синкали
    ERROR = "error"  # последняя синхронизация не удалась


class GeoList(SQLModel, table=True):
    """GeoIP-список (страна → CIDR-блоки), общий справочник."""

    __tablename__ = "geo_list"

    id: int | None = Field(default=None, primary_key=True)
    country: str = Field(index=True)
    name: str
    source_url: str
    ipv4_count: int = Field(default=0)
    ipv6_count: int = Field(default=0)
    custom_count: int = Field(default=0)
    last_synced_at: datetime | None = Field(default=None)
    status: str = Field(default=GeoListStatus.STALE.value)
