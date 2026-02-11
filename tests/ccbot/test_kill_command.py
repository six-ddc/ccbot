"""Tests for /kill command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import kill_command


def _make_update(user_id: int, thread_id: int | None) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.message = AsyncMock()
    msg = update.message
    msg.message_thread_id = thread_id
    if thread_id and thread_id != 1:
        msg.message_thread_id = thread_id
    else:
        msg.message_thread_id = None
    return update


def _make_context(bot: AsyncMock | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot = bot or AsyncMock()
    return ctx


@pytest.fixture(autouse=True)
def _allow_user():
    with patch("ccbot.bot.is_user_allowed", return_value=True):
        yield


class TestKillCommand:
    @pytest.mark.parametrize(
        "thread_id",
        [
            pytest.param(None, id="no-thread"),
            pytest.param(42, id="unbound-thread"),
        ],
    )
    async def test_kill_unbound_topic(self, thread_id: int | None) -> None:
        update = _make_update(100, thread_id)
        ctx = _make_context()

        with patch("ccbot.bot.session_manager") as mock_sm:
            mock_sm.resolve_window_for_thread.return_value = None
            await kill_command(update, ctx)

        update.message.reply_text.assert_called()

    async def test_kill_bound_topic(self) -> None:
        update = _make_update(100, 42)
        ctx = _make_context()

        with (
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tm,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "myproj"
            mock_sm.iter_thread_bindings.return_value = [
                (100, 42, "@5"),
                (200, 99, "@5"),
                (300, 10, "@9"),
            ]
            mock_tm.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id="@5")
            )
            mock_tm.kill_window = AsyncMock(return_value=True)

            await kill_command(update, ctx)

        mock_tm.kill_window.assert_called_once_with("@5")
        assert mock_sm.unbind_thread.call_count == 2
        mock_sm.unbind_thread.assert_any_call(100, 42)
        mock_sm.unbind_thread.assert_any_call(200, 99)
        assert mock_clear.call_count == 2

    async def test_kill_window_already_gone(self) -> None:
        update = _make_update(100, 42)
        ctx = _make_context()

        with (
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tm,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "myproj"
            mock_sm.iter_thread_bindings.return_value = [(100, 42, "@5")]
            mock_tm.find_window_by_id = AsyncMock(return_value=None)
            mock_tm.kill_window = AsyncMock()

            await kill_command(update, ctx)

        mock_tm.kill_window.assert_not_called()
        mock_sm.unbind_thread.assert_called_once_with(100, 42)

    async def test_unbinds_all_users(self) -> None:
        update = _make_update(100, 42)
        ctx = _make_context()

        with (
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tm,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "shared"
            mock_sm.iter_thread_bindings.return_value = [
                (100, 42, "@5"),
                (200, 50, "@5"),
                (300, 60, "@5"),
            ]
            mock_tm.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id="@5")
            )
            mock_tm.kill_window = AsyncMock(return_value=True)

            await kill_command(update, ctx)

        assert mock_sm.unbind_thread.call_count == 3
        assert mock_clear.call_count == 3
