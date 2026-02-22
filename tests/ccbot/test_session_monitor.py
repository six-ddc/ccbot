"""Unit tests for SessionMonitor JSONL reading and offset handling."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import SessionInfo, SessionMonitor


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


@pytest.mark.asyncio
async def test_process_session_file_emits_message(tmp_path):
    """_process_session_file reads new JSONL lines and returns NewMessage objects."""
    jsonl = tmp_path / "test-session.jsonl"
    jsonl.write_text(
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hello"}]},"isFinal":true}\n'
    )

    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._last_session_map = {"@0": "test-session"}

    tracked = TrackedSession(
        session_id="test-session",
        file_path=str(jsonl),
        last_byte_offset=0,
    )
    monitor.state.update_session(tracked)

    session_info = SessionInfo(session_id="test-session", file_path=jsonl)
    active_ids = {"test-session"}

    messages = await monitor._process_session_file(session_info, active_ids)
    assert len(messages) == 1
    assert messages[0].text == "hello"
    assert messages[0].session_id == "test-session"


@pytest.mark.asyncio
async def test_housekeeping_loop_runs_cleanup(tmp_path):
    """_housekeeping_loop calls session lifecycle management at least once."""
    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._running = True

    cleanup_called = []

    async def fake_cleanup():
        cleanup_called.append(1)
        monitor._running = False  # stop after one iteration
        return {}

    monitor._detect_and_cleanup_changes = fake_cleanup

    with patch("ccbot.session.session_manager") as mock_sm:
        mock_sm.load_session_map = AsyncMock()
        await monitor._housekeeping_loop()

    assert len(cleanup_called) == 1


@pytest.mark.asyncio
async def test_file_watch_loop_processes_jsonl_change(tmp_path):
    """_file_watch_loop calls _process_session_file when a .jsonl file changes."""
    import watchfiles

    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._running = True
    monitor._last_session_map = {"@0": "abc-session"}

    processed = []

    async def fake_process(session_info, active_ids):
        processed.append(session_info.session_id)
        monitor._running = False  # stop after first event
        return []

    monitor._process_session_file = fake_process

    jsonl_path = tmp_path / "-some-project" / "abc-session.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    jsonl_path.touch()

    fake_changes = [(watchfiles.Change.modified, str(jsonl_path))]

    async def fake_awatch(*args, **kwargs):
        yield fake_changes

    with patch("ccbot.session_monitor.watchfiles.awatch", fake_awatch):
        await monitor._file_watch_loop()

    assert "abc-session" in processed


@pytest.mark.asyncio
async def test_start_creates_two_tasks(tmp_path):
    """start() creates both _file_watch_loop and _housekeeping_loop tasks."""
    monitor = SessionMonitor(projects_path=tmp_path)

    async def noop_coro():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            pass

    monitor._file_watch_loop = noop_coro
    monitor._housekeeping_loop = noop_coro

    monitor.start()
    await asyncio.sleep(0.05)

    assert monitor._task is not None
    assert monitor._watch_task is not None
    assert monitor._running is True

    monitor.stop()
    assert monitor._running is False
