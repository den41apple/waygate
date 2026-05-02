import pytest

from server.auth.bootstrap import bootstrap_admin_from_env
from server.auth.passwords import hash_password, verify_password
from server.config import settings
from server.models import User


def test_password_hash_roundtrip():
    hashed = hash_password(plaintext="hunter2")
    assert hashed != "hunter2"
    assert verify_password(plaintext="hunter2", hashed=hashed) is True
    assert verify_password(plaintext="wrong", hashed=hashed) is False


def test_password_verify_safe_on_garbage():
    # Не должно кидать exception на битом хеше — просто False.
    assert verify_password(plaintext="x", hashed="not-a-bcrypt-hash") is False


async def test_login_with_correct_password(anon_client, admin_user):
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.username, "password": "test-admin-pass-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["ttl_seconds"] > 0
    assert body["user"]["username"] == admin_user.username
    assert body["user"]["is_admin"] is True


async def test_login_with_wrong_password(anon_client, admin_user):
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.username, "password": "wrong-password"},
    )
    assert response.status_code == 401


async def test_login_unknown_user_returns_401(anon_client):
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "whatever"},
    )
    assert response.status_code == 401


async def test_me_with_valid_token(client, admin_user):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {"username": admin_user.username, "is_admin": True}


async def test_me_without_token_returns_401(anon_client):
    response = await anon_client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_protected_endpoint_requires_auth(anon_client):
    response = await anon_client.get("/api/v1/servers")
    assert response.status_code == 401


async def test_protected_endpoint_with_invalid_token_returns_401(anon_client):
    response = await anon_client.get(
        "/api/v1/servers",
        headers={"Authorization": "Bearer invalid-jwt"},
    )
    assert response.status_code == 401


async def test_logout_returns_204(client):
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 204


async def test_logout_requires_auth(anon_client):
    response = await anon_client.post("/api/v1/auth/logout")
    assert response.status_code == 401


async def test_inactive_user_cannot_use_session(client, anon_client, session_maker, admin_user):
    # Деактивируем юзера в БД и пробуем сходить старым токеном
    async with session_maker() as session:
        loaded = await session.get(User, admin_user.id)
        assert loaded is not None
        loaded.is_active = False
        await session.commit()

    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.fixture
async def _bootstrap_env(monkeypatch):
    """Подменяет settings.admin_username/password на тестовые значения."""
    monkeypatch.setattr(settings, "admin_username", "boot-admin")
    monkeypatch.setattr(settings, "admin_password", "boot-pass-secret")


async def test_bootstrap_creates_admin_when_db_empty(
    session_maker,
    _bootstrap_env,
):
    await bootstrap_admin_from_env(session_maker=session_maker)

    async with session_maker() as session:
        from sqlmodel import select

        result = await session.execute(select(User).where(User.username == "boot-admin"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.is_admin is True
        assert verify_password(plaintext="boot-pass-secret", hashed=user.password_hash) is True


async def test_bootstrap_idempotent(session_maker, _bootstrap_env):
    await bootstrap_admin_from_env(session_maker=session_maker)
    await bootstrap_admin_from_env(session_maker=session_maker)

    async with session_maker() as session:
        from sqlmodel import select

        result = await session.execute(select(User).where(User.username == "boot-admin"))
        all_admins = result.scalars().all()
        assert len(all_admins) == 1


async def test_bootstrap_skips_if_env_empty(session_maker, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")

    await bootstrap_admin_from_env(session_maker=session_maker)

    async with session_maker() as session:
        from sqlmodel import select

        result = await session.execute(select(User))
        users = result.scalars().all()
        assert users == []
