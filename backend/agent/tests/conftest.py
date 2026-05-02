import os

# Подкидываем токен ДО импорта приложения — agent.config.settings читает env при старте.
os.environ.setdefault("TOKEN", "test-token-1234567890")
