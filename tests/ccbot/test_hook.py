"""Tests for runtime session tracking hooks."""

import io
import json
import subprocess
import sys

import pytest

from ccbot.hook import _UUID_RE, _has_command_hook, _resolve_install_targets, hook_main


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


class TestHasCommandHook:
    def test_hook_present(self) -> None:
        entries = [
            {"hooks": [{"type": "command", "command": "ccbot hook", "timeout": 5}]}
        ]
        assert _has_command_hook(entries) is True

    def test_different_hook_command(self) -> None:
        entries = [{"hooks": [{"type": "command", "command": "other-tool hook"}]}]
        assert _has_command_hook(entries) is False

    def test_full_path_matches(self) -> None:
        entries = [
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
        assert _has_command_hook(entries) is True


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

    def test_codex_session_start_registers_transcript_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_PANE", "%1")
        monkeypatch.setenv("CCBOT_RUNTIME", "codex")

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="ccbot:@12:project\n",
                stderr="",
            ),
        )

        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "019cdc6f-d3c8-7003-9730-bf3608dcaec9",
                "cwd": "/tmp/project",
                "hook_event_name": "SessionStart",
                "transcript_path": "/tmp/project/session.jsonl",
            },
            tmux_pane="%1",
        )

        session_map = json.loads((tmp_path / "session_map.json").read_text())
        assert session_map["ccbot:@12"]["runtime"] == "codex"
        assert session_map["ccbot:@12"]["transcript_path"] == "/tmp/project/session.jsonl"


class TestCodexHookInstall:
    def test_resolve_install_targets_defaults_to_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)
        assert _resolve_install_targets(None) == ["claude", "codex"]

    def test_resolve_install_targets_uses_env_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCBOT_RUNTIME", "codex")
        assert _resolve_install_targets(None) == ["codex"]

    def test_install_codex_writes_hooks_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        codex_home = tmp_path / ".codex"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setattr(sys, "argv", ["ccbot", "hook", "--install-codex"])

        with pytest.raises(SystemExit) as exc:
            hook_main()

        assert exc.value.code == 0
        hooks = json.loads((codex_home / "hooks.json").read_text())
        assert "hooks" in hooks
        assert "SessionStart" in hooks["hooks"]
        assert _has_command_hook(hooks["hooks"]["SessionStart"]) is True

    def test_install_with_runtime_codex_writes_hooks_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        codex_home = tmp_path / ".codex"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setattr(
            sys, "argv", ["ccbot", "hook", "--install", "--run", "codex"]
        )

        with pytest.raises(SystemExit) as exc:
            hook_main()

        assert exc.value.code == 0
        hooks = json.loads((codex_home / "hooks.json").read_text())
        assert "hooks" in hooks
        assert "SessionStart" in hooks["hooks"]

    def test_install_codex_migrates_legacy_top_level_session_start(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        codex_home = tmp_path / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "hooks.json").write_text(
            json.dumps(
                {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo legacy",
                                }
                            ]
                        }
                    ]
                }
            )
        )
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setattr(sys, "argv", ["ccbot", "hook", "--install-codex"])

        with pytest.raises(SystemExit) as exc:
            hook_main()

        assert exc.value.code == 0
        hooks = json.loads((codex_home / "hooks.json").read_text())
        assert "SessionStart" not in hooks
        assert "hooks" in hooks
        assert _has_command_hook(hooks["hooks"]["SessionStart"]) is True
