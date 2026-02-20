"""Tests for dead window detection and recovery UI (TASK-009 + TASK-010)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import text_handler
from ccbot.handlers.recovery_callbacks import (
    _SessionEntry,
    build_recovery_keyboard,
    handle_recovery_callback,
    scan_sessions_for_cwd,
)
from ccbot.handlers.callback_data import (
    CB_RECOVERY_BACK,
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_PICK,
    CB_RECOVERY_RESUME,
)
from ccbot.handlers.user_state import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    RECOVERY_SESSIONS,
    RECOVERY_WINDOW_ID,
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


def _recovery_user_data(
    thread_id: int = 42,
    text: str = "hello",
    window_id: str = "@0",
) -> dict:
    return {
        PENDING_THREAD_ID: thread_id,
        PENDING_THREAD_TEXT: text,
        RECOVERY_WINDOW_ID: window_id,
    }


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

    def test_hides_continue_when_unsupported(self) -> None:
        with patch("ccbot.handlers.recovery_callbacks.get_provider") as mock_gp:
            caps = mock_gp.return_value.capabilities
            caps.supports_continue = False
            caps.supports_resume = True
            kb = build_recovery_keyboard("@0")

        action_row = kb.inline_keyboard[0]
        datas = [b.callback_data for b in action_row]
        assert any(d.startswith(CB_RECOVERY_FRESH) for d in datas)
        assert not any(d.startswith(CB_RECOVERY_CONTINUE) for d in datas)
        assert any(d.startswith(CB_RECOVERY_RESUME) for d in datas)

    def test_hides_resume_when_unsupported(self) -> None:
        with patch("ccbot.handlers.recovery_callbacks.get_provider") as mock_gp:
            caps = mock_gp.return_value.capabilities
            caps.supports_continue = True
            caps.supports_resume = False
            kb = build_recovery_keyboard("@0")

        action_row = kb.inline_keyboard[0]
        datas = [b.callback_data for b in action_row]
        assert any(d.startswith(CB_RECOVERY_FRESH) for d in datas)
        assert any(d.startswith(CB_RECOVERY_CONTINUE) for d in datas)
        assert not any(d.startswith(CB_RECOVERY_RESUME) for d in datas)

    def test_fresh_only_when_no_continue_or_resume(self) -> None:
        with patch("ccbot.handlers.recovery_callbacks.get_provider") as mock_gp:
            caps = mock_gp.return_value.capabilities
            caps.supports_continue = False
            caps.supports_resume = False
            kb = build_recovery_keyboard("@0")

        action_row = kb.inline_keyboard[0]
        assert len(action_row) == 1
        assert action_row[0].callback_data.startswith(CB_RECOVERY_FRESH)


@pytest.fixture(autouse=True)
def _allow_user():
    with patch("ccbot.bot.is_user_allowed", return_value=True):
        yield


@pytest.fixture()
def _no_group():
    with patch("ccbot.bot.config") as mock_config:
        mock_config.group_id = None
        yield mock_config


_TH = "ccbot.handlers.text_handler"


class TestTextHandlerDeadWindow:
    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
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

        with patch(f"{_TH}.Path") as mock_path:
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

    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
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

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            mock_path.cwd.return_value = mock_path.return_value
            await text_handler(update, ctx)

        assert user_data[PENDING_THREAD_TEXT] == "my pending message"
        assert user_data[PENDING_THREAD_ID] == 42
        assert user_data[RECOVERY_WINDOW_ID] == "@0"

    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_directory_browser")
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

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            await text_handler(update, ctx)

        mock_sm.unbind_thread.assert_called_once()
        mock_browser.assert_called_once()

    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
    @patch(f"{_TH}.build_directory_browser")
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

        with patch(f"{_TH}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            mock_path.cwd.return_value = mock_path.return_value
            str_mock = MagicMock(return_value="/cwd")
            mock_path.cwd.return_value.__str__ = str_mock
            await text_handler(update, ctx)

        mock_sm.unbind_thread.assert_called_once()

    @patch(f"{_TH}.tmux_manager")
    @patch(f"{_TH}.session_manager")
    @patch(f"{_TH}.safe_reply", new_callable=AsyncMock)
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

        with patch(f"{_TH}.Path") as mock_path:
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
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_sm.unbind_thread.assert_called_once_with(100, 42)
        mock_tm.create_window.assert_called_once_with("/tmp/project", claude_args="")
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
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_sm.send_to_window.assert_called_once_with("@5", "hello")
        assert PENDING_THREAD_TEXT not in user_data
        assert PENDING_THREAD_ID not in user_data
        assert RECOVERY_WINDOW_ID not in user_data

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
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "no longer exists" in mock_safe_edit.call_args.args[1].lower()

    async def test_fresh_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_FRESH}@0", thread_id=99)
        user_data = {PENDING_THREAD_ID: 42, RECOVERY_WINDOW_ID: "@0"}
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
            PENDING_THREAD_ID: 42,
            RECOVERY_WINDOW_ID: "@0",
        }
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()


class TestRecoveryContinueCallback:
    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_continue_creates_window_with_continue_flag(
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

        update = _make_callback_update(data=f"{CB_RECOVERY_CONTINUE}@0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_tm.create_window.assert_called_once_with(
            "/tmp/project", claude_args="--continue"
        )
        mock_sm.bind_thread.assert_called_once_with(
            100, 42, "@5", window_name="project"
        )

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    @patch(f"{_RC}.safe_send", new_callable=AsyncMock)
    async def test_continue_forwards_pending_message(
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

        update = _make_callback_update(data=f"{CB_RECOVERY_CONTINUE}@0")
        user_data = _recovery_user_data(text="my message")
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_sm.send_to_window.assert_called_once_with("@5", "my message")
        assert PENDING_THREAD_TEXT not in user_data

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_continue_fails_when_cwd_gone(
        self,
        mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        _mock_tm: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/gone")

        update = _make_callback_update(data=f"{CB_RECOVERY_CONTINUE}@0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = False
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "no longer exists" in mock_safe_edit.call_args.args[1].lower()

    async def test_continue_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_CONTINUE}@0", thread_id=99)
        user_data = {PENDING_THREAD_ID: 42, RECOVERY_WINDOW_ID: "@0"}
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()


class TestRecoveryResumeCallback:
    @patch(f"{_RC}.scan_sessions_for_cwd")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_resume_shows_session_picker(
        self,
        mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
        mock_scan: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp/project")
        mock_scan.return_value = [
            _SessionEntry("sess-1", "Fix login bug"),
            _SessionEntry("sess-2", "Add tests"),
        ]

        update = _make_callback_update(data=f"{CB_RECOVERY_RESUME}@0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "Select a session" in mock_safe_edit.call_args.args[1]
        assert RECOVERY_SESSIONS in user_data
        assert len(user_data[RECOVERY_SESSIONS]) == 2
        assert user_data[RECOVERY_SESSIONS][0]["session_id"] == "sess-1"

    @patch(f"{_RC}.scan_sessions_for_cwd")
    @patch(f"{_RC}.session_manager")
    async def test_resume_no_sessions_shows_alert(
        self,
        mock_sm: MagicMock,
        mock_scan: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp/project")
        mock_scan.return_value = []

        update = _make_callback_update(data=f"{CB_RECOVERY_RESUME}@0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "no sessions" in query.answer.call_args.args[0].lower()

    async def test_resume_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_RESUME}@0", thread_id=99)
        user_data = {PENDING_THREAD_ID: 42, RECOVERY_WINDOW_ID: "@0"}
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()


class TestRecoveryResumePickCallback:
    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_pick_creates_window_with_resume_flag(
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

        update = _make_callback_update(data=f"{CB_RECOVERY_PICK}0")
        user_data = _recovery_user_data()
        user_data[RECOVERY_SESSIONS] = [
            {
                "session_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "summary": "Fix login bug",
            },
            {
                "session_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "summary": "Add tests",
            },
        ]
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_tm.create_window.assert_called_once_with(
            "/tmp/project", claude_args="--resume a1b2c3d4-0000-0000-0000-000000000001"
        )
        mock_sm.bind_thread.assert_called_once()

    @patch(f"{_RC}.tmux_manager")
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_pick_second_session(
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

        update = _make_callback_update(data=f"{CB_RECOVERY_PICK}1")
        user_data = _recovery_user_data()
        user_data[RECOVERY_SESSIONS] = [
            {
                "session_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "summary": "Fix login bug",
            },
            {
                "session_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "summary": "Add tests",
            },
        ]
        ctx = _make_context(user_data)
        query = update.callback_query

        with patch(f"{_RC}.Path") as mock_path:
            mock_path.return_value.is_dir.return_value = True
            await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_tm.create_window.assert_called_once_with(
            "/tmp/project", claude_args="--resume a1b2c3d4-0000-0000-0000-000000000002"
        )

    async def test_pick_invalid_index_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_PICK}99")
        user_data = _recovery_user_data()
        user_data[RECOVERY_SESSIONS] = [
            {"session_id": "a1b2c3d4-0000-0000-0000-000000000001", "summary": "test"},
        ]
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "invalid" in query.answer.call_args.args[0].lower()

    async def test_pick_no_sessions_stored_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_PICK}0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "invalid" in query.answer.call_args.args[0].lower()

    async def test_pick_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_PICK}0", thread_id=99)
        user_data = _recovery_user_data()
        user_data[RECOVERY_SESSIONS] = [
            {"session_id": "a1b2c3d4-0000-0000-0000-000000000001", "summary": "test"},
        ]
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()


class TestRecoveryBackCallback:
    @patch(f"{_RC}.session_manager")
    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_back_shows_recovery_menu(
        self,
        mock_safe_edit: AsyncMock,
        mock_sm: MagicMock,
    ) -> None:
        mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp/project")
        update = _make_callback_update(data=f"{CB_RECOVERY_BACK}@0")
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        mock_safe_edit.assert_called_once()
        assert "Choose an option" in mock_safe_edit.call_args.args[1]
        query.answer.assert_called_once()

    async def test_back_topic_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_BACK}@0", thread_id=99)
        user_data = {PENDING_THREAD_ID: 42, RECOVERY_WINDOW_ID: "@0"}
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()

    async def test_back_no_pending_state_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_BACK}@0")
        ctx = _make_context({})
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        query.answer.assert_called_once()
        assert "mismatch" in query.answer.call_args.args[0].lower()

    async def test_back_window_id_mismatch_rejected(self) -> None:
        update = _make_callback_update(data=f"{CB_RECOVERY_BACK}@999")
        user_data = {PENDING_THREAD_ID: 42, RECOVERY_WINDOW_ID: "@0"}
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
        user_data = _recovery_user_data()
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        assert PENDING_THREAD_TEXT not in user_data
        assert PENDING_THREAD_ID not in user_data
        assert RECOVERY_WINDOW_ID not in user_data
        mock_safe_edit.assert_called_once()

    @patch(f"{_RC}.safe_edit", new_callable=AsyncMock)
    async def test_cancel_also_clears_recovery_sessions(
        self,
        mock_safe_edit: AsyncMock,
    ) -> None:
        update = _make_callback_update(data=CB_RECOVERY_CANCEL)
        user_data = _recovery_user_data()
        user_data[RECOVERY_SESSIONS] = [{"session_id": "x", "summary": "y"}]
        ctx = _make_context(user_data)
        query = update.callback_query

        await handle_recovery_callback(query, 100, query.data, update, ctx)

        assert RECOVERY_SESSIONS not in user_data


class TestScanSessionsForCwd:
    def test_returns_sessions_matching_cwd(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-1.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "sess-1",
                    "fullPath": str(session_file),
                    "projectPath": resolved,
                    "summary": "Fix the bug",
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_sessions_for_cwd(str(work_dir))

        assert len(result) == 1
        assert result[0].session_id == "sess-1"
        assert result[0].summary == "Fix the bug"

    def test_returns_empty_for_no_match(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        proj_dir = projects_path / "-tmp-other"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-1.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": str(other_dir.resolve()),
            "entries": [
                {
                    "sessionId": "sess-1",
                    "fullPath": str(session_file),
                    "projectPath": str(other_dir.resolve()),
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_sessions_for_cwd(str(work_dir))

        assert result == []

    def test_returns_empty_when_projects_path_missing(self, tmp_path) -> None:
        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = tmp_path / "nonexistent"
            result = scan_sessions_for_cwd("/some/path")

        assert result == []

    def test_sorted_by_mtime_descending(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        import time

        old_file = proj_dir / "sess-old.jsonl"
        old_file.write_text('{"type":"summary"}\n')
        time.sleep(0.05)

        new_file = proj_dir / "sess-new.jsonl"
        new_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "sess-old",
                    "fullPath": str(old_file),
                    "projectPath": resolved,
                    "summary": "Old session",
                },
                {
                    "sessionId": "sess-new",
                    "fullPath": str(new_file),
                    "projectPath": resolved,
                    "summary": "New session",
                },
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_sessions_for_cwd(str(work_dir))

        assert len(result) == 2
        assert result[0].session_id == "sess-new"
        assert result[1].session_id == "sess-old"

    def test_skips_missing_session_files(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "sess-gone",
                    "fullPath": str(proj_dir / "nonexistent.jsonl"),
                    "projectPath": resolved,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_sessions_for_cwd(str(work_dir))

        assert result == []

    def test_uses_session_id_as_summary_fallback(self, tmp_path) -> None:
        projects_path = tmp_path / "projects"
        work_dir = tmp_path / "myproj"
        work_dir.mkdir()
        resolved = str(work_dir.resolve())

        proj_dir = projects_path / "-tmp-myproj"
        proj_dir.mkdir(parents=True)

        session_file = proj_dir / "sess-abc123.jsonl"
        session_file.write_text('{"type":"summary"}\n')

        index = {
            "originalPath": resolved,
            "entries": [
                {
                    "sessionId": "a1b2c3d4-0000-0000-0000-abc123000000",
                    "fullPath": str(session_file),
                    "projectPath": resolved,
                }
            ],
        }
        (proj_dir / "sessions-index.json").write_text(json.dumps(index))

        with patch(f"{_RC}.config") as mock_config:
            mock_config.claude_projects_path = projects_path
            result = scan_sessions_for_cwd(str(work_dir))

        assert len(result) == 1
        assert result[0].summary == "a1b2c3d4-000"
