from enum import StrEnum

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class DirectionSourceType(StrEnum):
    """Тип источника трафика, привязанного к direction'у.

    Раньше source-ID'ы вычислялись reverse-lookup'ом из `RoutingRule.ipset_name`
    (`geoip-ru-v4` → GeoList с country=ru). Это string-magic'ом, легко
    путалось при добавлении нового типа источника. С B1 каждый источник
    хранится явно в `direction_sources`.
    """

    GEO_LIST = "geo_list"
    DNS_RULE = "dns_rule"
    IPSET_GROUP = "ipset_group"


class DirectionSource(SQLModel, table=True):
    """Pivot-таблица «direction → источник трафика».

    Каждая запись = «direction id=X использует источник source_type=Y, id=Z».
    На один direction может быть N таких записей (по одной на каждый
    выбранный GeoList/DnsRule/IpsetGroup).

    UNIQUE(direction_id, source_type, source_id) — нельзя дважды добавить
    один и тот же источник к direction'у. ondelete=CASCADE на direction —
    удаление direction'а зачищает pivot.
    """

    __tablename__ = "direction_sources"
    __table_args__ = (
        UniqueConstraint(
            "direction_id",
            "source_type",
            "source_id",
            name="uq_direction_source",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    direction_id: int = Field(
        sa_column=Column(
            "direction_id",
            Integer,
            ForeignKey("routing_directions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    source_type: str = Field(description="`geo_list`, `dns_rule`, `ipset_group` (см. DirectionSourceType)")
    source_id: int = Field(description="ID соответствующей сущности (GeoList/DnsRule/IpsetGroup)")
