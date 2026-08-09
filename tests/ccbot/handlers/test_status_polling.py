"""Tests for status_polling — Settings UI detection via the poller path.

Simulates the user workflow: /model is sent to Claude Code, the Settings
model picker renders in the terminal, and the status poller detects it
on its next 1s tick.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.status_polling import update_status_message


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 999
    bot.send_message.return_value = sent_msg
    return bot


@pytest.fixture
def _clear_interactive_state():
    """Ensure interactive state is clean before and after each test."""
    from ccbot.handlers.interactive_ui import _interactive_mode, _interactive_msgs

    _interactive_mode.clear()
    _interactive_msgs.clear()
    yield
    _interactive_mode.clear()
    _interactive_msgs.clear()


@pytest.mark.usefixtures("_clear_interactive_state")
class TestStatusPollerSettingsDetection:
    """Simulate the status poller detecting a Settings UI in the terminal.

    This is the actual code path for /model: no JSONL tool_use entry exists,
    so the status poller (update_status_message) is the only detector.
    """

    @pytest.mark.asyncio
    async def test_settings_ui_detected_and_keyboard_sent(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """Poller captures Settings pane → handle_interactive_ui sends keyboard."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_handle_ui.return_value = True

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            mock_handle_ui.assert_called_once_with(mock_bot, 1, window_id, 42)

    @pytest.mark.asyncio
    async def test_normal_pane_no_interactive_ui(self, mock_bot: AsyncMock):
        """Normal pane text → no handle_interactive_ui call, just status check."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id
        normal_pane = (
            "some output\n"
            "✻ Reading file\n"
            "──────────────────────────────────────\n"
            "❯ \n"
            "──────────────────────────────────────\n"
            "  [Opus 4.6] Context: 50%\n"
        )

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch(
                "ccbot.handlers.status_polling.enqueue_status_update",
                new_callable=AsyncMock,
            ),
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=normal_pane)

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            mock_handle_ui.assert_not_called()

    @pytest.mark.asyncio
    async def test_settings_ui_end_to_end_sends_telegram_keyboard(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """Full end-to-end: poller → is_interactive_ui → handle_interactive_ui
        → bot.send_message with keyboard.

        Uses real handle_interactive_ui (not mocked) to verify the full path.
        """
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux_poll,
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux_ui,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux_poll.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux_poll.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_tmux_ui.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux_ui.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_sm.resolve_chat_id.return_value = 100

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            # Verify bot.send_message was called with keyboard
            mock_bot.send_message.assert_called_once()
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == 100
            assert call_kwargs["message_thread_id"] == 42
            keyboard = call_kwargs["reply_markup"]
            assert keyboard is not None
            # Verify the message text contains model picker content
            assert "Select model" in call_kwargs["text"]


@pytest.mark.usefixtures("_clear_interactive_state")
class TestStatusPollerAutoConfirm:
    """CCBOT_TOPIC_AUTO_CONFIRM: Permission Prompt / Bash-approval UIs get
    Enter sent directly instead of being posted to Telegram."""

    @pytest.mark.asyncio
    async def test_permission_prompt_auto_confirmed_sends_enter_no_card(
        self, mock_bot: AsyncMock, sample_pane_permission: str
    ):
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_permission)
            mock_tmux.send_keys = AsyncMock()
            mock_config.is_topic_auto_confirm.return_value = True

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        mock_tmux.send_keys.assert_called_once_with(
            window_id, "Enter", enter=False, literal=False
        )
        mock_handle_ui.assert_not_called()
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_permission_prompt_not_auto_confirmed_shows_card(
        self, mock_bot: AsyncMock, sample_pane_permission: str
    ):
        """Default (topic not in CCBOT_TOPIC_AUTO_CONFIRM) — normal card flow."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_permission)
            mock_handle_ui.return_value = True

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        mock_handle_ui.assert_called_once_with(mock_bot, 1, window_id, 42)

    @pytest.mark.asyncio
    async def test_auto_confirm_does_not_apply_to_settings_ui(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """AUTO_CONFIRM_UI_NAMES excludes Settings — always shown, never auto-sent."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_tmux.send_keys = AsyncMock()
            mock_config.is_topic_auto_confirm.return_value = True

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        mock_tmux.send_keys.assert_not_called()
        mock_handle_ui.assert_called_once_with(mock_bot, 1, window_id, 42)
