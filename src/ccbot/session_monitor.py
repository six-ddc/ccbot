"""Session monitoring service — watches JSONL files for new messages.

Runs an async polling loop that:
  1. Loads the current session_map to know which sessions to watch.
  2. Detects session_map changes (new/changed/deleted windows) and cleans up.
  3. Reads new JSONL lines from each session file using byte-offset tracking.
  4. Parses entries via TranscriptParser and emits NewMessage objects to a callback.

Optimizations: mtime cache skips unchanged files; byte offset avoids re-reading.

Key classes: SessionMonitor, NewMessage, SessionInfo.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

import aiofiles
from telegram.error import TelegramError

from .config import config
from .monitor_state import MonitorState, TrackedSession
from .session import parse_session_map
from .tmux_manager import tmux_manager
from .transcript_parser import TranscriptParser
from .utils import read_cwd_from_jsonl

_CallbackError = (OSError, RuntimeError, TelegramError)
# Top-level loop resilience: catch any error to keep monitoring alive
_LoopError = (OSError, RuntimeError, json.JSONDecodeError, ValueError, TelegramError)

logger = logging.getLogger(__name__)

_PathResolveError = (OSError, ValueError)
_SessionMapError = (json.JSONDecodeError, OSError)

_MSG_PREVIEW_LENGTH = 80


@dataclass
class SessionInfo:
    """Information about a Claude Code session."""

    session_id: str
    file_path: Path


@dataclass
class NewMessage:
    """A new message detected by the monitor."""

    session_id: str
    text: str
    is_complete: bool  # True when stop_reason is set (final message)
    content_type: str = "text"  # "text" or "thinking"
    tool_use_id: str | None = None
    role: str = "assistant"  # "user" or "assistant"
    tool_name: str | None = None  # For tool_use messages, the tool name


@dataclass
class NewWindowEvent:
    """A new tmux window detected via session_map changes."""

    window_id: str
    session_id: str
    window_name: str
    cwd: str


class SessionMonitor:
    """Monitors Claude Code sessions for new assistant messages.

    Uses simple async polling with aiofiles for non-blocking I/O.
    Emits both intermediate and complete assistant messages.
    """

    def __init__(
        self,
        projects_path: Path | None = None,
        poll_interval: float | None = None,
        state_file: Path | None = None,
    ):
        self.projects_path = (
            projects_path if projects_path is not None else config.claude_projects_path
        )
        self.poll_interval = (
            poll_interval if poll_interval is not None else config.monitor_poll_interval
        )

        self.state = MonitorState(state_file=state_file or config.monitor_state_file)
        self.state.load()

        self._running = False
        self._task: asyncio.Task | None = None
        self._message_callback: Callable[[NewMessage], Awaitable[None]] | None = None
        self._new_window_callback: (
            Callable[[NewWindowEvent], Awaitable[None]] | None
        ) = None
        # Per-session pending tool_use state carried across poll cycles
        self._pending_tools: dict[str, dict[str, Any]] = {}  # session_id -> pending
        # Track last known session_map for detecting changes
        # Keys may be window_id (@12) or window_name (old format) during transition
        self._last_session_map: dict[str, dict[str, str]] = {}  # window_key -> details
        # In-memory mtime cache for quick file change detection (not persisted)
        self._file_mtimes: dict[str, float] = {}  # session_id -> last_seen_mtime

    def set_message_callback(
        self, callback: Callable[[NewMessage], Awaitable[None]]
    ) -> None:
        self._message_callback = callback

    def set_new_window_callback(
        self, callback: Callable[[NewWindowEvent], Awaitable[None]]
    ) -> None:
        self._new_window_callback = callback

    async def _get_active_cwds(self) -> set[str]:
        """Get normalized cwds of all active tmux windows."""
        cwds = set()
        windows = await tmux_manager.list_windows()
        for w in windows:
            try:
                cwds.add(str(Path(w.cwd).resolve()))
            except _PathResolveError:
                cwds.add(w.cwd)
        return cwds

    def _scan_projects_sync(self, active_cwds: set[str]) -> list[SessionInfo]:
        """Scan filesystem for session files matching active cwds (sync, for to_thread)."""
        sessions: list[SessionInfo] = []

        if not self.projects_path.exists():
            return sessions

        for project_dir in self.projects_path.iterdir():
            if not project_dir.is_dir():
                continue

            index_file = project_dir / "sessions-index.json"
            original_path = ""
            indexed_ids: set[str] = set()

            if index_file.exists():
                try:
                    index_data = json.loads(index_file.read_text())
                    entries = index_data.get("entries", [])
                    original_path = index_data.get("originalPath", "")

                    for entry in entries:
                        session_id = entry.get("sessionId", "")
                        full_path = entry.get("fullPath", "")
                        project_path = entry.get("projectPath", original_path)

                        if not session_id or not full_path:
                            continue

                        try:
                            norm_pp = str(Path(project_path).resolve())
                        except _PathResolveError:
                            norm_pp = project_path
                        if norm_pp not in active_cwds:
                            continue

                        indexed_ids.add(session_id)
                        file_path = Path(full_path)
                        if file_path.exists():
                            sessions.append(
                                SessionInfo(
                                    session_id=session_id,
                                    file_path=file_path,
                                )
                            )

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Error reading index %s: %s", index_file, e)

            # Pick up un-indexed .jsonl files
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session_id = jsonl_file.stem
                    if session_id in indexed_ids:
                        continue

                    file_project_path = original_path
                    if not file_project_path:
                        file_project_path = read_cwd_from_jsonl(jsonl_file)
                    if not file_project_path:
                        dir_name = project_dir.name
                        if dir_name.startswith("-"):
                            file_project_path = dir_name.replace("-", "/")

                    try:
                        norm_fp = str(Path(file_project_path).resolve())
                    except _PathResolveError:
                        norm_fp = file_project_path

                    if norm_fp not in active_cwds:
                        continue

                    sessions.append(
                        SessionInfo(
                            session_id=session_id,
                            file_path=jsonl_file,
                        )
                    )
            except OSError as e:
                logger.debug("Error scanning jsonl files in %s: %s", project_dir, e)

        return sessions

    async def scan_projects(self) -> list[SessionInfo]:
        """Scan projects that have active tmux windows.

        Filesystem scanning runs in a thread to avoid blocking the event loop.
        """
        active_cwds = await self._get_active_cwds()
        if not active_cwds:
            return []
        return await asyncio.to_thread(self._scan_projects_sync, active_cwds)

    async def _read_new_lines(
        self, session: TrackedSession, file_path: Path
    ) -> list[dict]:
        """Read new lines from a session file using byte offset for efficiency.

        Detects file truncation (e.g. after /clear) and resets offset.
        """
        new_entries = []
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                # Get file size to detect truncation
                await f.seek(0, 2)  # Seek to end
                file_size = await f.tell()

                # Detect file truncation: if offset is beyond file size, reset
                if session.last_byte_offset > file_size:
                    logger.info(
                        "File truncated for session %s "
                        "(offset %d > size %d). Resetting.",
                        session.session_id,
                        session.last_byte_offset,
                        file_size,
                    )
                    session.last_byte_offset = 0

                # Seek to last read position for incremental reading
                await f.seek(session.last_byte_offset)

                # Read only new lines from the offset.
                # Track safe_offset: only advance past lines that parsed
                # successfully. A non-empty line that fails JSON parsing is
                # likely a partial write; stop and retry next cycle.
                safe_offset = session.last_byte_offset
                async for line in f:
                    data = TranscriptParser.parse_line(line)
                    if data:
                        new_entries.append(data)
                        safe_offset = await f.tell()
                    elif line.strip():
                        # Partial JSONL line — don't advance offset past it
                        logger.warning(
                            "Partial JSONL line in session %s, will retry next cycle",
                            session.session_id,
                        )
                        break
                    else:
                        # Empty line — safe to skip
                        safe_offset = await f.tell()

                session.last_byte_offset = safe_offset

        except OSError:
            logger.exception("Error reading session file %s", file_path)
        return new_entries

    async def _process_session_file(
        self, session_id: str, file_path: Path, new_messages: list[NewMessage]
    ) -> None:
        """Process a single session file for new messages.

        Handles tracking initialization, mtime checking, incremental reading,
        and parsing. Appends any new messages to the provided list.
        """
        tracked = self.state.get_session(session_id)

        if tracked is None:
            # For new sessions, initialize offset to end of file
            # to avoid re-processing old messages
            try:
                st = file_path.stat()
                file_size, current_mtime = st.st_size, st.st_mtime
            except OSError:
                file_size = 0
                current_mtime = 0.0
            tracked = TrackedSession(
                session_id=session_id,
                file_path=str(file_path),
                last_byte_offset=file_size,
            )
            self.state.update_session(tracked)
            self._file_mtimes[session_id] = current_mtime
            logger.debug("Started tracking session: %s", session_id)
            return

        # Check mtime to see if file has changed
        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            return

        last_mtime = self._file_mtimes.get(session_id, 0.0)
        if current_mtime <= last_mtime:
            return

        # File changed, read new content from last offset
        new_entries = await self._read_new_lines(tracked, file_path)
        self._file_mtimes[session_id] = current_mtime

        if new_entries:
            logger.debug(
                "Read %d new entries for session %s",
                len(new_entries),
                session_id,
            )

        # Parse new entries using the shared logic, carrying over pending tools
        carry = self._pending_tools.get(session_id, {})
        parsed_entries, remaining = TranscriptParser.parse_entries(
            new_entries,
            pending_tools=carry,
        )
        if remaining:
            self._pending_tools[session_id] = remaining
        else:
            self._pending_tools.pop(session_id, None)

        for entry in parsed_entries:
            if not entry.text:
                continue
            new_messages.append(
                NewMessage(
                    session_id=session_id,
                    text=entry.text,
                    is_complete=True,
                    content_type=entry.content_type,
                    tool_use_id=entry.tool_use_id,
                    role=entry.role,
                    tool_name=entry.tool_name,
                )
            )

        self.state.update_session(tracked)

    async def check_for_updates(
        self, current_map: dict[str, dict[str, str]]
    ) -> list[NewMessage]:
        """Check all sessions for new assistant messages.

        Reads from last byte offset. Emits both intermediate
        (stop_reason=null) and complete messages.

        Uses two paths:
        1. Primary: entries with transcript_path are read directly (no scanning).
        2. Fallback: entries without transcript_path use scan_projects() + session_id match.

        Args:
            current_map: Window key -> details from session_map
        """
        new_messages: list[NewMessage] = []

        # Separate entries with direct transcript_path from those needing scan
        direct_sessions: list[tuple[str, Path]] = []
        fallback_session_ids: set[str] = set()

        for details in current_map.values():
            session_id = details["session_id"]
            transcript_path = details.get("transcript_path", "")
            if transcript_path:
                path = Path(transcript_path)
                if path.exists():
                    direct_sessions.append((session_id, path))
                    continue
            fallback_session_ids.add(session_id)

        # Primary path: read directly from transcript_path
        for session_id, file_path in direct_sessions:
            try:
                await self._process_session_file(session_id, file_path, new_messages)
            except OSError as e:
                logger.debug("Error processing session %s: %s", session_id, e)

        # Fallback path: scan projects for sessions without transcript_path
        if fallback_session_ids:
            sessions = await self.scan_projects()
            for session_info in sessions:
                if session_info.session_id not in fallback_session_ids:
                    continue
                try:
                    await self._process_session_file(
                        session_info.session_id, session_info.file_path, new_messages
                    )
                except OSError as e:
                    logger.debug(
                        "Error processing session %s: %s", session_info.session_id, e
                    )

        self.state.save_if_dirty()
        return new_messages

    async def _load_current_session_map(self) -> dict[str, dict[str, str]]:
        """Load current session_map and return window_key -> details mapping.

        Keys in session_map are formatted as "tmux_session:window_id"
        (e.g. "ccbot:@12"). Old-format keys ("ccbot:window_name") are also
        accepted so that sessions running before a code upgrade continue
        to be monitored until the hook re-fires with new format.
        Only entries matching our tmux_session_name are processed.

        Returns {window_key: {"session_id": ..., "cwd": ..., "window_name": ...}}.
        """
        if config.session_map_file.exists():
            try:
                async with aiofiles.open(config.session_map_file, "r") as f:
                    content = await f.read()
                raw = json.loads(content)
                prefix = f"{config.tmux_session_name}:"
                return parse_session_map(raw, prefix)
            except _SessionMapError:
                pass
        return {}

    async def _cleanup_all_stale_sessions(self) -> None:
        """Clean up all tracked sessions not in current session_map (used on startup)."""
        current_map = await self._load_current_session_map()
        active_session_ids = {v["session_id"] for v in current_map.values()}

        stale_sessions = []
        for session_id in self.state.tracked_sessions:
            if session_id not in active_session_ids:
                stale_sessions.append(session_id)

        if stale_sessions:
            logger.info(
                "[Startup cleanup] Removing %d stale sessions", len(stale_sessions)
            )
            for session_id in stale_sessions:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
                self._pending_tools.pop(session_id, None)
            self.state.save_if_dirty()

    async def _detect_and_cleanup_changes(self) -> dict[str, dict[str, str]]:
        """Detect session_map changes, cleanup replaced/removed sessions, fire new window events.

        Returns current session_map for further processing.
        """
        current_map = await self._load_current_session_map()

        sessions_to_remove: set[str] = set()

        # Check for window session changes (window exists in both, but session_id changed)
        for window_id, old_details in self._last_session_map.items():
            new_details = current_map.get(window_id)
            if new_details and new_details["session_id"] != old_details["session_id"]:
                logger.info(
                    "Window '%s' session changed: %s -> %s",
                    window_id,
                    old_details["session_id"],
                    new_details["session_id"],
                )
                sessions_to_remove.add(old_details["session_id"])

        # Check for deleted windows (window in old map but not in current)
        old_windows = set(self._last_session_map.keys())
        current_windows = set(current_map.keys())
        deleted_windows = old_windows - current_windows

        for window_id in deleted_windows:
            old_sid = self._last_session_map[window_id]["session_id"]
            logger.info(
                "Window '%s' deleted, removing session %s",
                window_id,
                old_sid,
            )
            sessions_to_remove.add(old_sid)

        # Perform cleanup
        if sessions_to_remove:
            for session_id in sessions_to_remove:
                self.state.remove_session(session_id)
                self._file_mtimes.pop(session_id, None)
                self._pending_tools.pop(session_id, None)
            self.state.save_if_dirty()

        # Detect new windows and fire callback
        new_windows = current_windows - old_windows
        if new_windows and self._new_window_callback:
            for window_id in new_windows:
                details = current_map[window_id]
                event = NewWindowEvent(
                    window_id=window_id,
                    session_id=details["session_id"],
                    window_name=details.get("window_name", ""),
                    cwd=details.get("cwd", ""),
                )
                try:
                    await self._new_window_callback(event)
                except _CallbackError:
                    logger.exception("New window callback error for %s", window_id)

        # Update last known map
        self._last_session_map = current_map

        return current_map

    async def _monitor_loop(self) -> None:
        """Background loop for checking session updates.

        Uses simple async polling with aiofiles for non-blocking I/O.
        """
        logger.info("Session monitor started, polling every %ss", self.poll_interval)

        # Deferred import to avoid circular dependency (cached once)
        from .session import session_manager

        # Clean up all stale sessions on startup
        await self._cleanup_all_stale_sessions()
        # Initialize last known session_map
        self._last_session_map = await self._load_current_session_map()

        while self._running:
            try:
                # Load hook-based session map updates
                await session_manager.load_session_map()

                # Detect session_map changes and cleanup replaced/removed sessions
                current_map = await self._detect_and_cleanup_changes()

                # Detect unbound tmux windows (no Claude Code yet)
                all_windows = await tmux_manager.list_windows()
                live_window_ids = {w.window_id for w in all_windows}
                session_manager.prune_session_map(live_window_ids)
                known_window_ids = set(current_map.keys())
                for window in all_windows:
                    if window.window_id in known_window_ids:
                        continue
                    already_bound = any(
                        wid == window.window_id
                        for _, _, wid in session_manager.iter_thread_bindings()
                    )
                    if not already_bound and self._new_window_callback:
                        event = NewWindowEvent(
                            window_id=window.window_id,
                            session_id="",
                            window_name=window.window_name,
                            cwd=window.cwd,
                        )
                        try:
                            await self._new_window_callback(event)
                        except _CallbackError:
                            logger.exception(
                                "New window callback error for %s",
                                window.window_id,
                            )

                # Check for new messages (all I/O is async)
                new_messages = await self.check_for_updates(current_map)

                for msg in new_messages:
                    status = "complete" if msg.is_complete else "streaming"
                    preview = msg.text[:_MSG_PREVIEW_LENGTH] + (
                        "..." if len(msg.text) > _MSG_PREVIEW_LENGTH else ""
                    )
                    logger.debug("[%s] session=%s: %s", status, msg.session_id, preview)
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except _CallbackError:
                            logger.exception(
                                "Message callback error for session=%s",
                                msg.session_id,
                            )

            except _LoopError:
                logger.exception("Monitor loop error")

            await asyncio.sleep(self.poll_interval)

        logger.info("Session monitor stopped")

    def start(self) -> None:
        if self._running:
            logger.warning("Monitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.state.save()
        logger.info("Session monitor stopped and state saved")
