"""Tests for topic emoji status updates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, TelegramError

from conftest import make_mock_provider

from ccbot.handlers.topic_emoji import (
    DEBOUNCE_SECONDS,
    EMOJI_ACTIVE,
    EMOJI_DEAD,
    EMOJI_DONE,
    EMOJI_IDLE,
    clear_topic_emoji_state,
    reset_all_state,
    strip_emoji_prefix,
    update_topic_emoji,
)


@pytest.fixture(autouse=True)
def _reset():
    from ccbot.handlers.status_polling import reset_seen_status_state

    reset_all_state()
    reset_seen_status_state()
    yield
    reset_all_state()
    reset_seen_status_state()


class TestStripEmojiPrefix:
    @pytest.mark.parametrize(
        "emoji", [EMOJI_ACTIVE, EMOJI_IDLE, EMOJI_DONE, EMOJI_DEAD]
    )
    def test_strips_known_emoji(self, emoji: str) -> None:
        assert strip_emoji_prefix(f"{emoji} myproject") == "myproject"

    def test_no_prefix(self) -> None:
        assert strip_emoji_prefix("myproject") == "myproject"

    def test_double_prefix_strips_once(self) -> None:
        result = strip_emoji_prefix(f"{EMOJI_ACTIVE} {EMOJI_IDLE} myproject")
        assert result == f"{EMOJI_IDLE} myproject"


_PATCH_MONOTONIC = "ccbot.handlers.topic_emoji.time.monotonic"


async def _debounced_update(
    bot: AsyncMock,
    chat_id: int,
    thread_id: int,
    state: str,
    display_name: str,
) -> None:
    """Call update_topic_emoji twice with enough time gap to pass debounce."""
    with patch(_PATCH_MONOTONIC) as mock_monotonic:
        mock_monotonic.return_value = 0.0
        await update_topic_emoji(bot, chat_id, thread_id, state, display_name)
        mock_monotonic.return_value = DEBOUNCE_SECONDS + 0.1
        await update_topic_emoji(bot, chat_id, thread_id, state, display_name)


_STATE_EMOJI = [
    ("active", EMOJI_ACTIVE),
    ("idle", EMOJI_IDLE),
    ("done", EMOJI_DONE),
    ("dead", EMOJI_DEAD),
]


class TestUpdateTopicEmoji:
    async def test_first_call_starts_debounce(self) -> None:
        bot = AsyncMock()
        with patch(_PATCH_MONOTONIC, return_value=0.0):
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    @pytest.mark.parametrize("state,emoji", _STATE_EMOJI)
    async def test_sets_emoji_after_debounce(self, state: str, emoji: str) -> None:
        bot = AsyncMock()
        await _debounced_update(bot, -100, 42, state, "myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{emoji} myproject",
        )

    async def test_skips_same_state(self) -> None:
        bot = AsyncMock()
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_updates_on_state_change(self) -> None:
        bot = AsyncMock()
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await _debounced_update(bot, -100, 42, "idle", "myproject")
        bot.edit_forum_topic.assert_called_once()

    async def test_strips_existing_prefix(self) -> None:
        bot = AsyncMock()
        await _debounced_update(bot, -100, 42, "idle", f"{EMOJI_ACTIVE} myproject")
        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_IDLE} myproject",
        )

    async def test_rapid_toggling_suppressed(self) -> None:
        bot = AsyncMock()
        with patch(_PATCH_MONOTONIC) as mock_monotonic:
            # Rapid toggling for 10s — never stable long enough to pass debounce
            for i in range(10):
                mock_monotonic.return_value = float(i)
                state = "active" if i % 2 == 0 else "idle"
                await update_topic_emoji(bot, -100, 42, state, "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_stable_state_after_flickering(self) -> None:
        bot = AsyncMock()
        with patch(_PATCH_MONOTONIC) as mock_monotonic:
            # Rapid toggling for 4s — debounce never reached
            for i in range(4):
                mock_monotonic.return_value = float(i)
                state = "active" if i % 2 == 0 else "idle"
                await update_topic_emoji(bot, -100, 42, state, "myproject")
            bot.edit_forum_topic.assert_not_called()

            # Settle on "active" and wait past DEBOUNCE_SECONDS
            mock_monotonic.return_value = 4.0
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
            mock_monotonic.return_value = 4.0 + DEBOUNCE_SECONDS + 0.1
            await update_topic_emoji(bot, -100, 42, "active", "myproject")

        bot.edit_forum_topic.assert_called_once_with(
            chat_id=-100,
            message_thread_id=42,
            name=f"{EMOJI_ACTIVE} myproject",
        )

    async def test_permission_error_disables_chat(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = BadRequest("Not enough rights")
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await _debounced_update(bot, -100, 42, "idle", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_topic_not_modified_still_tracks(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = BadRequest("TOPIC_NOT_MODIFIED")
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_other_telegram_error_ignored(self) -> None:
        bot = AsyncMock()
        bot.edit_forum_topic.side_effect = TelegramError("Network error")
        await _debounced_update(bot, -100, 42, "active", "myproject")
        assert bot.edit_forum_topic.called

    async def test_invalid_state_ignored(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "unknown", "myproject")
        bot.edit_forum_topic.assert_not_called()

    async def test_debounce_not_reached(self) -> None:
        bot = AsyncMock()
        with patch(_PATCH_MONOTONIC) as mock_monotonic:
            mock_monotonic.return_value = 0.0
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
            mock_monotonic.return_value = DEBOUNCE_SECONDS - 0.1
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()


class TestClearTopicEmojiState:
    async def test_clear_allows_re_update(self) -> None:
        bot = AsyncMock()
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.reset_mock()
        clear_topic_emoji_state(-100, 42)
        await _debounced_update(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_called_once()

    async def test_clear_resets_pending_transition(self) -> None:
        bot = AsyncMock()
        with patch(_PATCH_MONOTONIC, return_value=0.0):
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
        clear_topic_emoji_state(-100, 42)
        with patch(_PATCH_MONOTONIC) as mock_monotonic:
            mock_monotonic.return_value = 100.0
            await update_topic_emoji(bot, -100, 42, "active", "myproject")
            bot.edit_forum_topic.assert_not_called()
            mock_monotonic.return_value = 100.0 + DEBOUNCE_SECONDS + 0.1
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
                "ccbot.handlers.status_polling.get_provider",
                return_value=make_mock_provider(has_status=True),
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_tm.find_window_by_id = AsyncMock(return_value=MagicMock())
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
                "ccbot.handlers.status_polling.get_provider",
                return_value=make_mock_provider(has_status=False),
            ),
        ):
            from ccbot.handlers.status_polling import (
                _has_seen_status,
                update_status_message,
            )

            _has_seen_status.add("@0")

            mock_window = MagicMock()
            mock_window.pane_current_command = "node"
            mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "myproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_emoji.assert_called_once_with(bot, -100, 42, "idle", "myproject")

    async def test_startup_window_shows_active_not_idle(self) -> None:
        """New window with no spinner yet should show active, not idle."""
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji") as mock_emoji,
            patch(
                "ccbot.handlers.status_polling.get_interactive_window",
                return_value=None,
            ),
            patch(
                "ccbot.handlers.status_polling.get_provider",
                return_value=make_mock_provider(has_status=False),
            ),
        ):
            from ccbot.handlers.status_polling import (
                _has_seen_status,
                update_status_message,
            )

            _has_seen_status.discard("@99")

            mock_window = MagicMock()
            mock_window.pane_current_command = "node"
            mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "newproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@99", thread_id=99)

            mock_emoji.assert_called_once_with(bot, -100, 99, "active", "newproject")

    async def test_done_when_shell_prompt(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji") as mock_emoji,
            patch(
                "ccbot.handlers.status_polling.get_interactive_window",
                return_value=None,
            ),
            patch(
                "ccbot.handlers.status_polling.get_provider",
                return_value=make_mock_provider(has_status=False),
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_window = MagicMock()
            mock_window.pane_current_command = "zsh"
            mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "myproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_emoji.assert_called_once_with(bot, -100, 42, "done", "myproject")

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
                "ccbot.handlers.status_polling.get_provider",
                return_value=make_mock_provider(has_status=True),
            ),
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_tm.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_tm.capture_pane = AsyncMock(return_value="some output")

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=None)

            mock_emoji.assert_not_called()
