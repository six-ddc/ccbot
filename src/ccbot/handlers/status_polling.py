"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, done, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Detects Claude process exit (pane command reverts to shell)
  - Syncs tmux window renames to Telegram topic titles
  - Auto-closes stale topics after configurable timeout
  - Periodically probes topic existence via unpin_all_forum_topic_messages
    (silent no-op when no pins); cleans up deleted topics (kills tmux window
    + unbinds thread)

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - TOPIC_CHECK_INTERVAL: Topic existence probe frequency (60 seconds)
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
  - is_shell_prompt: Detect Claude exit (shell resumed in pane)
  - clear_dead_notification: Clear dead window notification tracking
  - Proactive recovery: sends recovery keyboard when a window dies
  - Auto-close: closes topics stuck in done/dead state
"""

import asyncio
import logging
import time
from pathlib import Path

from telegram import Bot
from telegram.error import BadRequest, TelegramError

from ..config import config
from ..session import session_manager
from ..terminal_parser import is_interactive_ui, parse_status_line
from ..tmux_manager import tmux_manager
from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .cleanup import clear_topic_state
from .message_queue import enqueue_status_update, get_message_queue
from .message_sender import rate_limit_send_message
from .recovery_callbacks import build_recovery_keyboard
from .topic_emoji import rename_topic, update_topic_emoji

# Top-level loop resilience: catch any error to keep polling alive
_LoopError = (TelegramError, OSError, RuntimeError, ValueError)

logger = logging.getLogger(__name__)

# Status polling interval
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)

# Topic existence probe interval
TOPIC_CHECK_INTERVAL = 60.0  # seconds

# Track which (user_id, thread_id, window_id) tuples have been notified about death
_dead_notified: set[tuple[int, int, str]] = set()

# Shell commands indicating Claude has exited and the shell prompt is back
SHELL_COMMANDS = frozenset({"bash", "zsh", "fish", "sh", "dash", "tcsh", "csh", "ksh"})

# Auto-close timers: (user_id, thread_id) -> (state, monotonic_time_entered)
_autoclose_timers: dict[tuple[int, int], tuple[str, float]] = {}


def is_shell_prompt(pane_current_command: str) -> bool:
    """Check if the pane is running a shell (Claude has exited)."""
    cmd = pane_current_command.strip().rsplit("/", 1)[-1]
    return cmd in SHELL_COMMANDS


def clear_autoclose_timer(user_id: int, thread_id: int) -> None:
    """Remove autoclose timer for a topic (called on cleanup)."""
    _autoclose_timers.pop((user_id, thread_id), None)


def reset_autoclose_state() -> None:
    """Reset all autoclose tracking (for testing)."""
    _autoclose_timers.clear()


def clear_dead_notification(user_id: int, thread_id: int) -> None:
    """Remove dead notification tracking for a topic (called on cleanup)."""
    _dead_notified.difference_update(
        {k for k in _dead_notified if k[0] == user_id and k[1] == thread_id}
    )


def reset_dead_notification_state() -> None:
    """Reset all dead notification tracking (for testing)."""
    _dead_notified.clear()


def _start_autoclose_timer(
    user_id: int, thread_id: int, state: str, now: float
) -> None:
    """Start or maintain an autoclose timer for a topic in done/dead state."""
    key = (user_id, thread_id)
    existing = _autoclose_timers.get(key)
    if existing is None or existing[0] != state:
        _autoclose_timers[key] = (state, now)


def _clear_autoclose_if_active(user_id: int, thread_id: int) -> None:
    """Clear autoclose timer when topic becomes active/idle (session alive)."""
    _autoclose_timers.pop((user_id, thread_id), None)


async def _check_autoclose_timers(bot: Bot) -> None:
    """Close topics whose done/dead timers have expired."""
    if not _autoclose_timers:
        return

    now = time.monotonic()
    expired: list[tuple[int, int]] = []

    for (user_id, thread_id), (state, entered_at) in _autoclose_timers.items():
        if state == "done":
            timeout = config.autoclose_done_minutes * 60
        elif state == "dead":
            timeout = config.autoclose_dead_minutes * 60
        else:
            continue

        if timeout <= 0:
            continue

        if now - entered_at >= timeout:
            expired.append((user_id, thread_id))

    for user_id, thread_id in expired:
        _autoclose_timers.pop((user_id, thread_id), None)
        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        try:
            await bot.close_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
            logger.info(
                "Auto-closed topic: chat=%d thread=%d (user=%d)",
                chat_id,
                thread_id,
                user_id,
            )
        except TelegramError as e:
            logger.debug("Failed to auto-close topic thread=%d: %s", thread_id, e)


async def update_status_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> None:
    """Poll terminal and enqueue status update for user's active window.

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        # Window gone, enqueue clear
        await enqueue_status_update(bot, user_id, window_id, None, thread_id=thread_id)
        return

    # Detect window rename → sync to Telegram topic title
    if thread_id is not None:
        stored_name = session_manager.get_display_name(window_id)
        if stored_name and w.window_name != stored_name:
            session_manager.set_display_name(window_id, w.window_name)
            chat_id = session_manager.resolve_chat_id(user_id, thread_id)
            await rename_topic(bot, chat_id, thread_id, w.window_name)
            logger.info(
                "Window renamed: %s -> %s (window_id=%s)",
                stored_name,
                w.window_name,
                window_id,
            )

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    interactive_window = get_interactive_window(user_id, thread_id)
    should_check_new_ui = True

    if interactive_window == window_id:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await clear_interactive_msg(user_id, bot, thread_id)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await clear_interactive_msg(user_id, bot, thread_id)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    if should_check_new_ui and is_interactive_ui(pane_text):
        await handle_interactive_ui(bot, user_id, window_id, thread_id)
        return

    # Normal status line check
    status_line = parse_status_line(pane_text)

    if status_line:
        await enqueue_status_update(
            bot,
            user_id,
            window_id,
            status_line,
            thread_id=thread_id,
        )
        # Update topic emoji to active (Claude is working)
        if thread_id is not None:
            chat_id = session_manager.resolve_chat_id(user_id, thread_id)
            display = session_manager.get_display_name(window_id)
            await update_topic_emoji(bot, chat_id, thread_id, "active", display)
            _clear_autoclose_if_active(user_id, thread_id)
    else:
        # No status line — check if Claude exited (shell prompt) or just idle
        if thread_id is not None:
            chat_id = session_manager.resolve_chat_id(user_id, thread_id)
            display = session_manager.get_display_name(window_id)
            if is_shell_prompt(w.pane_current_command):
                # Claude exited, shell is back
                await update_topic_emoji(bot, chat_id, thread_id, "done", display)
                _start_autoclose_timer(user_id, thread_id, "done", time.monotonic())
            else:
                # Claude still running, just no spinner
                await update_topic_emoji(bot, chat_id, thread_id, "idle", display)
                _clear_autoclose_if_active(user_id, thread_id)


