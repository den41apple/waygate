import time
from dataclasses import dataclass

import jwt

from server.config import settings
from server.models import User

_ALGORITHM = "HS256"
_SCOPE = "session"


class SessionTokenError(RuntimeError):
    """JWT сессии недействителен."""


@dataclass(frozen=True)
class SessionClaims:
    user_id: int
    username: str


def sign_session_token(*, user: User, ttl_seconds: int | None = None) -> str:
    """Подписывает session-JWT для авторизованного юзера.

    Срок жизни — settings.session_ttl_seconds (default 12 часов).
    """
    if user.id is None:
        raise SessionTokenError("у юзера нет id — забыли persist?")
    ttl = ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "scope": _SCOPE,
        "exp": int(time.time()) + ttl,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_session_token(*, token: str) -> SessionClaims:
    """Кидает SessionTokenError при просроченном/невалидном токене или wrong scope."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise SessionTokenError("сессия истекла") from exc
    except jwt.PyJWTError as exc:
        raise SessionTokenError(f"невалидный токен: {exc}") from exc
    if payload.get("scope") != _SCOPE:
        raise SessionTokenError("неверная область применения токена")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise SessionTokenError("в токене нет sub") from exc
    username = payload.get("username")
    if not isinstance(username, str):
        raise SessionTokenError("в токене нет username") from None
    return SessionClaims(user_id=user_id, username=username)
