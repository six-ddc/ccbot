"""Tests for message_queue — secondary-message rolling delete-on-next behavior.

Covers: _can_merge_tasks refusing to fold secondary and final-answer tasks
together, _process_content_task deleting the previous secondary message
when the next one arrives, the tool_use/tool_result edit-in-place path
being left untouched by that deletion, and clear_secondary_msg_info.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import RetryAfter

from ccbot.handlers.message_queue import (
    MessageTask,
    _can_merge_tasks,
    _process_content_task,
    _secondary_msg_info,
    _tool_msg_ids,
    clear_secondary_msg_info,
)

USER_ID = 1
THREAD_ID = 42
CHAT_ID = 555


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    sent = MagicMock()
    sent.message_id = 100
    bot.send_message.return_value = sent
    return bot


@pytest.fixture(autouse=True)
def _clear_queue_state():
    _secondary_msg_info.clear()
    _tool_msg_ids.clear()
    yield
    _secondary_msg_info.clear()
    _tool_msg_ids.clear()


def _task(
    content_type: str,
    is_secondary: bool,
    parts: list[str] | None = None,
    tool_use_id: str | None = None,
) -> MessageTask:
    return MessageTask(
        task_type="content",
        window_id="@1",
        parts=parts or ["hello"],
        content_type=content_type,
        thread_id=THREAD_ID,
        is_secondary=is_secondary,
        tool_use_id=tool_use_id,
    )


class TestCanMergeSecondary:
    def test_secondary_and_final_do_not_merge(self) -> None:
        base = _task("thinking", is_secondary=True)
        candidate = _task("text", is_secondary=False)
        assert _can_merge_tasks(base, candidate) is False

    def test_same_secondary_merges(self) -> None:
        base = _task("thinking", is_secondary=True)
        candidate = _task("thinking", is_secondary=True)
        assert _can_merge_tasks(base, candidate) is True


@pytest.mark.asyncio
class TestSecondaryMessageRollingDelete:
    async def test_secondary_message_is_tracked(self, mock_bot: AsyncMock) -> None:
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(mock_bot, USER_ID, _task("thinking", True))

        assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (100, "@1")
        mock_bot.delete_message.assert_not_called()

    async def test_next_secondary_deletes_previous(self, mock_bot: AsyncMock) -> None:
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(mock_bot, USER_ID, _task("thinking", True))

            second_sent = MagicMock()
            second_sent.message_id = 101
            mock_bot.send_message.return_value = second_sent
            await _process_content_task(mock_bot, USER_ID, _task("tool_result", True))

        mock_bot.delete_message.assert_called_once_with(chat_id=CHAT_ID, message_id=100)
        assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (101, "@1")

    async def test_final_answer_deletes_previous_but_is_not_tracked(
        self, mock_bot: AsyncMock
    ) -> None:
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(mock_bot, USER_ID, _task("thinking", True))

            final_sent = MagicMock()
            final_sent.message_id = 102
            mock_bot.send_message.return_value = final_sent
            await _process_content_task(
                mock_bot, USER_ID, _task("text", False, parts=["Fixed it"])
            )

        mock_bot.delete_message.assert_called_once_with(chat_id=CHAT_ID, message_id=100)
        assert (USER_ID, THREAD_ID) not in _secondary_msg_info

    async def test_tool_result_edit_in_place_does_not_delete(
        self, mock_bot: AsyncMock
    ) -> None:
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(
                mock_bot, USER_ID, _task("tool_use", True, tool_use_id="tu1")
            )
            assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (100, "@1")

            await _process_content_task(
                mock_bot, USER_ID, _task("tool_result", True, tool_use_id="tu1")
            )

        mock_bot.delete_message.assert_not_called()
        mock_bot.edit_message_text.assert_called_once()
        # Same message_id throughout — edited in place, tracking stays valid.
        assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (100, "@1")

    async def test_retry_after_on_delete_keeps_tracking_for_retry(
        self, mock_bot: AsyncMock
    ) -> None:
        """A flood-controlled delete must not orphan the tracked message.

        Regression test: popping _secondary_msg_info before the delete call
        succeeded meant a RetryAfter (bare `except Exception: pass`) silently
        dropped the delete and forgot the message — it was never cleaned up
        by a later retry. The entry must survive so a retried call can still
        find and delete it.
        """
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(mock_bot, USER_ID, _task("thinking", True))
            assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (100, "@1")

            mock_bot.delete_message.side_effect = RetryAfter(5)
            with pytest.raises(RetryAfter):
                await _process_content_task(mock_bot, USER_ID, _task("tool_result", True))

        # Still tracked (not silently dropped) so a retry can delete it.
        assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (100, "@1")

    async def test_non_retryable_delete_failure_still_clears_tracking(
        self, mock_bot: AsyncMock
    ) -> None:
        with (
            patch("ccbot.handlers.message_queue.session_manager") as mock_sm,
            patch("ccbot.handlers.message_queue.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = CHAT_ID
            mock_tmux.find_window_by_id = AsyncMock(return_value=None)

            await _process_content_task(mock_bot, USER_ID, _task("thinking", True))

            mock_bot.delete_message.side_effect = Exception("message to delete not found")
            second_sent = MagicMock()
            second_sent.message_id = 101
            mock_bot.send_message.return_value = second_sent
            await _process_content_task(mock_bot, USER_ID, _task("tool_result", True))

        # Gives up cleanly rather than retrying forever, and still tracks the new message.
        assert _secondary_msg_info[(USER_ID, THREAD_ID)] == (101, "@1")


class TestClearSecondaryMsgInfo:
    def test_clears_tracked_entry(self) -> None:
        _secondary_msg_info[(USER_ID, THREAD_ID)] = (100, "@1")
        clear_secondary_msg_info(USER_ID, THREAD_ID)
        assert (USER_ID, THREAD_ID) not in _secondary_msg_info
