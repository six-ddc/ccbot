"""Tests for status polling: shell detection, autoclose timers, rename sync."""

from unittest.mock import AsyncMock, patch

import pytest

from ccbot.handlers.status_polling import (
    _autoclose_timers,
    _check_autoclose_timers,
    _clear_autoclose_if_active,
    _start_autoclose_timer,
    clear_autoclose_timer,
    is_shell_prompt,
    reset_autoclose_state,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_autoclose_state()
    yield
    reset_autoclose_state()


class TestIsShellPrompt:
    def test_bash(self) -> None:
        assert is_shell_prompt("bash") is True

    def test_zsh(self) -> None:
        assert is_shell_prompt("zsh") is True

    def test_fish(self) -> None:
        assert is_shell_prompt("fish") is True

    def test_sh(self) -> None:
        assert is_shell_prompt("sh") is True

    def test_full_path(self) -> None:
        assert is_shell_prompt("/usr/bin/zsh") is True

    def test_with_whitespace(self) -> None:
        assert is_shell_prompt("  bash  ") is True

    def test_node_is_not_shell(self) -> None:
        assert is_shell_prompt("node") is False

    def test_claude_is_not_shell(self) -> None:
        assert is_shell_prompt("claude") is False

    def test_npx_is_not_shell(self) -> None:
        assert is_shell_prompt("npx") is False

    def test_empty_string(self) -> None:
        assert is_shell_prompt("") is False

    def test_dash(self) -> None:
        assert is_shell_prompt("dash") is True

    def test_ksh(self) -> None:
        assert is_shell_prompt("ksh") is True


class TestAutocloseTimers:
    def test_start_timer(self) -> None:
        _start_autoclose_timer(1, 42, "done", 100.0)
        assert _autoclose_timers[(1, 42)] == ("done", 100.0)

    def test_start_timer_preserves_existing_same_state(self) -> None:
        _start_autoclose_timer(1, 42, "done", 100.0)
        _start_autoclose_timer(1, 42, "done", 200.0)
        assert _autoclose_timers[(1, 42)] == ("done", 100.0)

    def test_start_timer_resets_on_state_change(self) -> None:
        _start_autoclose_timer(1, 42, "done", 100.0)
        _start_autoclose_timer(1, 42, "dead", 200.0)
        assert _autoclose_timers[(1, 42)] == ("dead", 200.0)

    def test_clear_on_active(self) -> None:
        _start_autoclose_timer(1, 42, "done", 100.0)
        _clear_autoclose_if_active(1, 42)
        assert (1, 42) not in _autoclose_timers

    def test_clear_timer(self) -> None:
        _start_autoclose_timer(1, 42, "done", 100.0)
        clear_autoclose_timer(1, 42)
        assert (1, 42) not in _autoclose_timers

    def test_clear_nonexistent_is_noop(self) -> None:
        clear_autoclose_timer(1, 42)

    async def test_check_done_expired(self) -> None:
        _start_autoclose_timer(1, 42, "done", 0.0)
        bot = AsyncMock()
        with (
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.time") as mock_time,
        ):
            mock_config.autoclose_done_minutes = 30
            mock_config.autoclose_dead_minutes = 10
            mock_time.monotonic.return_value = 30 * 60 + 1
            mock_sm.resolve_chat_id.return_value = -100
            await _check_autoclose_timers(bot)
        bot.close_forum_topic.assert_called_once_with(
            chat_id=-100, message_thread_id=42
        )
        assert (1, 42) not in _autoclose_timers

    async def test_check_dead_expired(self) -> None:
        _start_autoclose_timer(1, 42, "dead", 0.0)
        bot = AsyncMock()
        with (
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.time") as mock_time,
        ):
            mock_config.autoclose_done_minutes = 30
            mock_config.autoclose_dead_minutes = 10
            mock_time.monotonic.return_value = 10 * 60 + 1
            mock_sm.resolve_chat_id.return_value = -100
            await _check_autoclose_timers(bot)
        bot.close_forum_topic.assert_called_once_with(
            chat_id=-100, message_thread_id=42
        )

    async def test_check_not_expired_yet(self) -> None:
        _start_autoclose_timer(1, 42, "done", 0.0)
        bot = AsyncMock()
        with (
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch("ccbot.handlers.status_polling.time") as mock_time,
        ):
            mock_config.autoclose_done_minutes = 30
            mock_config.autoclose_dead_minutes = 10
            mock_time.monotonic.return_value = 29 * 60
            await _check_autoclose_timers(bot)
        bot.close_forum_topic.assert_not_called()
        assert (1, 42) in _autoclose_timers

    async def test_check_disabled_when_zero(self) -> None:
        _start_autoclose_timer(1, 42, "done", 0.0)
        bot = AsyncMock()
        with (
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch("ccbot.handlers.status_polling.time") as mock_time,
        ):
            mock_config.autoclose_done_minutes = 0
            mock_config.autoclose_dead_minutes = 0
            mock_time.monotonic.return_value = 999999
            await _check_autoclose_timers(bot)
        bot.close_forum_topic.assert_not_called()

    async def test_check_telegram_error_handled(self) -> None:
        from telegram.error import TelegramError

        _start_autoclose_timer(1, 42, "done", 0.0)
        bot = AsyncMock()
        bot.close_forum_topic.side_effect = TelegramError("fail")
        with (
            patch("ccbot.handlers.status_polling.config") as mock_config,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.time") as mock_time,
        ):
            mock_config.autoclose_done_minutes = 30
            mock_config.autoclose_dead_minutes = 10
            mock_time.monotonic.return_value = 30 * 60 + 1
            mock_sm.resolve_chat_id.return_value = -100
            await _check_autoclose_timers(bot)
        assert (1, 42) not in _autoclose_timers


class TestWindowRenameSync:
    async def test_rename_detected_calls_rename_topic(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji"),
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
            patch("ccbot.handlers.status_polling.rename_topic") as mock_rename,
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_window = AsyncMock()
            mock_window.window_id = "@0"
            mock_window.window_name = "new-name"
            mock_window.pane_current_command = "node"
            mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "old-name"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_sm.set_display_name.assert_called_once_with("@0", "new-name")
            mock_rename.assert_called_once_with(bot, -100, 42, "new-name")

    async def test_no_rename_when_names_match(self) -> None:
        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tm,
            patch("ccbot.handlers.status_polling.session_manager") as mock_sm,
            patch("ccbot.handlers.status_polling.update_topic_emoji"),
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
            patch("ccbot.handlers.status_polling.rename_topic") as mock_rename,
        ):
            from ccbot.handlers.status_polling import update_status_message

            mock_window = AsyncMock()
            mock_window.window_id = "@0"
            mock_window.window_name = "myproject"
            mock_window.pane_current_command = "node"
            mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tm.capture_pane = AsyncMock(return_value="some output")
            mock_sm.resolve_chat_id.return_value = -100
            mock_sm.get_display_name.return_value = "myproject"

            bot = AsyncMock()
            await update_status_message(bot, 1, "@0", thread_id=42)

            mock_sm.set_display_name.assert_not_called()
            mock_rename.assert_not_called()
