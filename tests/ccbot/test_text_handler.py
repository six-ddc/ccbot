"""Tests for text_handler step functions (TASK-024)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.text_handler import (
    _check_ui_guards,
    _forward_message,
    _handle_dead_window,
    _handle_unbound_topic,
)
from ccbot.handlers.directory_browser import (
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_WINDOW,
)

_TH = "ccbot.handlers.text_handler"


class TestCheckUiGuards:
    async def test_window_picker_same_thread_blocks(self) -> None:
        message = AsyncMock()
        user_data = {STATE_KEY: STATE_SELECTING_WINDOW, "_pending_thread_id": 42}

        with patch(f"{_TH}.safe_reply", new_callable=AsyncMock) as mock_reply:
            result = await _check_ui_guards(user_data, 42, message)

        assert result is True
        mock_reply.assert_called_once()
        assert "window picker" in mock_reply.call_args.args[1]

    async def test_window_picker_stale_thread_clears(self) -> None:
        message = AsyncMock()
        user_data = {
            STATE_KEY: STATE_SELECTING_WINDOW,
            "_pending_thread_id": 99,
            "_pending_thread_text": "old",
        }

        result = await _check_ui_guards(user_data, 42, message)

        assert result is False
        assert STATE_KEY not in user_data
        assert "_pending_thread_id" not in user_data
        assert "_pending_thread_text" not in user_data

    async def test_directory_browser_same_thread_blocks(self) -> None:
        message = AsyncMock()
        user_data = {STATE_KEY: STATE_BROWSING_DIRECTORY, "_pending_thread_id": 42}

        with patch(f"{_TH}.safe_reply", new_callable=AsyncMock) as mock_reply:
            result = await _check_ui_guards(user_data, 42, message)

        assert result is True
        mock_reply.assert_called_once()
        assert "directory browser" in mock_reply.call_args.args[1]

    async def test_directory_browser_stale_thread_clears(self) -> None:
        message = AsyncMock()
        user_data = {
            STATE_KEY: STATE_BROWSING_DIRECTORY,
            "_pending_thread_id": 99,
            "_pending_thread_text": "old",
        }

        result = await _check_ui_guards(user_data, 42, message)

        assert result is False
        assert STATE_KEY not in user_data
        assert "_pending_thread_id" not in user_data

    async def test_no_state_continues(self) -> None:
        message = AsyncMock()
        user_data: dict = {}

        result = await _check_ui_guards(user_data, 42, message)

        assert result is False

    async def test_none_user_data_continues(self) -> None:
        message = AsyncMock()

        result = await _check_ui_guards(None, 42, message)

        assert result is False


class TestHandleUnboundTopic:
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_bound_topic_returns_false(
        self, mock_sm: MagicMock, _mock_tm: MagicMock
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        message = AsyncMock()

        result = await _handle_unbound_topic(100, 42, "hello", {}, message)

        assert result is False

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_window_picker")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_shows_window_picker(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_picker: MagicMock,
        mock_reply: AsyncMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_sm.iter_thread_bindings.return_value = []
        w = MagicMock(window_id="@5", window_name="proj", cwd="/tmp")
        mock_tm.list_windows = AsyncMock(return_value=[w])
        mock_picker.return_value = ("Pick:", MagicMock(), ["@5"])

        user_data: dict = {}
        message = AsyncMock()

        result = await _handle_unbound_topic(100, 42, "hello", user_data, message)

        assert result is True
        mock_picker.assert_called_once()
        mock_reply.assert_called_once()
        assert user_data[STATE_KEY] == STATE_SELECTING_WINDOW
        assert user_data["_pending_thread_text"] == "hello"

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_directory_browser")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_shows_directory_browser(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_browser: MagicMock,
        mock_reply: AsyncMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_sm.iter_thread_bindings.return_value = []
        mock_tm.list_windows = AsyncMock(return_value=[])
        mock_browser.return_value = ("Browse:", MagicMock(), [])

        user_data: dict = {}
        message = AsyncMock()

        result = await _handle_unbound_topic(100, 42, "hello", user_data, message)

        assert result is True
        mock_browser.assert_called_once()
        assert user_data[STATE_KEY] == STATE_BROWSING_DIRECTORY

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_window_picker")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_stores_pending_state(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_picker: MagicMock,
        _mock_reply: AsyncMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = None
        mock_sm.iter_thread_bindings.return_value = []
        w = MagicMock(window_id="@5", window_name="proj", cwd="/tmp")
        mock_tm.list_windows = AsyncMock(return_value=[w])
        mock_picker.return_value = ("Pick:", MagicMock(), ["@5"])

        user_data: dict = {}
        message = AsyncMock()

        await _handle_unbound_topic(100, 42, "my text", user_data, message)

        assert user_data["_pending_thread_id"] == 42
        assert user_data["_pending_thread_text"] == "my text"


class TestHandleDeadWindow:
    @patch(f"{_TH}.tmux_manager")
    async def test_alive_window_returns_false(self, mock_tm: MagicMock) -> None:
        mock_tm.find_window_by_id = AsyncMock(return_value=MagicMock())
        message = AsyncMock()

        result = await _handle_dead_window("@0", 100, 42, "hello", {}, message)

        assert result is False

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_recovery_keyboard")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_shows_recovery_ui(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_kb: MagicMock,
        mock_reply: AsyncMock,
    ) -> None:
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        mock_sm.get_display_name.return_value = "project"
        ws = MagicMock()
        ws.cwd = "/tmp/project"
        mock_sm.get_window_state.return_value = ws
        mock_kb.return_value = MagicMock()

        user_data: dict = {}
        message = AsyncMock()

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            result = await _handle_dead_window(
                "@0", 100, 42, "hello", user_data, message
            )

        assert result is True
        mock_reply.assert_called_once()
        assert "no longer running" in mock_reply.call_args.args[1]
        assert user_data["_recovery_window_id"] == "@0"

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_directory_browser")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_falls_back_to_browser_no_cwd(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_browser: MagicMock,
        _mock_reply: AsyncMock,
    ) -> None:
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        mock_sm.get_display_name.return_value = "project"
        ws = MagicMock()
        ws.cwd = ""
        mock_sm.get_window_state.return_value = ws
        mock_browser.return_value = ("Browse:", MagicMock(), [])

        user_data: dict = {}
        message = AsyncMock()

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            result = await _handle_dead_window(
                "@0", 100, 42, "hello", user_data, message
            )

        assert result is True
        mock_sm.unbind_thread.assert_called_once_with(100, 42)
        mock_browser.assert_called_once()

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_directory_browser")
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    async def test_falls_back_to_browser_invalid_cwd(
        self,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_browser: MagicMock,
        _mock_reply: AsyncMock,
    ) -> None:
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        mock_sm.get_display_name.return_value = "project"
        ws = MagicMock()
        ws.cwd = "/nonexistent"
        mock_sm.get_window_state.return_value = ws
        mock_browser.return_value = ("Browse:", MagicMock(), [])

        user_data: dict = {}
        message = AsyncMock()

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            result = await _handle_dead_window(
                "@0", 100, 42, "hello", user_data, message
            )

        assert result is True
        mock_sm.unbind_thread.assert_called_once_with(100, 42)


class TestForwardMessage:
    @patch(f"{_TH}.session_manager")
    async def test_sends_to_window(self, mock_sm: MagicMock) -> None:
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        bot = AsyncMock()
        message = AsyncMock()

        with patch(f"{_TH}.get_interactive_window", return_value=None):
            await _forward_message("@0", 100, 42, "hello", bot, message)

        mock_sm.send_to_window.assert_called_once_with("@0", "hello")

    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.session_manager")
    async def test_send_failure_replies_error(
        self, mock_sm: MagicMock, mock_reply: AsyncMock
    ) -> None:
        mock_sm.send_to_window = AsyncMock(return_value=(False, "Window not found"))
        bot = AsyncMock()
        message = AsyncMock()

        await _forward_message("@0", 100, 42, "hello", bot, message)

        mock_reply.assert_called_once()
        assert "Window not found" in mock_reply.call_args.args[1]

    @patch(f"{_TH}.get_interactive_window", return_value=None)
    @patch(f"{_TH}._capture_bash_output")
    @patch(f"{_TH}.session_manager")
    async def test_bash_capture_for_bang_command(
        self,
        mock_sm: MagicMock,
        mock_capture: MagicMock,
        _mock_interactive: MagicMock,
    ) -> None:
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        bot = AsyncMock()
        message = AsyncMock()

        await _forward_message("@0", 100, 42, "!ls -la", bot, message)

        # A task should have been created (asyncio.create_task was called)
        from ccbot.handlers.text_handler import _bash_capture_tasks

        key = (100, 42)
        assert key in _bash_capture_tasks
        task = _bash_capture_tasks.pop(key)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @patch(f"{_TH}.get_interactive_window", return_value=None)
    @patch(f"{_TH}.session_manager")
    async def test_cancels_existing_bash_capture(
        self, mock_sm: MagicMock, _mock_interactive: MagicMock
    ) -> None:
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        bot = AsyncMock()
        message = AsyncMock()

        from ccbot.handlers.text_handler import _bash_capture_tasks

        dummy_task = AsyncMock(spec=asyncio.Task)
        dummy_task.done.return_value = False
        _bash_capture_tasks[(100, 42)] = dummy_task

        await _forward_message("@0", 100, 42, "hello", bot, message)

        dummy_task.cancel.assert_called_once()
        assert (100, 42) not in _bash_capture_tasks

    @patch(f"{_TH}.handle_interactive_ui", new_callable=AsyncMock)
    @patch(f"{_TH}.get_interactive_window", return_value="@0")
    @patch(f"{_TH}.session_manager")
    async def test_refreshes_interactive_ui(
        self,
        mock_sm: MagicMock,
        _mock_get_iw: MagicMock,
        mock_handle_ui: AsyncMock,
    ) -> None:
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        bot = AsyncMock()
        message = AsyncMock()

        await _forward_message("@0", 100, 42, "hello", bot, message)

        mock_handle_ui.assert_called_once_with(bot, 100, "@0", 42)
