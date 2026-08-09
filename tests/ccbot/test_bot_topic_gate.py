"""Tests for bot._topic_gate — CCBOT_TOPIC_ALLOWLIST enforcement.

_topic_gate is registered in an earlier PTB handler group (see
bot.create_bot) so it runs before every other handler; raising
ApplicationHandlerStop there is what actually blocks a disallowed topic.
"""

from unittest.mock import MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from ccbot.bot import _topic_gate


def _update_with_thread(thread_id: int | None) -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.message_thread_id = thread_id
    update.callback_query = None
    return update


@pytest.mark.asyncio
class TestTopicGate:
    async def test_blocks_disallowed_topic(self) -> None:
        with patch("ccbot.bot.config") as mock_config:
            mock_config.is_topic_allowed.return_value = False
            with pytest.raises(ApplicationHandlerStop):
                await _topic_gate(_update_with_thread(999), context=MagicMock())

    async def test_allows_allowed_topic(self) -> None:
        with patch("ccbot.bot.config") as mock_config:
            mock_config.is_topic_allowed.return_value = True
            # Should not raise.
            await _topic_gate(_update_with_thread(299), context=MagicMock())
            mock_config.is_topic_allowed.assert_called_once_with(299)

    async def test_general_topic_never_gated(self) -> None:
        """thread_id 1 ("General") normalizes to None in _get_thread_id and
        is never subject to the allowlist — existing per-handler
        `if thread_id is None: return` guards handle it as before."""
        with patch("ccbot.bot.config") as mock_config:
            mock_config.is_topic_allowed.return_value = False
            await _topic_gate(_update_with_thread(1), context=MagicMock())
            mock_config.is_topic_allowed.assert_not_called()
