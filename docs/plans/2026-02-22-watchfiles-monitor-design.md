# Design: Replace Polling with File Watching in SessionMonitor

**Date**: 2026-02-22
**Status**: Approved
**Scope**: `session_monitor.py`, `pyproject.toml`, `config.py`

## Problem

The `SessionMonitor` polls JSONL transcript files every 2 seconds (default `MONITOR_POLL_INTERVAL`). This introduces up to 2000ms latency between Claude writing a new message and the bot forwarding it to Telegram.

The root cause is time-based polling, not Python's runtime performance. A Rust rewrite would not meaningfully improve this — the bottleneck is the sleep interval, not CPU or memory.

## Goal

Reduce message latency from ~0–2000ms (average ~1000ms) to ~10–50ms by switching from time-based polling to OS-level file change notifications.

## Solution: watchfiles + Dual Task Architecture

### Dependency

Add `watchfiles>=0.21` to `pyproject.toml`. The `watchfiles` library is implemented in Rust and wraps:
- **macOS**: FSEvents (kernel-level notifications)
- **Linux**: inotify

It provides a native asyncio API (`watchfiles.awatch()`), making integration straightforward.

### Architecture

Replace the single `_monitor_loop()` with two concurrent asyncio tasks:

```
Before (single polling loop):
  while running:
      load_session_map()
      detect_changes()
      check_for_updates()    ← file stat + read
      sleep(2.0)             ← source of latency

After (dual task):
  Task 1: _file_watch_loop   ← event-driven, near-instant
      awatch(projects_path)
      on *.jsonl change → read new lines → callback

  Task 2: _housekeeping_loop ← time-based, low frequency
      every 10s:
          load_session_map()
          detect_and_cleanup_changes()
```

### Hot Path (latency-critical)

`_file_watch_loop` watches `claude_projects_path` recursively. On any `*.jsonl` change event:
1. Identify the session_id from the file path
2. Check if the session is in the active session map
3. Call `_read_new_lines()` (byte-offset incremental read — unchanged)
4. Parse via `TranscriptParser` (unchanged)
5. Invoke `_message_callback` (unchanged)

### Housekeeping Path (administrative)

`_housekeeping_loop` runs every 10 seconds (configurable via `MONITOR_POLL_INTERVAL`):
- `session_manager.load_session_map()`
- `_detect_and_cleanup_changes()` (session added/removed/replaced)
- `state.save_if_dirty()`

This is infrequent enough that 10s latency on session lifecycle events is acceptable (session creation is already mediated by the directory browser UI).

## Files to Modify

### `pyproject.toml`

```toml
dependencies = [
    ...
    "watchfiles>=0.21",
]
```

### `src/ccbot/session_monitor.py`

- `start()`: create two asyncio tasks instead of one
- `stop()`: cancel both tasks
- `_monitor_loop()` → split into `_file_watch_loop()` + `_housekeeping_loop()`
- `_file_watch_loop()`: use `watchfiles.awatch(self.projects_path)` to react to JSONL changes
- `_housekeeping_loop()`: retain session lifecycle management at lower frequency
- Internal `_process_session_file(session_info)` helper: extract per-file processing from `check_for_updates()` for use in both paths

### `src/ccbot/config.py`

- `monitor_poll_interval`: redefine as housekeeping interval (semantic change only; default raised from 2.0 to 10.0)
- Add comment clarifying it no longer affects message latency

## Unchanged Interfaces

The following are preserved without modification:
- `start()` / `stop()` / `set_message_callback()` — public API unchanged
- `_read_new_lines()` — byte-offset incremental reader
- `TranscriptParser` integration
- `MonitorState` persistence
- `check_for_updates()` can be retained as a method (called by housekeeping) or refactored into the helper

## Fallback / Degradation

If `watchfiles` fails to initialize (e.g., filesystem doesn't support events), catch the exception and fall back to the existing polling loop. Log a warning so the operator is aware.

## Testing

Existing tests remain valid — they test `_read_new_lines()`, `check_for_updates()`, and `TranscriptParser` independently of the monitoring loop. Add:
- Unit test: `_file_watch_loop` correctly calls `_process_session_file` on a JSONL change event (mock `watchfiles.awatch`)
- Integration test (optional): write to a temp JSONL file, assert callback fires within 200ms

## Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Message latency (avg) | ~1000ms | ~10–50ms |
| Message latency (worst) | ~2000ms | ~100ms |
| CPU usage | unchanged | unchanged (event-driven, no busy-wait) |
| Memory usage | unchanged | +minimal (watchfiles watcher thread) |
| session lifecycle latency | ~2000ms | ~10s (acceptable) |
