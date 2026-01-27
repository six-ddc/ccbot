"""Monitor state persistence for session tracking.

Persists the state of monitored sessions to avoid re-sending
notifications after restarts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrackedSession:
    """State for a tracked Claude Code session."""

    session_id: str
    file_path: str  # Path to .jsonl file
    last_mtime: float  # File modification time
    last_line_count: int  # Number of lines read
    last_message_uuid: str | None = None  # UUID of last processed message
    project_path: str = ""  # Working directory
    pending_streaming_uuid: str | None = None  # UUID of last streaming (incomplete) msg
    pending_streaming_mtime: float = 0.0  # mtime when pending streaming was set

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackedSession:
        """Create from dict."""
        return cls(
            session_id=data.get("session_id", ""),
            file_path=data.get("file_path", ""),
            last_mtime=data.get("last_mtime", 0.0),
            last_line_count=data.get("last_line_count", 0),
            last_message_uuid=data.get("last_message_uuid"),
            project_path=data.get("project_path", ""),
            pending_streaming_uuid=data.get("pending_streaming_uuid"),
            pending_streaming_mtime=data.get("pending_streaming_mtime", 0.0),
        )


@dataclass
class MonitorState:
    """Persistent state for the session monitor.

    Stores tracking information for all monitored sessions
    to prevent duplicate notifications after restarts.
    """

    state_file: Path
    tracked_sessions: dict[str, TrackedSession] = field(default_factory=dict)
    _dirty: bool = field(default=False, repr=False)

    def load(self) -> None:
        """Load state from file."""
        if not self.state_file.exists():
            logger.debug(f"State file does not exist: {self.state_file}")
            return

        try:
            data = json.loads(self.state_file.read_text())
            sessions = data.get("tracked_sessions", {})
            self.tracked_sessions = {
                k: TrackedSession.from_dict(v) for k, v in sessions.items()
            }
            logger.info(f"Loaded {len(self.tracked_sessions)} tracked sessions from state")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load state file: {e}")
            self.tracked_sessions = {}

    def save(self) -> None:
        """Save state to file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "tracked_sessions": {
                k: v.to_dict() for k, v in self.tracked_sessions.items()
            }
        }

        try:
            self.state_file.write_text(json.dumps(data, indent=2))
            self._dirty = False
            logger.debug(f"Saved {len(self.tracked_sessions)} tracked sessions to state")
        except OSError as e:
            logger.error(f"Failed to save state file: {e}")

    def get_session(self, session_id: str) -> TrackedSession | None:
        """Get tracked session by ID."""
        return self.tracked_sessions.get(session_id)

    def update_session(self, session: TrackedSession) -> None:
        """Update or add a tracked session."""
        self.tracked_sessions[session.session_id] = session
        self._dirty = True

    def remove_session(self, session_id: str) -> None:
        """Remove a tracked session."""
        if session_id in self.tracked_sessions:
            del self.tracked_sessions[session_id]
            self._dirty = True

    def save_if_dirty(self) -> None:
        """Save state only if it has been modified."""
        if self._dirty:
            self.save()

    def cleanup_stale_sessions(self) -> None:
        """Remove sessions for files that no longer exist."""
        stale = []
        for session_id, session in self.tracked_sessions.items():
            if not Path(session.file_path).exists():
                stale.append(session_id)

        for session_id in stale:
            logger.info(f"Removing stale session: {session_id}")
            del self.tracked_sessions[session_id]

        if stale:
            self._dirty = True
