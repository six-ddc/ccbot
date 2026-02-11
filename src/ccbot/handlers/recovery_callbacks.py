"""Recovery UI callback handlers.

Handles inline keyboard callbacks for dead window recovery:
  - CB_RECOVERY_FRESH: Create a fresh session in the same directory
  - CB_RECOVERY_CONTINUE: Continue existing session (future)
  - CB_RECOVERY_RESUME: Resume session from checkpoint (future)
  - CB_RECOVERY_CANCEL: Cancel recovery

Key function: handle_recovery_callback (uniform callback handler signature).
"""

import logging
from pathlib import Path

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_RESUME,
)
from .callback_helpers import get_thread_id
from .message_sender import safe_edit, safe_send

logger = logging.getLogger(__name__)


def build_recovery_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for dead window recovery options."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f195 Fresh",
                    callback_data=f"{CB_RECOVERY_FRESH}{window_id}"[:64],
                ),
                InlineKeyboardButton(
                    "\u25b6 Continue",
                    callback_data=f"{CB_RECOVERY_CONTINUE}{window_id}"[:64],
                ),
                InlineKeyboardButton(
                    "\U0001f4c2 Resume",
                    callback_data=f"{CB_RECOVERY_RESUME}{window_id}"[:64],
                ),
            ],
            [
                InlineKeyboardButton("\u2716 Cancel", callback_data=CB_RECOVERY_CANCEL),
            ],
        ]
    )


async def handle_recovery_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle recovery UI callbacks."""
    if data.startswith(CB_RECOVERY_FRESH):
        await _handle_fresh(query, user_id, data, update, context)
    elif data.startswith(CB_RECOVERY_CONTINUE):
        await query.answer(
            "Continue will be available in a future update", show_alert=True
        )
    elif data.startswith(CB_RECOVERY_RESUME):
        await query.answer(
            "Resume will be available in a future update", show_alert=True
        )
    elif data == CB_RECOVERY_CANCEL:
        await _handle_cancel(query, update, context)


async def _handle_fresh(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_FRESH: create fresh session in same directory."""
    old_wid = data[len(CB_RECOVERY_FRESH) :]
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return

    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    stored_wid = (
        context.user_data.get("_recovery_window_id") if context.user_data else None
    )
    if pending_tid is None or thread_id != pending_tid or stored_wid != old_wid:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    ws = session_manager.get_window_state(old_wid)
    cwd = ws.cwd if ws.cwd else ""
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "\u274c Directory no longer exists.")
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
            context.user_data.pop("_recovery_window_id", None)
        await query.answer("Failed")
        return

    # Unbind old dead window
    session_manager.unbind_thread(user_id, thread_id)

    # Create new window in same cwd
    success, message, created_wname, created_wid = await tmux_manager.create_window(cwd)
    if not success:
        await safe_edit(query, f"\u274c {message}")
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
            context.user_data.pop("_recovery_window_id", None)
        await query.answer("Failed")
        return

    await session_manager.wait_for_session_map_entry(created_wid)
    session_manager.bind_thread(
        user_id, thread_id, created_wid, window_name=created_wname
    )

    try:
        await context.bot.edit_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=created_wname,
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    await safe_edit(query, f"\u2705 {message}\n\nFresh session started.")

    # Forward pending text
    pending_text = (
        context.user_data.get("_pending_thread_text") if context.user_data else None
    )
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_text", None)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_recovery_window_id", None)
    if pending_text:
        send_ok, send_msg = await session_manager.send_to_window(
            created_wid, pending_text
        )
        if not send_ok:
            logger.warning("Failed to forward pending text: %s", send_msg)
            await safe_send(
                context.bot,
                session_manager.resolve_chat_id(user_id, thread_id),
                f"\u274c Failed to send pending message: {send_msg}",
                message_thread_id=thread_id,
            )
    await query.answer("Created")


async def _handle_cancel(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_CANCEL: cancel recovery."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is None or get_thread_id(update) != pending_tid:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)
        context.user_data.pop("_recovery_window_id", None)
    await safe_edit(query, "Cancelled. Send a message to try again.")
    await query.answer("Cancelled")
