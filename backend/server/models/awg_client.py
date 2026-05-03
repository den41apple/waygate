from datetime import datetime
from enum import StrEnum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class AwgClientStatus(StrEnum):
    PENDING = "pending"  # POST на агента ушёл, контейнер ещё не up
    RUNNING = "running"  # docker сообщает state=running
    STOPPED = "stopped"  # контейнер существует, но остановлен
    ERROR = "error"  # docker run упал, или контейнер crashed


class AwgClient(SQLModel, table=True):
    """Развёрнутый Waygate AmneziaWG-клиент в docker-контейнере на target-сервере.

    Конфиг (с PrivateKey) хранится зашифрованным Fernet — расшифровка
    производится при выдаче .conf-файла или QR-кода в UI.
    """

    __tablename__ = "awg_clients"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_awg_client_server_name"),)

    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    name: str = Field(description="Идентификатор клиента (a-z0-9-): waygate-amnezia-client-<name>")
    container_name: str = Field(description="Полное docker-имя — для управления через AgentClient")
    config_encrypted: str = Field(description="Fernet-зашифрованный .conf-файл")
    peer_endpoint: str | None = Field(default=None, description="host:port VPN-сервера из [Peer]")
    peer_pubkey: str | None = Field(default=None, description="PublicKey VPN-сервера — для UI")
    interface_address: str | None = Field(default=None, description="Address клиента в туннеле")
    country: str | None = Field(default=None, description="ISO-2 страны VPN (для отображения)")
    status: str = Field(default=AwgClientStatus.PENDING.value, description="Текущий жизненный цикл")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
