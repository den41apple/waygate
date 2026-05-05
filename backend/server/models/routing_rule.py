from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel


class RoutingRule(SQLModel, table=True):
    """Правило маршрутизации трафика, привязанное к серверу."""

    __tablename__ = "routing_rule"

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    # Группирующая принадлежность к направлению (см. RoutingDirection). null —
    # legacy-правило, созданное напрямую через старый API /rules без direction.
    # ondelete=CASCADE: при удалении направления удаляются все его child-правила.
    # Прописываем через `sa_column` чтобы CASCADE применился и в alembic, и в
    # `metadata.create_all` (используется в тестах).
    direction_id: int | None = Field(
        default=None,
        sa_column=Column(
            "direction_id",
            Integer,
            ForeignKey("routing_directions.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    country: str
    # Для catch-all (`is_default_egress=True`) ipset_name пустой ("") — в этом
    # случае agent применяет unconditional MARK для пакетов с `mark==0`.
    ipset_name: str
    fwmark: int
    table_id: int
    via_interface: str
    via_gateway: str
    enabled: bool = Field(default=True)
    # Где применять: "host" — текущее поведение, "container" — внутри netns
    # указанного контейнера через nsenter.
    scope: str = Field(default="host")
    scope_target: str | None = Field(default=None)
    # Catch-all egress: помечать все пакеты с mark=0 этим fwmark'ом. Один такой
    # rule на (server, scope) max — гарантируется API directions'а.
    is_default_egress: bool = Field(default=False)
