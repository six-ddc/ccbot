"""Tests for /kill bot command handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(user_id: int = 1, thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.message_thread_id = thread_id
    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


class TestKillCommand:
    @pytest.mark.asyncio
    async def test_kills_window_and_unbinds_topic(self):
        update = _make_update()
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id="@5")
            )
            mock_tmux.kill_window = AsyncMock(return_value=True)

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_called_once_with("@5")
            mock_sm.unbind_thread.assert_called_once_with(1, 42)
            mock_reply.assert_called()

    @pytest.mark.asyncio
    async def test_no_binding_returns_error(self):
        update = _make_update()
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = None

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_reply.assert_called_once()
            assert "No session bound" in mock_reply.call_args.args[1]

    @pytest.mark.asyncio
    async def test_window_already_gone_still_unbinds(self):
        update = _make_update()
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_not_called()
            mock_sm.unbind_thread.assert_called_once_with(1, 42)
