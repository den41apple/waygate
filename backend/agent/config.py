from envparse import env


class Settings:
    """Конфигурация агента, читается из переменных окружения."""

    port: int = env.int("PORT", default=7743)
    token: str = env.str("TOKEN", default="")
    log_level: str = env.str("LOG_LEVEL", default="INFO")
    tls_dir: str = env.str("TLS_DIR", default="/etc/waygate/tls")
    data_dir: str = env.str("DATA_DIR", default="/var/lib/waygate-agent")
    clients_dir: str = env.str("CLIENTS_DIR", default="/etc/waygate/clients")
    awg_client_image: str = env.str(
        "AWG_CLIENT_IMAGE",
        default="ghcr.io/den41apple/waygate-awg-client:latest",
    )
    metrics_interval_seconds: int = env.int("METRICS_INTERVAL_SECONDS", default=30)
    metrics_buffer_size: int = env.int("METRICS_BUFFER_SIZE", default=60)
    # Сколько секунд ждать появления awg-iface в host netns после `docker run -d`.
    # awg-quick внутри контейнера инициализирует туннель не мгновенно — без ожидания
    # последующий `ip rule add ... iif awg-X` падает с "Cannot find device".
    awg_iface_wait_seconds: float = env.float("AWG_IFACE_WAIT_SECONDS", default=10.0)


settings = Settings()
