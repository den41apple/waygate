from envparse import env

# Литералы, которые исторически валялись как дефолты — отклоняем явно, чтобы
# забытый ENV не превращался в «всё работает, ключи predictable». Список
# держим коротким; если в будущем нужно добавить — просто сюда.
_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "",
        "dev-secret-key-change-me-in-production-please",
        "change-me",
        "secret",
    },
)


def _require_secret_key() -> str:
    """`SECRET_KEY` должен быть выставлен из ENV — без fallback'а на dev-default.

    Любой dev-default означает predictable Fernet/JWT-ключи: оператор клонировал
    репо → запустил docker-compose без ENV → его SSH-креды лежат в БД зашифрованные
    общеизвестным ключом. Проще сразу падать на старте.
    """
    value: str = env.str("SECRET_KEY", default="")
    if value in _FORBIDDEN_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY не задан или совпадает с dev-default. Сгенерируй случайный "
            "(`openssl rand -hex 32`) и положи в ENV/.env. Без этого Fernet и JWT "
            "используют predictable ключ — recovery невозможна.",
        )
    return value


class Settings:
    """Конфигурация control-plane сервера, читается из переменных окружения."""

    db_url: str = env.str("DATABASE_URL", default="sqlite+aiosqlite:///waygate.db")
    secret_key: str = _require_secret_key()
    port: int = env.int("PORT", default=8000)
    cors_origins: list[str] = env.list("CORS_ORIGINS", default=["http://localhost:5173"])
    log_level: str = env.str("LOG_LEVEL", default="INFO")
    metrics_poll_seconds: int = env.int("METRICS_POLL_SECONDS", default=30)
    healthcheck_seconds: int = env.int("HEALTHCHECK_SECONDS", default=60)
    metrics_retention_days: int = env.int("METRICS_RETENTION_DAYS", default=30)
    agent_request_timeout_seconds: int = env.int("AGENT_REQUEST_TIMEOUT_SECONDS", default=60)
    agent_connect_timeout_seconds: int = env.int("AGENT_CONNECT_TIMEOUT_SECONDS", default=5)

    # Провижионинг.
    # URL должен указывать на собранный wheel в GitHub Release. По умолчанию
    # — `den41apple/waygate`, переопределяется через ENV под свой fork/org.
    # Workflow `release-agent.yml` грузит и версионную, и стабильную копию
    # `waygate_agent-py3-none-any.whl` — поэтому /latest/download работает.
    agent_wheel_url: str = env.str(
        "AGENT_WHEEL_URL",
        default="https://github.com/den41apple/waygate/releases/latest/download/waygate_agent-py3-none-any.whl",
    )
    agent_default_port: int = env.int("AGENT_DEFAULT_PORT", default=7743)
    provision_healthcheck_timeout_seconds: int = env.int("PROVISION_HEALTHCHECK_TIMEOUT_SECONDS", default=120)
    # GitHub-репо где живут agent-wheel-релизы (тег `agent-v*`). Используется
    # для GET /api/v1/agent-releases — UI показывает список и подсовывает URL'ы
    # в UpdateAgentModal.
    agent_releases_repo: str = env.str("AGENT_RELEASES_REPO", default="den41apple/waygate")

    # Auth: session JWT для UI и bootstrap первого админа
    session_ttl_seconds: int = env.int("SESSION_TTL_SECONDS", default=12 * 3600)
    admin_username: str = env.str("WAYGATE_ADMIN_USER", default="")
    admin_password: str = env.str("WAYGATE_ADMIN_PASSWORD", default="")


settings = Settings()
