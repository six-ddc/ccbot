"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, done, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Detects Claude process exit (pane command reverts to shell)
  - Syncs tmux window renames to Telegram topic titles
  - Auto-closes stale topics after configurable timeout
  - Auto-kills unbound windows (topic closed, window kept alive) after TTL
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
import contextlib
import logging
import time
from pathlib import Path

from telegram import Bot
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError

from ..config import config
from ..providers import get_provider
from ..session import session_manager
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

# Unbound window TTL: window_id -> monotonic_time_first_seen_unbound
_unbound_window_timers: dict[str, float] = {}

# Windows where we've observed at least one status line (spinner).
# Until a spinner is seen, the window is treated as "active" (starting up),
# not "idle", to avoid showing 💤 during Claude Code startup.
_has_seen_status: set[str] = set()

# Typing indicator throttle: (user_id, thread_id) -> monotonic time last sent.
# Telegram typing action expires after ~5s; we re-send every 4s.
_TYPING_INTERVAL = 4.0
_last_typing_sent: dict[tuple[int, int], float] = {}


def is_shell_prompt(pane_current_command: str) -> bool:
    """Check if the pane is running a shell (Claude has exited)."""
    cmd = pane_current_command.strip().rsplit("/", 1)[-1]
    return cmd in SHELL_COMMANDS


async def _send_typing_throttled(bot: Bot, user_id: int, thread_id: int | None) -> None:
    """Send typing indicator if enough time has elapsed since the last one."""
    if thread_id is None:
        return
    key = (user_id, thread_id)
    now = time.monotonic()
    if now - _last_typing_sent.get(key, 0.0) < _TYPING_INTERVAL:
        return
    _last_typing_sent[key] = now
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    with contextlib.suppress(TelegramError):
        await bot.send_chat_action(
            chat_id=chat_id,
            message_thread_id=thread_id,
            action=ChatAction.TYPING,
        )


def clear_autoclose_timer(user_id: int, thread_id: int) -> None:
    """Remove autoclose timer for a topic (called on cleanup)."""
    _autoclose_timers.pop((user_id, thread_id), None)


def reset_autoclose_state() -> None:
    """Reset all autoclose tracking (for testing)."""
    _autoclose_timers.clear()
    _unbound_window_timers.clear()


def clear_dead_notification(user_id: int, thread_id: int) -> None:
    """Remove dead notification tracking for a topic (called on cleanup)."""
    _dead_notified.difference_update(
        {k for k in _dead_notified if k[0] == user_id and k[1] == thread_id}
    )


def reset_dead_notification_state() -> None:
    """Reset all dead notification tracking (for testing)."""
    _dead_notified.clear()


def clear_typing_state(user_id: int, thread_id: int) -> None:
    """Clear typing indicator throttle for a topic (called on cleanup)."""
    _last_typing_sent.pop((user_id, thread_id), None)


def clear_seen_status(window_id: str) -> None:
    """Clear startup status tracking for a window (called on cleanup)."""
    _has_seen_status.discard(window_id)


def reset_seen_status_state() -> None:
    """Reset all startup status tracking (for testing)."""
    _has_seen_status.clear()


def reset_typing_state() -> None:
    """Reset all typing indicator tracking (for testing)."""
    _last_typing_sent.clear()


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


async def _check_unbound_window_ttl() -> None:
    """Kill unbound tmux windows whose TTL has expired.

    Unbound windows are live tmux windows not bound to any topic. They appear
    when a topic is closed (window kept alive for rebinding). After
    autoclose_done_minutes they are auto-killed.
    """
    timeout = config.autoclose_done_minutes * 60
    if timeout <= 0:
        return

    # Build set of currently bound window IDs
    bound_ids: set[str] = set()
    for _, _, wid in session_manager.iter_thread_bindings():
        bound_ids.add(wid)

    # Get all live tmux windows
    live_windows = await tmux_manager.list_windows()
    live_ids = {w.window_id for w in live_windows}

    # Remove timers for windows that got rebound or no longer exist
    stale_timer_keys = [
        wid for wid in _unbound_window_timers if wid in bound_ids or wid not in live_ids
    ]
    for wid in stale_timer_keys:
        del _unbound_window_timers[wid]

    # Track newly unbound windows
    now = time.monotonic()
    for w in live_windows:
        if w.window_id not in bound_ids:
            _unbound_window_timers.setdefault(w.window_id, now)

    # Kill expired unbound windows
    expired = [
        wid
        for wid, first_seen in _unbound_window_timers.items()
        if now - first_seen >= timeout
    ]
    for wid in expired:
        _unbound_window_timers.pop(wid, None)
        await tmux_manager.kill_window(wid)
        logger.info("Auto-killed unbound window %s (TTL expired)", wid)


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

    # Parse terminal status once and reuse the result
    status = get_provider().parse_terminal_status(pane_text)

    if interactive_window == window_id:
        # User is in interactive mode for THIS window
        if status is not None and status.is_interactive:
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
    if should_check_new_ui and status is not None and status.is_interactive:
        await handle_interactive_ui(bot, user_id, window_id, thread_id)
        return

    # Normal status line check — use display_label for formatted text
    status_line = status.display_label if status and not status.is_interactive else None

    # Suppress status message updates for muted/errors_only windows,
    # but only AFTER interactive UI detection, rename sync, and emoji updates above.
    notif_mode = session_manager.get_notification_mode(window_id)

    if status_line:
        _has_seen_status.add(window_id)
        await _send_typing_throttled(bot, user_id, thread_id)
        if notif_mode not in ("muted", "errors_only"):
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
                _last_typing_sent.pop((user_id, thread_id), None)
            elif window_id in _has_seen_status:
                # Was active before, now idle (spinner disappeared)
                await update_topic_emoji(bot, chat_id, thread_id, "idle", display)
                _clear_autoclose_if_active(user_id, thread_id)
                _last_typing_sent.pop((user_id, thread_id), None)
            else:
                # Never seen a spinner — still starting up, show as active
                await _send_typing_throttled(bot, user_id, thread_id)
                await update_topic_emoji(bot, chat_id, thread_id, "active", display)
                _clear_autoclose_if_active(user_id, thread_id)


async def _handle_dead_window_notification(
    bot: Bot, user_id: int, thread_id: int, wid: str
) -> None:
    """Send proactive recovery notification for a dead window (once per death)."""
    dead_key = (user_id, thread_id, wid)
    if dead_key in _dead_notified:
        return
    _has_seen_status.discard(wid)
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    display = session_manager.get_display_name(wid)
    await update_topic_emoji(bot, chat_id, thread_id, "dead", display)
    _start_autoclose_timer(user_id, thread_id, "dead", time.monotonic())

    window_state = session_manager.get_window_state(wid)
    cwd = window_state.cwd or ""
    try:
        dir_exists = bool(cwd) and await asyncio.to_thread(Path(cwd).is_dir)
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
                            await clear_topic_state(
                                user_id, thread_id, bot, window_id=wid
                            )
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
                    if (user_id, thread_id, wid) in _dead_notified:
                        continue

                    w = await tmux_manager.find_window_by_id(wid)
                    if not w:
                        await _handle_dead_window_notification(
                            bot, user_id, thread_id, wid
                        )
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

            # Check auto-close timers and unbound window TTL at end of each poll cycle
            await _check_autoclose_timers(bot)
            await _check_unbound_window_ttl()

        except _LoopError:
            logger.exception("Status poll loop error")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
