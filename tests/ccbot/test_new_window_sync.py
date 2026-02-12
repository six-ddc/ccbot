"""Integration tests for manual tmux window -> Telegram topic sync (TASK-033).

Verifies the full chain: session_map update -> SessionMonitor detection ->
_handle_new_window callback -> topic creation -> binding established.
Unlike unit tests in test_handle_new_window.py, these tests wire the monitor's
detection logic through to _handle_new_window with a real SessionManager
(disk I/O mocked) to verify end-to-end state changes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import _handle_new_window
from ccbot.session import SessionManager
from ccbot.session_monitor import NewWindowEvent, SessionMonitor


@pytest.fixture
def sm(monkeypatch: pytest.MonkeyPatch) -> SessionManager:
    """SessionManager with disk I/O disabled."""
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


@pytest.fixture
def monitor(tmp_path) -> SessionMonitor:
    return SessionMonitor(
        projects_path=tmp_path / "projects",
        poll_interval=0.1,
        state_file=tmp_path / "monitor_state.json",
    )


def _make_topic(thread_id: int = 999) -> MagicMock:
    topic = MagicMock()
    topic.message_thread_id = thread_id
    return topic


class TestNewWindowSyncWithBindings:
    """Full flow: existing bindings provide target group, monitor detects new window."""

    async def test_monitor_detect_triggers_topic_and_binding(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        user_id = 100
        existing_thread = 5
        existing_window = "@1"
        group_chat = -100200
        new_window = "@7"
        new_thread = 77

        sm.thread_bindings = {user_id: {existing_thread: existing_window}}
        sm.group_chat_ids = {f"{user_id}:{existing_thread}": group_chat}

        bot = AsyncMock()
        bot.create_forum_topic = AsyncMock(return_value=_make_topic(new_thread))

        captured_events: list[NewWindowEvent] = []

        async def on_new_window(event: NewWindowEvent) -> None:
            captured_events.append(event)
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = None
                mock_config.allowed_users = {user_id}
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {
            existing_window: {
                "session_id": "old-sess",
                "cwd": "/proj",
                "window_name": "proj",
            }
        }

        new_map = {
            existing_window: {
                "session_id": "old-sess",
                "cwd": "/proj",
                "window_name": "proj",
            },
            new_window: {
                "session_id": "new-sess",
                "cwd": "/home/user/new-project",
                "window_name": "new-project",
            },
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        assert len(captured_events) == 1
        assert captured_events[0].window_id == new_window
        assert captured_events[0].window_name == "new-project"

        bot.create_forum_topic.assert_called_once_with(
            chat_id=group_chat, name="new-project"
        )

        assert sm.get_window_for_thread(user_id, new_thread) == new_window
        assert sm.resolve_chat_id(user_id, new_thread) == group_chat
        assert sm.window_display_names.get(new_window) == "new-project"

    async def test_multiple_groups_get_separate_topics(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        user_a, user_b = 100, 200
        group_a, group_b = -100100, -100200
        new_window = "@9"

        sm.thread_bindings = {user_a: {1: "@1"}, user_b: {2: "@2"}}
        sm.group_chat_ids = {
            f"{user_a}:1": group_a,
            f"{user_b}:2": group_b,
        }

        topic_counter = iter([50, 60])
        bot = AsyncMock()
        bot.create_forum_topic = AsyncMock(
            side_effect=lambda **kw: _make_topic(next(topic_counter))
        )

        async def on_new_window(event: NewWindowEvent) -> None:
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = None
                mock_config.allowed_users = {user_a, user_b}
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {}

        new_map = {
            new_window: {
                "session_id": "s1",
                "cwd": "/proj",
                "window_name": "proj",
            }
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        assert bot.create_forum_topic.call_count == 2
        called_chats = {
            call.kwargs["chat_id"] for call in bot.create_forum_topic.call_args_list
        }
        assert called_chats == {group_a, group_b}


class TestNewWindowSyncColdStart:
    """Cold-start: no existing bindings, CCBOT_GROUP_ID drives topic creation."""

    async def test_cold_start_with_group_id_creates_and_binds(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        group_id = -100500
        user_id = 12345
        new_window = "@3"
        new_thread = 42

        bot = AsyncMock()
        bot.create_forum_topic = AsyncMock(return_value=_make_topic(new_thread))

        async def on_new_window(event: NewWindowEvent) -> None:
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = group_id
                mock_config.allowed_users = {user_id}
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {}

        new_map = {
            new_window: {
                "session_id": "fresh-sess",
                "cwd": "/home/user/project",
                "window_name": "project",
            }
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        bot.create_forum_topic.assert_called_once_with(chat_id=group_id, name="project")
        assert sm.get_window_for_thread(user_id, new_thread) == new_window
        assert sm.resolve_chat_id(user_id, new_thread) == group_id

    async def test_cold_start_without_group_id_skips(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        bot = AsyncMock()

        async def on_new_window(event: NewWindowEvent) -> None:
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = None
                mock_config.allowed_users = set()
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {}

        new_map = {
            "@4": {
                "session_id": "s1",
                "cwd": "/proj",
                "window_name": "proj",
            }
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        bot.create_forum_topic.assert_not_called()
        assert sm.thread_bindings == {}


class TestNewWindowSyncEdgeCases:
    """Edge cases in the sync flow."""

    async def test_already_bound_window_skips_topic_creation(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        user_id = 100
        window_id = "@5"
        thread_id = 10

        sm.thread_bindings = {user_id: {thread_id: window_id}}
        sm.group_chat_ids = {f"{user_id}:{thread_id}": -100100}

        bot = AsyncMock()

        async def on_new_window(event: NewWindowEvent) -> None:
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = -100100
                mock_config.allowed_users = {user_id}
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {}

        new_map = {
            window_id: {
                "session_id": "s-new",
                "cwd": "/proj",
                "window_name": "proj",
            }
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        bot.create_forum_topic.assert_not_called()

    async def test_topic_name_falls_back_to_cwd(
        self, monitor: SessionMonitor, sm: SessionManager
    ) -> None:
        group_id = -100500
        user_id = 12345
        new_thread = 88

        bot = AsyncMock()
        bot.create_forum_topic = AsyncMock(return_value=_make_topic(new_thread))

        async def on_new_window(event: NewWindowEvent) -> None:
            with (
                patch("ccbot.bot.session_manager", sm),
                patch("ccbot.bot.config") as mock_config,
            ):
                mock_config.group_id = group_id
                mock_config.allowed_users = {user_id}
                await _handle_new_window(event, bot)

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {}

        new_map = {
            "@6": {
                "session_id": "s1",
                "cwd": "/home/user/cool-project",
                "window_name": "",
            }
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        bot.create_forum_topic.assert_called_once_with(
            chat_id=group_id, name="cool-project"
        )

    async def test_session_change_does_not_fire_new_window(
        self, monitor: SessionMonitor
    ) -> None:
        callback_fired = False

        async def on_new_window(event: NewWindowEvent) -> None:
            nonlocal callback_fired
            callback_fired = True

        monitor.set_new_window_callback(on_new_window)
        monitor._last_session_map = {
            "@1": {"session_id": "old-sess", "cwd": "/proj", "window_name": "proj"}
        }

        updated_map = {
            "@1": {"session_id": "new-sess", "cwd": "/proj", "window_name": "proj"}
        }
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=updated_map,
        ):
            await monitor._detect_and_cleanup_changes()

        assert not callback_fired
