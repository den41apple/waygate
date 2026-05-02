import secrets

from fastapi import Header, HTTPException, status

from agent.config import settings


async def verify_bearer_token(authorization: str = Header(default="")) -> None:
    """FastAPI-зависимость: проверяет заголовок Authorization: Bearer <token>.

    Сравнение токена идёт через secrets.compare_digest для защиты от timing-атак.
    Если токен в окружении не задан — отклоняем все запросы (агент не настроен).
    """
    if not settings.token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Агент не настроен: TOKEN не задан",
        )

    expected_prefix = "Bearer "
    if not authorization.startswith(expected_prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = authorization[len(expected_prefix) :]
    if not secrets.compare_digest(provided, settings.token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
