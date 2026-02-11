"""Window picker callback handlers.

Handles inline keyboard callbacks for the window picker UI:
  - CB_WIN_BIND: Bind an existing unbound tmux window to the current topic
  - CB_WIN_NEW: Transition from window picker to directory browser for new session
  - CB_WIN_CANCEL: Cancel the window picker

Key function: handle_window_callback (uniform callback handler signature).
"""

import logging
from pathlib import Path

from telegram import CallbackQuery, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import CB_WIN_BIND, CB_WIN_CANCEL, CB_WIN_NEW
from .callback_helpers import get_thread_id
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    clear_window_picker_state,
)
from .message_sender import safe_edit, safe_send

logger = logging.getLogger(__name__)


async def handle_window_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle window picker callbacks.

    Dispatches to the appropriate sub-handler based on callback data prefix.
    """
    if data.startswith(CB_WIN_BIND):
        await _handle_bind(query, user_id, data, update, context)
    elif data == CB_WIN_NEW:
        await _handle_new(query, update, context)
    elif data == CB_WIN_CANCEL:
        await _handle_cancel(query, update, context)


async def _handle_bind(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_WIN_BIND: bind existing unbound window to current topic."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale picker (topic mismatch)", show_alert=True)
        return
    try:
        idx = int(data[len(CB_WIN_BIND) :])
    except ValueError:
        await query.answer("Invalid data")
        return

    cached_windows: list[str] = (
        context.user_data.get(UNBOUND_WINDOWS_KEY, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(cached_windows):
        await query.answer("Window list changed, please retry", show_alert=True)
        return
    selected_wid = cached_windows[idx]

    w = await tmux_manager.find_window_by_id(selected_wid)
    if not w:
        display = session_manager.get_display_name(selected_wid)
        await query.answer(f"Window '{display}' no longer exists", show_alert=True)
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Not in a topic", show_alert=True)
        return

    display = w.window_name
    clear_window_picker_state(context.user_data)
    session_manager.bind_thread(user_id, thread_id, selected_wid, window_name=display)

    try:
        await context.bot.edit_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=display,
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    await safe_edit(
        query,
        f"✅ Bound to window `{display}`",
    )

    pending_text = (
        context.user_data.get("_pending_thread_text") if context.user_data else None
    )
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_text", None)
        context.user_data.pop("_pending_thread_id", None)
    if pending_text:
        send_ok, send_msg = await session_manager.send_to_window(
            selected_wid, pending_text
        )
        if not send_ok:
            logger.warning("Failed to forward pending text: %s", send_msg)
            await safe_send(
                context.bot,
                session_manager.resolve_chat_id(user_id, thread_id),
                f"❌ Failed to send pending message: {send_msg}",
                message_thread_id=thread_id,
            )
    await query.answer("Bound")


async def _handle_new(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_WIN_NEW: transition from window picker to directory browser."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale picker (topic mismatch)", show_alert=True)
        return
    clear_window_picker_state(context.user_data)
    start_path = str(Path.cwd())
    msg_text, keyboard, subdirs = build_directory_browser(start_path)
    if context.user_data is not None:
        context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        context.user_data[BROWSE_PATH_KEY] = start_path
        context.user_data[BROWSE_PAGE_KEY] = 0
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_cancel(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_WIN_CANCEL: cancel the window picker."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale picker (topic mismatch)", show_alert=True)
        return
    clear_window_picker_state(context.user_data)
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")
