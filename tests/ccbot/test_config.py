"""Unit tests for Config — env var loading, validation, and user access."""

from pathlib import Path

import pytest

from ccbot.config import Config


@pytest.fixture
def _base_env(monkeypatch, tmp_path):
    # chdir to tmp_path so load_dotenv won't find the real .env in repo root
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("ALLOWED_USERS", "12345")
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))


@pytest.mark.usefixtures("_base_env")
class TestConfigValid:
    def test_valid_config(self):
        cfg = Config()
        assert cfg.telegram_bot_token == "test:token"
        assert cfg.allowed_users == {12345}

    def test_custom_tmux_session_name(self, monkeypatch):
        monkeypatch.setenv("TMUX_SESSION_NAME", "mysession")
        cfg = Config()
        assert cfg.tmux_session_name == "mysession"

    def test_custom_monitor_poll_interval(self, monkeypatch):
        monkeypatch.setenv("MONITOR_POLL_INTERVAL", "5.0")
        cfg = Config()
        assert cfg.monitor_poll_interval == 5.0

    def test_is_user_allowed_true(self):
        cfg = Config()
        assert cfg.is_user_allowed(12345) is True

    def test_is_user_allowed_false(self):
        cfg = Config()
        assert cfg.is_user_allowed(99999) is False


@pytest.mark.usefixtures("_base_env")
class TestConfigMissingEnv:
    def test_missing_telegram_bot_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            Config()

    def test_missing_allowed_users(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_USERS", raising=False)
        with pytest.raises(ValueError, match="ALLOWED_USERS"):
            Config()

    def test_non_numeric_allowed_users(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USERS", "abc")
        with pytest.raises(ValueError, match="non-numeric"):
            Config()


@pytest.mark.usefixtures("_base_env")
class TestConfigClaudeProjectsPath:
    def test_default_claude_projects_path(self, monkeypatch):
        """Default path is ~/.claude/projects when no env vars are set."""
        # Ensure no custom path env vars are set
        monkeypatch.delenv("CCBOT_CLAUDE_PROJECTS_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        cfg = Config()
        assert cfg.claude_projects_path == Path.home() / ".claude" / "projects"

    def test_custom_claude_projects_path(self, monkeypatch):
        """CCBOT_CLAUDE_PROJECTS_PATH overrides the default path."""
        custom_path = "/custom/projects/path"
        monkeypatch.setenv("CCBOT_CLAUDE_PROJECTS_PATH", custom_path)
        cfg = Config()
        assert cfg.claude_projects_path == Path(custom_path)

    def test_claude_config_dir_projects_path(self, monkeypatch):
        """CLAUDE_CONFIG_DIR sets path to $CLAUDE_CONFIG_DIR/projects."""
        custom_config_dir = "/custom/claude/config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", custom_config_dir)
        cfg = Config()
        assert cfg.claude_projects_path == Path(custom_config_dir) / "projects"

    def test_ccbot_projects_path_takes_priority(self, monkeypatch):
        """CCBOT_CLAUDE_PROJECTS_PATH takes priority over CLAUDE_CONFIG_DIR."""
        monkeypatch.setenv("CCBOT_CLAUDE_PROJECTS_PATH", "/priority/path")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/lower/priority")
        cfg = Config()
        assert cfg.claude_projects_path == Path("/priority/path")
