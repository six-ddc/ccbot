"""Tests for interactive_ui — handle_interactive_ui and keyboard layout."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

from ccbot.handlers.interactive_ui import (
    _build_interactive_keyboard,
    get_interactive_choice_state,
    handle_interactive_ui,
)
from ccbot.handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_RIGHT,
    CB_ASK_SELECT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)


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
    from ccbot.handlers.interactive_ui import (
        _interactive_choices,
        _interactive_last_render,
        _interactive_mode,
        _interactive_msgs,
    )

    _interactive_mode.clear()
    _interactive_msgs.clear()
    _interactive_choices.clear()
    _interactive_last_render.clear()
    yield
    _interactive_mode.clear()
    _interactive_msgs.clear()
    _interactive_choices.clear()
    _interactive_last_render.clear()


@pytest.mark.usefixtures("_clear_interactive_state")
class TestHandleInteractiveUI:
    @pytest.mark.asyncio
    async def test_handle_settings_ui_sends_keyboard(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """handle_interactive_ui captures Settings pane, sends message with keyboard."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_sm.resolve_chat_id.return_value = 100

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is True
        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args
        assert call_kwargs.kwargs["chat_id"] == 100
        assert call_kwargs.kwargs["message_thread_id"] == 42
        assert call_kwargs.kwargs["reply_markup"] is not None
        assert get_interactive_choice_state(1, 42) == (2, 3)

    @pytest.mark.asyncio
    async def test_handle_no_ui_returns_false(self, mock_bot: AsyncMock):
        """Returns False when no interactive UI detected in pane."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager"),
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value="$ echo hello\nhello\n$\n")

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is False
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_command_approval_with_wedge_cursor(
        self, mock_bot: AsyncMock, sample_pane_command_approval: str
    ):
        """Codex approval menu with `›` cursor should be sent with numeric state."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_command_approval)
            mock_sm.resolve_chat_id.return_value = 100

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is True
        assert get_interactive_choice_state(1, 42) == (1, 3)
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_identical_edit_does_not_send_new_message(
        self, mock_bot: AsyncMock, sample_pane_command_approval: str
    ):
        """No-op edit ('message is not modified') must not create duplicate messages."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        from ccbot.handlers import interactive_ui as module

        module._interactive_msgs[(1, 42)] = 777
        mock_bot.edit_message_text.side_effect = BadRequest("Message is not modified")

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_command_approval)
            mock_sm.resolve_chat_id.return_value = 100

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is True
        assert module._interactive_msgs[(1, 42)] == 777
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_identical_payload_without_msg_id_is_rate_limited(
        self, mock_bot: AsyncMock, sample_pane_command_approval: str
    ):
        """When msg_id is absent, same payload should not be resent every poll tick."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        from ccbot.handlers import interactive_ui as module

        payload = (
            module.extract_interactive_content(sample_pane_command_approval).content
        )
        module._interactive_last_render[(1, 42)] = (payload, module.time.monotonic())

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_command_approval)
            mock_sm.resolve_chat_id.return_value = 100

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is True
        mock_bot.send_message.assert_not_called()


class TestKeyboardLayoutForSettings:
    def test_settings_keyboard_includes_all_nav_keys(self):
        """Settings keyboard includes Tab, arrows (not vertical_only), Space, Esc, Enter."""
        keyboard = _build_interactive_keyboard("@5", ui_name="Settings", option_count=3)
        # Flatten all callback data values
        all_cb_data = [
            btn.callback_data for row in keyboard.inline_keyboard for btn in row
        ]
        assert any(CB_ASK_TAB in d for d in all_cb_data if d)
        assert any(CB_ASK_SPACE in d for d in all_cb_data if d)
        assert any(CB_ASK_UP in d for d in all_cb_data if d)
        assert any(CB_ASK_DOWN in d for d in all_cb_data if d)
        assert any(CB_ASK_LEFT in d for d in all_cb_data if d)
        assert any(CB_ASK_RIGHT in d for d in all_cb_data if d)
        assert any(CB_ASK_ESC in d for d in all_cb_data if d)
        assert any(CB_ASK_ENTER in d for d in all_cb_data if d)
        assert any(CB_ASK_SELECT in d for d in all_cb_data if d)
