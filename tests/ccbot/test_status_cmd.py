"""Tests for ccbot status command."""

import contextlib
import json

from ccbot.status_cmd import _read_json, status_main


class TestReadJson:
    def test_valid_json(self, tmp_path) -> None:
        path = tmp_path / "test.json"
        path.write_text('{"key": "value"}')
        assert _read_json(path) == {"key": "value"}

    def test_missing_file(self, tmp_path) -> None:
        assert _read_json(tmp_path / "nonexistent.json") == {}

    def test_invalid_json(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert _read_json(path) == {}


class TestStatusMain:
    def test_no_state_files(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "test-session")
        monkeypatch.setattr("ccbot.status_cmd._list_tmux_windows", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "ccbot" in captured.out
        assert "test-session (0 windows)" in captured.out
        assert "Monitored sessions: 0" in captured.out

    def test_with_bound_window(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        state = {
            "thread_bindings": {"12345": {"42": "@5"}},
            "window_display_names": {"@5": "my-project"},
        }
        (tmp_path / "state.json").write_text(json.dumps(state))

        session_map = {
            "ccbot:@5": {"session_id": "abc-123", "cwd": "/tmp"},
        }
        (tmp_path / "session_map.json").write_text(json.dumps(session_map))

        monkeypatch.setattr(
            "ccbot.status_cmd._list_tmux_windows",
            lambda _: [{"id": "@5", "name": "my-project"}],
        )

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "1 windows" in captured.out
        assert "Monitored sessions: 1" in captured.out
        assert "@5" in captured.out
        assert "my-project" in captured.out
        assert "topic 42" in captured.out
        assert "alive" in captured.out

    def test_dead_binding(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        state = {
            "thread_bindings": {"12345": {"42": "@5"}},
            "window_display_names": {"@5": "gone-project"},
        }
        (tmp_path / "state.json").write_text(json.dumps(state))

        monkeypatch.setattr("ccbot.status_cmd._list_tmux_windows", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "dead" in captured.out
        assert "gone-project" in captured.out

    def test_unbound_window(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_SESSION_NAME", "ccbot")

        monkeypatch.setattr(
            "ccbot.status_cmd._list_tmux_windows",
            lambda _: [{"id": "@10", "name": "orphan"}],
        )

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "(unbound)" in captured.out
        assert "orphan" in captured.out

    def test_shows_provider_info(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("CCBOT_PROVIDER", "claude")
        monkeypatch.setenv("TMUX_SESSION_NAME", "test")
        monkeypatch.setattr("ccbot.status_cmd._list_tmux_windows", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "Provider: claude" in captured.out
        assert "hook" in captured.out
        assert "resume" in captured.out

    def test_hookless_provider_capabilities(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setenv("CCBOT_PROVIDER", "codex")
        monkeypatch.setenv("TMUX_SESSION_NAME", "test")
        monkeypatch.setattr("ccbot.status_cmd._list_tmux_windows", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        captured = capsys.readouterr()
        assert "Provider: codex" in captured.out
        assert "hook" not in captured.out.split("Provider:")[1].split("\n")[0]
