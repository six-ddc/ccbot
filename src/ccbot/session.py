"""Claude Code session management — the core state hub.

Manages the key mappings:
  Window→Session (window_states): which Claude session_id a window holds.
  User→Thread→Window (thread_bindings): topic-to-window bindings (1 topic = 1 window).

Responsibilities:
  - Persist/load state to ~/.ccbot/state.json.
  - Sync window↔session bindings from session_map.json (written by hook).
  - Resolve window names to ClaudeSession objects (JSONL file reading).
  - Track per-user read offsets for unread-message detection.
  - Manage thread↔window bindings for Telegram topic routing.
  - Send keystrokes to tmux windows and retrieve message history.

Key class: SessionManager (singleton instantiated as `session_manager`).
Key methods for thread binding access:
  - resolve_window_for_thread: Get window name for a user's thread
  - iter_thread_bindings: Generator for iterating all (user_id, thread_id, window_name)
  - find_users_for_session: Find all users bound to a session_id
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import aiofiles

from .config import config
from .tmux_manager import tmux_manager
from .transcript_parser import TranscriptParser
from .utils import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class WindowState:
    """Persistent state for a tmux window.

    Attributes:
        session_id: Associated Claude session ID (empty if not yet detected)
        cwd: Working directory for direct file path construction
    """

    session_id: str = ""
    cwd: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowState":
        return cls(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
        )


@dataclass
class ClaudeSession:
    """Information about a Claude Code session."""

    session_id: str
    summary: str
    message_count: int
    file_path: str

    @property
    def short_summary(self) -> str:
        if len(self.summary) > 30:
            return self.summary[:27] + "..."
        return self.summary


@dataclass
class UnreadInfo:
    """Information about unread messages for a user's window."""

    has_unread: bool
    start_offset: int  # User's last read offset
    end_offset: int  # Current file size