async def status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all thread-bound windows."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    last_topic_check = 0.0
    while True:
        try:
            # Periodic topic existence probe
            now = time.monotonic()
            if now - last_topic_check >= TOPIC_CHECK_INTERVAL:
                last_topic_check = now
                for user_id, thread_id, wid in list(
                    session_manager.iter_thread_bindings()
                ):
                    try:
                        await bot.unpin_all_forum_topic_messages(
                            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
                            message_thread_id=thread_id,
                        )
                    except BadRequest as e:
                        if "Topic_id_invalid" in str(e):
                            # Topic deleted — kill window, unbind, and clean up state
                            w = await tmux_manager.find_window_by_id(wid)
                            if w:
                                await tmux_manager.kill_window(w.window_id)
                            session_manager.unbind_thread(user_id, thread_id)
                            await clear_topic_state(user_id, thread_id, bot)
                            logger.info(
                                "Topic deleted: killed window_id '%s' and "
                                "unbound thread %d for user %d",
                                wid,
                                thread_id,
                                user_id,
                            )
                        else:
                            logger.debug(
                                "Topic probe error for %s: %s",
                                wid,
                                e,
                            )
                    except TelegramError as e:
                        logger.debug(
                            "Topic probe error for %s: %s",
                            wid,
                            e,
                        )

            for user_id, thread_id, wid in list(session_manager.iter_thread_bindings()):
                try:
                    # Already notified about this dead window — skip tmux check
                    dead_key = (user_id, thread_id, wid)
                    if dead_key in _dead_notified:
                        continue

                    w = await tmux_manager.find_window_by_id(wid)
                    if not w:
                        # Mark topic as dead
                        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
                        display = session_manager.get_display_name(wid)
                        await update_topic_emoji(
                            bot, chat_id, thread_id, "dead", display
                        )
                        _start_autoclose_timer(
                            user_id, thread_id, "dead", time.monotonic()
                        )
                        # Send proactive recovery notification (once per death)
                        window_state = session_manager.get_window_state(wid)
                        cwd = window_state.cwd or ""
                        try:
                            dir_exists = cwd and await asyncio.to_thread(
                                Path(cwd).is_dir
                            )
                        except OSError:
                            dir_exists = False
                        if dir_exists:
                            keyboard = build_recovery_keyboard(wid)
                            text = (
                                f"\u26a0 Session `{display}` ended.\n"
                                f"\U0001f4c2 `{cwd}`\n\n"
                                "Tap a button or send a message to recover."
                            )
                        else:
                            text = f"\u26a0 Session `{display}` ended."
                            keyboard = None
                        sent = await rate_limit_send_message(
                            bot,
                            chat_id,
                            text,
                            message_thread_id=thread_id,
                            reply_markup=keyboard,
                        )
                        if sent:
                            _dead_notified.add(dead_key)
                        continue

                    queue = get_message_queue(user_id)
                    if queue and not queue.empty():
                        continue
                    await update_status_message(
                        bot,
                        user_id,
                        wid,
                        thread_id=thread_id,
                    )
                except (TelegramError, OSError) as e:
                    logger.debug(
                        "Status update error for user %s thread %s: %s",
                        user_id,
                        thread_id,
                        e,
                    )

            # Check auto-close timers at end of each poll cycle
            await _check_autoclose_timers(bot)

        except _LoopError:
            logger.exception("Status poll loop error")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
