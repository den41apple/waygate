from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.session import SessionTokenError, decode_session_token
from server.db import get_session
from server.models import User

_BEARER_PREFIX = "Bearer "


def _extract_token(*, authorization: str, query_token: str) -> str:
    """Bearer header → JWT query-param как fallback (для EventSource, который
    не умеет custom headers).
    """
    if authorization.startswith(_BEARER_PREFIX):
        return authorization[len(_BEARER_PREFIX) :]
    if query_token:
        return query_token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется Authorization: Bearer <token> или ?access_token=…",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    authorization: str = Header(default=""),
    access_token: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Грузит User из БД по session-JWT. 401 при любой проблеме.

    Принимает токен либо из `Authorization: Bearer <jwt>`, либо из `?access_token=<jwt>`
    (нужно EventSource'у — он не умеет ставить custom headers).
    """
    token = _extract_token(authorization=authorization, query_token=access_token)
    try:
        claims = decode_session_token(token=token)
    except SessionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="юзер не найден или отключён",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="нужны права администратора",
        )
    return user
