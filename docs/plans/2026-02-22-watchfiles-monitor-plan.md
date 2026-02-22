# watchfiles SessionMonitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 2-second polling in `SessionMonitor` with OS-level file watching via `watchfiles`, reducing message latency from ~1000ms to ~10–50ms.

**Architecture:** Split the single `_monitor_loop()` into two concurrent asyncio tasks: `_file_watch_loop()` for instant JSONL change detection via `watchfiles.awatch()`, and `_housekeeping_loop()` for session lifecycle management every 10 seconds.

**Tech Stack:** `watchfiles>=0.21` (Rust-backed, wraps FSEvents on macOS / inotify on Linux), Python asyncio, existing `aiofiles` + `TranscriptParser` unchanged.

**Design doc:** `docs/plans/2026-02-22-watchfiles-monitor-design.md`

---

### Task 1: Add watchfiles dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add dependency**

In `pyproject.toml`, add `watchfiles>=0.21` to the `dependencies` list (after `aiofiles`):

```toml
dependencies = [
    "python-telegram-bot[rate-limiter]>=21.0",
    "python-dotenv>=1.0.0",
    "httpx>=0.27.0",
    "libtmux>=0.37.0",
    "Pillow>=10.0.0",
    "telegramify-markdown>=0.5.0",
    "aiofiles>=24.0.0",
    "watchfiles>=0.21",
]
```

**Step 2: Install and verify**

```bash
uv sync
python -c "import watchfiles; print(watchfiles.__version__)"
```

Expected: prints a version string like `0.24.0`

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add watchfiles dependency"
```

---

### Task 2: Update config.py housekeeping interval semantics

**Files:**
- Modify: `src/ccbot/config.py:82`

**Step 1: Update the config field**

Change line 82 from:
```python
self.monitor_poll_interval = float(os.getenv("MONITOR_POLL_INTERVAL", "2.0"))
```

To:
```python
# Housekeeping interval (session lifecycle management). No longer affects
# message latency — file changes are detected via OS events (watchfiles).
self.monitor_poll_interval = float(os.getenv("MONITOR_POLL_INTERVAL", "10.0"))
```

**Step 2: Run type check to verify no errors**

```bash
uv run pyright src/ccbot/config.py
```

Expected: `0 errors, 0 warnings`

**Step 3: Commit**

```bash
git add src/ccbot/config.py
git commit -m "chore: raise default housekeeping interval to 10s"
```

---

### Task 3: Extract _process_session_file helper

**Files:**
- Modify: `src/ccbot/session_monitor.py`
- Test: `tests/test_session_monitor.py` (create or extend)

**Context:** `check_for_updates()` contains the per-session processing logic. We need to extract it into a standalone `_process_session_file()` helper that both the file watch loop and housekeeping loop can call. This is the refactor that makes the new architecture testable.

**Step 1: Write failing test**

In `tests/test_session_monitor.py` (create if missing):

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from ccbot.session_monitor import SessionMonitor, NewMessage


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

    from ccbot.session_monitor import SessionInfo
    session_info = SessionInfo(session_id="test-session", file_path=jsonl)
    active_ids = {"test-session"}

    messages = await monitor._process_session_file(session_info, active_ids)
    assert len(messages) == 1
    assert messages[0].text == "hello"
    assert messages[0].session_id == "test-session"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_session_monitor.py::test_process_session_file_emits_message -v
```

Expected: FAIL with `AttributeError: 'SessionMonitor' object has no attribute '_process_session_file'`

**Step 3: Extract _process_session_file from check_for_updates**

In `session_monitor.py`, add this method (extract the inner loop body from `check_for_updates()`):

