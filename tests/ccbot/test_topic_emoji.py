"""Tests for topic emoji status updates."""

from unittest.mock import AsyncMock, patch

import pytest
from telegram.error import BadRequest, TelegramError

from ccbot.handlers.topic_emoji import (
    EMOJI_ACTIVE,
    EMOJI_DEAD,
    EMOJI_IDLE,
    clear_topic_emoji_state,
    reset_all_state,
    strip_emoji_prefix,
    update_topic_emoji,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_all_state()
    yield
    reset_all_state()


class TestStripEmojiPrefix:
    def test_strips_active(self) -> None:
        assert strip_emoji_prefix(f"{EMOJI_ACTIVE} myproject") == "myproject"

    def test_strips_idle(self) -> None:
        assert strip_emoji_prefix(f"{EMOJI_IDLE} myproject") == "myproject"

    def test_strips_dead(self) -> None:
        assert strip_emoji_prefix(f"{EMOJI_DEAD} myproject") == "myproject"

    def test_no_prefix(self) -> None:
        assert strip_emoji_prefix("myproject") == "myproject"

    def test_double_prefix_strips_once(self) -> None:
        result = strip_emoji_prefix(f"{EMOJI_ACTIVE} {EMOJI_IDLE} myproject")
        assert result == f"{EMOJI_IDLE} myproject"


class TestUpdateTopicEmoji:
    async def test_sets_active_emoji(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_ACTIVE} myproject",
        )

    async def test_sets_idle_emoji(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "idle", "myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_IDLE} myproject",
        )

    async def test_sets_dead_emoji(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "dead", "myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_DEAD} myproject",
        )

    async def test_skips_same_state(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_updates_on_state_change(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "idle", "myproject")
        bot.edit_forum_topic.assert_called_once()

    async def test_strips_existing_prefix(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "idle", f"{EMOJI_ACTIVE} myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_IDLE} myproject",
        )

    async def test_permission_error_disables_chat(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = BadRequest("Not enough rights")
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "idle", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_topic_not_modified_still_tracks(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = BadRequest("TOPIC_NOT_MODIFIED")
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_other_telegram_error_ignored(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = TelegramError("Network error")
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        assert bot.edit_forum_topic.called

    async def test_invalid_state_ignored(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "unknown", "myproject")
        bot.edit_forum_topic.assert_not_called()


class TestClearTopicEmojiState:
    async def test_clear_allows_re_update(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        clear_topic_emoji_state(-100, 42)
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_called_once()


class TestStatusPollingIntegration:
    async def test_active_window_with_status_updates_emoji(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji") as mock_emoji,
            patch("ccbot.handlers.status_polling.enqueue_status_update"),
            patch(
                "ccbot.handlers.status_polling.get_interactive_window",
                return_value=None,
            ),
            patch(
                "ccbot.handlers.status_polling.is_interactive_ui",
                return_value=False,
            ),
            patch(
                "ccbot.handlers.status_polling.parse_status_line",
                return_value="Working...",
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_tm.find_window_by_id = AsyncMock(return_value=AsyncMock())
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "myproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_emoji.assert_called_once_with(bot, -100, 42, "active", "myproject")

    async def test_idle_window_without_status_updates_emoji(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji") as mock_emoji,
            patch(
                "ccbot.handlers.status_polling.get_interactive_window",
                return_value=None,
            ),
            patch(
                "ccbot.handlers.status_polling.is_interactive_ui",
                return_value=False,
            ),
            patch(
                "ccbot.handlers.status_polling.parse_status_line",
                return_value=None,
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_tm.find_window_by_id = AsyncMock(return_value=AsyncMock())
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "myproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_emoji.assert_called_once_with(bot, -100, 42, "idle", "myproject")

    async def test_no_thread_id_skips_emoji(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager"),
            patch("ccbot.handlers.status_polling.update_topic_emoji") as mock_emoji,
            patch("ccbot.handlers.status_polling.enqueue_status_update"),
            patch(
                "ccbot.handlers.status_polling.get_interactive_window",
                return_value=None,
            ),
            patch(
                "ccbot.handlers.status_polling.is_interactive_ui",
                return_value=False,
            ),
            patch(
                "ccbot.handlers.status_polling.parse_status_line",
                return_value="Working...",
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_tm.find_window_by_id = AsyncMock(return_value=AsyncMock())
            mock_tm.capture_pane = AsyncMock(return_value="some output")

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=None)

            mock_emoji.assert_not_called()
