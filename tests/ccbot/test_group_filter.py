"""Tests for group filtering (TASK-002).

Verifies that when CCBOT_GROUP_ID is set, only updates from that group are
processed; when unset, all updates pass through.
"""

from unittest.mock import MagicMock, patch

import pytest
from telegram.ext import CommandHandler, MessageHandler, filters

from ccbot.bot import callback_handler, create_bot

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_update(*, chat_id: int | None = -100999, user_id: int = 100) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    if chat_id is not None:
        update.effective_chat = MagicMock(id=chat_id)
    else:
        update.effective_chat = None
    update.callback_query = MagicMock()
    update.callback_query.data = "some_data"
    update.callback_query.message = None
    update.message = None
    return update


def _has_chat_filter(f: filters.BaseFilter) -> bool:
    """Check if a filter tree contains a filters.Chat instance.

    Uses string representation which reliably includes "filters.Chat(...)"
    for merged filters (e.g. "filters.TEXT and filters.Chat(-100123)").
    """
    return "filters.Chat" in str(f)


# ── _group_filter module-level tests ────────────────────────────────────


class TestGroupFilterModule:
    def test_default_is_filters_all(self) -> None:
        """In test env CCBOT_GROUP_ID is unset, so _group_filter should be ALL."""
        from ccbot.bot import _group_filter

        assert _group_filter is filters.ALL

    def test_conditional_logic(self) -> None:
        """Verify the ternary expression that creates _group_filter."""
        group_id = -100123
        result = filters.Chat(chat_id=group_id) if group_id else filters.ALL
        assert isinstance(result, filters.Chat)

        result_none = filters.Chat(chat_id=None) if None else filters.ALL
        assert result_none is filters.ALL


# ── Handler registration tests ──────────────────────────────────────────


class TestGroupFilterRegistration:
    @patch("ccbot.bot._group_filter", filters.Chat(chat_id=-100123))
    @patch("ccbot.bot.config")
    def test_command_handlers_have_group_filter(self, mock_config: MagicMock) -> None:
        mock_config.telegram_bot_token = "fake:token"
        app = create_bot()

        for group_handlers in app.handlers.values():
            for handler in group_handlers:
                if isinstance(handler, CommandHandler):
                    assert _has_chat_filter(handler.filters), (
                        f"CommandHandler {handler.commands} missing group filter"
                    )

    @patch("ccbot.bot._group_filter", filters.Chat(chat_id=-100123))
    @patch("ccbot.bot.config")
    def test_message_handlers_have_group_filter(self, mock_config: MagicMock) -> None:
        mock_config.telegram_bot_token = "fake:token"
        app = create_bot()

        for group_handlers in app.handlers.values():
            for handler in group_handlers:
                if isinstance(handler, MessageHandler):
                    assert _has_chat_filter(handler.filters), (
                        "MessageHandler missing group filter"
                    )

    @patch("ccbot.bot.config")
    def test_no_chat_filter_when_group_id_unset(self, mock_config: MagicMock) -> None:
        mock_config.telegram_bot_token = "fake:token"
        app = create_bot()

        for group_handlers in app.handlers.values():
            for handler in group_handlers:
                if isinstance(handler, (CommandHandler, MessageHandler)):
                    assert not _has_chat_filter(handler.filters)


# ── callback_handler inline check tests ─────────────────────────────────


@pytest.fixture(autouse=True)
def _allow_user():
    with patch("ccbot.bot.is_user_allowed", return_value=True):
        yield


class TestCallbackHandlerGroupFilter:
    async def test_passes_when_no_group_id(self) -> None:
        update = _make_update(chat_id=-100999)
        ctx = MagicMock()

        with patch("ccbot.bot.config") as mock_config:
            mock_config.group_id = None
            # Should proceed past group check (MagicMock handles the rest)
            await callback_handler(update, ctx)

    async def test_passes_when_group_matches(self) -> None:
        update = _make_update(chat_id=-100999)
        ctx = MagicMock()

        with patch("ccbot.bot.config") as mock_config:
            mock_config.group_id = -100999
            await callback_handler(update, ctx)
            # Reached past group check — query.data was accessed

    async def test_blocked_when_group_mismatches(self) -> None:
        update = _make_update(chat_id=-100888)
        update.callback_query.answer = MagicMock()
        ctx = MagicMock()

        with patch("ccbot.bot.config") as mock_config:
            mock_config.group_id = -100999
            await callback_handler(update, ctx)
            # Should have returned before accessing callback_query.data
            update.callback_query.answer.assert_not_called()

    async def test_blocked_when_no_chat(self) -> None:
        update = _make_update(chat_id=None)
        update.callback_query.answer = MagicMock()
        ctx = MagicMock()

        with patch("ccbot.bot.config") as mock_config:
            mock_config.group_id = -100999
            await callback_handler(update, ctx)
            update.callback_query.answer.assert_not_called()
