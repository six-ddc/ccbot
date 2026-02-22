"""Tests for SessionMonitor._process_session_file helper."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from ccbot.session_monitor import SessionMonitor, NewMessage, SessionInfo


@pytest.mark.asyncio
async def test_process_session_file_emits_message(tmp_path):
    """_process_session_file reads new JSONL lines and returns NewMessage objects."""
    # Create a minimal JSONL file with one assistant message
    jsonl = tmp_path / "test-session.jsonl"
    jsonl.write_text(
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hello"}]},"isFinal":true}\n'
    )

    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._last_session_map = {"@0": "test-session"}

    from ccbot.monitor_state import TrackedSession
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
        return {}  # _detect_and_cleanup_changes returns dict

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

    # Create the file that the event will point to
    jsonl_path = tmp_path / "-some-project" / "abc-session.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    jsonl_path.touch()

    fake_changes = [(watchfiles.Change.modified, str(jsonl_path))]

    async def fake_awatch(*args, **kwargs):
        yield fake_changes

    with patch("ccbot.session_monitor.watchfiles.awatch", fake_awatch):
        await monitor._file_watch_loop()

    assert "abc-session" in processed
