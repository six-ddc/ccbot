"""Screenshot and status button callback handlers.

Handles inline keyboard callbacks for screenshot UI and status message buttons:
  - CB_SCREENSHOT_REFRESH: Refresh an existing screenshot
  - CB_STATUS_ESC: Send Escape key from status message
  - CB_STATUS_SCREENSHOT: Take a screenshot from status message
  - CB_KEYS_PREFIX: Send a quick key from screenshot keyboard

Key function: handle_screenshot_callback (uniform callback handler signature).
"""

import asyncio
import contextlib
import io
import logging

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..screenshot import text_to_image
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    CB_STATUS_ESC,
    CB_STATUS_SCREENSHOT,
)
from .callback_helpers import get_thread_id, user_owns_window

logger = logging.getLogger(__name__)

# key_id -> (tmux_key, enter, literal)
KEYS_SEND_MAP: dict[str, tuple[str, bool, bool]] = {
    "up": ("Up", False, False),
    "dn": ("Down", False, False),
    "lt": ("Left", False, False),
    "rt": ("Right", False, False),
    "esc": ("Escape", False, False),
    "ent": ("Enter", False, False),
    "spc": ("Space", False, False),
    "tab": ("Tab", False, False),
    "cc": ("C-c", False, False),
}

# key_id -> display label (shown in callback answer toast)
KEY_LABELS: dict[str, str] = {
    "up": "\u2191",
    "dn": "\u2193",
    "lt": "\u2190",
    "rt": "\u2192",
    "esc": "\u238b Esc",
    "ent": "\u23ce Enter",
    "spc": "\u2423 Space",
    "tab": "\u21e5 Tab",
    "cc": "^C",
}


def build_screenshot_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for screenshot: control keys + refresh."""

    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{window_id}"[:64],
        )

    return InlineKeyboardMarkup(
        [
            [btn("\u2423 Space", "spc"), btn("\u2191", "up"), btn("\u21e5 Tab", "tab")],
            [btn("\u2190", "lt"), btn("\u2193", "dn"), btn("\u2192", "rt")],
            [btn("\u238b Esc", "esc"), btn("^C", "cc"), btn("\u23ce Enter", "ent")],
            [
                InlineKeyboardButton(
                    "\U0001f504 Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{window_id}"[:64],
                )
            ],
        ]
    )


async def handle_screenshot_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle screenshot, status button, and quick-key callbacks."""
    if data.startswith(CB_SCREENSHOT_REFRESH):
        await _handle_refresh(query, data)
    elif data.startswith(CB_STATUS_ESC):
        await _handle_status_esc(query, user_id, data)
    elif data.startswith(CB_STATUS_SCREENSHOT):
        await _handle_status_screenshot(query, user_id, data, update)
    elif data.startswith(CB_KEYS_PREFIX):
        await _handle_keys(query, data)


async def _handle_refresh(query: CallbackQuery, data: str) -> None:
    """Handle CB_SCREENSHOT_REFRESH: refresh an existing screenshot."""
    window_id = data[len(CB_SCREENSHOT_REFRESH) :]
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window no longer exists", show_alert=True)
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    try:
        await query.edit_message_media(
            media=InputMediaDocument(
                media=io.BytesIO(png_bytes), filename="screenshot.png"
            ),
            reply_markup=keyboard,
        )
        await query.answer("Refreshed")
    except TelegramError as e:
        logger.error("Failed to refresh screenshot: %s", e)
        await query.answer("Failed to refresh", show_alert=True)


async def _handle_status_esc(query: CallbackQuery, user_id: int, data: str) -> None:
    """Handle CB_STATUS_ESC: send Escape key from status message."""
    window_id = data[len(CB_STATUS_ESC) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await tmux_manager.send_keys(w.window_id, "Escape", enter=False, literal=False)
        await query.answer("\u238b Sent Escape")
    else:
        await query.answer("Window not found", show_alert=True)


async def _handle_status_screenshot(
    query: CallbackQuery, user_id: int, data: str, update: Update
) -> None:
    """Handle CB_STATUS_SCREENSHOT: take screenshot from status message."""
    window_id = data[len(CB_STATUS_SCREENSHOT) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return
    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture", show_alert=True)
        return
    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    await query.get_bot().send_document(
        chat_id=chat_id,
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=keyboard,
        message_thread_id=thread_id,
    )
    await query.answer("\U0001f4f8")


async def _handle_keys(query: CallbackQuery, data: str) -> None:
    """Handle CB_KEYS_PREFIX: send a quick key from screenshot keyboard."""
    rest = data[len(CB_KEYS_PREFIX) :]
    colon_idx = rest.find(":")
    if colon_idx < 0:
        await query.answer("Invalid data")
        return
    key_id = rest[:colon_idx]
    window_id = rest[colon_idx + 1 :]

    key_info = KEYS_SEND_MAP.get(key_id)
    if not key_info:
        await query.answer("Unknown key")
        return

    tmux_key, enter, literal = key_info
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return

    await tmux_manager.send_keys(w.window_id, tmux_key, enter=enter, literal=literal)
    await query.answer(KEY_LABELS.get(key_id, key_id))

    # Refresh screenshot after key press
    await asyncio.sleep(0.5)
    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if text:
        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = build_screenshot_keyboard(window_id)
        with contextlib.suppress(TelegramError):
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes),
                    filename="screenshot.png",
                ),
                reply_markup=keyboard,
            )
