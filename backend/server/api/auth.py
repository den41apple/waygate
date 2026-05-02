from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from server.auth.dependencies import require_user
from server.auth.passwords import verify_password
from server.auth.session import sign_session_token
from server.config import settings
from server.db import get_session
from server.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class UserResponse(BaseModel):
    username: str
    is_admin: bool


class LoginResponse(BaseModel):
    token: str
    ttl_seconds: int
    user: UserResponse


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Логин по username/password. Возвращает session-JWT + профиль юзера."""
    result = await session.execute(select(User).where(User.username == request.username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(plaintext=request.password, hashed=user.password_hash):
        # Не различаем «нет юзера» и «неверный пароль» — стандартная защита от user enum.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="неверный логин или пароль")

    user.last_login_at = datetime.now()
    await session.commit()
    await session.refresh(user)

    token = sign_session_token(user=user)
    logger.info("auth: login успешный username={}", user.username)
    return LoginResponse(
        token=token,
        ttl_seconds=settings.session_ttl_seconds,
        user=UserResponse(username=user.username, is_admin=user.is_admin),
    )


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(require_user)) -> UserResponse:
    """Возвращает текущего юзера. Используется фронтом при старте для проверки сессии."""
    return UserResponse(username=user.username, is_admin=user.is_admin)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: User = Depends(require_user)) -> None:
    """No-op для stateless JWT — фронт сам чистит localStorage.

    Эндпоинт нужен для аудит-лога (зафиксировать факт выхода) и под будущий
    blacklist при переходе на refresh-tokens.
    """
    logger.info("auth: logout username={}", user.username)
