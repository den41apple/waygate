from agent.config import settings
from agent.token_rotate import _replace_token_line, rotate_agent_token


def test_replace_existing_token_line():
    env = "PORT=7743\nTOKEN=old-token\nLOG_LEVEL=INFO\n"
    result = _replace_token_line(env_text=env, new_token="new-token")
    assert "TOKEN=new-token" in result
    assert "TOKEN=old-token" not in result
    assert "PORT=7743" in result
    assert "LOG_LEVEL=INFO" in result


def test_replace_appends_when_missing():
    env = "PORT=7743\n"
    result = _replace_token_line(env_text=env, new_token="brand-new")
    assert result.endswith("TOKEN=brand-new\n")
    assert "PORT=7743\n" in result


async def test_rotate_writes_new_token_and_updates_settings(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("PORT=7743\nTOKEN=old-token-123\n")
    original_token = settings.token

    new_token = await rotate_agent_token(env_path=env_path)

    try:
        assert new_token != "old-token-123"
        assert len(new_token) >= 48
        content = env_path.read_text()
        assert f"TOKEN={new_token}" in content
        assert "old-token-123" not in content
        assert settings.token == new_token
    finally:
        # вернуть исходное состояние, чтобы соседние тесты не вылетели на auth
        monkeypatch.setattr(settings, "token", original_token)
