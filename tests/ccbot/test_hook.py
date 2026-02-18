"""Tests for Claude Code session tracking hook."""

import io
import json
import sys

import pytest

from ccbot.hook import (
    _UUID_RE,
    _hook_status,
    _install_hook,
    _is_hook_installed,
    _uninstall_hook,
    hook_main,
)


class TestInstallHook:
    def test_install_into_empty_settings(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _install_hook()
        assert result == 0

        settings = json.loads(settings_file.read_text())
        session_start = settings["hooks"]["SessionStart"]
        assert len(session_start) == 1
        assert session_start[0]["hooks"][0]["command"] == "ccbot hook"

    def test_install_adds_to_existing_matcher_group(
        self, tmp_path, monkeypatch
    ) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "session-start.sh",
                                "timeout": 5,
                            }
                        ],
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _install_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        session_start = updated["hooks"]["SessionStart"]
        assert len(session_start) == 1
        hooks_list = session_start[0]["hooks"]
        assert len(hooks_list) == 2
        assert hooks_list[1]["command"] == "ccbot hook"

    def test_install_skips_when_already_present_with_wrapper(
        self, tmp_path, monkeypatch
    ) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "ccbot hook 2>/dev/null || true",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _install_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        hooks_list = updated["hooks"]["SessionStart"][0]["hooks"]
        assert len(hooks_list) == 1

    def test_install_skips_when_already_present_with_full_path(
        self, tmp_path, monkeypatch
    ) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/local/bin/ccbot hook",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _install_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        hooks_list = updated["hooks"]["SessionStart"][0]["hooks"]
        assert len(hooks_list) == 1

    def test_install_uses_path_relative_command(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        _install_hook()

        updated = json.loads(settings_file.read_text())
        cmd = updated["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert cmd == "ccbot hook"
        assert "/" not in cmd


class TestUuidRegex:
    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "00000000-0000-0000-0000-000000000000",
            "abcdef01-2345-6789-abcd-ef0123456789",
        ],
        ids=["standard", "all-zeros", "all-hex"],
    )
    def test_valid_uuid_matches(self, value: str) -> None:
        assert _UUID_RE.match(value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "not-a-uuid",
            "550e8400-e29b-41d4-a716",
            "550e8400-e29b-41d4-a716-44665544000g",
            "",
        ],
        ids=["gibberish", "truncated", "invalid-hex-char", "empty"],
    )
    def test_invalid_uuid_no_match(self, value: str) -> None:
        assert _UUID_RE.match(value) is None


class TestIsHookInstalled:
    def test_hook_present(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "ccbot hook", "timeout": 5}
                        ]
                    }
                ]
            }
        }
        assert _is_hook_installed(settings) is True

    def test_no_hooks_key(self) -> None:
        assert _is_hook_installed({}) is False

    def test_different_hook_command(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "other-tool hook"}]}
                ]
            }
        }
        assert _is_hook_installed(settings) is False

    def test_shell_wrapped_command_matches(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "ccbot hook 2>/dev/null || true",
                            }
                        ]
                    }
                ]
            }
        }
        assert _is_hook_installed(settings) is True

    def test_full_path_matches(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/bin/ccbot hook",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        assert _is_hook_installed(settings) is True


class TestHookMainValidation:
    def _run_hook_main(
        self, monkeypatch: pytest.MonkeyPatch, payload: dict, *, tmux_pane: str = ""
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["ccbot", "hook"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        if tmux_pane:
            monkeypatch.setenv("TMUX_PANE", tmux_pane)
        else:
            monkeypatch.delenv("TMUX_PANE", raising=False)
        hook_main()

    def test_missing_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {"cwd": "/tmp", "hook_event_name": "SessionStart"},
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_invalid_uuid_format(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "not-a-uuid",
                "cwd": "/tmp",
                "hook_event_name": "SessionStart",
            },
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_relative_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "cwd": "relative/path",
                "hook_event_name": "SessionStart",
            },
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_non_session_start_event(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "cwd": "/tmp",
                "hook_event_name": "Stop",
            },
        )
        assert not (tmp_path / "session_map.json").exists()


class TestUninstallHook:
    def test_uninstall_removes_hook(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "ccbot hook", "timeout": 5}
                        ]
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _uninstall_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        assert not _is_hook_installed(updated)

    def test_uninstall_no_settings_file(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "nonexistent.json"
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _uninstall_hook()
        assert result == 0

    def test_uninstall_preserves_other_hooks_in_same_group(
        self, tmp_path, monkeypatch
    ) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "session-start.sh",
                                "timeout": 5,
                            },
                            {"type": "command", "command": "ccbot hook", "timeout": 5},
                        ],
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _uninstall_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        session_start = updated["hooks"]["SessionStart"]
        assert len(session_start) == 1
        hooks_list = session_start[0]["hooks"]
        assert len(hooks_list) == 1
        assert hooks_list[0]["command"] == "session-start.sh"

    def test_uninstall_removes_wrapped_variant(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "ccbot hook 2>/dev/null || true",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _uninstall_hook()
        assert result == 0

        updated = json.loads(settings_file.read_text())
        assert not _is_hook_installed(updated)
        assert updated["hooks"]["SessionStart"] == []

    def test_uninstall_not_installed(self, tmp_path, monkeypatch) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"hooks": {}}))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _uninstall_hook()
        assert result == 0


class TestHookStatus:
    def test_installed(self, tmp_path, monkeypatch, capsys) -> None:
        settings_file = tmp_path / "settings.json"
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "/usr/bin/ccbot hook"}]}
                ]
            }
        }
        settings_file.write_text(json.dumps(settings))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _hook_status()
        assert result == 0
        assert "Installed" in capsys.readouterr().out

    def test_not_installed(self, tmp_path, monkeypatch, capsys) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"hooks": {}}))
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _hook_status()
        assert result == 1
        assert "Not installed" in capsys.readouterr().out

    def test_no_settings_file(self, tmp_path, monkeypatch, capsys) -> None:
        settings_file = tmp_path / "nonexistent.json"
        monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)

        result = _hook_status()
        assert result == 1
        assert "Not installed" in capsys.readouterr().out
