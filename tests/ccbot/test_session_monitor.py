"""Tests for SessionMonitor."""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import NewWindowEvent, SessionMonitor


@pytest.fixture
def monitor(tmp_path) -> SessionMonitor:
    return SessionMonitor(
        projects_path=tmp_path / "projects",
        poll_interval=0.1,
        state_file=tmp_path / "monitor_state.json",
    )


class TestPendingToolsCleanup:
    async def test_cleanup_stale_removes_pending_tools(
        self, monitor: SessionMonitor
    ) -> None:
        monitor._pending_tools["stale-session"] = {"tool_1": {"name": "Read"}}
        monitor.state.update_session(
            TrackedSession(session_id="stale-session", file_path="/fake/path")
        )

        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value={},
        ):
            await monitor._cleanup_all_stale_sessions()

        assert "stale-session" not in monitor._pending_tools

    async def test_detect_changes_removes_pending_tools(
        self, monitor: SessionMonitor
    ) -> None:
        old_sid = "old-session"
        new_sid = "new-session"

        monitor._pending_tools[old_sid] = {"tool_1": {"name": "Write"}}
        monitor._last_session_map = {
            "my-window": {"session_id": old_sid, "cwd": "/a", "window_name": ""}
        }
        monitor.state.update_session(
            TrackedSession(session_id=old_sid, file_path="/fake/path")
        )

        new_map = {"my-window": {"session_id": new_sid, "cwd": "/a", "window_name": ""}}
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        assert old_sid not in monitor._pending_tools


class TestNewWindowDetection:
    async def test_callback_fires_for_new_window(self, monitor: SessionMonitor) -> None:
        cb = AsyncMock(spec=lambda event: None)
        monitor.set_new_window_callback(cb)
        monitor._last_session_map = {}

        new_map = {"@5": {"session_id": "s1", "cwd": "/proj", "window_name": "proj"}}
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_called_once()
        event = cb.call_args[0][0]
        assert isinstance(event, NewWindowEvent)
        assert event.window_id == "@5"
        assert event.session_id == "s1"
        assert event.window_name == "proj"

    async def test_startup_does_not_trigger_callback(
        self, monitor: SessionMonitor
    ) -> None:
        cb = AsyncMock(spec=lambda event: None)
        monitor.set_new_window_callback(cb)

        initial_map = {"@0": {"session_id": "s0", "cwd": "/a", "window_name": "a"}}
        monitor._last_session_map = initial_map

        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=initial_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_not_called()

    async def test_callback_error_does_not_crash(self, monitor: SessionMonitor) -> None:
        cb = AsyncMock(side_effect=RuntimeError("boom"))
        monitor.set_new_window_callback(cb)
        monitor._last_session_map = {}

        new_map = {"@1": {"session_id": "s1", "cwd": "/x", "window_name": "x"}}
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_called_once()


class TestReadNewLines:
    async def test_truncation_resets_offset(self, tmp_path) -> None:
        session_file = tmp_path / "test.jsonl"
        session_file.write_text(
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n'
        )

        monitor = SessionMonitor(
            projects_path=tmp_path,
            state_file=tmp_path / "ms.json",
        )
        tracked = TrackedSession(
            session_id="t1",
            file_path=str(session_file),
            last_byte_offset=99999,
        )
        entries = await monitor._read_new_lines(tracked, session_file)
        assert tracked.last_byte_offset < 99999
        assert len(entries) >= 1

    async def test_incremental_read_from_offset(self, tmp_path) -> None:
        session_file = tmp_path / "test.jsonl"
        line1 = '{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}\n'
        line2 = '{"type":"assistant","message":{"content":[{"type":"text","text":"second"}]}}\n'
        session_file.write_text(line1 + line2)

        monitor = SessionMonitor(
            projects_path=tmp_path,
            state_file=tmp_path / "ms.json",
        )
        tracked = TrackedSession(
            session_id="t1",
            file_path=str(session_file),
            last_byte_offset=len(line1.encode()),
        )
        entries = await monitor._read_new_lines(tracked, session_file)
        assert len(entries) == 1

    async def test_partial_line_stops_reading(self, tmp_path) -> None:
        session_file = tmp_path / "test.jsonl"
        good_line = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
        )
        session_file.write_text(good_line + '{"type":"ass')

        monitor = SessionMonitor(
            projects_path=tmp_path,
            state_file=tmp_path / "ms.json",
        )
        tracked = TrackedSession(
            session_id="t1", file_path=str(session_file), last_byte_offset=0
        )
        entries = await monitor._read_new_lines(tracked, session_file)
        assert len(entries) == 1
        assert tracked.last_byte_offset == len(good_line.encode())


