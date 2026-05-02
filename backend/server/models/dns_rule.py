from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class DnsRule(SQLModel, table=True):
    """Правило подмены DNS — dnsmasq резолвит домен и сразу пишет в ipset."""

    __tablename__ = "dns_rule"

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    name: str
    domains: list[str] = Field(sa_type=JSON)
    ipset_name: str
    enabled: bool = Field(default=True)
