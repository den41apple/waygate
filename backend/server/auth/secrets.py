"""Симметричное шифрование секретов в БД ключом, производным от `SECRET_KEY`.

Используется для конфигов AWG-клиентов: в БД лежит зашифрованный текст, при
запросе агент или UI получают расшифрованный. Если SECRET_KEY ротируется —
старые секреты придётся пересоздавать (Fernet с одним ключом, без MultiFernet).
"""

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from server.config import settings

_HKDF_INFO = b"waygate.fernet.v1"


class SecretCipherError(RuntimeError):
    """Не удалось расшифровать токен (изменился SECRET_KEY или повреждение)."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Делает Fernet-ключ из SECRET_KEY через HKDF-SHA256.

    Lru-cache на 1 — Fernet потокобезопасный, инициализируем один раз.
    """
    raw_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(settings.secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(raw_key))


def encrypt(*, plaintext: str) -> str:
    """Шифрует UTF-8 текст и возвращает urlsafe-base64 token."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(*, token: str) -> str:
    """Расшифровывает Fernet-token обратно в UTF-8 текст.

    Кидает SecretCipherError если токен повреждён или SECRET_KEY изменился.
    """
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretCipherError("не удалось расшифровать секрет — возможно SECRET_KEY изменён") from exc
