"""Topic emoji status updates via editForumTopic.

Updates topic names with status emoji prefixes to reflect session state:
  - Active (working): topic name prefixed with working emoji
  - Idle (waiting): topic name prefixed with idle emoji
  - Dead (window gone): topic name prefixed with dead emoji

Tracks per-topic state to avoid redundant API calls. Debounces transitions
to prevent rapid active/idle toggling from flooding the chat with rename
messages. Gracefully degrades when the bot lacks editForumTopic permission.

Key functions:
  - update_topic_emoji: Update emoji for a specific topic (debounced)
  - clear_topic_emoji_state: Clean up tracking for a topic
"""

import logging
import time

from telegram import Bot
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

# Emoji prefixes for session states
EMOJI_ACTIVE = "\U0001f7e2"  # Green circle
EMOJI_IDLE = "\U0001f4a4"  # Zzz / sleeping
EMOJI_DEAD = "\u274c"  # Cross mark
_EMOJI_DEAD_OLD = "\u26ab"  # Legacy dead emoji (black circle, pre-2026-02)

# Debounce: state must be stable for this many seconds before updating topic name.
# Prevents rapid active↔idle toggling from flooding chat with rename messages.
DEBOUNCE_SECONDS = 5.0

# Topic state tracking: (chat_id, thread_id) -> current_state
_topic_states: dict[tuple[int, int], str] = {}

# Pending transitions: (chat_id, thread_id) -> (desired_state, first_seen_monotonic)
_pending_transitions: dict[tuple[int, int], tuple[str, float]] = {}

# Chats where editForumTopic is disabled due to permission errors
_disabled_chats: set[int] = set()


async def update_topic_emoji(
    bot: Bot,
    chat_id: int,
    thread_id: int,
    state: str,
    display_name: str,
) -> None:
    """Update topic name with emoji prefix reflecting session state.

    Debounces transitions: the new state must be requested consistently for
    DEBOUNCE_SECONDS before the API call is made. This prevents rapid
    active/idle flickering from generating lots of "topic renamed" messages.

    Args:
        bot: Telegram Bot instance
        chat_id: Group chat ID
        thread_id: Forum topic thread ID
        state: One of "active", "idle", "dead"
        display_name: Base topic name (without emoji prefix)
    """
    if chat_id in _disabled_chats:
        return

    key = (chat_id, thread_id)

    # Already in this state — no transition needed
    if _topic_states.get(key) == state:
        _pending_transitions.pop(key, None)
        return

    emoji = {
        "active": EMOJI_ACTIVE,
        "idle": EMOJI_IDLE,
        "dead": EMOJI_DEAD,
    }.get(state, "")

    if not emoji:
        return

    # Debounce: require the new state to be stable before applying
    now = time.monotonic()
    pending = _pending_transitions.get(key)
    if pending is None or pending[0] != state:
        # New or changed desired state — start debounce timer
        _pending_transitions[key] = (state, now)
        return

    if now - pending[1] < DEBOUNCE_SECONDS:
        # Not stable long enough yet
        return

    # Debounce passed — execute the transition
    _pending_transitions.pop(key, None)

    # Strip any existing emoji prefix from display name
    clean_name = strip_emoji_prefix(display_name)
    new_name = f"{emoji} {clean_name}"

    try:
        await bot.edit_forum_topic(
            chat_id=chat_id,
            message_thread_id=thread_id,
            name=new_name,
        )
        _topic_states[key] = state
        logger.debug(
            "Updated topic emoji: chat=%d thread=%d state=%s name='%s'",
            chat_id,
            thread_id,
            state,
            new_name,
        )
    except BadRequest as e:
        if "Not enough rights" in str(e) or "TOPIC_NOT_MODIFIED" in str(e):
            if "Not enough rights" in str(e):
                _disabled_chats.add(chat_id)
                logger.info(
                    "Topic emoji disabled for chat %d: insufficient permissions",
                    chat_id,
                )
            else:
                # Topic already has the right name, update our tracking
                _topic_states[key] = state
        else:
            logger.debug("Failed to update topic emoji: %s", e)
    except TelegramError as e:
        logger.debug("Failed to update topic emoji: %s", e)


def strip_emoji_prefix(name: str) -> str:
    """Remove known emoji prefix from a topic name."""
    for emoji in (EMOJI_ACTIVE, EMOJI_IDLE, EMOJI_DEAD, _EMOJI_DEAD_OLD):
        prefix = f"{emoji} "
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def clear_topic_emoji_state(chat_id: int, thread_id: int) -> None:
    """Clear emoji tracking for a topic (called on topic cleanup)."""
    key = (chat_id, thread_id)
    _topic_states.pop(key, None)
    _pending_transitions.pop(key, None)


def reset_all_state() -> None:
    """Reset all tracking state (for testing)."""
    _topic_states.clear()
    _pending_transitions.clear()
    _disabled_chats.clear()