```python
async def _process_session_file(
    self, session_info: "SessionInfo", active_session_ids: set[str]
) -> list[NewMessage]:
    """Process a single session file, returning any new messages.

    Reads from last byte offset using the existing incremental reader.
    Called by both _file_watch_loop (on change event) and check_for_updates.
    """
    if session_info.session_id not in active_session_ids:
        return []

    new_messages = []
    try:
        tracked = self.state.get_session(session_info.session_id)

        if tracked is None:
            try:
                file_size = session_info.file_path.stat().st_size
                current_mtime = session_info.file_path.stat().st_mtime
            except OSError:
                file_size = 0
                current_mtime = 0.0
            tracked = TrackedSession(
                session_id=session_info.session_id,
                file_path=str(session_info.file_path),
                last_byte_offset=file_size,
            )
            self.state.update_session(tracked)
            self._file_mtimes[session_info.session_id] = current_mtime
            logger.info(f"Started tracking session: {session_info.session_id}")
            return []

        # Check mtime + file size (skip if unchanged — handles duplicate events)
        try:
            st = session_info.file_path.stat()
            current_mtime = st.st_mtime
            current_size = st.st_size
        except OSError:
            return []

        last_mtime = self._file_mtimes.get(session_info.session_id, 0.0)
        if current_mtime <= last_mtime and current_size <= tracked.last_byte_offset:
            return []

        new_entries = await self._read_new_lines(tracked, session_info.file_path)
        self._file_mtimes[session_info.session_id] = current_mtime

        carry = self._pending_tools.get(session_info.session_id, {})
        parsed_entries, remaining = TranscriptParser.parse_entries(
            new_entries, pending_tools=carry
        )
        if remaining:
            self._pending_tools[session_info.session_id] = remaining
        else:
            self._pending_tools.pop(session_info.session_id, None)

        for entry in parsed_entries:
            if not entry.text and not entry.image_data:
                continue
            if entry.role == "user" and not config.show_user_messages:
                continue
            new_messages.append(
                NewMessage(
                    session_id=session_info.session_id,
                    text=entry.text,
                    is_complete=True,
                    content_type=entry.content_type,
                    tool_use_id=entry.tool_use_id,
                    role=entry.role,
                    tool_name=entry.tool_name,
                    image_data=entry.image_data,
                )
            )

        self.state.update_session(tracked)

    except OSError as e:
        logger.debug(f"Error processing session {session_info.session_id}: {e}")

    return new_messages
```

Also add `TrackedSession` to the imports at the top of `session_monitor.py` if not already present (it comes from `.monitor_state`).

**Step 4: Simplify check_for_updates to use the new helper**

Replace the inner `for session_info in sessions:` loop body in `check_for_updates()` with:

```python
for session_info in sessions:
    msgs = await self._process_session_file(session_info, active_session_ids)
    new_messages.extend(msgs)
```

**Step 5: Run tests**

```bash
uv run pytest tests/test_session_monitor.py -v
uv run pyright src/ccbot/session_monitor.py
```

Expected: tests PASS, 0 type errors

**Step 6: Commit**

```bash
git add src/ccbot/session_monitor.py tests/test_session_monitor.py
git commit -m "refactor: extract _process_session_file helper from check_for_updates"
```

---

### Task 4: Implement _housekeeping_loop

**Files:**
- Modify: `src/ccbot/session_monitor.py`
- Test: `tests/test_session_monitor.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_housekeeping_loop_runs_cleanup(tmp_path):
    """_housekeeping_loop calls session lifecycle management at least once."""
    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._running = True

    cleanup_called = []

    async def fake_cleanup():
        cleanup_called.append(1)
        monitor._running = False  # stop after one iteration

    monitor._detect_and_cleanup_changes = fake_cleanup

    with patch("ccbot.session_monitor.session_manager") as mock_sm:
        mock_sm.load_session_map = AsyncMock()
        await monitor._housekeeping_loop()

    assert len(cleanup_called) == 1
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_session_monitor.py::test_housekeeping_loop_runs_cleanup -v
```

Expected: FAIL with `AttributeError: 'SessionMonitor' object has no attribute '_housekeeping_loop'`

**Step 3: Implement _housekeeping_loop**

Add to `session_monitor.py` (replace the relevant portion of `_monitor_loop`):

```python
async def _housekeeping_loop(self) -> None:
    """Background loop for session lifecycle management.

    Runs at low frequency (default 10s). Handles session_map changes,
    session creation/deletion cleanup, and state persistence.
    Does NOT handle file reading — that's done by _file_watch_loop.
    """
    from .session import session_manager  # avoid circular import

    logger.info(
        "Housekeeping loop started, interval=%ss", self.poll_interval
    )

    # Startup: clean up stale sessions and initialize session_map snapshot
    await self._cleanup_all_stale_sessions()
    self._last_session_map = await self._load_current_session_map()

    while self._running:
        try:
            await session_manager.load_session_map()
            await self._detect_and_cleanup_changes()
            self.state.save_if_dirty()
        except Exception as e:
            logger.error(f"Housekeeping loop error: {e}")
        await asyncio.sleep(self.poll_interval)

    logger.info("Housekeeping loop stopped")
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_session_monitor.py -v
uv run pyright src/ccbot/session_monitor.py
```

Expected: all PASS, 0 errors

**Step 5: Commit**

```bash
git add src/ccbot/session_monitor.py tests/test_session_monitor.py
git commit -m "feat: add _housekeeping_loop for session lifecycle management"
```

---

