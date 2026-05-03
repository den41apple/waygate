from datetime import datetime

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class IpsetGroup(SQLModel, table=True):
    """Пользовательский ipset из явно перечисленных CIDR'ов.

    Третья сущность для маршрутизации (наряду с GeoIP-списками и DNS-правилами):
    user добавляет конкретные IP/подсети, агент собирает их в ipset того же
    имени и iptables использует его в mangle PREROUTING.
    """

    __tablename__ = "ipset_groups"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_ipset_group_server_name"),)

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    name: str = Field(description="Имя ipset (a-z0-9_-, ≤31 символ — лимит ipset)")
    cidrs: list[str] = Field(sa_type=JSON, default_factory=list, description="Список CIDR/IP")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
