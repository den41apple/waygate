import time

import jwt

from server.config import settings


class WsTokenError(RuntimeError):
    """JWT для WS-подключения недействителен."""


_ALGORITHM = "HS256"
_DEFAULT_TTL_SECONDS = 3600


def sign_ws_token(*, ttl_seconds: int = _DEFAULT_TTL_SECONDS, username: str | None = None) -> str:
    """Подписывает JWT для WebSocket-подключения. Срок жизни — 1 час по умолчанию.

    Если передан username — кладём в claim для будущего user-scoped audit.
    """
    payload: dict[str, object] = {"exp": int(time.time()) + ttl_seconds, "scope": "ws"}
    if username is not None:
        payload["username"] = username
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def verify_ws_token(*, token: str) -> None:
    """Кидает WsTokenError при недействительном токене."""
    try:
        decoded = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise WsTokenError("токен истёк") from exc
    except jwt.PyJWTError as exc:
        raise WsTokenError(f"недействительный токен: {exc}") from exc
    if decoded.get("scope") != "ws":
        raise WsTokenError("неверная область применения токена")
