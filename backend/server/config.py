from envparse import env


class Settings:
    """Конфигурация control-plane сервера, читается из переменных окружения."""

    db_url: str = env.str("DATABASE_URL", default="sqlite+aiosqlite:///waygate.db")
    secret_key: str = env.str(
        "SECRET_KEY",
        default="dev-secret-key-change-me-in-production-please",
    )
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

    # Auth: session JWT для UI и bootstrap первого админа
    session_ttl_seconds: int = env.int("SESSION_TTL_SECONDS", default=12 * 3600)
    admin_username: str = env.str("WAYGATE_ADMIN_USER", default="")
    admin_password: str = env.str("WAYGATE_ADMIN_PASSWORD", default="")


settings = Settings()
