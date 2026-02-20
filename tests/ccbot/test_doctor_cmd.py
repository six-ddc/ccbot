"""Tests for ccbot doctor command."""

import json

import pytest

from ccbot.doctor_cmd import (
    _check_allowed_users,
    _check_config_dir,
    _check_tmux,
    _find_orphaned_windows,
    doctor_main,
)


class TestCheckTmux:
    def test_tmux_found(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "ccbot.doctor_cmd.shutil.which", lambda _cmd: "/usr/bin/tmux"
        )
        status, _msg = _check_tmux()
        assert status == "pass"

    def test_tmux_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("ccbot.doctor_cmd.shutil.which", lambda _cmd: None)
        status, msg = _check_tmux()
        assert status == "fail"
        assert "not found" in msg


class TestCheckConfigDir:
    def test_exists(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        status, _ = _check_config_dir()
        assert status == "pass"

    def test_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path / "nonexistent"))
        status, _ = _check_config_dir()
        assert status == "fail"


class TestCheckAllowedUsers:
    def test_set(self, monkeypatch) -> None:
        monkeypatch.setenv("ALLOWED_USERS", "123,456")
        status, msg = _check_allowed_users()
        assert status == "pass"
        assert "2 user(s)" in msg

    def test_not_set(self, monkeypatch) -> None:
        monkeypatch.delenv("ALLOWED_USERS", raising=False)
        status, _ = _check_allowed_users()
        assert status == "fail"

    def test_invalid(self, monkeypatch) -> None:
        monkeypatch.setenv("ALLOWED_USERS", "abc")
        status, _ = _check_allowed_users()
        assert status == "fail"


class TestFindOrphanedWindows:
    def test_no_orphans(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        state = {"thread_bindings": {"123": {"42": "@5"}}}
        (tmp_path / "state.json").write_text(json.dumps(state))

        monkeypatch.setattr(
            "ccbot.doctor_cmd._list_live_windows",
            lambda _: {"@5": "bound-window"},
        )

        assert _find_orphaned_windows() == []

    def test_finds_orphan(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        monkeypatch.setattr(
            "ccbot.doctor_cmd._list_live_windows",
            lambda _: {"@10": "orphan-window"},
        )

        result = _find_orphaned_windows()
        assert len(result) == 1
        assert result[0] == ("@10", "orphan-window")


class TestDoctorMain:
    def test_runs_without_crash(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "test")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        tmp_path.mkdir(exist_ok=True)

        monkeypatch.setattr(
            "ccbot.doctor_cmd.shutil.which",
            lambda _cmd: f"/usr/bin/{_cmd}",
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._check_tmux_session",
            lambda: ("pass", 'tmux session "test" exists'),
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._check_hook",
            lambda: ("pass", "hook installed", True),
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._find_orphaned_windows",
            lambda: [],
        )

        with pytest.raises(SystemExit) as exc_info:
            doctor_main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "\u2713" in captured.out

    def test_shows_provider_name(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("CCBOT_PROVIDER", "claude")
        monkeypatch.setenv("TMUX_SESSION_NAME", "test")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

        monkeypatch.setattr(
            "ccbot.doctor_cmd.shutil.which",
            lambda _cmd: f"/usr/bin/{_cmd}",
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._check_tmux_session",
            lambda: ("pass", "ok"),
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._check_hook",
            lambda: ("pass", "hook installed", True),
        )
        monkeypatch.setattr("ccbot.doctor_cmd._find_orphaned_windows", lambda: [])

        with pytest.raises(SystemExit):
            doctor_main()

        captured = capsys.readouterr()
        assert "Provider: claude" in captured.out

    def test_skips_hook_check_for_hookless_provider(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("CCBOT_PROVIDER", "codex")
        monkeypatch.setenv("TMUX_SESSION_NAME", "test")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

        monkeypatch.setattr(
            "ccbot.doctor_cmd.shutil.which",
            lambda _cmd: f"/usr/bin/{_cmd}",
        )
        monkeypatch.setattr(
            "ccbot.doctor_cmd._check_tmux_session",
            lambda: ("pass", "ok"),
        )
        monkeypatch.setattr("ccbot.doctor_cmd._find_orphaned_windows", lambda: [])

        with pytest.raises(SystemExit):
            doctor_main()

        captured = capsys.readouterr()
        assert "Provider: codex" in captured.out
        assert "hook check skipped" in captured.out


class TestCheckProviderCommand:
    def test_found(self, monkeypatch) -> None:
        from ccbot.doctor_cmd import _check_provider_command

        monkeypatch.setattr(
            "ccbot.doctor_cmd.shutil.which", lambda _cmd: "/usr/bin/codex"
        )
        status, msg = _check_provider_command("codex")
        assert status == "pass"
        assert "codex" in msg

    def test_not_found(self, monkeypatch) -> None:
        from ccbot.doctor_cmd import _check_provider_command

        monkeypatch.setattr("ccbot.doctor_cmd.shutil.which", lambda _cmd: None)
        status, msg = _check_provider_command("codex")
        assert status == "fail"
        assert "codex" in msg
