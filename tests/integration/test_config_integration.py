"""Integration tests for Config — real .env files and filesystem."""

import pytest

from ccbot.config import Config

pytestmark = pytest.mark.integration


class TestConfigIntegration:
    def test_reads_env_file_from_config_dir(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "TELEGRAM_BOT_TOKEN=from-dotenv-token\nALLOWED_USERS=99999\n"
        )
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("ALLOWED_USERS", raising=False)
        cfg = Config()
        assert cfg.telegram_bot_token == "from-dotenv-token"
        assert cfg.is_user_allowed(99999)

    def test_creates_config_dir_if_missing(self, tmp_path, monkeypatch):
        new_dir = tmp_path / "nonexistent"
        monkeypatch.setenv("CCBOT_DIR", str(new_dir))
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-create-dir")
        monkeypatch.setenv("ALLOWED_USERS", "1")
        Config()
        assert new_dir.is_dir()

    def test_multiple_comma_separated_allowed_users(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-multi")
        monkeypatch.setenv("ALLOWED_USERS", "123,456,789")
        cfg = Config()
        assert cfg.is_user_allowed(123)
        assert cfg.is_user_allowed(456)
        assert cfg.is_user_allowed(789)
        assert not cfg.is_user_allowed(999)
