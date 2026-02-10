"""Unified cleanup API for topic state.

Provides centralized cleanup functions that coordinate state cleanup across
all modules, preventing memory leaks when topics are deleted.

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
"""

from typing import Any

from telegram import Bot

from .interactive_ui import clear_interactive_msg
from .message_queue import clear_status_msg_info, clear_tool_msg_ids_for_topic


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    bot: Bot | None = None,
    user_data: dict[str, Any] | None = None,
) -> None:
    """Clear all memory state associated with a topic.

    This should be called when:
      - A topic is closed or deleted
      - A thread binding becomes stale (window deleted externally)

    Cleans up:
      - _status_msg_info (status message tracking)
      - _tool_msg_ids (tool_use → message_id mapping)
      - _interactive_msgs and _interactive_mode (interactive UI state)
      - user_data pending state (_pending_thread_id, _pending_thread_text)
    """
    # Clear status message tracking
    clear_status_msg_info(user_id, thread_id)

    # Clear tool message ID tracking
    clear_tool_msg_ids_for_topic(user_id, thread_id)

    # Clear interactive UI state (also deletes message from chat)
    await clear_interactive_msg(user_id, bot, thread_id)

    # Clear pending thread state from user_data
    if user_data is not None:
        if user_data.get("_pending_thread_id") == thread_id:
            user_data.pop("_pending_thread_id", None)
            user_data.pop("_pending_thread_text", None)
