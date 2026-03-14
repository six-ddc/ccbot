"""Unit tests for SessionMonitor JSONL reading and offset handling."""

import json

import pytest

from ccbot.runtimes import RUNTIME_CODEX
from ccbot.session import WindowState
from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import SessionMonitor


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
    async def test_resolve_active_sessions_for_codex(
        self, monitor, tmp_path, monkeypatch
    ):
        """Codex runtime reads active transcript files from session_map state."""
        from ccbot import session as session_module
        from ccbot import session_monitor as monitor_module

        transcript_file = (
            tmp_path
            / "2026"
            / "03"
            / "11"
            / "rollout-2026-03-11T18-27-22-019cdc6f-d3c8-7003-9730-bf3608dcaec9.jsonl"
        )
        transcript_file.parent.mkdir(parents=True)
        transcript_file.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "019cdc6f-d3c8-7003-9730-bf3608dcaec9",
                        "cwd": "/tmp/project",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(monitor_module.config, "runtime", RUNTIME_CODEX)
        monkeypatch.setattr(session_module.config, "runtime", RUNTIME_CODEX)
        monkeypatch.setattr(session_module.config, "codex_sessions_path", tmp_path)
        monkeypatch.setattr(
            session_module.session_manager,
            "window_states",
            {
                "@3": WindowState(
                    session_id="019cdc6f-d3c8-7003-9730-bf3608dcaec9",
                    cwd="/tmp/project",
                    runtime=RUNTIME_CODEX,
                    transcript_path=str(transcript_file),
                )
            },
        )

        sessions = await monitor._resolve_active_sessions(
            {"019cdc6f-d3c8-7003-9730-bf3608dcaec9"}
        )

        assert len(sessions) == 1
        assert sessions[0].session_id == "019cdc6f-d3c8-7003-9730-bf3608dcaec9"
        assert sessions[0].file_path == transcript_file