class TestCheckForUpdates:
    async def test_new_session_initializes_to_eof_fallback(self, tmp_path) -> None:
        """Fallback path: entries without transcript_path use scan_projects."""
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-new.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "sess-new",
                    "fullPath": str(session_file),
                    "projectPath": resolved,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        current_map = {
            "@0": {"session_id": "sess-new", "cwd": resolved, "window_name": "proj"},
        }
        with patch.object(
            monitor,
            "_get_active_cwds",
            spec=True,
            new_callable=AsyncMock,
            return_value={resolved},
        ):
            msgs = await monitor.check_for_updates(current_map)

        assert msgs == []
        tracked = monitor.state.get_session("sess-new")
        assert tracked is not None
        assert tracked.last_byte_offset == session_file.stat().st_size

    async def test_new_session_initializes_to_eof_direct(self, tmp_path) -> None:
        """Primary path: entries with transcript_path are read directly."""
        session_file = tmp_path / "transcript.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        monitor = SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "ms.json",
        )
        current_map = {
            "@0": {
                "session_id": "sess-direct",
                "cwd": "/proj",
                "window_name": "proj",
                "transcript_path": str(session_file),
            },
        }
        msgs = await monitor.check_for_updates(current_map)

        assert msgs == []
        tracked = monitor.state.get_session("sess-direct")
        assert tracked is not None
        assert tracked.last_byte_offset == session_file.stat().st_size

    async def test_unchanged_mtime_skips_read(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-1.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "sess-1",
                    "fullPath": str(session_file),
                    "projectPath": resolved,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        file_size = session_file.stat().st_size
        tracked = TrackedSession(
            session_id="sess-1",
            file_path=str(session_file),
            last_byte_offset=file_size,
        )
        monitor.state.update_session(tracked)
        monitor._file_mtimes["sess-1"] = session_file.stat().st_mtime

        current_map = {
            "@0": {"session_id": "sess-1", "cwd": resolved, "window_name": "proj"},
        }
        with (
            patch.object(
                monitor,
                "_get_active_cwds",
                spec=True,
                new_callable=AsyncMock,
                return_value={resolved},
            ),
            patch.object(
                monitor, "_read_new_lines", spec=True, new_callable=AsyncMock
            ) as mock_read,
        ):
            await monitor.check_for_updates(current_map)

        mock_read.assert_not_called()

    async def test_same_mtime_but_larger_size_triggers_read(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        projects_path.mkdir()

        session_file = tmp_path / "sess-1.jsonl"
        session_file.write_text('{"type":"summary"}\n')
        original_mtime = session_file.stat().st_mtime

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        tracked = TrackedSession(
            session_id="sess-1",
            file_path=str(session_file),
            last_byte_offset=0,
        )
        monitor.state.update_session(tracked)
        monitor._file_mtimes["sess-1"] = original_mtime

        # Append content without changing mtime (simulate sub-second write)
        with open(session_file, "a") as f:
            f.write(
                '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n'
            )
        os.utime(session_file, (original_mtime, original_mtime))

        current_map = {
            "@0": {
                "session_id": "sess-1",
                "cwd": str(tmp_path),
                "window_name": "proj",
                "transcript_path": str(session_file),
            },
        }
        with patch.object(
            monitor, "_read_new_lines", spec=True, new_callable=AsyncMock
        ) as mock_read:
            await monitor.check_for_updates(current_map)

        mock_read.assert_called_once()

    async def test_direct_path_reads_new_content(self, tmp_path) -> None:
        """Primary path reads new content from transcript_path."""
        session_file = tmp_path / "transcript.jsonl"
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n'
        session_file.write_text(line)

        monitor = SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "ms.json",
        )
        # Pre-track at offset 0 so it reads the content
        tracked = TrackedSession(
            session_id="sess-d",
            file_path=str(session_file),
            last_byte_offset=0,
        )
        monitor.state.update_session(tracked)

        current_map = {
            "@1": {
                "session_id": "sess-d",
                "cwd": "/proj",
                "window_name": "proj",
                "transcript_path": str(session_file),
            },
        }
        msgs = await monitor.check_for_updates(current_map)

        assert len(msgs) == 1
        assert msgs[0].session_id == "sess-d"
        assert "hello" in msgs[0].text


class TestScanProjects:
    def test_scan_projects_sync_reads_index(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproject"
        work_dir.mkdir()
        resolved_cwd = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproject"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-123.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved_cwd,
            "entries": [
                {
                    "sessionId": "sess-123",
                    "fullPath": str(session_file),
                    "projectPath": resolved_cwd,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        active_cwds = {resolved_cwd}
        result = monitor._scan_projects_sync(active_cwds)

        assert len(result) == 1
        assert result[0].session_id == "sess-123"

    def test_scan_projects_sync_picks_up_unindexed_jsonl(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproject"
        work_dir.mkdir()
        resolved_cwd = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproject"
        proj_dir.mkdir(parents=True)

        jsonl = proj_dir / "orphan-sess.jsonl"
        jsonl.write_text(json.dumps({"cwd": resolved_cwd}) + "\n")

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        active_cwds = {resolved_cwd}
        result = monitor._scan_projects_sync(active_cwds)

        assert len(result) == 1
        assert result[0].session_id == "orphan-sess"

    def test_scan_projects_sync_filters_by_active_cwds(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        resolved_other = str(other_dir.resolve())

        proj_dir = projects_path / "-tmp-other"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-456.jsonl"
        session_file.write_text('{"type":"summary"}\n')
        index = {
            "originalPath": resolved_other,
            "entries": [
                {
                    "sessionId": "sess-456",
                    "fullPath": str(session_file),
                    "projectPath": resolved_other,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        monitor = SessionMonitor(
            projects_path=projects_path,
            state_file=tmp_path / "ms.json",
        )
        active_cwds = {str((tmp_path / "myproject").resolve())}
        result = monitor._scan_projects_sync(active_cwds)

        assert len(result) == 0

    def test_scan_projects_sync_skips_missing_dir(self, tmp_path) -> None:
        monitor = SessionMonitor(
            projects_path=tmp_path / "nonexistent",
            state_file=tmp_path / "ms.json",
        )
        result = monitor._scan_projects_sync({"/tmp/something"})
        assert result == []
