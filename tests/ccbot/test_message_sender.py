"""Tests for message_sender rate limiting (PR 1 changes)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.handlers.message_sender import (
    MESSAGE_SEND_INTERVAL,
    _last_send_time,
    rate_limit_send,
)


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    """Reset global rate limit state between tests."""
    _last_send_time.clear()
    yield
    _last_send_time.clear()


class TestRateLimitSend:
    async def test_first_call_no_wait(self) -> None:
        """First call to a fresh chat_id should not sleep."""
        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await rate_limit_send(123)
            mock_sleep.assert_not_called()

    async def test_second_call_within_interval_waits(self) -> None:
        """Second call within MESSAGE_SEND_INTERVAL should sleep."""
        # First call — records timestamp
        await rate_limit_send(123)

        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await rate_limit_send(123)
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert 0 < wait_time <= MESSAGE_SEND_INTERVAL

    async def test_different_chat_ids_independent(self) -> None:
        """Rate limit for chat_id=1 should not affect chat_id=2."""
        await rate_limit_send(1)

        with patch(
            "ccbot.handlers.message_sender.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await rate_limit_send(2)
            mock_sleep.assert_not_called()

    async def test_updates_last_send_time(self) -> None:
        """_last_send_time should be updated after each call."""
        assert 123 not in _last_send_time
        await rate_limit_send(123)
        assert 123 in _last_send_time
        first_time = _last_send_time[123]

        # Small delay so monotonic() advances
        await asyncio.sleep(0.01)
        await rate_limit_send(123)
        assert _last_send_time[123] > first_time
