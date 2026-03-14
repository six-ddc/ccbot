"""Unit tests for SessionMonitor JSONL reading and offset handling."""

import json
import logging
from unittest.mock import AsyncMock

import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import (
    _HEARTBEAT_INTERVAL_SECONDS,
    _MAX_EMITTED_TEXT_CHARS,
    _MAX_DUPLICATE_OFFSET_GAP_BYTES,
    _MAX_INITIAL_BACKLOG_BYTES,
    SessionInfo,
    SessionMonitor,
)
from ccbot.transcript_parser import ParsedEntry


class TestReadNewLinesOffsetRecovery:
    """Tests for _read_new_lines offset corruption recovery."""

    @pytest.fixture
    def monitor(self, tmp_path):
        """Create a SessionMonitor with temp state file."""
        return SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "monitor_state.json",
        )

    @pytest.mark.asyncio
    async def test_mid_line_offset_recovery(self, monitor, tmp_path, make_jsonl_entry):
        """Recover from corrupted offset pointing mid-line."""
        # Create JSONL file with two valid lines
        jsonl_file = tmp_path / "session.jsonl"
        entry1 = make_jsonl_entry(msg_type="assistant", content="first message")
        entry2 = make_jsonl_entry(msg_type="assistant", content="second message")
        jsonl_file.write_text(
            json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n",
            encoding="utf-8",
        )

        # Calculate offset pointing into the middle of line 1
        line1_bytes = len(json.dumps(entry1).encode("utf-8")) // 2
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=line1_bytes,  # Mid-line (corrupted)
        )

        # Read should recover and return empty (offset moved to next line)
        result = await monitor._read_new_lines(session, jsonl_file)

        # Should return empty list (recovery skips to next line, no new content yet)
        assert result == []

        # Offset should now point to start of line 2
        line1_full = len(json.dumps(entry1).encode("utf-8")) + 1  # +1 for newline
        assert session.last_byte_offset == line1_full

    @pytest.mark.asyncio
    async def test_valid_offset_reads_normally(
        self, monitor, tmp_path, make_jsonl_entry
    ):
        """Normal reading when offset points to line start."""
        jsonl_file = tmp_path / "session.jsonl"
        entry1 = make_jsonl_entry(msg_type="assistant", content="first")
        entry2 = make_jsonl_entry(msg_type="assistant", content="second")
        jsonl_file.write_text(
            json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n",
            encoding="utf-8",
        )

        # Offset at 0 should read both lines
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=0,
        )

        result = await monitor._read_new_lines(session, jsonl_file)

        assert len(result) == 2
        assert session.last_byte_offset == jsonl_file.stat().st_size

    @pytest.mark.asyncio
    async def test_truncation_detection(self, monitor, tmp_path, make_jsonl_entry):
        """Detect file truncation and reset offset."""
        jsonl_file = tmp_path / "session.jsonl"
        entry = make_jsonl_entry(msg_type="assistant", content="content")
        jsonl_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        # Set offset beyond file size (simulates truncation)
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=9999,  # Beyond file size
        )

        result = await monitor._read_new_lines(session, jsonl_file)

        # Should reset offset to 0 and read the line
        assert session.last_byte_offset == jsonl_file.stat().st_size
        assert len(result) == 1


