"""Tmux session/window management via libtmux.

Wraps libtmux to provide async-friendly operations on a single tmux session:
  - list_windows / find_window_by_name: discover Claude Code windows.
  - capture_pane: read terminal content (plain or with ANSI colors).
  - send_keys: forward user input or control keys to a window.
  - create_window / kill_window: lifecycle management.

All blocking libtmux calls are wrapped in asyncio.to_thread().

Key class: TmuxManager (singleton instantiated as `tmux_manager`).
"""

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import libtmux
from libtmux._internal.query_list import ObjectDoesNotExist
from libtmux.exc import LibTmuxException

from .config import config

logger = logging.getLogger(__name__)

_TmuxError = (
    LibTmuxException,
    ObjectDoesNotExist,
    OSError,
    subprocess.CalledProcessError,
)


@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str  # Current working directory
    pane_current_command: str = ""  # Process running in active pane


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

    def _reset_server(self) -> None:
        """Reset cached server connection (e.g. after tmux server restart)."""
        self._server = None

    def get_session(self) -> libtmux.Session | None:
        """Get the tmux session if it exists."""
        try:
            return self.server.sessions.get(session_name=self.session_name)
        except _TmuxError:
            self._reset_server()
            return None

    def get_or_create_session(self) -> libtmux.Session:
        """Get existing session or create a new one."""
        session = self.get_session()
        if session:
            return session

        # Create new session with main window named specifically
        session = self.server.new_session(
            session_name=self.session_name,
            start_directory=str(Path.home()),
        )
        # Rename the default window to the main window name
        if session.windows:
            session.windows[0].rename_window(config.tmux_main_window_name)
        return session

    async def list_windows(self) -> list[TmuxWindow]:
        """List all windows in the session with their working directories.

        Returns:
            List of TmuxWindow with window info and cwd
        """

        def _sync_list_windows() -> list[TmuxWindow]:
            windows = []
            session = self.get_session()

            if not session:
                return windows

            for window in session.windows:
                name = window.window_name or ""
                # Skip the main window (placeholder window)
                if name == config.tmux_main_window_name:
                    continue

                try:
                    # Get the active pane's current path and command
                    pane = window.active_pane
                    if pane:
                        cwd = pane.pane_current_path or ""
                        pane_cmd = pane.pane_current_command or ""
                    else:
                        cwd = ""
                        pane_cmd = ""

                    windows.append(
                        TmuxWindow(
                            window_id=window.window_id or "",
                            window_name=name,
                            cwd=cwd,
                            pane_current_command=pane_cmd,
                        )
                    )
                except _TmuxError as e:
                    logger.debug("Error getting window info: %s", e)

            return windows

        return await asyncio.to_thread(_sync_list_windows)

    async def find_window_by_name(self, window_name: str) -> TmuxWindow | None:
        """Find a window by its name.

        Args:
            window_name: The window name to match

        Returns:
            TmuxWindow if found, None otherwise
        """
        windows = await self.list_windows()
        for window in windows:
            if window.window_name == window_name:
                return window
        logger.debug("Window not found by name: %s", window_name)
        return None

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a window by its tmux window ID (e.g. '@0', '@12').

        Args:
            window_id: The tmux window ID to match

        Returns:
            TmuxWindow if found, None otherwise
        """
        windows = await self.list_windows()
        for window in windows:
            if window.window_id == window_id:
                return window
        logger.debug("Window not found by id: %s", window_id)
        return None

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture the visible text content of a window's active pane.

        Args:
            window_id: The window ID to capture
            with_ansi: If True, capture with ANSI color codes

        Returns:
            The captured text (stripped of trailing whitespace),
            or None on failure or empty content.
        """
        if with_ansi:
            return await self._capture_pane_ansi(window_id)

        return await self._capture_pane_plain(window_id)

    async def _capture_pane_ansi(self, window_id: str) -> str | None:
        """Capture pane with ANSI colors via tmux subprocess."""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "capture-pane",
                "-e",
                "-p",
                "-t",
                window_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                logger.warning(
                    "Failed to capture pane %s: %s",
                    window_id,
                    stderr.decode("utf-8", errors="replace"),
                )
                return None
            text = stdout.decode("utf-8", errors="replace").rstrip()
            return text if text else None
        except TimeoutError:
            logger.warning("Capture pane %s timed out", window_id)
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return None
        except OSError:
            logger.exception("Unexpected error capturing pane %s", window_id)
            return None

    async def _capture_pane_plain(self, window_id: str) -> str | None:
        """Capture pane as plain text via libtmux."""

        def _sync_capture() -> str | None:
            session = self.get_session()
            if not session:
                return None
            try:
                window = session.windows.get(window_id=window_id)
                if not window:
                    return None
                pane = window.active_pane
                if not pane:
                    return None
                lines = pane.capture_pane()
                text = "\n".join(lines) if isinstance(lines, list) else str(lines)
                text = text.rstrip()
                return text if text else None
            except _TmuxError as e:
                logger.warning("Failed to capture pane %s: %s", window_id, e)
                self._reset_server()
                return None

        return await asyncio.to_thread(_sync_capture)

    def _pane_send(
        self, window_id: str, chars: str, *, enter: bool, literal: bool
    ) -> bool:
        """Synchronous helper: send keys to the active pane of a window."""
        session = self.get_session()
        if not session:
            logger.warning("No tmux session found")
            return False
        try:
            window = session.windows.get(window_id=window_id)
            if not window:
                logger.warning("Window %s not found", window_id)
                return False
            pane = window.active_pane
            if not pane:
                logger.warning("No active pane in window %s", window_id)
                return False
            pane.send_keys(chars, enter=enter, literal=literal)
            return True
        except _TmuxError:
            logger.exception("Failed to send keys to window %s", window_id)
            return False

    async def _send_literal_then_enter(self, window_id: str, text: str) -> bool:
        """Send literal text followed by Enter with a delay.

        Claude Code's TUI sometimes interprets a rapid-fire Enter
        (arriving in the same input batch as the text) as a newline
        rather than submit.  A 500ms gap lets the TUI process the
        text before receiving Enter.

        Handles ``!`` command mode: sends ``!`` first so the TUI switches
        to bash mode, waits 1s, then sends the rest.
        """
        if text.startswith("!"):
            if not await asyncio.to_thread(
                self._pane_send, window_id, "!", enter=False, literal=True
            ):
                return False
            rest = text[1:]
            if rest:
                await asyncio.sleep(1.0)
                if not await asyncio.to_thread(
                    self._pane_send, window_id, rest, enter=False, literal=True
                ):
                    return False
        else:
            if not await asyncio.to_thread(
                self._pane_send, window_id, text, enter=False, literal=True
            ):
                return False
        await asyncio.sleep(0.5)
        return await asyncio.to_thread(
            self._pane_send, window_id, "", enter=True, literal=False
        )

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a specific window.

        Args:
            window_id: The window ID to send to
            text: Text to send
            enter: Whether to press enter after the text
            literal: If True, send text literally. If False, interpret special keys
                     like "Up", "Down", "Left", "Right", "Escape", "Enter".

        Returns:
            True if successful, False otherwise
        """
        if literal and enter:
            return await self._send_literal_then_enter(window_id, text)

        return await asyncio.to_thread(
            self._pane_send, window_id, text, enter=enter, literal=literal
        )

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window by its ID."""

        def _sync_kill() -> bool:
            session = self.get_session()
            if not session:
                return False
            try:
                window = session.windows.get(window_id=window_id)
                if not window:
                    return False
                window.kill()
                logger.info("Killed window %s", window_id)
                return True
            except _TmuxError:
                logger.exception("Failed to kill window %s", window_id)
                return False

        return await asyncio.to_thread(_sync_kill)

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_claude: bool = True,
        claude_args: str = "",
    ) -> tuple[bool, str, str, str]:
        """Create a new tmux window and optionally start Claude Code.

        Args:
            work_dir: Working directory for the new window
            window_name: Optional window name (defaults to directory name)
            start_claude: Whether to start claude command
            claude_args: Extra arguments appended to the claude command
                         (e.g. "--continue", "--resume <id>")

        Returns:
            Tuple of (success, message, window_name, window_id)
        """
        # Validate directory first
        path = Path(work_dir).expanduser().resolve()
        if not path.exists():
            return False, f"Directory does not exist: {work_dir}", "", ""
        if not path.is_dir():
            return False, f"Not a directory: {work_dir}", "", ""

        # Create window name, adding suffix if name already exists
        final_window_name = window_name if window_name else path.name

        # Check for existing window name
        base_name = final_window_name
        counter = 2
        while await self.find_window_by_name(final_window_name):
            final_window_name = f"{base_name}-{counter}"
            counter += 1

        # Create window in thread
        def _create_and_start() -> tuple[bool, str, str, str]:
            session = self.get_or_create_session()
            try:
                # Create new window
                window = session.new_window(
                    window_name=final_window_name,
                    start_directory=str(path),
                )

                new_window_id = window.window_id or ""

                # Start Claude Code if requested
                if start_claude:
                    pane = window.active_pane
                    if pane:
                        cmd = config.claude_command
                        if claude_args:
                            cmd = f"{cmd} {claude_args}"
                        pane.send_keys(cmd, enter=True)

                logger.info(
                    "Created window '%s' (id=%s) at %s",
                    final_window_name,
                    new_window_id,
                    path,
                )
                return (
                    True,
                    f"Created window '{final_window_name}' at {path}",
                    final_window_name,
                    new_window_id,
                )

            except _TmuxError as e:
                logger.exception("Failed to create window")
                return False, f"Failed to create window: {e}", "", ""

        return await asyncio.to_thread(_create_and_start)


# Global instance with default session name
tmux_manager = TmuxManager()
