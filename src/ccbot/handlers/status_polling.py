"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Periodically probes topic existence via unpin_all_forum_topic_messages
    (silent no-op when no pins); cleans up deleted topics (kills tmux window
    + unbinds thread)
  - Adaptive throttle for timer-based status lines to avoid Telegram rate limits

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - TOPIC_CHECK_INTERVAL: Topic existence probe frequency (60 seconds)
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
  - _should_send_status: Adaptive throttle for timer status updates
"""

import asyncio
import logging
import re
import time

from telegram import Bot
from telegram.error import BadRequest

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

logger = logging.getLogger(__name__)

# Status polling interval — kept at 1s for fast interactive UI detection.
# Timer-based status updates are throttled adaptively by _should_send_status().
STATUS_POLL_INTERVAL = 1.0  # seconds

# Topic existence probe interval
TOPIC_CHECK_INTERVAL = 60.0  # seconds

# ── Adaptive throttle for timer status lines ─────────────────────────────
#
# Claude Code shows a running timer in the status line (e.g. "Thinking… 5s",
# "Bash echo hello 1m 30s").  Without throttling, every tick produces a
# Telegram edit_message_text call (~60/min), which quickly hits Telegram's
# rate limit and causes the bot to go silent for extended periods.
#
# Strategy: detect timer suffixes, then increase the update interval the
# longer the same status persists.  Default tiers (configurable via
# STATUS_THROTTLE_INTERVALS env var, comma-separated):
#   0–10 s  →  every 1 s   (real-time, meaningful for short tasks)
#   10–60 s →  every 5 s
#   60 s+   →  every 30 s
#
# Non-timer status changes (e.g. "Reading file" → "Writing file") always
# send immediately.  The poll interval itself stays at 1 s so interactive
# UI detection is never delayed.

# Matches a timer in the status line.  Claude Code uses two formats:
#   1. Bare suffix:        "Thinking… 5s"  or  "Bash echo hello 1m 30s"
#   2. Inside parentheses: "Drizzling… (54s · ↓ 776 tokens)"
#                          "Coalescing… (25m 8s · ↓ 5.8k tokens · thought for 6s)"
# Both may include optional trailing text (parenthetical or · metadata).
_TIMER_RE = re.compile(
    r"\s+(?:"
    r"\((?:\d+m\s*)?\d+s\b[^)]*\)"   # (54s · …) or (1m 30s · …)
    r"|"
    r"(?:(?:\d+m\s*)?\d+s|\d+m)"      # bare 5s / 1m 30s / 2m
    r"(?:\s+\(.*\))?"                  # optional trailing (Esc to interrupt)
    r")\s*$"
)

# (user_id, thread_id_or_0) → (base_text, first_seen, last_sent)
_timer_throttle: dict[tuple[int, int], tuple[str, float, float]] = {}


def _should_send_status(
    user_id: int, thread_id: int | None, status_text: str
) -> bool:
    """Decide whether a status update should be enqueued.

    For non-timer status lines, always returns True.
    For timer status lines, applies adaptive interval based on elapsed time.
    """
    key = (user_id, thread_id or 0)
    now = time.monotonic()

    m = _TIMER_RE.search(status_text)
    if not m:
        # Not a timer — always send, clear any tracked state
        _timer_throttle.pop(key, None)
        return True

    # Extract base text (everything before the timer suffix)
    base = status_text[: m.start()].rstrip()

    prev = _timer_throttle.get(key)
    if prev is None or prev[0] != base:
        # New status or base text changed — reset and send immediately
        _timer_throttle[key] = (base, now, now)
        return True

    _, first_seen, last_sent = prev
    elapsed = now - first_seen
    since_sent = now - last_sent

    # Adaptive interval: widen as the timer runs longer
    t1, t2, t3 = config.status_throttle_intervals
    if elapsed <= 10:
        min_interval = t1    # real-time for the first 10 seconds
    elif elapsed <= 60:
        min_interval = t2    # every few seconds up to 1 minute
    else:
        min_interval = t3    # reduced frequency for long-running tasks

    if since_sent >= min_interval:
        _timer_throttle[key] = (base, first_seen, now)
        return True

    return False


async def update_status_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
    skip_status: bool = False,
) -> None:
    """Poll terminal and check for interactive UIs and status updates.

    UI detection always happens regardless of skip_status. When skip_status=True,
    only UI detection runs (used when message queue is non-empty to avoid
    flooding the queue with status updates).

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        # Window gone, enqueue clear (unless skipping status)
        if not skip_status:
            await enqueue_status_update(
                bot, user_id, window_id, None, thread_id=thread_id
            )
        return

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
    # ALWAYS check UI, regardless of skip_status
    if should_check_new_ui and is_interactive_ui(pane_text):
        logger.debug(
            "Interactive UI detected in polling (user=%d, window=%s, thread=%s)",
            user_id,
            window_id,
            thread_id,
        )
        await handle_interactive_ui(bot, user_id, window_id, thread_id)
        return

    # Normal status line check — skip if queue is non-empty
    if skip_status:
        return

    status_line = parse_status_line(pane_text)

    if status_line:
        if not _should_send_status(user_id, thread_id, status_line):
            return
        await enqueue_status_update(
            bot,
            user_id,
            window_id,
            status_line,
            thread_id=thread_id,
        )
    # If no status line, keep existing status message (don't clear on transient state)


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
                    except Exception as e:
                        logger.debug(
                            "Topic probe error for %s: %s",
                            wid,
                            e,
                        )

            for user_id, thread_id, wid in list(session_manager.iter_thread_bindings()):
                try:
                    # Clean up stale bindings (window no longer exists)
                    w = await tmux_manager.find_window_by_id(wid)
                    if not w:
                        session_manager.unbind_thread(user_id, thread_id)
                        await clear_topic_state(user_id, thread_id, bot)
                        logger.info(
                            "Cleaned up stale binding: user=%d thread=%d window_id=%s",
                            user_id,
                            thread_id,
                            wid,
                        )
                        continue

                    # UI detection happens unconditionally in update_status_message.
                    # Status enqueue is skipped inside update_status_message when
                    # interactive UI is detected (returns early) or when queue is non-empty.
                    queue = get_message_queue(user_id)
                    skip_status = queue is not None and not queue.empty()

                    await update_status_message(
                        bot,
                        user_id,
                        wid,
                        thread_id=thread_id,
                        skip_status=skip_status,
                    )
                except Exception as e:
                    logger.debug(
                        f"Status update error for user {user_id} "
                        f"thread {thread_id}: {e}"
                    )
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
