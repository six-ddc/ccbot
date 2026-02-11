"""Interactive UI callback handlers.

Handles inline keyboard callbacks for AskUserQuestion/ExitPlanMode/Permission UIs:
  - CB_ASK_* direction/action keys: navigate interactive UI via tmux keys
  - CB_ASK_REFRESH: refresh the interactive UI display

Key function: handle_interactive_callback (uniform callback handler signature).
"""

import asyncio
import logging

from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)
from .interactive_ui import clear_interactive_msg, handle_interactive_ui

logger = logging.getLogger(__name__)

# cb_prefix -> (tmux_key, refresh_ui_after)
INTERACTIVE_KEY_MAP: dict[str, tuple[str, bool]] = {
    CB_ASK_UP: ("Up", True),
    CB_ASK_DOWN: ("Down", True),
    CB_ASK_LEFT: ("Left", True),
    CB_ASK_RIGHT: ("Right", True),
    CB_ASK_ESC: ("Escape", False),
    CB_ASK_ENTER: ("Enter", True),
    CB_ASK_SPACE: ("Space", True),
    CB_ASK_TAB: ("Tab", True),
}

# Answer-toast labels for interactive key callbacks
INTERACTIVE_KEY_LABELS: dict[str, str] = {
    CB_ASK_ESC: "\u238b Esc",
    CB_ASK_ENTER: "\u23ce Enter",
    CB_ASK_SPACE: "\u2423 Space",
    CB_ASK_TAB: "\u21e5 Tab",
}

# All interactive prefixes (key map + refresh)
INTERACTIVE_PREFIXES: tuple[str, ...] = (
    *INTERACTIVE_KEY_MAP,
    CB_ASK_REFRESH,
)


def match_interactive_prefix(data: str) -> tuple[str, str] | None:
    """Match callback data against interactive UI prefixes.

    Returns (cb_prefix, window_id) or None.
    """
    for prefix in INTERACTIVE_PREFIXES:
        if data.startswith(prefix):
            return prefix, data[len(prefix) :]
    return None


async def handle_interactive_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle interactive UI callbacks (AskUserQuestion/ExitPlanMode navigation)."""
    matched = match_interactive_prefix(data)
    if not matched:
        return

    cb_prefix, window_id = matched
    from .callback_helpers import get_thread_id

    thread_id = get_thread_id(update)

    if cb_prefix == CB_ASK_REFRESH:
        await handle_interactive_ui(context.bot, user_id, window_id, thread_id)
        await query.answer("\U0001f504")
    else:
        tmux_key, refresh_ui = INTERACTIVE_KEY_MAP[cb_prefix]
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, tmux_key, enter=False, literal=False
            )
            if refresh_ui:
                await asyncio.sleep(0.5)
                await handle_interactive_ui(context.bot, user_id, window_id, thread_id)
            else:
                await clear_interactive_msg(user_id, context.bot, thread_id)
        await query.answer(INTERACTIVE_KEY_LABELS.get(cb_prefix, ""))
