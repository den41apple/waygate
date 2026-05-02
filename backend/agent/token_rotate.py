import re
import secrets
from pathlib import Path

from loguru import logger

from agent.config import settings

_ENV_PATH = Path("/etc/waygate/agent.env")
_TOKEN_LINE_RE = re.compile(r"^TOKEN=.*$", re.MULTILINE)


class RotateError(RuntimeError):
    """Не удалось ротировать токен агента."""


def _replace_token_line(*, env_text: str, new_token: str) -> str:
    if _TOKEN_LINE_RE.search(env_text):
        return _TOKEN_LINE_RE.sub(f"TOKEN={new_token}", env_text)
    suffix = "" if env_text.endswith("\n") or not env_text else "\n"
    return f"{env_text}{suffix}TOKEN={new_token}\n"


async def rotate_agent_token(*, env_path: Path | None = None) -> str:
    """Генерирует новый Bearer-токен и переписывает /etc/waygate/agent.env.

    Возвращает новый токен. settings.token обновляется в памяти, чтобы следующий
    запрос с новым токеном прошёл; старый токен моментально становится невалидным.
    """
    target = env_path or _ENV_PATH
    new_token = secrets.token_urlsafe(48)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        target.write_text(_replace_token_line(env_text=existing, new_token=new_token))
        # Хардненим — токен виден только агенту/root.
        target.chmod(0o600)
    except OSError as exc:
        raise RotateError(f"не удалось перезаписать {target}: {exc}") from exc

    settings.token = new_token
    logger.info("token: ротация выполнена, длина={} байт", len(new_token))
    return new_token
