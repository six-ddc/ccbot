"""Simplified tmux manager for discovering and sending to Claude Code sessions.

Only provides functionality to:
1. Discover tmux windows and their working directories
2. Send keys to windows
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import libtmux

from .config import config

logger = logging.getLogger(__name__)


@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str  # Current working directory
    session_name: str


class TmuxManager:
    """Manages tmux windows for Claude Code sessions."""

    def __init__(self, session_name: str | None = None):
        """Initialize tmux manager.

        Args:
            session_name: Name of the tmux session to use (default from config)
        """
        self.session_name = session_name or config.tmux_session_name
        self._server: libtmux.Server | None = None

    @property
    def server(self) -> libtmux.Server:
        """Get or create tmux server connection."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def get_session(self) -> libtmux.Session | None:
        """Get the tmux session if it exists."""
        try:
            return self.server.sessions.get(session_name=self.session_name)
        except Exception:
            return None

    def get_or_create_session(self) -> libtmux.Session:
        """Get existing session or create a new one."""
        session = self.get_session()
        if session:
            return session

        # Create new session
        return self.server.new_session(
            session_name=self.session_name,
            start_directory=str(Path.home()),
        )

    def list_windows(self) -> list[TmuxWindow]:
        """List all windows in the session with their working directories.

        Returns:
            List of TmuxWindow with window info and cwd
        """
        windows = []
        session = self.get_session()

        if not session:
            return windows

        for window in session.windows:
            try:
                # Get the active pane's current path
                pane = window.active_pane
                if pane:
                    cwd = pane.pane_current_path or ""
                else:
                    cwd = ""

                windows.append(
                    TmuxWindow(
                        window_id=window.window_id or "",
                        window_name=window.window_name or "",
                        cwd=cwd,
                        session_name=self.session_name,
                    )
                )
            except Exception as e:
                logger.debug(f"Error getting window info: {e}")

        return windows

    def find_window_by_cwd(self, target_cwd: str) -> TmuxWindow | None:
        """Find a window by its working directory.

        Args:
            target_cwd: The working directory to match

        Returns:
            TmuxWindow if found, None otherwise
        """
        try:
            normalized_target = str(Path(target_cwd).resolve())
        except (OSError, ValueError):
            normalized_target = target_cwd

        for window in self.list_windows():
            try:
                normalized_cwd = str(Path(window.cwd).resolve())
            except (OSError, ValueError):
                normalized_cwd = window.cwd

            if normalized_cwd == normalized_target:
                return window

        return None

    def send_keys(self, window_id: str, text: str, enter: bool = True) -> bool:
        """Send keys to a specific window.

        Args:
            window_id: The window ID to send to
            text: Text to send
            enter: Whether to press enter after the text

        Returns:
            True if successful, False otherwise
        """
        session = self.get_session()
        if not session:
            logger.error("No tmux session found")
            return False

        try:
            window = session.windows.get(window_id=window_id)
            if not window:
                logger.error(f"Window {window_id} not found")
                return False

            pane = window.active_pane
            if not pane:
                logger.error(f"No active pane in window {window_id}")
                return False

            pane.send_keys(text, enter=enter)
            return True

        except Exception as e:
            logger.error(f"Failed to send keys to window {window_id}: {e}")
            return False

    def send_keys_by_cwd(self, target_cwd: str, text: str, enter: bool = True) -> bool:
        """Send keys to a window matched by working directory.

        Args:
            target_cwd: The working directory to match
            text: Text to send
            enter: Whether to press enter after the text

        Returns:
            True if successful, False otherwise
        """
        window = self.find_window_by_cwd(target_cwd)
        if not window:
            logger.warning(f"No window found for cwd: {target_cwd}")
            return False

        return self.send_keys(window.window_id, text, enter)

    def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_claude: bool = True,
    ) -> tuple[bool, str]:
        """Create a new tmux window and optionally start Claude Code.

        Args:
            work_dir: Working directory for the new window
            window_name: Optional window name (defaults to directory name)
            start_claude: Whether to start claude command

        Returns:
            Tuple of (success, message)
        """
        session = self.get_or_create_session()

        # Validate directory
        path = Path(work_dir).expanduser().resolve()
        if not path.exists():
            return False, f"Directory does not exist: {work_dir}"
        if not path.is_dir():
            return False, f"Not a directory: {work_dir}"

        # Check if window for this directory already exists
        existing = self.find_window_by_cwd(str(path))
        if existing:
            return False, f"Window already exists for this directory: {existing.window_name}"

        # Create window name from directory name if not provided
        if not window_name:
            window_name = path.name

        try:
            # Create new window
            window = session.new_window(
                window_name=window_name,
                start_directory=str(path),
            )

            # Start Claude Code if requested
            if start_claude:
                pane = window.active_pane
                if pane:
                    pane.send_keys("claude", enter=True)

            return True, f"Created window '{window_name}' at {path}"

        except Exception as e:
            logger.error(f"Failed to create window: {e}")
            return False, f"Failed to create window: {e}"


# Global instance with default session name
tmux_manager = TmuxManager()
