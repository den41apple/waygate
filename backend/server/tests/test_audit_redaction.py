"""Tests for audit redaction (BACKLOG C1).

Покрываем что `_SENSITIVE_KEYS` действительно стираются в payload audit-записи,
включая nested dict'ы и списки. Регрессия: при добавлении новой sensitive-схемы
(как было с `config_text` AWG-клиента) легко забыть добавить ключ в список —
тест-парам сваливается на каждом ключе из `_SENSITIVE_KEYS`, явно показывая
покрытие.
"""

import pytest

from server.audit import _SENSITIVE_KEYS, redact


@pytest.mark.parametrize("sensitive_key", sorted(_SENSITIVE_KEYS))
def test_redact_replaces_sensitive_keys_at_top_level(sensitive_key: str) -> None:
    """Каждый ключ из `_SENSITIVE_KEYS` → значение становится `***`."""
    payload = {sensitive_key: "secret-value", "name": "edge-eu"}
    redacted = redact(payload)
    assert redacted[sensitive_key] == "***"
    assert redacted["name"] == "edge-eu"  # non-sensitive — не трогаем


def test_redact_replaces_sensitive_in_nested_dict() -> None:
    """Sensitive ключ в вложенном dict тоже redact'ится."""
    payload = {
        "name": "client-1",
        "credentials": {
            "ssh_password": "real-pass",
            "ssh_user": "root",
        },
    }
    redacted = redact(payload)
    assert redacted["credentials"]["ssh_password"] == "***"
    assert redacted["credentials"]["ssh_user"] == "root"


def test_redact_replaces_sensitive_inside_list() -> None:
    """Список с dict'ами — каждый элемент redact'ится отдельно."""
    payload = {
        "clients": [
            {"name": "a", "config_text": "[Interface]\nPrivateKey = abc"},
            {"name": "b", "config_text": "[Interface]\nPrivateKey = def"},
        ],
    }
    redacted = redact(payload)
    assert redacted["clients"][0]["config_text"] == "***"
    assert redacted["clients"][1]["config_text"] == "***"
    assert redacted["clients"][0]["name"] == "a"


def test_redact_does_not_mutate_input() -> None:
    """`redact` не модифицирует исходный payload — это требование чтобы middleware
    не портил оригинальный body request'а перед обработкой."""
    payload = {"password": "real-pass"}
    redact(payload)
    assert payload["password"] == "real-pass"  # исходник не тронут


def test_redact_passes_through_primitives() -> None:
    """Числа, строки, None, bool — отдаются как есть."""
    assert redact(42) == 42
    assert redact("plain string") == "plain string"
    assert redact(None) is None
    assert redact(True) is True


def test_redact_handles_deeply_nested_combinations() -> None:
    """dict внутри списка внутри dict — все sensitive-ключи во всех уровнях redact'ятся."""
    payload = {
        "directions": [
            {
                "name": "geo-ru",
                "rules": [
                    {"ipset": "russia", "PrivateKey": "wg-priv-key"},
                ],
            },
        ],
        "ssh_private_key": "-----BEGIN-----\n...\n-----END-----",
    }
    redacted = redact(payload)
    assert redacted["ssh_private_key"] == "***"
    assert redacted["directions"][0]["rules"][0]["PrivateKey"] == "***"
    assert redacted["directions"][0]["rules"][0]["ipset"] == "russia"
