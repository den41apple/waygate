from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """Юзер control-plane'а. Авторизация по паролю — bcrypt-хеш в `password_hash`."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    password_hash: str = Field(max_length=255)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)
    last_login_at: datetime | None = Field(default=None)
