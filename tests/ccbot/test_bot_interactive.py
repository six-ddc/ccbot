"""Regression tests for interactive UI lifecycle in handle_new_message."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import handle_new_message
from ccbot.session_monitor import NewMessage


@pytest.mark.asyncio
async def test_handle_new_message_keeps_interactive_when_ui_visible():
    """Do not clear interactive message while prompt is still visible in tmux."""
    msg = NewMessage(
        session_id="sid-1",
        text="regular update",
        is_complete=False,
        content_type="text",
        role="assistant",
    )
    mock_bot = AsyncMock()
    mock_window = MagicMock()
    mock_window.window_id = "@2"

    with (
        patch("ccbot.bot.session_manager") as mock_sm,
        patch("ccbot.bot.tmux_manager") as mock_tmux,
        patch("ccbot.bot.get_message_queue", return_value=None),
        patch("ccbot.bot.get_interactive_msg_id", return_value=999),
        patch("ccbot.bot.clear_interactive_msg", new_callable=AsyncMock) as mock_clear,
        patch("ccbot.bot.is_interactive_ui", return_value=True),
        patch("ccbot.bot.build_response_parts", return_value=["regular update"]),
    ):
        mock_sm.find_users_for_session = AsyncMock(return_value=[(1, "@2", 42)])
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
        mock_tmux.capture_pane = AsyncMock(return_value="approval prompt text")

        await handle_new_message(msg, mock_bot)

        mock_clear.assert_not_called()


@pytest.mark.asyncio
async def test_handle_new_message_clears_interactive_when_ui_not_visible():
    """Clear interactive message once prompt is no longer visible."""
    msg = NewMessage(
        session_id="sid-1",
        text="regular update",
        is_complete=False,
        content_type="text",
        role="assistant",
    )
    mock_bot = AsyncMock()
    mock_window = MagicMock()
    mock_window.window_id = "@2"

    with (
        patch("ccbot.bot.session_manager") as mock_sm,
        patch("ccbot.bot.tmux_manager") as mock_tmux,
        patch("ccbot.bot.get_message_queue", return_value=None),
        patch("ccbot.bot.get_interactive_msg_id", return_value=999),
        patch("ccbot.bot.clear_interactive_msg", new_callable=AsyncMock) as mock_clear,
        patch("ccbot.bot.is_interactive_ui", return_value=False),
        patch("ccbot.bot.build_response_parts", return_value=["regular update"]),
    ):
        mock_sm.find_users_for_session = AsyncMock(return_value=[(1, "@2", 42)])
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
        mock_tmux.capture_pane = AsyncMock(return_value="normal pane text")

        await handle_new_message(msg, mock_bot)

        mock_clear.assert_called_once_with(1, mock_bot, 42)
