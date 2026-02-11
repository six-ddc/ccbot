"""Tests for message_sender rate limiting and send-with-fallback."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from telegram import Message
from telegram.error import RetryAfter

from ccbot.handlers.message_sender import (
    MESSAGE_SEND_INTERVAL,
    _last_send_time,
    _send_with_fallback,
    rate_limit_send,
)


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    _last_send_time.clear()
    yield
    _last_send_time.clear()


class TestRateLimitSend:
    async def test_first_call_no_wait(self) -> None:
        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep",
            new_callable=AsyncMock,
            spec=asyncio.sleep,
        ) as mock_sleep:
            await rate_limit_send(123)
            mock_sleep.assert_not_called()

    async def test_second_call_within_interval_waits(self) -> None:
        await rate_limit_send(123)

        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep",
            new_callable=AsyncMock,
            spec=asyncio.sleep,
        ) as mock_sleep:
            await rate_limit_send(123)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert 0 < wait_time <= MESSAGE_SEND_INTERVAL

    async def test_different_chat_ids_independent(self) -> None:
        await rate_limit_send(1)

        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep",
            new_callable=AsyncMock,
            spec=asyncio.sleep,
        ) as mock_sleep:
            await rate_limit_send(2)
            mock_sleep.assert_not_called()

    async def test_updates_last_send_time(self) -> None:
        assert 123 not in _last_send_time
        await rate_limit_send(123)
        assert 123 in _last_send_time
        first_time = _last_send_time[123]

        await asyncio.sleep(0.01)
        await rate_limit_send(123)
        assert _last_send_time[123] > first_time


class TestSendWithFallback:
    async def test_markdown_success(self) -> None:
        bot = AsyncMock()
        sent = AsyncMock(spec=Message)
        bot.send_message.return_value = sent

        result = await _send_with_fallback(bot, 123, "hello")
        assert result is sent
        bot.send_message.assert_called_once()
        assert bot.send_message.call_args.kwargs["parse_mode"] == "MarkdownV2"

    async def test_fallback_to_plain(self) -> None:
        bot = AsyncMock()
        sent = AsyncMock(spec=Message)
        bot.send_message.side_effect = [Exception("parse error"), sent]

        result = await _send_with_fallback(bot, 123, "hello")
        assert result is sent
        assert bot.send_message.call_count == 2
        assert "parse_mode" not in bot.send_message.call_args_list[1].kwargs

    async def test_both_fail_returns_none(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = [Exception("md fail"), Exception("plain fail")]

        result = await _send_with_fallback(bot, 123, "hello")
        assert result is None

    async def test_retry_after_reraised(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = RetryAfter(30)

        with pytest.raises(RetryAfter):
            await _send_with_fallback(bot, 123, "hello")
