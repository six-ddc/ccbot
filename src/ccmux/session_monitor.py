"""Session monitoring service for Claude Code sessions.

Polls Claude Code session files and detects new assistant messages.
Emits both intermediate (streaming) and complete messages to enable
real-time Telegram updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable

from .config import config
from .monitor_state import MonitorState, TrackedSession
from .tmux_manager import tmux_manager
from .transcript_parser import TranscriptParser

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about a Claude Code session."""

    session_id: str
    file_path: Path
    file_mtime: float
    project_path: str


@dataclass
class NewMessage:
    """A new assistant message detected by the monitor."""

    session_id: str
    project_path: str
    text: str
    uuid: str | None
    is_complete: bool  # True when stop_reason is set (final message)
    msg_id: str | None = None  # API message ID (same across streaming chunks)
    content_type: str = "text"  # "text" or "thinking"


class SessionMonitor:
    """Monitors Claude Code sessions for new assistant messages.

    Reads new JSONL lines immediately on mtime change (no stability wait),
    emitting both intermediate and complete assistant messages.
    """

    def __init__(
        self,
        projects_path: Path | None = None,
        poll_interval: float | None = None,
        state_file: Path | None = None,
    ):
        self.projects_path = projects_path or config.claude_projects_path
        self.poll_interval = poll_interval or config.monitor_poll_interval

        self.state = MonitorState(
            state_file=state_file or config.monitor_state_file
        )
        self.state.load()

        self._running = False
        self._task: asyncio.Task | None = None
        self._message_callback: Callable[[NewMessage], Awaitable[None]] | None = None

    def set_message_callback(
        self, callback: Callable[[NewMessage], Awaitable[None]]
    ) -> None:
        self._message_callback = callback

    def _get_active_cwds(self) -> set[str]:
        """Get normalized cwds of all active tmux windows."""
        cwds = set()
        for w in tmux_manager.list_windows():
            try:
                cwds.add(str(Path(w.cwd).resolve()))
            except (OSError, ValueError):
                cwds.add(w.cwd)
        return cwds

    def scan_projects(self) -> list[SessionInfo]:
        """Scan projects that have active tmux windows."""
        active_cwds = self._get_active_cwds()
        if not active_cwds:
            return []

        sessions = []

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
                        file_mtime = entry.get("fileMtime", 0)
                        project_path = entry.get("projectPath", original_path)

                        if not session_id or not full_path:
                            continue

                        try:
                            norm_pp = str(Path(project_path).resolve())
                        except (OSError, ValueError):
                            norm_pp = project_path
                        if norm_pp not in active_cwds:
                            continue

                        indexed_ids.add(session_id)
                        file_path = Path(full_path)
                        if file_path.exists():
                            sessions.append(SessionInfo(
                                session_id=session_id,
                                file_path=file_path,
                                file_mtime=file_mtime,
                                project_path=project_path,
                            ))

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"Error reading index {index_file}: {e}")

            # Pick up un-indexed .jsonl files
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session_id = jsonl_file.stem
                    if session_id in indexed_ids:
                        continue

                    # Determine project_path for this file
                    file_project_path = original_path
                    if not file_project_path:
                        file_project_path = self._read_cwd_from_jsonl(jsonl_file)
                    if not file_project_path:
                        dir_name = project_dir.name
                        if dir_name.startswith("-"):
                            file_project_path = dir_name.replace("-", "/")

                    try:
                        norm_fp = str(Path(file_project_path).resolve())
                    except (OSError, ValueError):
                        norm_fp = file_project_path

                    if norm_fp not in active_cwds:
                        continue

                    try:
                        file_mtime = jsonl_file.stat().st_mtime
                    except OSError:
                        continue
                    sessions.append(SessionInfo(
                        session_id=session_id,
                        file_path=jsonl_file,
                        file_mtime=file_mtime,
                        project_path=file_project_path,
                    ))
            except OSError as e:
                logger.debug(f"Error scanning jsonl files in {project_dir}: {e}")

        return sessions

    @staticmethod
    def _read_cwd_from_jsonl(file_path: Path) -> str:
        """Read the cwd field from the first JSONL entry that has one."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        cwd = data.get("cwd")
                        if cwd:
                            return cwd
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return ""

    def _read_new_lines(self, session: TrackedSession, file_path: Path) -> list[dict]:
        """Read new lines from a session file."""
        new_entries = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for _ in range(session.last_line_count):
                    f.readline()
                line_count = session.last_line_count
                for line in f:
                    line_count += 1
                    data = TranscriptParser.parse_line(line)
                    if data:
                        new_entries.append(data)
                session.last_line_count = line_count
        except OSError as e:
            logger.error(f"Error reading session file {file_path}: {e}")
        return new_entries

    async def check_for_updates(self) -> list[NewMessage]:
        """Check all sessions for new assistant messages.

        Reads immediately on mtime change. Emits both intermediate
        (stop_reason=null) and complete messages.
        """
        new_messages = []
        sessions = self.scan_projects()

        for session_info in sessions:
            try:
                actual_mtime = session_info.file_path.stat().st_mtime
                tracked = self.state.get_session(session_info.session_id)

                if tracked is None:
                    tracked = TrackedSession(
                        session_id=session_info.session_id,
                        file_path=str(session_info.file_path),
                        last_mtime=actual_mtime,
                        last_line_count=self._count_lines(session_info.file_path),
                        project_path=session_info.project_path,
                    )
                    self.state.update_session(tracked)
                    logger.info(f"Started tracking session: {session_info.session_id}")
                    continue

                if actual_mtime <= tracked.last_mtime:
                    continue

                # Read immediately — no stability wait
                new_entries = self._read_new_lines(tracked, session_info.file_path)

                if new_entries:
                    logger.debug(
                        f"Read {len(new_entries)} new entries for "
                        f"session {session_info.session_id}"
                    )

                # Claude Code JSONL writes each assistant entry as a
                # complete message (not streaming chunks), so every
                # assistant entry is treated as is_complete=True.
                best_per_msg_id: dict[str, dict] = {}
                local_cmd_entries: list[tuple[dict, str]] = []  # (entry, cmd_name)
                last_cmd_name: str = ""
                for entry in new_entries:
                    if TranscriptParser.is_assistant_message(entry):
                        last_cmd_name = ""
                        message = entry.get("message", {})
                        msg_id = message.get("id", "") if isinstance(message, dict) else ""
                        if msg_id:
                            best_per_msg_id[msg_id] = entry
                        else:
                            uuid = TranscriptParser.get_uuid(entry) or ""
                            best_per_msg_id[f"_uuid_{uuid}"] = entry
                    elif TranscriptParser.is_user_message(entry):
                        parsed = TranscriptParser.parse_message(entry)
                        if not parsed:
                            continue
                        if parsed.message_type == "local_command_invoke":
                            last_cmd_name = parsed.tool_name or ""
                        elif parsed.message_type == "local_command" and parsed.text.strip():
                            cmd = parsed.tool_name or last_cmd_name
                            local_cmd_entries.append((entry, cmd))
                            last_cmd_name = ""

                # Track the last msg_id we see for this session
                last_seen_msg_id: str | None = None

                for _, entry in best_per_msg_id.items():
                    message = entry.get("message", {})
                    msg_id = message.get("id") if isinstance(message, dict) else None

                    result = TranscriptParser.extract_assistant_content(entry)
                    if not result:
                        continue
                    text, content_type = result

                    msg_uuid = TranscriptParser.get_uuid(entry)
                    if tracked.last_message_uuid == msg_uuid:
                        continue

                    new_messages.append(NewMessage(
                        session_id=session_info.session_id,
                        project_path=session_info.project_path,
                        text=text,
                        uuid=msg_uuid,
                        is_complete=True,
                        msg_id=msg_id,
                        content_type=content_type,
                    ))
                    tracked.last_message_uuid = msg_uuid
                    if msg_id:
                        last_seen_msg_id = msg_id

                # Emit local command stdout as messages
                for entry, cmd_name in local_cmd_entries:
                    parsed = TranscriptParser.parse_message(entry)
                    if not parsed:
                        continue
                    msg_uuid = TranscriptParser.get_uuid(entry)
                    if tracked.last_message_uuid == msg_uuid:
                        continue
                    prefix = f"❯ {cmd_name}\n" if cmd_name else ""
                    new_messages.append(NewMessage(
                        session_id=session_info.session_id,
                        project_path=session_info.project_path,
                        text=f"{prefix}{parsed.text}",
                        uuid=msg_uuid,
                        is_complete=True,
                        content_type="text",
                    ))
                    tracked.last_message_uuid = msg_uuid

                tracked.last_mtime = actual_mtime
                tracked.project_path = session_info.project_path
                self.state.update_session(tracked)

                # Update last_msg_id in session manager for windows using this session
                if last_seen_msg_id:
                    self._update_window_last_msg_id(
                        session_info.session_id, last_seen_msg_id
                    )

            except OSError as e:
                logger.debug(f"Error processing session {session_info.session_id}: {e}")

        self.state.save_if_dirty()
        return new_messages

    def _update_window_last_msg_id(self, session_id: str, msg_id: str) -> None:
        """Update last_msg_id for any window using this session."""
        # Import here to avoid circular import
        from .session import session_manager

        for window_name, window_state in session_manager.window_states.items():
            if window_state.session_id == session_id:
                if window_state.last_msg_id != msg_id:
                    session_manager.update_last_msg_id(window_name, msg_id)

    def _count_lines(self, file_path: Path) -> int:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def _try_detect_pending_sessions(self) -> None:
        """Try to detect and associate sessions for windows with pending text.

        This is called during the monitor loop to detect new sessions
        after a user sends their first message.
        """
        # Import here to avoid circular import
        from .session import session_manager

        for window_name, window_state in session_manager.window_states.items():
            if window_state.pending_text and not window_state.session_id:
                detected = session_manager.try_detect_session(window_name)
                if detected:
                    logger.info(
                        f"Detected session {detected.session_id} for window {window_name}"
                    )

    async def _monitor_loop(self) -> None:
        logger.info(f"Session monitor started, polling every {self.poll_interval}s")

        while self._running:
            try:
                # Try to detect new sessions for windows with pending text
                self._try_detect_pending_sessions()

                new_messages = await self.check_for_updates()

                for msg in new_messages:
                    status = "complete" if msg.is_complete else "streaming"
                    logger.info(
                        f"[{status}] session={msg.session_id}: "
                        f"{msg.text[:80]}..."
                    )
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except Exception as e:
                            logger.error(f"Message callback error: {e}")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

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
