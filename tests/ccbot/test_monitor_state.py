"""Unit tests for MonitorState and TrackedSession persistence."""

import json

import pytest

from ccbot.monitor_state import MonitorState, TrackedSession


class TestTrackedSession:
    def test_to_dict_from_dict_roundtrip(self):
        original = TrackedSession(
            session_id="sess-1",
            file_path="/tmp/test.jsonl",
            last_byte_offset=42,
        )
        restored = TrackedSession.from_dict(original.to_dict())
        assert restored.session_id == "sess-1"
        assert restored.file_path == "/tmp/test.jsonl"
        assert restored.last_byte_offset == 42

    def test_from_dict_missing_fields_uses_defaults(self):
        session = TrackedSession.from_dict({})
        assert session.session_id == ""
        assert session.file_path == ""
        assert session.last_byte_offset == 0


class TestMonitorStateLoad:
    def test_load_missing_file(self, tmp_path):
        state = MonitorState(state_file=tmp_path / "missing.json")
        state.load()
        assert state.tracked_sessions == {}

    def test_load_valid_json(self, tmp_path):
        state_file = tmp_path / "state.json"
        data = {
            "tracked_sessions": {
                "s1": {
                    "session_id": "s1",
                    "file_path": "/a.jsonl",
                    "last_byte_offset": 100,
                }
            }
        }
        state_file.write_text(json.dumps(data))
        state = MonitorState(state_file=state_file)
        state.load()
        assert "s1" in state.tracked_sessions
        assert state.tracked_sessions["s1"].last_byte_offset == 100

    def test_load_corrupt_json(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid json!!!")
        state = MonitorState(state_file=state_file)
        state.load()
        assert state.tracked_sessions == {}


class TestMonitorStateSave:
    def test_save_writes_via_atomic_write(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        state = MonitorState(state_file=state_file)
        state.update_session(
            TrackedSession(session_id="s1", file_path="/a.jsonl", last_byte_offset=10)
        )
        calls: list[tuple] = []

        def fake_write(path, data, indent=2):
            calls.append((path, data))

        monkeypatch.setattr("ccbot.utils.atomic_write_json", fake_write)
        state.save()
        assert len(calls) == 1
        path, data = calls[0]
        assert path == state_file
        assert "s1" in data["tracked_sessions"]
        assert data["tracked_sessions"]["s1"]["last_byte_offset"] == 10


class TestMonitorStateOperations:
    @pytest.fixture
    def state(self, tmp_path) -> MonitorState:
        return MonitorState(state_file=tmp_path / "state.json")

    @pytest.mark.parametrize(
        "key, expected_found",
        [
            pytest.param("s1", True, id="existing"),
            pytest.param("nonexistent", False, id="missing"),
        ],
    )
    def test_get_session(self, state, key, expected_found):
        session = TrackedSession(session_id="s1", file_path="/a.jsonl")
        state.tracked_sessions["s1"] = session
        result = state.get_session(key)
        if expected_found:
            assert result is session
        else:
            assert result is None

    def test_update_session_adds_new(self, state):
        session = TrackedSession(session_id="s1", file_path="/a.jsonl")
        state.update_session(session)
        assert state.tracked_sessions["s1"] is session

    def test_update_session_sets_dirty(self, state):
        state.update_session(TrackedSession(session_id="s1", file_path="/a.jsonl"))
        assert state._dirty is True

    def test_remove_session_deletes(self, state):
        state.tracked_sessions["s1"] = TrackedSession(
            session_id="s1", file_path="/a.jsonl"
        )
        state.remove_session("s1")
        assert "s1" not in state.tracked_sessions

    def test_remove_session_missing_no_error(self, state):
        state.remove_session("nonexistent")
        assert state.tracked_sessions == {}


class TestSaveIfDirty:
    def test_dirty_saves(self, tmp_path, monkeypatch):
        state = MonitorState(state_file=tmp_path / "state.json")
        state.update_session(TrackedSession(session_id="s1", file_path="/a.jsonl"))
        saved: list[bool] = []

        def fake_write(*_args, **_kwargs):
            saved.append(True)

        monkeypatch.setattr("ccbot.utils.atomic_write_json", fake_write)
        state.save_if_dirty()
        assert len(saved) == 1

    def test_not_dirty_skips_save(self, tmp_path, monkeypatch):
        state = MonitorState(state_file=tmp_path / "state.json")
        saved: list[bool] = []

        def fake_write(*_args, **_kwargs):
            saved.append(True)

        monkeypatch.setattr("ccbot.utils.atomic_write_json", fake_write)
        state.save_if_dirty()
        assert len(saved) == 0
