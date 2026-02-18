"""Unit tests for CLI argument parsing and env var application."""

import os

import pytest
from click.testing import CliRunner

from ccbot.cli import _FLAG_TO_ENV, apply_args_to_env, cli

_ALL_ENV_VARS = ["CCBOT_LOG_LEVEL", *[env for _, env in _FLAG_TO_ENV]]


@pytest.fixture(autouse=True)
def _clean_env():
    """Ensure apply_args_to_env changes don't leak between tests."""
    saved = {var: os.environ.get(var) for var in _ALL_ENV_VARS}
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


@pytest.fixture()
def runner():
    return CliRunner()


class TestCliCommands:
    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "ccbot" in result.output

    def test_help_shows_commands(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Commands:" in result.output
        assert "run" in result.output
        assert "hook" in result.output
        assert "status" in result.output
        assert "doctor" in result.output

    def test_run_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "--config-dir" in result.output

    def test_hook_help(self, runner):
        result = runner.invoke(cli, ["hook", "--help"])
        assert result.exit_code == 0
        assert "--install" in result.output
        assert "--uninstall" in result.output
        assert "--status" in result.output

    def test_doctor_help(self, runner):
        result = runner.invoke(cli, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--fix" in result.output

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0


class TestRunValidation:
    def test_zero_interval_rejected(self, runner):
        result = runner.invoke(cli, ["run", "--monitor-interval", "0"])
        assert result.exit_code != 0
        assert "must be positive" in result.output

    def test_negative_interval_rejected(self, runner):
        result = runner.invoke(cli, ["run", "--monitor-interval", "-1"])
        assert result.exit_code != 0
        assert "must be positive" in result.output

    def test_negative_autoclose_done_rejected(self, runner):
        result = runner.invoke(cli, ["run", "--autoclose-done", "-5"])
        assert result.exit_code != 0
        assert "must be non-negative" in result.output

    def test_negative_autoclose_dead_rejected(self, runner):
        result = runner.invoke(cli, ["run", "--autoclose-dead", "-1"])
        assert result.exit_code != 0
        assert "must be non-negative" in result.output

    def test_invalid_log_level_rejected(self, runner):
        result = runner.invoke(cli, ["run", "--log-level", "XYZZY"])
        assert result.exit_code != 0


class TestApplyArgsToEnv:
    def test_verbose_sets_debug(self):
        apply_args_to_env(verbose=True)
        assert os.environ["CCBOT_LOG_LEVEL"] == "DEBUG"

    def test_log_level_sets_env(self):
        apply_args_to_env(log_level="WARNING")
        assert os.environ["CCBOT_LOG_LEVEL"] == "WARNING"

    def test_verbose_overrides_log_level(self):
        apply_args_to_env(verbose=True, log_level="ERROR")
        assert os.environ["CCBOT_LOG_LEVEL"] == "DEBUG"

    def test_config_dir_resolved(self, tmp_path):
        apply_args_to_env(config_dir=tmp_path)
        assert os.environ["CCBOT_DIR"] == str(tmp_path.resolve())

    def test_tmux_session(self):
        apply_args_to_env(tmux_session="custom")
        assert os.environ["TMUX_SESSION_NAME"] == "custom"

    def test_group_id(self):
        apply_args_to_env(group_id=789)
        assert os.environ["CCBOT_GROUP_ID"] == "789"

    def test_monitor_interval(self):
        apply_args_to_env(monitor_interval=1.5)
        assert os.environ["MONITOR_POLL_INTERVAL"] == "1.5"

    def test_none_flags_dont_overwrite_env(self, monkeypatch):
        monkeypatch.setenv("TMUX_SESSION_NAME", "from-env")
        apply_args_to_env()
        assert os.environ["TMUX_SESSION_NAME"] == "from-env"

    def test_flag_overwrites_env(self, monkeypatch):
        monkeypatch.setenv("TMUX_SESSION_NAME", "from-env")
        apply_args_to_env(tmux_session="from-flag")
        assert os.environ["TMUX_SESSION_NAME"] == "from-flag"

    def test_all_flag_env_mappings(self):
        apply_args_to_env(
            config_dir="/tmp/cc",
            allowed_users="1,2",
            tmux_session="s",
            claude_command="c",
            monitor_interval=3.0,
            group_id=99,
            instance_name="n",
            autoclose_done=10,
            autoclose_dead=5,
        )

        assert os.environ["ALLOWED_USERS"] == "1,2"
        assert os.environ["TMUX_SESSION_NAME"] == "s"
        assert os.environ["CLAUDE_COMMAND"] == "c"
        assert os.environ["MONITOR_POLL_INTERVAL"] == "3.0"
        assert os.environ["CCBOT_GROUP_ID"] == "99"
        assert os.environ["CCBOT_INSTANCE_NAME"] == "n"
        assert os.environ["AUTOCLOSE_DONE_MINUTES"] == "10"
        assert os.environ["AUTOCLOSE_DEAD_MINUTES"] == "5"

    def test_autoclose_zero_accepted(self):
        apply_args_to_env(autoclose_done=0, autoclose_dead=0)
        assert os.environ["AUTOCLOSE_DONE_MINUTES"] == "0"
        assert os.environ["AUTOCLOSE_DEAD_MINUTES"] == "0"
