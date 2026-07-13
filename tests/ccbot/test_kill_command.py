"""Tests for kill_command — kill tmux window and delete forum topic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(user_id: int = 1, thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.message_thread_id = thread_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = 100
    update.effective_chat.type = "supergroup"
    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


class TestKillCommand:
    @pytest.mark.asyncio
    async def test_kill_in_bound_topic_kills_window_and_deletes_topic(self):
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
            mock_sm.remove_session_map_entry = AsyncMock()
            mock_window = MagicMock()
            mock_window.window_id = "@5"
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.kill_window = AsyncMock()

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_called_once_with("@5")
            mock_sm.unbind_thread.assert_called_once_with(1, 42)
            mock_sm.purge_window.assert_called_once_with("@5")
            mock_sm.remove_session_map_entry.assert_awaited_once_with("@5")
            context.bot.delete_forum_topic.assert_called_once_with(
                chat_id=100, message_thread_id=42
            )
            mock_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_in_unbound_topic_still_deletes_topic(self):
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
            mock_sm.get_window_for_thread.return_value = None
            mock_tmux.kill_window = AsyncMock()

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_not_called()
            mock_sm.unbind_thread.assert_not_called()
            context.bot.delete_forum_topic.assert_called_once_with(
                chat_id=100, message_thread_id=42
            )

    @pytest.mark.asyncio
    async def test_kill_outside_topic_replies_with_error(self):
        update = _make_update()
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=None),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_tmux.kill_window = AsyncMock()

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_reply.assert_called_once()
            mock_tmux.kill_window.assert_not_called()
            mock_sm.unbind_thread.assert_not_called()
            context.bot.delete_forum_topic.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_falls_back_to_reply_when_delete_fails(self):
        update = _make_update()
        context = _make_context()
        context.bot.delete_forum_topic = AsyncMock(side_effect=RuntimeError("nope"))

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
            mock_sm.remove_session_map_entry = AsyncMock()
            mock_window = MagicMock()
            mock_window.window_id = "@5"
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.kill_window = AsyncMock()

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_called_once_with("@5")
            mock_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_rejects_unauthorized_user(self):
        update = _make_update()
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=False),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
        ):
            mock_tmux.kill_window = AsyncMock()

            from ccbot.bot import kill_command

            await kill_command(update, context)

            mock_tmux.kill_window.assert_not_called()
            mock_sm.unbind_thread.assert_not_called()
            context.bot.delete_forum_topic.assert_not_called()
