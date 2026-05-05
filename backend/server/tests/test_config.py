"""Tests for server.config — fail-fast на predictable SECRET_KEY (BACKLOG A1)."""

import pytest

from server.config import _require_secret_key


@pytest.mark.parametrize(
    "bad_value",
    ["", "dev-secret-key-change-me-in-production-please", "change-me", "secret"],
)
def test_require_secret_key_rejects_dev_defaults(monkeypatch, bad_value: str) -> None:
    """SECRET_KEY = dev-default → RuntimeError.

    Регрессия: раньше `server/config.py` имел fallback на
    `dev-secret-key-change-me-...`. Если оператор забыл выставить ENV в проде,
    Fernet и JWT использовали predictable ключ → recovery невозможна.
    """
    monkeypatch.setenv("SECRET_KEY", bad_value)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _require_secret_key()


def test_require_secret_key_accepts_random_value(monkeypatch) -> None:
    """Случайная строка — функция должна вернуть значение."""
    monkeypatch.setenv("SECRET_KEY", "real-random-secret-from-openssl-rand-hex-32-foo")
    assert _require_secret_key() == "real-random-secret-from-openssl-rand-hex-32-foo"
