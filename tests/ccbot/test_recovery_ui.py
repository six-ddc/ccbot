"""Tests for dead window detection and recovery UI (TASK-009)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import text_handler
from ccbot.handlers.recovery_callbacks import (
    build_recovery_keyboard,
    handle_recovery_callback,
)
from ccbot.handlers.callback_data import (
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_RESUME,
)

_RC = "ccbot.handlers.recovery_callbacks"


def _make_update(
    *,
    chat_id: int = -100999,
    user_id: int = 100,
    thread_id: int = 42,
    text: str = "hello",
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    msg = MagicMock()
    msg.text = text
    msg.message_thread_id = thread_id
    msg.chat.type = "supergroup"
    msg.chat.is_forum = True
    msg.is_topic_message = True
    update.message = msg
    update.callback_query = None
    return update


def _make_callback_update(
    *,
    chat_id: int = -100999,
    user_id: int = 100,
    thread_id: int = 42,
    data: str = "",
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    query = AsyncMock()
    query.data = data
    query.message = MagicMock()
    query.message.chat.type = "supergroup"
    query.message.chat.id = chat_id
    query.message.message_thread_id = thread_id
    query.message.chat.is_forum = True
    query.message.is_topic_message = True
    update.callback_query = query
    update.message = None
    return update


def _make_context(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot = AsyncMock()
    return ctx


class TestBuildRecoveryKeyboard:
    def test_has_three_action_buttons(self) -> None:
        kb = build_recovery_keyboard("@0")
        action_row = kb.inline_keyboard[0]
        assert len(action_row) == 3

    def test_has_cancel_button(self) -> None:
        kb = build_recovery_keyboard("@0")
        cancel_row = kb.inline_keyboard[1]
        assert len(cancel_row) == 1
        assert cancel_row[0].callback_data == CB_RECOVERY_CANCEL

    def test_fresh_callback_data(self) -> None:
        kb = build_recovery_keyboard("@5")
        data = kb.inline_keyboard[0][0].callback_data
        assert data == f"{CB_RECOVERY_FRESH}@5"

    def test_continue_callback_data(self) -> None:
        kb = build_recovery_keyboard("@5")
        data = kb.inline_keyboard[0][1].callback_data
        assert data == f"{CB_RECOVERY_CONTINUE}@5"

    def test_resume_callback_data(self) -> None:
        kb = build_recovery_keyboard("@5")
        data = kb.inline_keyboard[0][2].callback_data
        assert data == f"{CB_RECOVERY_RESUME}@5"

    def test_callback_data_truncated_to_64_bytes(self) -> None:
        long_id = "@" + "x" * 60
        kb = build_recovery_keyboard(long_id)
        for row in kb.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data) <= 64


@pytest.fixture(autouse=True)
def _allow_user():
    with patch("ccbot.bot.is_user_allowed", return_value=True):
        yield


@pytest.fixture()
def _no_group():
    with patch("ccbot.bot.config") as mock_config:
        mock_config.group_id = None
        yield mock_config


class TestTextHandlerDeadWindow:
    @patch("ccbot.bot.tmux_manager")
    @patch("ccbot.bot.session_manager")
    @patch("ccbot.bot.safe_reply", new_callable=AsyncMock)
    async def test_dead_window_shows_recovery_ui(
        self,
        mock_safe_reply: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        _no_group: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        ws = MagicMock()
        ws.cwd = "/tmp/project"
        mock_sm.get_window_state.return_value = ws
        mock_sm.get_display_name.return_value = "project"

        update = _make_update()
        ctx = _make_context()

        with patch("ccbot.bot.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            mock_path.cwd.return_value = mock_path.return_value
            await text_handler(update, ctx)

        mock_safe_reply.assert_called_once()
        call_kwargs = mock_safe_reply.call_args
        msg_text = (
            call_kwargs.args[1]
            if len(call_kwargs.args) > 1
            else call_kwargs.kwargs.get("text", "")
        )
        assert "no longer running" in msg_text
        assert "recover" in msg_text.lower()

    @patch("ccbot.bot.tmux_manager")
    @patch("ccbot.bot.session_manager")
    @patch("ccbot.bot.safe_reply", new_callable=AsyncMock)
    async def test_dead_window_stores_pending_message(
        self,
        mock_safe_reply: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        _no_group: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        ws = MagicMock()
        ws.cwd = "/tmp/project"
        mock_sm.get_window_state.return_value = ws
        mock_sm.get_display_name.return_value = "project"

        update = _make_update(text="my pending message")
        user_data: dict = {}
        ctx = _make_context(user_data)

        with patch("ccbot.bot.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            mock_path.cwd.return_value = mock_path.return_value
            await text_handler(update, ctx)

        assert user_data["_pending_thread_text"] == "my pending message"
        assert user_data["_pending_thread_id"] == 42
        assert user_data["_recovery_window_id"] == "@0"

    @patch("ccbot.bot.tmux_manager")
    @patch("ccbot.bot.session_manager")
    @patch("ccbot.bot.safe_reply", new_callable=AsyncMock)
    @patch("ccbot.bot.build_directory_browser")
    async def test_dead_window_no_cwd_falls_back_to_browser(
        self,
        mock_browser: MagicMock,
        mock_safe_reply: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        _no_group: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        ws = MagicMock()
        ws.cwd = ""
        mock_sm.get_window_state.return_value = ws
        mock_sm.get_display_name.return_value = "project"
        mock_browser.return_value = ("Browse:", MagicMock(), [])

        update = _make_update()
        ctx = _make_context()

        with patch("ccbot.bot.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            await text_handler(update, ctx)

        mock_sm.unbind_thread.assert_called_once()
        mock_browser.assert_called_once()

    @patch("ccbot.bot.tmux_manager")
    @patch("ccbot.bot.session_manager")
    @patch("ccbot.bot.safe_reply", new_callable=AsyncMock)
    @patch("ccbot.bot.build_directory_browser")
    async def test_dead_window_invalid_cwd_falls_back_to_browser(
        self,
        mock_browser: MagicMock,
        mock_safe_reply: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        _no_group: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        ws = MagicMock()
        ws.cwd = "/nonexistent/path"
        mock_sm.get_window_state.return_value = ws
        mock_sm.get_display_name.return_value = "project"
        mock_browser.return_value = ("Browse:", MagicMock(), [])

        update = _make_update()
        ctx = _make_context()

        with patch("ccbot.bot.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            await text_handler(update, ctx)

        mock_sm.unbind_thread.assert_called_once()

    @patch("ccbot.bot.tmux_manager")
    @patch("ccbot.bot.session_manager")
    @patch("ccbot.bot.safe_reply", new_callable=AsyncMock)
    async def test_dead_window_does_not_unbind(
        self,
        mock_safe_reply: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        _no_group: MagicMock,
    ) -> None:
        mock_sm.get_window_for_thread.return_value = "@0"
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        ws = MagicMock()
        ws.cwd = "/tmp/project"
        mock_sm.get_window_state.return_value = ws
        mock_sm.get_display_name.return_value = "project"

        update = _make_update()
        ctx = _make_context()

        with patch("ccbot.bot.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            mock_path.cwd.return_value = mock_path.return_value
            await text_handler(update, ctx)

        mock_sm.unbind_thread.assert_not_called()


class TestRecoveryFreshCallback:
    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_fresh_creates_window_and_rebinds(
        self,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp/project")
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0")
        user_data = {
            "_pending_thread_id": 42,
            "_pending_thread_text": "hello",
            "_recovery_window_id": "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_sm.unbind_thread.assert_called_once_with(100, 42)
        mock_tm.create_window.assert_called_once_with("/tmp/project")
        mock_sm.bind_thread.assert_called_once_with(
            100, 42, "@5", window_name="project"
        )

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.safe_send", new_callable=AsyncMock)
    async def test_fresh_forwards_pending_message(
        self,
        _mock_safe_send: AsyncMock,
        _mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp/project")
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "project", "@5")
        )
        mock_sm.wait_for_session_map_entry = AsyncMock()
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0")
        user_data = {
            "_pending_thread_id": 42,
            "_pending_thread_text": "hello",
            "_recovery_window_id": "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_sm.send_to_window.assert_called_once_with("@5", "hello")
        assert "_pending_thread_text" not in user_data
        assert "_pending_thread_id" not in user_data
        assert "_recovery_window_id" not in user_data

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_fresh_fails_when_cwd_gone(
        self,
        mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        _mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/gone")
        mock_sm.resolve_chat_id.return_value = -100999

        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0")
        user_data = {
            "_pending_thread_id": 42,
            "_pending_thread_text": "hello",
            "_recovery_window_id": "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "no longer exists" in mock_safe_edit.call_args.args[1].lower()

    async def test_fresh_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0", thread_id=99)
        user_data = {"_pending_thread_id": 42, "_recovery_window_id": "@0"}
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()

    async def test_fresh_no_pending_state_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0")
        ctx = _make_context({})
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()

    async def test_fresh_window_id_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@999")
        user_data = {
            "_pending_thread_id": 42,
            "_recovery_window_id": "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()


class TestRecoveryCancelCallback:
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_cancel_clears_state(
        self,
        mock_safe_edit: AsyncMock,
    ) -> None:
        update = _make_callback_update(data=CB_RECOVERY_CANCEL)
        user_data = {
            "_pending_thread_id": 42,
            "_pending_thread_text": "hello",
            "_recovery_window_id": "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        assert "_pending_thread_text" not in user_data
        assert "_pending_thread_id" not in user_data
        assert "_recovery_window_id" not in user_data
        mock_safe_edit.assert_called_once()


class TestRecoveryStubCallbacks:
    async def test_continue_shows_coming_soon(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_CONTINUE}@0")
        ctx = _make_context()
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        call_args = query.answer.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "future" in msg.lower()

    async def test_resume_shows_coming_soon(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_RESUME}@0")
        ctx = _make_context()
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        call_args = query.answer.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "future" in msg.lower()
