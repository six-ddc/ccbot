"""Directory browser callback handlers.

Handles all inline keyboard callbacks for the directory browser UI:
  - CB_DIR_SELECT: Navigate into a subdirectory
  - CB_DIR_UP: Navigate to parent directory
  - CB_DIR_PAGE: Paginate directory listing
  - CB_DIR_CONFIRM: Confirm directory selection and create tmux window
  - CB_DIR_CANCEL: Cancel directory browsing

Key function: handle_directory_callback (uniform callback handler signature).
"""

import logging
from pathlib import Path

from telegram import CallbackQuery, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
)
from .callback_helpers import get_thread_id
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    build_directory_browser,
    clear_browse_state,
)
from .message_sender import safe_edit, safe_send

logger = logging.getLogger(__name__)


async def handle_directory_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle directory browser callbacks.

    Dispatches to the appropriate sub-handler based on callback data prefix.
    """
    if data.startswith(CB_DIR_SELECT):
        await _handle_select(query, user_id, data, update, context)
    elif data == CB_DIR_UP:
        await _handle_up(query, update, context)
    elif data.startswith(CB_DIR_PAGE):
        await _handle_page(query, data, update, context)
    elif data == CB_DIR_CONFIRM:
        await _handle_confirm(query, user_id, update, context)
    elif data == CB_DIR_CANCEL:
        await _handle_cancel(query, update, context)


async def _handle_select(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_DIR_SELECT: navigate into a subdirectory."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return
    try:
        idx = int(data[len(CB_DIR_SELECT) :])
    except ValueError:
        await query.answer("Invalid data")
        return

    cached_dirs: list[str] = (
        context.user_data.get(BROWSE_DIRS_KEY, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(cached_dirs):
        await query.answer("Directory list changed, please refresh", show_alert=True)
        return
    subdir_name = cached_dirs[idx]

    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    new_path = (Path(current_path) / subdir_name).resolve()

    if not new_path.exists() or not new_path.is_dir():
        await query.answer("Directory not found", show_alert=True)
        return

    new_path_str = str(new_path)
    if context.user_data is not None:
        context.user_data[BROWSE_PATH_KEY] = new_path_str
        context.user_data[BROWSE_PAGE_KEY] = 0

    msg_text, keyboard, subdirs = build_directory_browser(new_path_str)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_up(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_DIR_UP: navigate to parent directory."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return
    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    current = Path(current_path).resolve()
    parent = current.parent

    parent_path = str(parent)
    if context.user_data is not None:
        context.user_data[BROWSE_PATH_KEY] = parent_path
        context.user_data[BROWSE_PAGE_KEY] = 0

    msg_text, keyboard, subdirs = build_directory_browser(parent_path)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_page(
    query: CallbackQuery,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_DIR_PAGE: paginate directory listing."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return
    try:
        pg = int(data[len(CB_DIR_PAGE) :])
    except ValueError:
        await query.answer("Invalid data")
        return
    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    if context.user_data is not None:
        context.user_data[BROWSE_PAGE_KEY] = pg

    msg_text, keyboard, subdirs = build_directory_browser(current_path, pg)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_confirm(
    query: CallbackQuery,
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_DIR_CONFIRM: confirm directory selection, create tmux window."""
    default_path = str(Path.cwd())
    selected_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    pending_thread_id: int | None = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )

    confirm_thread_id = get_thread_id(update)
    if pending_thread_id is not None and confirm_thread_id != pending_thread_id:
        clear_browse_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return

    clear_browse_state(context.user_data)

    success, message, created_wname, created_wid = await tmux_manager.create_window(
        selected_path
    )
    if success:
        logger.info(
            "Window created: %s (id=%s) at %s (user=%d, thread=%s)",
            created_wname,
            created_wid,
            selected_path,
            user_id,
            pending_thread_id,
        )
        await session_manager.wait_for_session_map_entry(created_wid)

        if pending_thread_id is not None:
            session_manager.bind_thread(
                user_id, pending_thread_id, created_wid, window_name=created_wname
            )

            try:
                await context.bot.edit_forum_topic(
                    chat_id=session_manager.resolve_chat_id(user_id, pending_thread_id),
                    message_thread_id=pending_thread_id,
                    name=created_wname,
                )
            except TelegramError as e:
                logger.debug("Failed to rename topic: %s", e)

            await safe_edit(
                query,
                f"✅ {message}\n\nBound to this topic. Send messages here.",
            )

            pending_text = (
                context.user_data.get("_pending_thread_text")
                if context.user_data
                else None
            )
            if pending_text:
                logger.debug(
                    "Forwarding pending text to window %s (len=%d)",
                    created_wname,
                    len(pending_text),
                )
                if context.user_data is not None:
                    context.user_data.pop("_pending_thread_text", None)
                    context.user_data.pop("_pending_thread_id", None)
                send_ok, send_msg = await session_manager.send_to_window(
                    created_wid,
                    pending_text,
                )
                if not send_ok:
                    logger.warning("Failed to forward pending text: %s", send_msg)
                    await safe_send(
                        context.bot,
                        session_manager.resolve_chat_id(user_id, pending_thread_id),
                        f"❌ Failed to send pending message: {send_msg}",
                        message_thread_id=pending_thread_id,
                    )
            elif context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
        else:
            await safe_edit(query, f"✅ {message}")
    else:
        await safe_edit(query, f"❌ {message}")
        if pending_thread_id is not None and context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
    await query.answer("Created" if success else "Failed")


async def _handle_cancel(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_DIR_CANCEL: cancel directory browsing."""
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return
    clear_browse_state(context.user_data)
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")
