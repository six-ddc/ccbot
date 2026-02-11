"""Tests for SessionMonitor (PR 2 pending_tools cleanup + PR 3 scan_projects)."""

from unittest.mock import AsyncMock, patch

import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import SessionMonitor


@pytest.fixture
def monitor(tmp_path) -> SessionMonitor:
    """Create a SessionMonitor with temp state file."""
    return SessionMonitor(
        projects_path=tmp_path / "projects",
        poll_interval=0.1,
        state_file=tmp_path / "monitor_state.json",
    )


class TestPendingToolsCleanup:
    async def test_cleanup_stale_removes_pending_tools(
        self, monitor: SessionMonitor
    ) -> None:
        """_cleanup_all_stale_sessions should remove _pending_tools for stale sessions."""
        monitor._pending_tools["stale-session"] = {"tool_1": {"name": "Read"}}
        monitor.state.update_session(
            TrackedSession(session_id="stale-session", file_path="/fake/path")
        )

        with patch.object(
            monitor,
            "_load_current_session_map",
            new_callable=AsyncMock,
            return_value={},
        ):
            await monitor._cleanup_all_stale_sessions()

        assert "stale-session" not in monitor._pending_tools

    async def test_detect_changes_removes_pending_tools(
        self, monitor: SessionMonitor
    ) -> None:
        """_detect_and_cleanup_changes should remove _pending_tools when session changes."""
        old_sid = "old-session"
        new_sid = "new-session"

        monitor._pending_tools[old_sid] = {"tool_1": {"name": "Write"}}
        monitor._last_session_map = {"my-window": old_sid}
        monitor.state.update_session(
            TrackedSession(session_id=old_sid, file_path="/fake/path")
        )

        new_map = {"my-window": new_sid}
        with patch.object(
            monitor,
            "_load_current_session_map",
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        assert old_sid not in monitor._pending_tools
