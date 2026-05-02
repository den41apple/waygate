from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from server.auth.passwords import hash_password
from server.config import settings
from server.models import User


async def bootstrap_admin_from_env(*, session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Создаёт первого админа из ENV если задан и пользователя ещё нет в БД.

    Идемпотентно: повторный запуск при существующем юзере — no-op.
    Если в БД нет ни одного юзера и env не задан — логирует warning,
    панель в этом состоянии недоступна (вход невозможен).
    Best-effort: если таблица users ещё не мигрирована, тихо логируем и выходим.
    """
    try:
        async with session_maker() as session:
            await _bootstrap_within_session(session=session)
    except Exception as exc:
        logger.warning("auth: bootstrap пропущен ({}). Запустите alembic upgrade head.", exc)


async def _bootstrap_within_session(*, session: AsyncSession) -> None:
    result = await session.execute(select(User).limit(1))
    any_user = result.scalar_one_or_none()

    admin_user = settings.admin_username
    admin_password = settings.admin_password

    if any_user is None and not (admin_user and admin_password):
        logger.warning(
            "auth: в БД нет ни одного пользователя, и WAYGATE_ADMIN_USER/PASSWORD "
            "не заданы. Войти в панель невозможно — выставьте env-переменные и перезапустите.",
        )
        return

    if not (admin_user and admin_password):
        return  # юзеры есть, env не задан — нечего делать

    result = await session.execute(select(User).where(User.username == admin_user))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return  # уже создан

    session.add(
        User(
            username=admin_user,
            password_hash=hash_password(plaintext=admin_password),
            is_admin=True,
            is_active=True,
        ),
    )
    await session.commit()
    logger.info("auth: bootstrap создал админа username={}", admin_user)
