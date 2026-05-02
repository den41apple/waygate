from sqlmodel import Field, SQLModel


class RoutingRule(SQLModel, table=True):
    """Правило маршрутизации трафика, привязанное к серверу."""

    __tablename__ = "routing_rule"

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    country: str
    ipset_name: str
    fwmark: int
    table_id: int
    via_interface: str
    via_gateway: str
    enabled: bool = Field(default=True)
