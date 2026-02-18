"""Unified cleanup API for topic state.

Provides centralized cleanup functions that coordinate state cleanup across
all modules, preventing memory leaks when topics are deleted.

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
  - clear_dead_notification (delegated): Clear dead window notification tracking
"""

from typing import Any

from telegram import Bot

from .interactive_ui import clear_interactive_msg
from .message_queue import clear_status_msg_info, clear_tool_msg_ids_for_topic
from .topic_emoji import clear_topic_emoji_state
from .user_state import PENDING_THREAD_ID, PENDING_THREAD_TEXT


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    bot: Bot | None = None,
    user_data: dict[str, Any] | None = None,
    window_id: str | None = None,
) -> None:
    """Clear all memory state associated with a topic.

    This should be called when:
      - A topic is closed or deleted
      - A thread binding becomes stale (window deleted externally)

    Cleans up:
      - _status_msg_info (status message tracking)
      - _tool_msg_ids (tool_use -> message_id mapping)
      - _interactive_msgs and _interactive_mode (interactive UI state)
      - _topic_states (topic emoji tracking)
      - _has_seen_status (startup status tracking, if window_id provided)
      - user_data pending state (PENDING_THREAD_ID, PENDING_THREAD_TEXT)
    """
    # Clear status message tracking
    clear_status_msg_info(user_id, thread_id)

    # Clear tool message ID tracking
    clear_tool_msg_ids_for_topic(user_id, thread_id)

    # Clear dead window notification and autoclose tracking (lazy import to avoid circular dep)
    from .status_polling import (
        clear_autoclose_timer,
        clear_dead_notification,
        clear_seen_status,
    )

    clear_dead_notification(user_id, thread_id)
    clear_autoclose_timer(user_id, thread_id)
    if window_id:
        clear_seen_status(window_id)

    # Clear interactive UI state (also deletes message from chat)
    await clear_interactive_msg(user_id, bot, thread_id)

    # Clear topic emoji tracking (needs chat_id; use 0 as fallback)
    from ..session import session_manager

    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    clear_topic_emoji_state(chat_id, thread_id)

    # Clear pending thread state from user_data
    if user_data is not None and user_data.get(PENDING_THREAD_ID) == thread_id:
        user_data.pop(PENDING_THREAD_ID, None)
        user_data.pop(PENDING_THREAD_TEXT, None)