@dataclass
class SessionManager:
    """Manages session state for Claude Code.

    window_states: window_name -> WindowState (session_id)
    user_window_offsets: user_id -> {window_name -> byte_offset}
    thread_bindings: user_id -> {thread_id -> window_name}
    """

    window_states: dict[str, WindowState] = field(default_factory=dict)
    user_window_offsets: dict[int, dict[str, int]] = field(default_factory=dict)
    thread_bindings: dict[int, dict[int, str]] = field(default_factory=dict)
    # group_chat_ids: "user_id:thread_id" -> chat_id (supports multiple groups per user)
    group_chat_ids: dict[str, int] = field(default_factory=dict)

    # Reverse index: (user_id, window_name) -> thread_id for O(1) inbound lookups
    _window_to_thread: dict[tuple[int, str], int] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        self._load_state()
        self._rebuild_reverse_index()

    def _rebuild_reverse_index(self) -> None:
        """Rebuild _window_to_thread from thread_bindings."""
        self._window_to_thread = {}
        for uid, bindings in self.thread_bindings.items():
            for tid, wname in bindings.items():
                self._window_to_thread[(uid, wname)] = tid

    def _save_state(self) -> None:
        state = {
            "window_states": {k: v.to_dict() for k, v in self.window_states.items()},
            "user_window_offsets": {
                str(uid): offsets for uid, offsets in self.user_window_offsets.items()
            },
            "thread_bindings": {
                str(uid): {str(tid): wname for tid, wname in bindings.items()}
                for uid, bindings in self.thread_bindings.items()
            },
            "group_chat_ids": self.group_chat_ids,
        }
        atomic_write_json(config.state_file, state)
        logger.debug("State saved to %s", config.state_file)

    def _load_state(self) -> None:
        """Load state synchronously during initialization."""
        if config.state_file.exists():
            try:
                state = json.loads(config.state_file.read_text())
                self.window_states = {
                    k: WindowState.from_dict(v)
                    for k, v in state.get("window_states", {}).items()
                }
                self.user_window_offsets = {
                    int(uid): offsets
                    for uid, offsets in state.get("user_window_offsets", {}).items()
                }
                self.thread_bindings = {
                    int(uid): {int(tid): wname for tid, wname in bindings.items()}
                    for uid, bindings in state.get("thread_bindings", {}).items()
                }
                self.group_chat_ids = state.get("group_chat_ids", {})
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to load state: {e}")
                self.window_states = {}
                self.user_window_offsets = {}
                self.thread_bindings = {}
                self.group_chat_ids = {}

    async def wait_for_session_map_entry(
        self, window_name: str, timeout: float = 5.0, interval: float = 0.5
    ) -> bool:
        """Poll session_map.json until an entry for window_name appears.

        Returns True if the entry was found within timeout, False otherwise.
        """
        logger.debug(
            "Waiting for session_map entry: window=%s, timeout=%.1f",
            window_name,
            timeout,
        )
        key = f"{config.tmux_session_name}:{window_name}"
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                if config.session_map_file.exists():
                    async with aiofiles.open(config.session_map_file, "r") as f:
                        content = await f.read()
                    session_map = json.loads(content)
                    info = session_map.get(key, {})
                    if info.get("session_id"):
                        # Found — load into window_states immediately
                        logger.debug(
                            "session_map entry found for window %s", window_name
                        )
                        await self.load_session_map()
                        return True
            except (json.JSONDecodeError, OSError):
                pass
            await asyncio.sleep(interval)
        logger.warning(
            "Timed out waiting for session_map entry: window=%s", window_name
        )
        return False

    async def load_session_map(self) -> None:
        """Read session_map.json and update window_states with new session associations.

        Keys in session_map are formatted as "tmux_session:window_name".
        Only entries matching our tmux_session_name are processed.
        Also cleans up window_states entries not in current session_map.
        """
        if not config.session_map_file.exists():
            return
        try:
            async with aiofiles.open(config.session_map_file, "r") as f:
                content = await f.read()
            session_map = json.loads(content)
        except (json.JSONDecodeError, OSError):
            return

        prefix = f"{config.tmux_session_name}:"
        valid_windows: set[str] = set()
        changed = False

        for key, info in session_map.items():
            # Only process entries for our tmux session
            if not key.startswith(prefix):
                continue
            window_name = key[len(prefix) :]
            valid_windows.add(window_name)
            new_sid = info.get("session_id", "")
            new_cwd = info.get("cwd", "")
            if not new_sid:
                continue
            state = self.get_window_state(window_name)
            if state.session_id != new_sid or state.cwd != new_cwd:
                logger.info(
                    f"Session map: window {window_name} updated "
                    f"sid={new_sid}, cwd={new_cwd}"
                )
                state.session_id = new_sid
                state.cwd = new_cwd
                changed = True

        # Clean up window_states entries not in current session_map
        stale_windows = [w for w in self.window_states if w and w not in valid_windows]
        for window_name in stale_windows:
            logger.info(f"Removing stale window_state: {window_name}")
            del self.window_states[window_name]
            changed = True

        if changed:
            self._save_state()

    # --- Window state management ---

    def get_window_state(self, window_name: str) -> WindowState:
        """Get or create window state."""
        if window_name not in self.window_states:
            self.window_states[window_name] = WindowState()
        return self.window_states[window_name]

    def clear_window_session(self, window_name: str) -> None:
        """Clear session association for a window (e.g., after /clear command)."""
        state = self.get_window_state(window_name)
        state.session_id = ""
        self._save_state()
        logger.info(f"Cleared session for window {window_name}")

    def _build_session_file_path(self, session_id: str, cwd: str) -> Path | None:
        """Build the direct file path for a session from session_id and cwd."""
        if not session_id or not cwd:
            return None
        # Encode cwd: /data/code/ccbot -> -data-code-ccbot
        encoded_cwd = cwd.replace("/", "-")
        return config.claude_projects_path / encoded_cwd / f"{session_id}.jsonl"

    async def _get_session_direct(
        self, session_id: str, cwd: str
    ) -> ClaudeSession | None:
        """Get a ClaudeSession directly from session_id and cwd (no scanning)."""
        file_path = self._build_session_file_path(session_id, cwd)

        # Fallback: glob search if direct path doesn't exist
        if not file_path or not file_path.exists():
            pattern = f"*/{session_id}.jsonl"
            matches = list(config.claude_projects_path.glob(pattern))
            if matches:
                file_path = matches[0]
                logger.debug(f"Found session via glob: {file_path}")
            else:
                return None

        # Single pass: read file once, extract summary + count messages
        summary = ""
        last_user_msg = ""
        message_count = 0
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    message_count += 1
                    try:
                        data = json.loads(line)
                        # Check for summary
                        if data.get("type") == "summary":
                            s = data.get("summary", "")
                            if s:
                                summary = s
                        # Track last user message as fallback
                        elif TranscriptParser.is_user_message(data):
                            parsed = TranscriptParser.parse_message(data)
                            if parsed and parsed.text.strip():
                                last_user_msg = parsed.text.strip()
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return None

        if not summary:
            summary = last_user_msg[:50] if last_user_msg else "Untitled"

        return ClaudeSession(
            session_id=session_id,
            summary=summary,
            message_count=message_count,
            file_path=str(file_path),
        )

    # --- Window → Session resolution ---

    async def resolve_session_for_window(
        self, window_name: str
    ) -> ClaudeSession | None:
        """Resolve a tmux window to the best matching Claude session.

        Uses persisted session_id + cwd to construct file path directly.
        Returns None if no session is associated with this window.
        """
        state = self.get_window_state(window_name)

        if not state.session_id or not state.cwd:
            return None

        session = await self._get_session_direct(state.session_id, state.cwd)
        if session:
            return session

        # File no longer exists, clear state
        logger.warning(
            f"Session file no longer exists for window {window_name} "
            f"(sid={state.session_id}, cwd={state.cwd})"
        )
        state.session_id = ""
        state.cwd = ""
        self._save_state()
        return None

    # --- User window offset management ---

    def get_user_window_offset(self, user_id: int, window_name: str) -> int | None:
        """Get the user's last read offset for a window.

        Returns None if no offset has been recorded (first time).
        """
        user_offsets = self.user_window_offsets.get(user_id)
        if user_offsets is None:
            return None
        return user_offsets.get(window_name)

    def update_user_window_offset(
        self, user_id: int, window_name: str, offset: int
    ) -> None:
        """Update the user's last read offset for a window."""
        if user_id not in self.user_window_offsets:
            self.user_window_offsets[user_id] = {}
        self.user_window_offsets[user_id][window_name] = offset
        self._save_state()

    async def get_unread_info(
        self, user_id: int, window_name: str
    ) -> UnreadInfo | None:
        """Get unread message info for a user's window.

        Returns UnreadInfo if there are potentially unread messages,
        None if the session/file cannot be resolved.
        """
        session = await self.resolve_session_for_window(window_name)
        if not session or not session.file_path:
            return None

        file_path = Path(session.file_path)
        if not file_path.exists():
            return None

        try:
            file_size = file_path.stat().st_size
        except OSError:
            return None

        user_offset = self.get_user_window_offset(user_id, window_name)

        # If user has no offset, they haven't viewed this window before
        # Initialize to current file size (no unread)
        if user_offset is None:
            return UnreadInfo(
                has_unread=False,
                start_offset=file_size,
                end_offset=file_size,
            )

        # Detect file truncation (e.g., after /clear)
        if user_offset > file_size:
            # Reset offset to 0, show all content as unread
            user_offset = 0

        has_unread = user_offset < file_size
        return UnreadInfo(
            has_unread=has_unread,
            start_offset=user_offset,
            end_offset=file_size,
        )

    # --- Thread binding management ---

    def bind_thread(self, user_id: int, thread_id: int, window_name: str) -> None:
        """Bind a Telegram topic thread to a tmux window."""
        if user_id not in self.thread_bindings:
            self.thread_bindings[user_id] = {}
        self.thread_bindings[user_id][thread_id] = window_name
        self._window_to_thread[(user_id, window_name)] = thread_id
        self._save_state()
        logger.info(
            f"Bound thread {thread_id} -> window {window_name} for user {user_id}"
        )

    def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
        """Remove a thread binding. Returns the previously bound window name, or None."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings or thread_id not in bindings:
            return None
        window_name = bindings.pop(thread_id)
        self._window_to_thread.pop((user_id, window_name), None)
        if not bindings:
            del self.thread_bindings[user_id]
        self._save_state()
        logger.info(
            f"Unbound thread {thread_id} (was {window_name}) for user {user_id}"
        )
        return window_name

    def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
        """Look up the window bound to a thread."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings:
            return None
        return bindings.get(thread_id)

    def get_thread_for_window(self, user_id: int, window_name: str) -> int | None:
        """Reverse lookup: get thread_id for a window (O(1) via reverse index)."""
        return self._window_to_thread.get((user_id, window_name))

    def get_all_thread_windows(self, user_id: int) -> dict[int, str]:
        """Get all thread bindings for a user."""
        return dict(self.thread_bindings.get(user_id, {}))

    def resolve_window_for_thread(
        self,
        user_id: int,
        thread_id: int | None,
    ) -> str | None:
        """Resolve the tmux window for a user's thread.

        Returns None if thread_id is None or the thread is not bound.
        """
        if thread_id is None:
            return None
        return self.get_window_for_thread(user_id, thread_id)

    def iter_thread_bindings(self) -> Iterator[tuple[int, int, str]]:
        """Iterate all thread bindings as (user_id, thread_id, window_name).

        Provides encapsulated access to thread_bindings without exposing
        the internal data structure directly.
        """
        for user_id, bindings in self.thread_bindings.items():
            for thread_id, window_name in bindings.items():
                yield user_id, thread_id, window_name

    async def find_users_for_session(
        self,
        session_id: str,
    ) -> list[tuple[int, str, int]]:
        """Find all users whose thread-bound window maps to the given session_id.

        Returns list of (user_id, window_name, thread_id) tuples.
        """
        result: list[tuple[int, str, int]] = []
        for user_id, thread_id, window_name in self.iter_thread_bindings():
            resolved = await self.resolve_session_for_window(window_name)
            if resolved and resolved.session_id == session_id:
                result.append((user_id, window_name, thread_id))
        return result

    # --- Group chat ID management ---

    def set_group_chat_id(self, user_id: int, thread_id: int, chat_id: int) -> None:
        """Store the group chat ID for a user's thread (for forum topic message routing).

        Uses composite key "user_id:thread_id" to support multiple groups per user.
        """
        key = f"{user_id}:{thread_id}"
        if self.group_chat_ids.get(key) != chat_id:
            self.group_chat_ids[key] = chat_id
            self._save_state()
            logger.info(
                f"Stored group chat_id {chat_id} for user {user_id}, thread {thread_id}"
            )

    def resolve_chat_id(self, user_id: int, thread_id: int | None = None) -> int:
        """Resolve the chat_id for sending messages.

        In forum topics (thread_id is set), returns the stored group chat_id
        for that specific thread (user_id:thread_id).
        Falls back to user_id for direct messages or if no group_id stored.
        """
        if thread_id is not None:
            key = f"{user_id}:{thread_id}"
            group_id = self.group_chat_ids.get(key)
            if group_id is not None:
                return group_id
        return user_id

    # --- Tmux helpers ---

    async def send_to_window(self, window_name: str, text: str) -> tuple[bool, str]:
        """Send text to a tmux window by name and record for matching."""
        logger.debug("send_to_window: window=%s, text_len=%d", window_name, len(text))
        window = await tmux_manager.find_window_by_name(window_name)
        if not window:
            return False, "Window not found (may have been closed)"
        success = await tmux_manager.send_keys(window.window_id, text)
        if success:
            return True, f"Sent to {window_name}"
        return False, "Failed to send keys"

    # --- Message history ---

    async def get_recent_messages(
        self,
        window_name: str,
        *,
        start_byte: int = 0,
        end_byte: int | None = None,
    ) -> tuple[list[dict], int]:
        """Get user/assistant messages for a window's session.

        Resolves window → session, then reads the JSONL.
        Supports byte range filtering via start_byte/end_byte.
        Returns (messages, total_count).
        """
        session = await self.resolve_session_for_window(window_name)
        if not session or not session.file_path:
            return [], 0

        file_path = Path(session.file_path)
        if not file_path.exists():
            return [], 0

        # Read JSONL entries (optionally filtered by byte range)
        entries: list[dict] = []
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                if start_byte > 0:
                    await f.seek(start_byte)

                while True:
                    # Check byte limit before reading
                    if end_byte is not None:
                        current_pos = await f.tell()
                        if current_pos >= end_byte:
                            break

                    line = await f.readline()
                    if not line:
                        break

                    data = TranscriptParser.parse_line(line)
                    if data:
                        entries.append(data)
        except OSError as e:
            logger.error(f"Error reading session file {file_path}: {e}")
            return [], 0

        parsed_entries, _ = TranscriptParser.parse_entries(entries)
        all_messages = [
            {
                "role": e.role,
                "text": e.text,
                "content_type": e.content_type,
                "timestamp": e.timestamp,
            }
            for e in parsed_entries
        ]

        return all_messages, len(all_messages)


session_manager = SessionManager()
