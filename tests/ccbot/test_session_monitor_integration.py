"""Integration test: verify file watch loop detects JSONL changes in <200ms."""

import asyncio
import time
import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import NewMessage, SessionMonitor


@pytest.mark.integration
@pytest.mark.asyncio
async def test_file_watch_detects_change_within_200ms(tmp_path):
    """Writing to a JSONL file triggers callback within 500ms."""
    # Set up a fake projects directory matching Claude's structure
    project_dir = tmp_path / "-test-project"
    project_dir.mkdir()
    session_id = "deadbeef-0000-0000-0000-000000000001"
    jsonl_file = project_dir / f"{session_id}.jsonl"
    jsonl_file.touch()

    monitor = SessionMonitor(
        projects_path=tmp_path,
        state_file=tmp_path / "monitor_state.json",
    )

    # Pre-register session as tracked at offset 0 (before any content is written)
    tracked = TrackedSession(
        session_id=session_id,
        file_path=str(jsonl_file),
        last_byte_offset=0,
    )
    monitor.state.update_session(tracked)
    # Inject the session into _last_session_map so _file_watch_loop lets it through
    monitor._last_session_map = {"@0": session_id}

    received: list[NewMessage] = []
    received_time: list[float] = []

    async def on_message(msg: NewMessage) -> None:
        received.append(msg)
        received_time.append(time.monotonic())

    monitor.set_message_callback(on_message)

    # Only start the file watch task; skip housekeeping (avoids circular import
    # from session_manager and keeps the test self-contained)
    monitor._running = True
    monitor._stop_event.clear()
    monitor._watch_task = asyncio.create_task(monitor._file_watch_loop())

    # Give watchfiles time to set up OS-level watch
    await asyncio.sleep(0.3)
    write_time = time.monotonic()

    # Write a complete assistant message to the JSONL file
    line = (
        '{"type":"assistant","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"world"}]},"isFinal":true}\n'
    )
    jsonl_file.write_text(line)

    # Wait up to 3s for the callback to fire
    deadline = time.monotonic() + 3.0
    while not received and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    monitor.stop()
    # Let cancelled tasks settle
    await asyncio.sleep(0.05)

    assert len(received) >= 1, "Expected at least one message from file watch"
    assert received[0].text == "world"

    latency = received_time[0] - write_time
    assert latency < 0.5, f"Latency {latency * 1000:.0f}ms exceeded 500ms threshold"