class TestSessionMonitorOversizedProtection:
    @pytest.fixture
    def monitor(self, tmp_path):
        return SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "monitor_state.json",
        )

    @pytest.mark.asyncio
    async def test_check_for_updates_truncates_oversized_entry_text(
        self, monitor, tmp_path, monkeypatch
    ):
        session_id = "s1"
        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.write_text("{}\n", encoding="utf-8")

        tracked = TrackedSession(
            session_id=session_id,
            file_path=str(jsonl_file),
            last_byte_offset=0,
        )
        monitor.state.update_session(tracked)
        monitor._file_mtimes[session_id] = -1.0

        async def fake_scan_projects():
            return [SessionInfo(session_id=session_id, file_path=jsonl_file)]

        async def fake_read_new_lines(_tracked, _file):
            return [{"__ccbot_line_start": 10, "__ccbot_line_end": 20}]

        huge_text = "X" * (_MAX_EMITTED_TEXT_CHARS + 777)

        def fake_parse_entries(_entries, pending_tools=None):
            return (
                [ParsedEntry(role="assistant", text=huge_text, content_type="text")],
                pending_tools or {},
            )

        monkeypatch.setattr(monitor, "scan_projects", fake_scan_projects)
        monkeypatch.setattr(monitor, "_read_new_lines", fake_read_new_lines)
        monkeypatch.setattr(
            "ccbot.session_monitor.TranscriptParser.parse_entries",
            fake_parse_entries,
        )

        messages = await monitor.check_for_updates({session_id})
        assert len(messages) == 1
        assert len(messages[0].text) < len(huge_text)
        assert "truncated by CCBot" in messages[0].text

    @pytest.mark.asyncio
    async def test_check_for_updates_fast_forwards_large_startup_backlog(
        self, monitor, tmp_path, monkeypatch
    ):
        session_id = "s2"
        jsonl_file = tmp_path / "session.jsonl"
        payload = "A" * (_MAX_INITIAL_BACKLOG_BYTES + 100)
        jsonl_file.write_text(payload, encoding="utf-8")

        tracked = TrackedSession(
            session_id=session_id,
            file_path=str(jsonl_file),
            last_byte_offset=0,
        )
        monitor.state.update_session(tracked)
        monitor._file_mtimes[session_id] = -1.0

        async def fake_scan_projects():
            return [SessionInfo(session_id=session_id, file_path=jsonl_file)]

        mock_read_new_lines = AsyncMock(return_value=[])
        monkeypatch.setattr(monitor, "scan_projects", fake_scan_projects)
        monkeypatch.setattr(monitor, "_read_new_lines", mock_read_new_lines)

        messages = await monitor.check_for_updates({session_id})
        assert messages == []
        assert tracked.last_byte_offset == jsonl_file.stat().st_size
        mock_read_new_lines.assert_not_called()

    def test_duplicate_emit_guard_skips_nearby_identical_entries(self, monitor):
        assert (
            monitor._should_skip_duplicate_emit(
                session_id="dup",
                role="assistant",
                content_type="text",
                tool_use_id=None,
                text="same message",
                line_end=100,
            )
            is False
        )
        assert (
            monitor._should_skip_duplicate_emit(
                session_id="dup",
                role="assistant",
                content_type="text",
                tool_use_id=None,
                text="same message",
                line_end=150,
            )
            is True
        )

    def test_duplicate_emit_guard_allows_far_apart_repeats(self, monitor):
        assert (
            monitor._should_skip_duplicate_emit(
                session_id="dup2",
                role="assistant",
                content_type="text",
                tool_use_id=None,
                text="repeat later",
                line_end=10,
            )
            is False
        )
        assert (
            monitor._should_skip_duplicate_emit(
                session_id="dup2",
                role="assistant",
                content_type="text",
                tool_use_id=None,
                text="repeat later",
                line_end=10 + _MAX_DUPLICATE_OFFSET_GAP_BYTES + 1,
            )
            is False
        )

    def test_reset_monitor_state_clears_offsets_and_writes_file(self, monitor, tmp_path):
        state_path = tmp_path / "monitor_state.json"
        monitor.state.state_file = state_path
        monitor.state.tracked_sessions["a"] = TrackedSession(
            session_id="a",
            file_path="/tmp/a.jsonl",
            last_byte_offset=123,
        )
        monitor._file_mtimes["a"] = 1.0
        monitor._pending_tools["a"] = {"tool": "x"}
        monitor._seen_sessions.add("a")

        monitor._reset_monitor_state("test")

        assert monitor.state.tracked_sessions == {}
        assert monitor._file_mtimes == {}
        assert monitor._pending_tools == {}
        assert monitor._seen_sessions == set()
        assert monitor._silence_recovery_done is True
        assert state_path.exists()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload.get("tracked_sessions") == {}

    def test_monitor_heartbeat_logs_and_throttles(self, monitor, caplog, monkeypatch):
        monitor.state.tracked_sessions["s1"] = TrackedSession(
            session_id="s1",
            file_path="/tmp/s1.jsonl",
            last_byte_offset=10,
        )
        monitor._pending_tools["s1"] = {"tool": "x"}
        monitor._last_message_monotonic = 100.0
        monitor._last_codex_mapper_sync = 90.0

        ticks = iter(
            [
                120.0,  # first call -> heartbeat
                120.0 + (_HEARTBEAT_INTERVAL_SECONDS - 1.0),  # throttled
                120.0 + (_HEARTBEAT_INTERVAL_SECONDS + 1.0),  # heartbeat again
            ]
        )
        monkeypatch.setattr("ccbot.session_monitor.time.monotonic", lambda: next(ticks))

        with caplog.at_level(logging.INFO):
            monitor._maybe_log_heartbeat({"s1"})
            monitor._maybe_log_heartbeat({"s1"})
            monitor._maybe_log_heartbeat({"s1"})

        heartbeats = [
            rec.message for rec in caplog.records if rec.message.startswith("Monitor heartbeat:")
        ]
        assert len(heartbeats) == 2
