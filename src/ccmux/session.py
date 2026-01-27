"""Claude Code session management.

Manages active sessions and provides access to session information.

State is anchored to tmux window names (stable), not project paths (cwd, volatile).
Each window stores:
  - session_id: The associated Claude session ID (persisted)
  - last_msg_id: The last processed message ID (for polling new messages)
  - pending_text: Text sent but not yet matched to a session file
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import config
from .tmux_manager import TmuxWindow, tmux_manager
from .transcript_parser import TranscriptParser

logger = logging.getLogger(__name__)

# How many recent JSONL files to check when detecting new sessions
NEW_SESSION_CHECK_COUNT = 5


@dataclass
class WindowState:
    """Persistent state for a tmux window.

    Attributes:
        session_id: Associated Claude session ID (empty if not yet detected)
        last_msg_id: Last processed message ID for polling (API message ID)
        pending_text: Text sent but not yet matched to a session file
    """

    session_id: str = ""
    last_msg_id: str = ""
    pending_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "last_msg_id": self.last_msg_id,
            "pending_text": self.pending_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowState":
        return cls(
            session_id=data.get("session_id", ""),
            last_msg_id=data.get("last_msg_id", ""),
            pending_text=data.get("pending_text", ""),
        )


@dataclass
class ClaudeSession:
    """Information about a Claude Code session."""

    session_id: str
    summary: str
    project_path: str
    first_prompt: str
    message_count: int
    modified: str
    file_path: str

    @property
    def short_summary(self) -> str:
        if len(self.summary) > 30:
            return self.summary[:27] + "..."
        return self.summary

    @property
    def project_name(self) -> str:
        return Path(self.project_path).name


def _read_cwd_from_jsonl(file_path: str | Path) -> str:
    """Read the cwd field from the first entry that has one."""
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


def _read_summary_from_jsonl(file_path: str | Path) -> str:
    """Read the latest summary entry from a JSONL file."""
    summary = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "summary":
                        s = data.get("summary", "")
                        if s:
                            summary = s
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return summary


def _normalize_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, ValueError):
        return path


def _read_user_messages_from_jsonl(file_path: str | Path) -> list[str]:
    """Read all user message texts from a JSONL file, ordered chronologically."""
    messages: list[str] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                data = TranscriptParser.parse_line(line)
                if not data:
                    continue
                if TranscriptParser.is_user_message(data):
                    parsed = TranscriptParser.parse_message(data)
                    if parsed and parsed.text.strip():
                        messages.append(parsed.text.strip())
    except OSError as e:
        logger.debug(f"Error reading {file_path}: {e}")
    return messages


def _get_last_msg_id_from_jsonl(file_path: str | Path) -> str:
    """Get the last API message ID from a JSONL file."""
    last_id = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                data = TranscriptParser.parse_line(line)
                if not data:
                    continue
                message = data.get("message", {})
                if isinstance(message, dict):
                    msg_id = message.get("id", "")
                    if msg_id:
                        last_id = msg_id
    except OSError as e:
        logger.debug(f"Error reading {file_path}: {e}")
    return last_id


def _find_session_by_user_message(
    project_path: str, user_text: str
) -> tuple[str, str] | None:
    """Find a session that contains the given user message text.

    Searches recently modified JSONL files in the project directory.

    Returns:
        (session_id, file_path) if found, None otherwise
    """
    if not config.claude_projects_path.exists():
        return None

    norm_project = _normalize_path(project_path)

    # Collect all JSONL files with their mtime
    candidates: list[tuple[Path, float]] = []

    for project_dir in config.claude_projects_path.iterdir():
        if not project_dir.is_dir():
            continue

        # Check if this project dir matches the target project path
        index_file = project_dir / "sessions-index.json"
        original_path = ""
        if index_file.exists():
            try:
                original_path = json.loads(index_file.read_text()).get("originalPath", "")
            except (json.JSONDecodeError, OSError):
                pass

        try:
            for jsonl_file in project_dir.glob("*.jsonl"):
                # Determine project_path for this file
                file_project_path = original_path
                if not file_project_path:
                    file_project_path = _read_cwd_from_jsonl(jsonl_file)
                if not file_project_path:
                    dir_name = project_dir.name
                    if dir_name.startswith("-"):
                        file_project_path = dir_name.replace("-", "/")

                try:
                    norm_fp = _normalize_path(file_project_path)
                except (OSError, ValueError):
                    norm_fp = file_project_path

                if norm_fp == norm_project:
                    try:
                        mtime = jsonl_file.stat().st_mtime
                        candidates.append((jsonl_file, mtime))
                    except OSError:
                        pass
        except OSError:
            pass

    # Sort by mtime (newest first) and check recent files
    candidates.sort(key=lambda x: x[1], reverse=True)

    for jsonl_file, _ in candidates[:NEW_SESSION_CHECK_COUNT]:
        user_msgs = _read_user_messages_from_jsonl(jsonl_file)
        # Check if user_text matches any message (check last few for efficiency)
        for msg in user_msgs[-5:]:
            if msg.strip() == user_text.strip():
                session_id = jsonl_file.stem
                logger.info(
                    f"Found session {session_id} matching user text: {user_text[:50]}..."
                )
                return session_id, str(jsonl_file)

    return None


@dataclass
class SessionManager:
    """Manages active sessions for Claude Code.

    active_sessions: user_id -> tmux window_name
    window_states: window_name -> WindowState (session_id, last_msg_id, pending_text)
    """

    active_sessions: dict[int, str] = field(default_factory=dict)
    window_states: dict[str, WindowState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load_state()

    def _save_state(self) -> None:
        config.state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "active_sessions": {
                str(k): v for k, v in self.active_sessions.items()
            },
            "window_states": {
                k: v.to_dict() for k, v in self.window_states.items()
            },
        }
        config.state_file.write_text(json.dumps(state, indent=2))

    def _load_state(self) -> None:
        if config.state_file.exists():
            try:
                state = json.loads(config.state_file.read_text())
                self.active_sessions = {
                    int(k): v
                    for k, v in state.get("active_sessions", {}).items()
                }
                self.window_states = {
                    k: WindowState.from_dict(v)
                    for k, v in state.get("window_states", {}).items()
                }
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to load state: {e}")
                self.active_sessions = {}
                self.window_states = {}

    # --- Window state management ---

    def get_window_state(self, window_name: str) -> WindowState:
        """Get or create window state."""
        if window_name not in self.window_states:
            self.window_states[window_name] = WindowState()
        return self.window_states[window_name]

    def set_window_session(self, window_name: str, session_id: str, file_path: str = "") -> None:
        """Set the session ID for a window and update last_msg_id."""
        state = self.get_window_state(window_name)
        state.session_id = session_id
        state.pending_text = ""
        # Update last_msg_id from the session file
        if file_path:
            state.last_msg_id = _get_last_msg_id_from_jsonl(file_path)
        self._save_state()
        logger.info(f"Set window {window_name} -> session {session_id}, last_msg_id={state.last_msg_id}")

    def clear_window_session(self, window_name: str) -> None:
        """Clear session association for a window (e.g., after /clear command)."""
        state = self.get_window_state(window_name)
        state.session_id = ""
        state.last_msg_id = ""
        state.pending_text = ""
        self._save_state()
        logger.info(f"Cleared session for window {window_name}")

    def set_pending_text(self, window_name: str, text: str) -> None:
        """Set pending text for session detection after first message."""
        state = self.get_window_state(window_name)
        state.pending_text = text.strip()
        self._save_state()

    def update_last_msg_id(self, window_name: str, msg_id: str) -> None:
        """Update the last processed message ID for a window."""
        state = self.get_window_state(window_name)
        state.last_msg_id = msg_id
        self._save_state()

    def try_detect_session(self, window_name: str) -> ClaudeSession | None:
        """Try to detect and associate a session for a window with pending text.

        Called after sending a message when session_id is not yet set.
        Searches recent JSONL files for matching user message.

        Returns:
            ClaudeSession if found and associated, None otherwise
        """
        state = self.get_window_state(window_name)
        if state.session_id:
            # Already has a session
            return None
        if not state.pending_text:
            return None

        window = tmux_manager.find_window_by_name(window_name)
        if not window:
            return None

        result = _find_session_by_user_message(window.cwd, state.pending_text)
        if result:
            session_id, file_path = result
            self.set_window_session(window_name, session_id, file_path)
            # Return the session info
            return self._get_session_by_id(session_id)

        return None

    def _get_session_by_id(self, session_id: str) -> ClaudeSession | None:
        """Get a ClaudeSession by its ID."""
        for session in self.list_all_sessions():
            if session.session_id == session_id:
                return session
        return None

    # --- Message sending with session detection ---

    def record_sent_message(self, window_name: str, text: str) -> None:
        """Record a message sent to a window.

        If session is already associated, update last_msg_id later when we see
        the message in the JSONL file. If not associated, set as pending text
        for session detection.
        """
        state = self.get_window_state(window_name)
        if not state.session_id:
            # No session yet - set pending text for detection
            state.pending_text = text.strip()
            self._save_state()
        # Note: last_msg_id will be updated by monitor when message appears

    # --- Session index scanning ---

    def list_all_sessions(self) -> list[ClaudeSession]:
        """List all Claude Code sessions sorted by modification time (newest first)."""
        sessions: list[ClaudeSession] = []

        if not config.claude_projects_path.exists():
            return sessions

        for project_dir in config.claude_projects_path.iterdir():
            if not project_dir.is_dir():
                continue

            index_file = project_dir / "sessions-index.json"
            if not index_file.exists():
                continue

            try:
                index_data = json.loads(index_file.read_text())
                for entry in index_data.get("entries", []):
                    full_path = entry.get("fullPath", "")
                    jsonl_summary = _read_summary_from_jsonl(full_path) if full_path else ""
                    if not jsonl_summary and full_path:
                        msgs = _read_user_messages_from_jsonl(full_path)
                        jsonl_summary = msgs[-1][:50] if msgs else ""
                    summary = jsonl_summary or entry.get("summary", "Untitled")
                    session = ClaudeSession(
                        session_id=entry.get("sessionId", ""),
                        summary=summary,
                        project_path=entry.get("projectPath", ""),
                        first_prompt=entry.get("firstPrompt", ""),
                        message_count=entry.get("messageCount", 0),
                        modified=entry.get("modified", ""),
                        file_path=entry.get("fullPath", ""),
                    )
                    if session.session_id:
                        sessions.append(session)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"Error reading index {index_file}: {e}")

        # Also pick up JSONL files not yet in any index
        for project_dir in config.claude_projects_path.iterdir():
            if not project_dir.is_dir():
                continue
            indexed_ids = {s.session_id for s in sessions}
            index_file = project_dir / "sessions-index.json"
            original_path = ""
            if index_file.exists():
                try:
                    original_path = json.loads(index_file.read_text()).get("originalPath", "")
                except (json.JSONDecodeError, OSError):
                    pass

            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    sid = jsonl_file.stem
                    if sid in indexed_ids:
                        continue
                    project_path = original_path
                    if not project_path:
                        project_path = _read_cwd_from_jsonl(jsonl_file)
                    if not project_path:
                        dir_name = project_dir.name
                        if dir_name.startswith("-"):
                            project_path = dir_name.replace("-", "/")
                    user_msgs = _read_user_messages_from_jsonl(jsonl_file)
                    first_prompt = user_msgs[0] if user_msgs else ""
                    last_prompt = user_msgs[-1] if user_msgs else ""
                    summary = (
                        _read_summary_from_jsonl(jsonl_file)
                        or last_prompt[:50]
                        or "(new session)"
                    )
                    sessions.append(ClaudeSession(
                        session_id=sid,
                        summary=summary,
                        project_path=project_path,
                        first_prompt=first_prompt,
                        message_count=len(user_msgs),
                        modified="",
                        file_path=str(jsonl_file),
                    ))
            except OSError:
                pass

        sessions.sort(key=lambda s: s.modified, reverse=True)
        return sessions

    def list_active_sessions(self) -> list[ClaudeSession]:
        """List sessions that have an active tmux window (deduplicated by cwd)."""
        all_sessions = self.list_all_sessions()

        windows = tmux_manager.list_windows()
        window_cwds: set[str] = set()
        for w in windows:
            window_cwds.add(_normalize_path(w.cwd))

        seen_paths: set[str] = set()
        result: list[ClaudeSession] = []

        for session in all_sessions:
            normalized = _normalize_path(session.project_path)
            if normalized in window_cwds and normalized not in seen_paths:
                seen_paths.add(normalized)
                result.append(session)

        # Placeholder for tmux windows with no session record
        for w in windows:
            normalized = _normalize_path(w.cwd)
            if normalized not in seen_paths:
                seen_paths.add(normalized)
                result.append(ClaudeSession(
                    session_id=f"tmux-{w.window_id}",
                    summary="New session (no messages yet)",
                    project_path=normalized,
                    first_prompt="",
                    message_count=0,
                    modified="",
                    file_path="",
                ))

        return result

    # --- Window → Session resolution ---

    def resolve_session_for_window(self, window_name: str) -> ClaudeSession | None:
        """Resolve a tmux window to the best matching Claude session.

        Steps:
        1. Check if we have a persisted session_id for this window
        2. If yes, return that session (if it still exists)
        3. If no, try to detect session from pending_text
        4. Fallback: find by cwd match
        """
        state = self.get_window_state(window_name)

        # If we have a persisted session_id, use it
        if state.session_id:
            session = self._get_session_by_id(state.session_id)
            if session:
                return session
            # Session no longer exists, clear it
            logger.warning(f"Session {state.session_id} no longer exists for window {window_name}")
            state.session_id = ""
            self._save_state()

        # Try to detect session from pending text
        if state.pending_text:
            detected = self.try_detect_session(window_name)
            if detected:
                return detected

        # Fallback: find by cwd match
        window = tmux_manager.find_window_by_name(window_name)
        if not window:
            return None

        cwd = _normalize_path(window.cwd)

        # Find all sessions for this cwd
        candidates = [
            s for s in self.list_all_sessions()
            if _normalize_path(s.project_path) == cwd and s.file_path
        ]

        if not candidates:
            return None

        # Return the most recent one
        return candidates[0]

    # --- Active session (by window_name) ---

    def set_active_window(self, user_id: int, window_name: str) -> None:
        logger.info(f"set_active_window: user_id={user_id}, window_name={window_name}")
        self.active_sessions[user_id] = window_name
        self._save_state()

    def get_active_window_name(self, user_id: int) -> str | None:
        return self.active_sessions.get(user_id)

    def get_active_window(self, user_id: int) -> TmuxWindow | None:
        name = self.get_active_window_name(user_id)
        if not name:
            return None
        return tmux_manager.find_window_by_name(name)

    def get_active_cwd(self, user_id: int) -> str | None:
        window = self.get_active_window(user_id)
        if window:
            return _normalize_path(window.cwd)
        return None

    def clear_active_session(self, user_id: int) -> None:
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            self._save_state()

    # --- Tmux helpers ---

    def find_window_for_project(self, project_path: str) -> TmuxWindow | None:
        return tmux_manager.find_window_by_cwd(project_path)

    def has_active_terminal(self, project_path: str) -> bool:
        return self.find_window_for_project(project_path) is not None

    def send_to_window(self, window_name: str, text: str) -> tuple[bool, str]:
        """Send text to a tmux window by name and record for matching."""
        window = tmux_manager.find_window_by_name(window_name)
        if not window:
            return False, "Window not found (may have been closed)"
        success = tmux_manager.send_keys(window.window_id, text)
        if success:
            self.record_sent_message(window_name, text)
            return True, f"Sent to {window_name}"
        return False, "Failed to send keys"

    def send_to_active_session(self, user_id: int, text: str) -> tuple[bool, str]:
        name = self.get_active_window_name(user_id)
        if not name:
            return False, "No active session selected"
        return self.send_to_window(name, text)

    # --- Message history ---

    def get_recent_messages(
        self, window_name: str, count: int = 5, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Get recent user/assistant messages for a window's session.

        Resolves window → session, then reads the JSONL.
        Returns (messages, total_count).
        """
        session = self.resolve_session_for_window(window_name)
        if not session or not session.file_path:
            return [], 0

        file_path = Path(session.file_path)
        if not file_path.exists():
            return [], 0

        all_messages: list[dict] = []
        last_cmd_name: str | None = None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    data = TranscriptParser.parse_line(line)
                    if not data:
                        continue
                    parsed = TranscriptParser.parse_message(data)
                    if not parsed:
                        continue
                    # Track command name from invocation message
                    if parsed.message_type == "local_command_invoke":
                        last_cmd_name = parsed.tool_name
                        continue
                    # Local command stdout → render as bot reply
                    if parsed.message_type == "local_command":
                        cmd = parsed.tool_name or last_cmd_name or ""
                        prefix = f"❯ {cmd}\n  ⎿  " if cmd else "  ⎿  "
                        all_messages.append({
                            "role": "assistant",
                            "text": f"{prefix}{parsed.text}",
                        })
                        last_cmd_name = None
                        continue
                    last_cmd_name = None
                    if parsed.role in ("user", "assistant") and parsed.text.strip():
                        all_messages.append({
                            "role": parsed.role,
                            "text": parsed.text,
                        })
        except OSError as e:
            logger.error(f"Error reading session file {file_path}: {e}")
            return [], 0

        total = len(all_messages)
        if total == 0:
            return [], 0

        end_idx = total - offset
        start_idx = max(0, end_idx - count)
        if end_idx <= 0:
            return [], total

        return all_messages[start_idx:end_idx], total


session_manager = SessionManager()
