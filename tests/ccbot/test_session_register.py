"""Tests for tmux runtime session registration helpers."""

import json
import sys

import pytest

from ccbot.runtimes import RUNTIME_CODEX
from ccbot.session_register import build_session_map_key, register_session


class TestBuildSessionMapKey:
    def test_uses_explicit_tmux_session_name(self) -> None:
        assert build_session_map_key("@7", "bots") == "bots:@7"


class TestRegisterSession:
    def test_registers_runtime_session(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        ok = register_session(
            window_id="@12",
            session_id="019cdc6f-d3c8-7003-9730-bf3608dcaec9",
            cwd="/tmp/project",
            window_name="proj",
            runtime=RUNTIME_CODEX,
            transcript_path="/tmp/logs/rollout-2026-03-11T18-27-22-019cdc6f-d3c8-7003-9730-bf3608dcaec9.jsonl",
        )

        assert ok is True
        session_map = json.loads((tmp_path / "session_map.json").read_text())
        assert session_map["ccbot:@12"] == {
            "session_id": "019cdc6f-d3c8-7003-9730-bf3608dcaec9",
            "cwd": "/tmp/project",
            "window_name": "proj",
            "runtime": RUNTIME_CODEX,
            "transcript_path": "/tmp/logs/rollout-2026-03-11T18-27-22-019cdc6f-d3c8-7003-9730-bf3608dcaec9.jsonl",
        }

    def test_rejects_invalid_window_id(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))

        ok = register_session(
            window_id="proj",
            session_id="019cdc6f-d3c8-7003-9730-bf3608dcaec9",
            cwd="/tmp/project",
        )

        assert ok is False
        assert not (tmp_path / "session_map.json").exists()


class TestSessionRegisterCli:
    def test_cli_exits_success(self, monkeypatch, tmp_path) -> None:
        from ccbot.session_register import session_register_main

        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "ccbot",
                "session-register",
                "--window-id",
                "@9",
                "--session-id",
                "session-9",
                "--cwd",
                "/tmp/project",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            session_register_main()

        assert exc.value.code == 0
        session_map = json.loads((tmp_path / "session_map.json").read_text())
        assert session_map["ccbot:@9"]["session_id"] == "session-9"
