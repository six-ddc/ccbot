"""History pagination callback handler.

Handles inline keyboard callbacks for navigating through message history pages.
Dispatches CB_HISTORY_PREV and CB_HISTORY_NEXT callbacks to page through
history results.

Key function: handle_history_callback (uniform callback handler signature).
"""

import logging

from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from ..tmux_manager import tmux_manager
from .callback_data import CB_HISTORY_PREV
from .history import send_history
from .message_sender import safe_edit

logger = logging.getLogger(__name__)

# Minimum parts in history callback data: page:window_id:start:end
_HISTORY_CB_PARTS_MIN = 4


async def handle_history_callback(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    _update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle history pagination callbacks (CB_HISTORY_PREV / CB_HISTORY_NEXT).

    Callback data format: hp:<page>:<window_id>:<start>:<end>
    or hn:<page>:<window_id>:<start>:<end>.
    Old format (no byte range): hp:<page>:<window_id>.
    """
    prefix_len = len(CB_HISTORY_PREV)  # same length for both
    rest = data[prefix_len:]
    try:
        parts = rest.split(":")
        if len(parts) < _HISTORY_CB_PARTS_MIN:
            # Old format without byte range: page:window_id
            offset_str, window_id = rest.split(":", 1)
            start_byte, end_byte = 0, 0
        else:
            # New format: page:window_id:start:end (window_id may contain colons)
            offset_str = parts[0]
            start_byte = int(parts[-2])
            end_byte = int(parts[-1])
            window_id = ":".join(parts[1:-2])
        offset = int(offset_str)
    except (ValueError, IndexError):
        await query.answer("Invalid data")
        return

    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await send_history(
            query,
            window_id,
            offset=offset,
            edit=True,
            start_byte=start_byte,
            end_byte=end_byte,
            # Don't pass user_id for pagination - offset update only on initial view
            # This prevents offset from going backwards if new messages arrive while paging
        )
    else:
        await safe_edit(query, "Window no longer exists.")
    await query.answer("Page updated")