### Task 5: Implement _file_watch_loop

**Files:**
- Modify: `src/ccbot/session_monitor.py`
- Test: `tests/test_session_monitor.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_file_watch_loop_processes_jsonl_change(tmp_path):
    """_file_watch_loop calls _process_session_file when a .jsonl file changes."""
    monitor = SessionMonitor(projects_path=tmp_path)
    monitor._running = True
    monitor._last_session_map = {"@0": "abc-session"}

    processed = []

    async def fake_process(session_info, active_ids):
        processed.append(session_info.session_id)
        monitor._running = False  # stop after first event
        return []

    monitor._process_session_file = fake_process

    # Simulate watchfiles yielding one change event for a .jsonl file
    jsonl_path = tmp_path / "-some-project" / "abc-session.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    jsonl_path.touch()

    import watchfiles
    fake_changes = [(watchfiles.Change.modified, str(jsonl_path))]

    async def fake_awatch(*args, **kwargs):
        yield fake_changes

    with patch("ccbot.session_monitor.watchfiles.awatch", fake_awatch):
        with patch("ccbot.session_monitor.session_manager") as mock_sm:
            mock_sm.load_session_map = AsyncMock()
            await monitor._file_watch_loop()

    assert "abc-session" in processed
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_session_monitor.py::test_file_watch_loop_processes_jsonl_change -v
```

Expected: FAIL with `AttributeError: 'SessionMonitor' object has no attribute '_file_watch_loop'`

**Step 3: Add watchfiles import to session_monitor.py**

At the top of `session_monitor.py`, add:

```python
import watchfiles
```

**Step 4: Implement _file_watch_loop**

```python
async def _file_watch_loop(self) -> None:
    """Background loop for instant JSONL change detection.

    Uses OS-level file events (FSEvents on macOS, inotify on Linux) via
    watchfiles. Replaces the time-based polling for the hot path.
    """
    from .session import session_manager  # avoid circular import

    logger.info(
        "File watch loop started, watching %s", self.projects_path
    )

    if not self.projects_path.exists():
        logger.warning(
            "projects_path %s does not exist, file watch loop idle",
            self.projects_path,
        )
        # Wait until running is False (housekeeping will handle session map)
        while self._running:
            await asyncio.sleep(5)
        return

    try:
        async for changes in watchfiles.awatch(
            self.projects_path, stop_event=self._stop_event
        ):
            if not self._running:
                break

            # Reload session_map to get current active sessions
            current_map = self._last_session_map  # updated by housekeeping
            active_session_ids = set(current_map.values())

            for change_type, path_str in changes:
                path = Path(path_str)
                if path.suffix != ".jsonl":
                    continue

                # Derive session_id from filename stem
                session_id = path.stem
                if session_id not in active_session_ids:
                    continue

                session_info = SessionInfo(
                    session_id=session_id, file_path=path
                )
                new_messages = await self._process_session_file(
                    session_info, active_session_ids
                )

                for msg in new_messages:
                    preview = msg.text[:80] + ("..." if len(msg.text) > 80 else "")
                    logger.info(
                        "[complete] session=%s: %s", msg.session_id, preview
                    )
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except Exception as e:
                            logger.error(f"Message callback error: {e}")

                self.state.save_if_dirty()

    except Exception as e:
        if self._running:
            logger.error("File watch loop error: %s — falling back to no-op", e)
            # Housekeeping loop keeps running; file changes won't be detected
            # until bot restart. Log prominently.
            logger.warning(
                "File watching unavailable. Set MONITOR_POLL_INTERVAL to a "
                "lower value and restart to reduce latency."
            )

    logger.info("File watch loop stopped")
```

**Step 5: Add _stop_event to __init__**

In `SessionMonitor.__init__()`, add after `self._task`:

```python
self._task: asyncio.Task | None = None
self._watch_task: asyncio.Task | None = None
self._stop_event: asyncio.Event = asyncio.Event()
```

**Step 6: Run tests**

```bash
uv run pytest tests/test_session_monitor.py -v
uv run pyright src/ccbot/session_monitor.py
```

Expected: all PASS, 0 type errors

**Step 7: Commit**

```bash
git add src/ccbot/session_monitor.py tests/test_session_monitor.py
git commit -m "feat: add _file_watch_loop for instant JSONL change detection"
```

---

### Task 6: Update start() and stop() for dual-task architecture

