"""Tests for the send helpers' formatting fallback in message_sender.

Focus: the fallback to plain text must fire only when Telegram *rejects* the
formatting (BadRequest), never on an ambiguous transient error (e.g. TimedOut),
which may already have delivered the formatted message. Retrying on a transient
error is what produced duplicate messages (one formatted + one plain).
"""

from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from ccbot.handlers import message_sender
from ccbot.handlers.message_sender import (
    safe_reply,
    safe_send,
    send_with_fallback,
)


@pytest.fixture(autouse=True)
def _identity_formatter(monkeypatch):
    """Isolate these tests from the MarkdownV2 conversion layer."""
    monkeypatch.setattr(message_sender, "_ensure_formatted", lambda text: text)


class TestSendWithFallback:
    async def test_timeout_does_not_retry_and_avoids_duplicate(self):
        # Formatted send raises after (potentially) delivering — must NOT resend.
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=TimedOut("connection timed out"))

        result = await send_with_fallback(bot, 123, "hello")

        assert result is None
        assert bot.send_message.await_count == 1  # no second (duplicate) send

    async def test_network_error_does_not_retry(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=NetworkError("flaky link"))

        result = await send_with_fallback(bot, 123, "hello")

        assert result is None
        assert bot.send_message.await_count == 1

    async def test_bad_request_falls_back_to_plain_text(self):
        # BadRequest = formatting rejected => not delivered => plain retry is safe.
        sent = object()
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=[BadRequest("bad entities"), sent])

        result = await send_with_fallback(bot, 123, "hello")

        assert result is sent
        assert bot.send_message.await_count == 2
        # The plain retry must not carry a parse_mode.
        _, retry_kwargs = bot.send_message.await_args_list[1]
        assert "parse_mode" not in retry_kwargs

    async def test_retry_after_is_reraised(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=RetryAfter(5))

        with pytest.raises(RetryAfter):
            await send_with_fallback(bot, 123, "hello")
        assert bot.send_message.await_count == 1


class TestSafeSend:
    async def test_timeout_does_not_retry(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=TimedOut("timeout"))

        await safe_send(bot, 123, "hello")

        assert bot.send_message.await_count == 1

    async def test_bad_request_falls_back_to_plain_text(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=[BadRequest("bad"), object()])

        await safe_send(bot, 123, "hello")

        assert bot.send_message.await_count == 2
        _, retry_kwargs = bot.send_message.await_args_list[1]
        assert "parse_mode" not in retry_kwargs


class TestSafeReply:
    async def test_timeout_propagates_without_retry(self):
        message = AsyncMock()
        message.reply_text = AsyncMock(side_effect=TimedOut("timeout"))

        with pytest.raises(TimedOut):
            await safe_reply(message, "hello")
        assert message.reply_text.await_count == 1  # no duplicate reply

    async def test_bad_request_falls_back_to_plain_text(self):
        sent = object()
        message = AsyncMock()
        message.reply_text = AsyncMock(side_effect=[BadRequest("bad"), sent])

        result = await safe_reply(message, "hello")

        assert result is sent
        assert message.reply_text.await_count == 2
