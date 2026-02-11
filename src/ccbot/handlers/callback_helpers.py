"""Shared helpers for callback handler modules.

Provides utility functions used by multiple callback handler modules:
  - user_owns_window: Check if a user has any thread binding to a window
  - get_thread_id: Extract thread_id from a Telegram update
"""

from telegram import Update

from ..session import session_manager


def user_owns_window(user_id: int, window_id: str) -> bool:
    """Check if a user has any thread binding to the given window."""
    return window_id in session_manager.get_all_thread_windows(user_id).values()


def get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic."""
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid
