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
