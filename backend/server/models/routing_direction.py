from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class RoutingDirection(SQLModel, table=True):
    """Логическая группа routing-правил, привязанная к одному AWG-клиенту.

    «Направление» = «весь трафик X через VPN-клиента Y». В UI пользователь
    выбирает несколько источников трафика (GeoIP-зон, DNS-правил, custom IPset'ов)
    через чекбоксы — server создаёт по одному `RoutingRule` для каждого источника
    с **одним общим** `fwmark`/`table_id` и `direction_id=<this.id>`.

    Это позволяет помечать пакеты разных ipset'ов одной меткой → они все идут
    в одну routing-таблицу → через один и тот же VPN-туннель.
    """

    __tablename__ = "routing_directions"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_direction_server_name"),)

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    awg_client_id: int | None = Field(
        default=None,
        foreign_key="awg_clients.id",
        index=True,
        description="Через какого AWG-клиента маршрутизировать. None = host-уровень без клиента.",
    )
    name: str = Field(description="Человеческое имя: 'Streaming', 'Geo-RU' и т.п.")
    fwmark: int = Field(description="Метка пакетов; общая для всех правил направления")
    table_id: int = Field(description="ID custom routing-таблицы")
    via_interface: str = Field(description="Netdev на хосте — куда роутить (`awg-<name>`)")
    via_gateway: str = Field(description="IP next-hop'а внутри туннеля")
    scope: str = Field(default="host", description="`host` или `container`")
    scope_target: str | None = Field(default=None, description="Имя контейнера для scope=container")
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
