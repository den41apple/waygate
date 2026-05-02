import bcrypt

_ROUNDS = 12


def hash_password(*, plaintext: str) -> str:
    """Хеш пароля. bcrypt(12 rounds) — стандарт, ~250мс на современном CPU."""
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_ROUNDS)).decode("utf-8")


def verify_password(*, plaintext: str, hashed: str) -> bool:
    """Constant-time проверка. Возвращает False вместо исключения при битом хеше."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
