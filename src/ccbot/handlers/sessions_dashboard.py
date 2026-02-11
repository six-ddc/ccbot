"""Sessions dashboard — /sessions command showing all bound sessions.

Displays a summary of all thread-bound sessions for the current user
with alive/dead status indicators, per-session Kill buttons (two-step
confirmation), and refresh/new-session actions.

Key functions:
  - sessions_command(): /sessions command handler
  - handle_sessions_refresh(): refresh button callback
  - handle_sessions_kill(): first Kill tap — show confirmation
  - handle_sessions_kill_confirm(): second tap — kill and unbind
"""

import logging

from telegram import (
    Bot,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_SESSIONS_KILL,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
)
from .cleanup import clear_topic_state
from .message_sender import safe_edit, safe_reply

logger = logging.getLogger(__name__)

_REFRESH_BTN = InlineKeyboardButton("🔄 Refresh", callback_data=CB_SESSIONS_REFRESH)
_NEW_BTN = InlineKeyboardButton("➕ New Session", callback_data=CB_SESSIONS_NEW)


async def _build_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dashboard text and keyboard for a user's sessions."""
    bindings = session_manager.get_all_thread_windows(user_id)

    if not bindings:
        keyboard = InlineKeyboardMarkup([[_REFRESH_BTN, _NEW_BTN]])
        return (
            "No active sessions.\n\nCreate a new topic to start a session.",
            keyboard,
        )

    all_windows = await tmux_manager.list_windows()
    live_ids = {w.window_id for w in all_windows}

    lines: list[str] = []
    kill_buttons: list[list[InlineKeyboardButton]] = []
    for _thread_id, window_id in sorted(bindings.items()):
        display_name = session_manager.get_display_name(window_id)
        alive = window_id in live_ids
        status = "🟢" if alive else "⚫"
        lines.append(f"{status} {display_name}")
        if alive:
            kill_buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗑 Kill {display_name}",
                        callback_data=f"{CB_SESSIONS_KILL}{window_id}"[:64],
                    )
                ]
            )

    text = "Sessions\n\n" + "\n".join(lines)
    rows = kill_buttons + [[_REFRESH_BTN, _NEW_BTN]]
    return text, InlineKeyboardMarkup(rows)


async def sessions_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sessions — show dashboard of all bound sessions."""
    user = update.effective_user
    if not user or not update.message:
        return

    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    text, keyboard = await _build_dashboard(user.id)
    await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_sessions_refresh(query: CallbackQuery, user_id: int) -> None:
    """Handle refresh button — re-render the dashboard in-place."""
    text, keyboard = await _build_dashboard(user_id)
    await safe_edit(query, text, reply_markup=keyboard)


async def handle_sessions_kill(
    query: CallbackQuery, _user_id: int, window_id: str
) -> None:
    """First Kill tap — show confirmation prompt."""
    display = session_manager.get_display_name(window_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⚠ Confirm kill {display}",
                    callback_data=f"{CB_SESSIONS_KILL_CONFIRM}{window_id}"[:64],
                ),
            ],
            [_REFRESH_BTN],
        ]
    )
    await safe_edit(
        query,
        f"Kill session '{display}'?\n\nThis will terminate the Claude Code process.",
        reply_markup=keyboard,
    )


async def handle_sessions_kill_confirm(
    query: CallbackQuery, user_id: int, window_id: str, bot: Bot
) -> None:
    """Second tap — kill the tmux window, unbind all users, refresh dashboard."""
    display = session_manager.get_display_name(window_id)

    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await tmux_manager.kill_window(w.window_id)

    # Unbind ALL users bound to this window
    for uid, tid, bound_wid in list(session_manager.iter_thread_bindings()):
        if bound_wid == window_id:
            session_manager.unbind_thread(uid, tid)
            await clear_topic_state(uid, tid, bot)

    logger.info(
        "sessions_kill_confirm: killed window %s (%s), user=%d",
        window_id,
        display,
        user_id,
    )

    # Re-render dashboard
    text, keyboard = await _build_dashboard(user_id)
    await safe_edit(query, f"🗑 Killed '{display}'\n\n{text}", reply_markup=keyboard)