**Files:**
- Modify: `src/ccbot/session_monitor.py`
- Test: `tests/test_session_monitor.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_start_creates_two_tasks(tmp_path):
    """start() creates both _file_watch_loop and _housekeeping_loop tasks."""
    monitor = SessionMonitor(projects_path=tmp_path)

    with patch.object(monitor, "_file_watch_loop", new=AsyncMock()) as fw, \
         patch.object(monitor, "_housekeeping_loop", new=AsyncMock()) as hk:
        monitor.start()
        await asyncio.sleep(0.05)  # yield to event loop
        assert monitor._task is not None        # housekeeping task
        assert monitor._watch_task is not None  # file watch task
        monitor.stop()
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_session_monitor.py::test_start_creates_two_tasks -v
```

Expected: FAIL

**Step 3: Update start() and stop()**

Replace existing `start()` and `stop()` methods:

```python
def start(self) -> None:
    if self._running:
        logger.warning("Monitor already running")
        return
    self._running = True
    self._stop_event.clear()
    self._task = asyncio.create_task(self._housekeeping_loop())
    self._watch_task = asyncio.create_task(self._file_watch_loop())

def stop(self) -> None:
    self._running = False
    self._stop_event.set()
    if self._task:
        self._task.cancel()
        self._task = None
    if self._watch_task:
        self._watch_task.cancel()
        self._watch_task = None
    self.state.save()
    logger.info("Session monitor stopped and state saved")
```

**Step 4: Remove _monitor_loop (or keep as dead code with deprecation comment)**

The original `_monitor_loop` can be deleted since `_housekeeping_loop` + `_file_watch_loop` replace it entirely.

**Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
uv run pyright src/ccbot/
uv run ruff check src/ tests/
```

Expected: all PASS, 0 errors, 0 lint issues

**Step 6: Commit**

```bash
git add src/ccbot/session_monitor.py tests/test_session_monitor.py
git commit -m "feat: replace polling loop with dual file-watch + housekeeping tasks"
```

---

### Task 7: End-to-end smoke test

**Files:**
- Test: `tests/test_session_monitor_integration.py` (create)

**Step 1: Write integration test**

```python
"""Integration test: verify file watch loop detects JSONL changes in <200ms."""
import asyncio
import time
from pathlib import Path
import pytest
from ccbot.session_monitor import SessionMonitor, NewMessage
from ccbot.monitor_state import TrackedSession


@pytest.mark.integration
@pytest.mark.asyncio
async def test_file_watch_detects_change_within_200ms(tmp_path):
    """Writing to a JSONL file triggers callback within 200ms."""
    # Set up a fake projects directory matching Claude's structure
    project_dir = tmp_path / "-test-project"
    project_dir.mkdir()
    session_id = "deadbeef-0000-0000-0000-000000000001"
    jsonl_file = project_dir / f"{session_id}.jsonl"
    jsonl_file.touch()

    monitor = SessionMonitor(projects_path=tmp_path)

    # Pre-register session as tracked (at offset 0)
    tracked = TrackedSession(
        session_id=session_id,
        file_path=str(jsonl_file),
        last_byte_offset=0,
    )
    monitor.state.update_session(tracked)
    monitor._last_session_map = {"@0": session_id}

    received: list[NewMessage] = []
    received_time: list[float] = []

    async def on_message(msg: NewMessage):
        received.append(msg)
        received_time.append(time.monotonic())

    monitor.set_message_callback(on_message)
    monitor.start()

    start = time.monotonic()
    await asyncio.sleep(0.1)  # let watcher initialize

    # Write a message to the JSONL file
    line = '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"world"}]},"isFinal":true}\n'
    jsonl_file.write_text(line)

    # Wait up to 2s for message
    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    monitor.stop()

    assert len(received) == 1, "Expected exactly one message"
    assert received[0].text == "world"
    latency = received_time[0] - start - 0.1
    assert latency < 0.2, f"Latency {latency*1000:.0f}ms exceeded 200ms threshold"
```

**Step 2: Run integration test**

```bash
uv run pytest tests/test_session_monitor_integration.py -v -m integration
```

Expected: PASS (latency < 200ms on local dev machine)

**Step 3: Commit**

```bash
git add tests/test_session_monitor_integration.py
git commit -m "test: add integration test for file watch latency"
```

---

### Task 8: Final verification

**Step 1: Run full suite**

```bash
uv run pytest tests/ -v
uv run pyright src/ccbot/
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: all green

**Step 2: Restart bot and verify manually**

```bash
./scripts/restart.sh
```

Send a message via Telegram and observe that Claude's response appears in ~50ms instead of ~1–2s.

**Step 3: Final commit**

```bash
git add -p  # review any remaining changes
git commit -m "feat: replace session monitor polling with watchfiles for instant latency"
```
