import pytest

from server.auth.secrets import SecretCipherError, _cipher, decrypt, encrypt


@pytest.fixture(autouse=True)
def _clear_cipher_cache():
    """`_cipher` — lru-cached, чтобы тесты были независимы — обнуляем."""
    _cipher.cache_clear()
    yield
    _cipher.cache_clear()


def test_encrypt_decrypt_roundtrip_ascii() -> None:
    plaintext = "hello, waygate"
    token = encrypt(plaintext=plaintext)
    assert token != plaintext
    assert decrypt(token=token) == plaintext


def test_encrypt_decrypt_roundtrip_utf8_and_long_text() -> None:
    plaintext = "Привет 🌍 — это конфиг с 多语言 и emojis ✨\n[Interface]\nAddress = 10.66.66.2/24\n"
    token = encrypt(plaintext=plaintext)
    assert decrypt(token=token) == plaintext


def test_encrypt_produces_different_tokens_each_time() -> None:
    """Fernet включает random nonce — два encrypt одного и того же дают разные tokens."""
    plaintext = "same input"
    a = encrypt(plaintext=plaintext)
    b = encrypt(plaintext=plaintext)
    assert a != b
    assert decrypt(token=a) == decrypt(token=b) == plaintext


def test_decrypt_rejects_garbage() -> None:
    with pytest.raises(SecretCipherError):
        decrypt(token="not-a-real-fernet-token")


def test_decrypt_rejects_token_from_different_key(monkeypatch) -> None:
    """Если SECRET_KEY поменяли — старый токен не расшифровать."""
    from server.config import settings

    token = encrypt(plaintext="some secret")

    # Меняем secret_key и сбрасываем кеш cipher.
    monkeypatch.setattr(settings, "secret_key", "completely-different-secret-key-2026")
    _cipher.cache_clear()

    with pytest.raises(SecretCipherError):
        decrypt(token=token)
