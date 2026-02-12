"""Tests for /sessions dashboard command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.callback_data import (
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
    CB_STATUS_ESC,
    CB_STATUS_SCREENSHOT,
)
from ccbot.handlers.sessions_dashboard import (
    _build_dashboard,
    handle_sessions_refresh,
    sessions_command,
)
from ccbot.session import WindowState


@pytest.fixture(autouse=True)
def _patch_deps():
    with (
        patch("ccbot.handlers.sessions_dashboard.session_manager") as mock_sm,
        patch("ccbot.handlers.sessions_dashboard.tmux_manager") as mock_tm,
        patch("ccbot.handlers.sessions_dashboard.config") as mock_cfg,
    ):
        mock_sm.get_all_thread_windows.return_value = {}
        mock_sm.get_display_name.side_effect = lambda wid: wid
        mock_sm.get_window_state.side_effect = lambda wid: WindowState()
        mock_tm.list_windows = AsyncMock(return_value=[])
        mock_cfg.is_user_allowed.return_value = True
        yield mock_sm, mock_tm, mock_cfg


class TestBuildDashboard:
    async def test_empty(self, _patch_deps) -> None:
        text, keyboard = await _build_dashboard(100)
        assert "No active sessions" in text
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert CB_SESSIONS_REFRESH in data
        assert CB_SESSIONS_NEW in data

    async def test_alive_session(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.get_window_state.side_effect = lambda wid: WindowState(
            cwd="/home/user/myproject"
        )
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "\U0001f7e2 myproject" in text

    async def test_alive_session_shows_cwd(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.get_window_state.side_effect = lambda wid: WindowState(
            cwd="/home/user/myproject"
        )
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "/home/user/myproject" in text

    async def test_no_cwd_shows_no_path(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.get_window_state.side_effect = lambda wid: WindowState(cwd="")
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "    " not in text

    async def test_dead_session(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        text, _kb = await _build_dashboard(100)
        assert "\u26ab oldproject" in text

    async def test_multiple_sessions(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {10: "@0", 20: "@5"}
        mock_sm.get_display_name.side_effect = lambda wid: {
            "@0": "alive",
            "@5": "dead",
        }[wid]
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "\U0001f7e2 alive" in text
        assert "\u26ab dead" in text

    async def test_refresh_and_new_buttons(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert any("Refresh" in label for label in labels)
        assert any("New" in label for label in labels)
        assert CB_SESSIONS_REFRESH in data
        assert CB_SESSIONS_NEW in data

    async def test_alive_session_has_esc_button(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert any(d.startswith(CB_STATUS_ESC) for d in data)

    async def test_alive_session_has_screenshot_button(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert any(d.startswith(CB_STATUS_SCREENSHOT) for d in data)

    async def test_dead_session_no_action_buttons(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "deadproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert not any(d.startswith(CB_STATUS_ESC) for d in data)
        assert not any(d.startswith(CB_STATUS_SCREENSHOT) for d in data)


class TestSessionsCommand:
    async def test_calls_reply(self, _patch_deps) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch("ccbot.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_called_once()
            assert update.message == mock_reply.call_args[0][0]
            assert "No active sessions" in mock_reply.call_args[0][1]

    async def test_unauthorized(self, _patch_deps) -> None:
        _, _, mock_cfg = _patch_deps
        mock_cfg.is_user_allowed.return_value = False

        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch("ccbot.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_called_once()
            assert "not authorized" in mock_reply.call_args[0][1]

    async def test_no_user(self) -> None:
        update = MagicMock()
        update.effective_user = None
        update.message = AsyncMock()

        with patch("ccbot.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_not_called()

    async def test_no_message(self) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = None

        with patch("ccbot.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_not_called()


class TestSessionsRefresh:
    async def test_refresh_edits(self, _patch_deps) -> None:
        query = AsyncMock()

        with patch("ccbot.handlers.sessions_dashboard.safe_edit") as mock_edit:
            await handle_sessions_refresh(query, 100)
            mock_edit.assert_called_once()
            assert query == mock_edit.call_args[0][0]
            assert "No active sessions" in mock_edit.call_args[0][1]


class TestKillButtons:
    async def test_alive_session_has_kill_button(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "myproject"
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert any(d.startswith("sess:kill:") for d in data)

    async def test_dead_session_no_kill_button(self, _patch_deps) -> None:
        mock_sm, mock_tm, _ = _patch_deps
        mock_sm.get_all_thread_windows.return_value = {42: "@0"}
        mock_sm.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert not any(d.startswith("sess:kill:") for d in data)

    async def test_empty_dashboard_no_kill_button(self, _patch_deps) -> None:
        _text, keyboard = await _build_dashboard(100)
        data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert not any(d.startswith("sess:kill:") for d in data)
