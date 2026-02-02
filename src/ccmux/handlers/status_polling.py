"""Terminal status line polling for active sessions.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
"""

import asyncio
import logging

from telegram import Bot

from ..session import session_manager
from ..terminal_parser import is_interactive_ui, parse_status_line
from ..tmux_manager import tmux_manager
from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .message_queue import enqueue_status_update, get_message_queue

logger = logging.getLogger(__name__)

# Status polling interval
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)


async def update_status_message(bot: Bot, user_id: int, window_name: str) -> None:
    """Poll terminal and enqueue status update for user's active window.

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    w = await tmux_manager.find_window_by_name(window_name)
    if not w:
        # Window gone, enqueue clear
        await enqueue_status_update(bot, user_id, window_name, None)
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    interactive_window = get_interactive_window(user_id)
    should_check_new_ui = True

    if interactive_window == window_name:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await clear_interactive_msg(user_id, bot)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await clear_interactive_msg(user_id, bot)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    if should_check_new_ui and is_interactive_ui(pane_text):
        await handle_interactive_ui(bot, user_id, window_name)
        return

    # Normal status line check
    status_line = parse_status_line(pane_text)

    if status_line:
        await enqueue_status_update(bot, user_id, window_name, status_line)
    # If no status line, keep existing status message (don't clear on transient state)


async def status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all active users."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    while True:
        try:
            # Get all users with active sessions
            for user_id, wname in list(session_manager.active_sessions.items()):
                try:
                    # Skip terminal polling while content messages are pending
                    queue = get_message_queue(user_id)
                    if queue and not queue.empty():
                        continue
                    await update_status_message(bot, user_id, wname)
                except Exception as e:
                    logger.debug(f"Status update error for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
