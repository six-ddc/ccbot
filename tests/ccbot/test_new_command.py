"""Tests for /new command (renamed from /start)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import create_bot, new_command


def _make_update(user_id: int, thread_id: int | None = None) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.message = AsyncMock()
    update.message.message_thread_id = thread_id
    return update


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    return ctx


@pytest.fixture(autouse=True)
def _allow_user():
    with patch("ccbot.bot.is_user_allowed", return_value=True):
        yield


class TestNewCommand:
    async def test_sends_welcome(self) -> None:
        update = _make_update(100)
        ctx = _make_context()

        await new_command(update, ctx)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Claude Code Monitor" in text

    async def test_clears_browse_state(self) -> None:
        update = _make_update(100)
        ctx = _make_context()

        with patch("ccbot.bot.clear_browse_state") as mock_clear:
            await new_command(update, ctx)
            mock_clear.assert_called_once_with(ctx.user_data)

    async def test_unauthorized_user(self) -> None:
        update = _make_update(999)
        ctx = _make_context()

        with patch("ccbot.bot.is_user_allowed", return_value=False):
            await new_command(update, ctx)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "not authorized" in text

    async def test_no_message(self) -> None:
        update = _make_update(100)
        update.message = None
        ctx = _make_context()

        await new_command(update, ctx)

    async def test_no_user(self) -> None:
        update = _make_update(100)
        update.effective_user = None
        ctx = _make_context()

        await new_command(update, ctx)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "not authorized" in text


class TestCommandRegistration:
    @patch("ccbot.bot.config")
    def test_new_and_start_both_registered(self, mock_config: MagicMock) -> None:
        mock_config.telegram_bot_token = "fake:token"
        app = create_bot()

        handler_commands: list[str] = []
        for group_handlers in app.handlers.values():
            for handler in group_handlers:
                if hasattr(handler, "commands"):
                    handler_commands.extend(handler.commands)

        assert "new" in handler_commands
        assert "start" in handler_commands

    @patch("ccbot.bot.config")
    def test_start_alias_uses_new_command(self, mock_config: MagicMock) -> None:
        mock_config.telegram_bot_token = "fake:token"
        app = create_bot()

        new_handler = None
        start_handler = None
        for group_handlers in app.handlers.values():
            for handler in group_handlers:
                if hasattr(handler, "commands"):
                    if "new" in handler.commands:
                        new_handler = handler
                    if "start" in handler.commands:
                        start_handler = handler

        assert new_handler is not None
        assert start_handler is not None
        assert new_handler.callback is start_handler.callback
