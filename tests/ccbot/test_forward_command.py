"""Tests for command forwarding and runtime-specific helper commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import start_command


def _make_update(text: str, user_id: int = 1, thread_id: int = 42) -> MagicMock:
    """Build a minimal mock Update with message text in a forum topic."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 100
    return update


def _make_context() -> MagicMock:
    """Build a minimal mock context."""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


class TestForwardCommand:
    @pytest.mark.asyncio
    async def test_plan_sends_command_to_tmux_in_codex_runtime(self):
        update = _make_update("/plan")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch(
                "ccbot.bot.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/plan")
            mock_handle_ui.assert_not_called()

    @pytest.mark.asyncio
    async def test_removed_codex_menu_command_still_forwards_manually(self):
        update = _make_update("/memory")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch(
                "ccbot.bot.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/memory")
            mock_handle_ui.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_sends_command_to_tmux(self):
        """/model → send_to_window called with "/model"."""
        update = _make_update("/model")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch(
                "ccbot.bot.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_handle_ui.return_value = True

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/model")
            mock_handle_ui.assert_called_once_with(context.bot, 1, "@5", 42)

    @pytest.mark.asyncio
    async def test_cost_sends_command_to_tmux(self):
        """/cost → send_to_window called with "/cost"."""
        update = _make_update("/cost")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch(
                "ccbot.bot.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/cost")
            mock_handle_ui.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_reports_startup_blocker_when_picker_unavailable(self):
        update = _make_update("/model")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch(
                "ccbot.bot.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_window = MagicMock()
            mock_window.window_id = "@5"
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(
                return_value="• Model selection is disabled until startup completes."
            )
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_handle_ui.return_value = False

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

        assert mock_handle_ui.await_count == 20
        assert "Wait for startup to finish" in mock_reply.await_args_list[-1].args[1]

    @pytest.mark.asyncio
    async def test_clear_clears_session(self):
        """/clear → send_to_window + clear_window_session."""
        update = _make_update("/clear")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/clear")
            mock_sm.clear_window_session.assert_called_once_with("@5")


class TestKillCommand:
    @pytest.mark.asyncio
    async def test_kill_command_keeps_claude_hard_kill_behavior(self):
        update = _make_update("/kill")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "claude"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock(window_id="@5"))
            mock_tmux.kill_window = AsyncMock(return_value=True)

            from ccbot.bot import kill_command

            await kill_command(update, context)

        mock_sm.send_to_window.assert_not_called()
        mock_tmux.kill_window.assert_called_once_with("@5")
        mock_sm.unbind_thread.assert_called_once_with(1, 42)
        mock_sm.remove_window.assert_called_once_with("@5")
        mock_clear.assert_called_once_with(1, 42, context.bot, context.user_data)
        assert "Killed session" in mock_reply.await_args.args[1]

    @pytest.mark.asyncio
    async def test_kill_command_reports_failure_when_window_survives(self):
        update = _make_update("/kill")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "claude"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock(window_id="@5"))
            mock_tmux.kill_window = AsyncMock(return_value=False)

            from ccbot.bot import kill_command

            await kill_command(update, context)

        mock_sm.unbind_thread.assert_not_called()
        mock_sm.remove_window.assert_not_called()
        mock_clear.assert_not_called()
        assert "Failed to kill window" in mock_reply.await_args.args[1]

    @pytest.mark.asyncio
    async def test_kill_command_codex_exits_then_kills_window_and_clears_state(self):
        update = _make_update("/kill")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_tmux.find_window_by_id = AsyncMock(
                side_effect=[MagicMock(window_id="@5"), MagicMock(window_id="@5")]
            )
            mock_tmux.kill_window = AsyncMock(return_value=True)

            from ccbot.bot import kill_command

            await kill_command(update, context)

        mock_sm.send_to_window.assert_called_once_with("@5", "/exit")
        mock_tmux.kill_window.assert_called_once_with("@5")
        mock_sm.unbind_thread.assert_called_once_with(1, 42)
        mock_sm.remove_window.assert_called_once_with("@5")
        mock_clear.assert_called_once_with(1, 42, context.bot, context.user_data)
        assert "Killed session" in mock_reply.await_args.args[1]

    @pytest.mark.asyncio
    async def test_kill_command_codex_succeeds_when_window_is_gone_after_exit(self):
        update = _make_update("/kill")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            mock_tmux.find_window_by_id = AsyncMock(
                side_effect=[MagicMock(window_id="@5"), None]
            )

            from ccbot.bot import kill_command

            await kill_command(update, context)

        mock_sm.send_to_window.assert_called_once_with("@5", "/exit")
        mock_tmux.kill_window.assert_not_called()
        mock_sm.unbind_thread.assert_called_once_with(1, 42)
        mock_sm.remove_window.assert_called_once_with("@5")
        mock_clear.assert_called_once_with(1, 42, context.bot, context.user_data)
        assert "Killed session" in mock_reply.await_args.args[1]

    @pytest.mark.asyncio
    async def test_kill_command_codex_hard_kills_when_exit_send_fails(self):
        update = _make_update("/kill")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.clear_topic_state", new_callable=AsyncMock) as mock_clear,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_sm.get_window_for_thread.return_value = "@5"
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(False, "send failed"))
            mock_tmux.find_window_by_id = AsyncMock(
                side_effect=[MagicMock(window_id="@5"), MagicMock(window_id="@5")]
            )
            mock_tmux.kill_window = AsyncMock(return_value=True)

            from ccbot.bot import kill_command

            await kill_command(update, context)

        mock_sm.send_to_window.assert_called_once_with("@5", "/exit")
        mock_tmux.kill_window.assert_called_once_with("@5")
        mock_sm.unbind_thread.assert_called_once_with(1, 42)
        mock_sm.remove_window.assert_called_once_with("@5")
        mock_clear.assert_called_once_with(1, 42, context.bot, context.user_data)
        assert "Killed session" in mock_reply.await_args.args[1]


class TestRuntimeStatusCommand:
    @pytest.mark.asyncio
    async def test_usage_command_uses_status_for_codex_runtime(self):
        update = _make_update("/usage")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot._get_thread_id", return_value=42),
            patch("ccbot.bot.session_manager") as mock_sm,
            patch("ccbot.bot.tmux_manager") as mock_tmux,
            patch("ccbot.bot.asyncio.sleep", new_callable=AsyncMock),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
            patch(
                "ccbot.terminal_parser.parse_usage_output",
                return_value=None,
            ),
        ):
            mock_sm.resolve_window_for_thread.return_value = "@5"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock(window_id="@5"))
            mock_tmux.capture_pane = AsyncMock(return_value="Codex status output")
            mock_tmux.send_keys = AsyncMock(return_value=True)

            from ccbot.bot import usage_command

            await usage_command(update, context)

        sent_keys = [call.args[1] for call in mock_tmux.send_keys.await_args_list]
        assert sent_keys == ["/status", "Escape"]
        assert "Codex status output" in mock_reply.await_args.args[1]

    def test_runtime_status_bot_command_uses_status_for_codex(self):
        with patch("ccbot.bot.config.runtime", "codex"):
            from ccbot.bot import _runtime_status_bot_command

            assert _runtime_status_bot_command() == ("status", "Show Codex status")

    def test_runtime_status_bot_command_uses_usage_for_claude(self):
        with patch("ccbot.bot.config.runtime", "claude"):
            from ccbot.bot import _runtime_status_bot_command

            assert _runtime_status_bot_command() == (
                "usage",
                "Show Claude Code usage remaining",
            )


class TestRuntimeTextHelpers:
    def test_runtime_monitor_title_is_codex_specific(self):
        with patch("ccbot.bot.config.runtime", "codex"):
            from ccbot.bot import _runtime_monitor_title

            assert _runtime_monitor_title() == "Codex Monitor"

    def test_runtime_forwarded_commands_are_codex_specific(self):
        with patch("ccbot.bot.config.runtime", "codex"):
            from ccbot.bot import _runtime_forwarded_bot_commands

            commands = _runtime_forwarded_bot_commands()
            assert commands == {
                "clear": "↗ Clear conversation history",
                "compact": "↗ Compact conversation context",
                "plan": "↗ Use plan mode for the next task",
            }

    def test_runtime_escape_description_is_codex_specific(self):
        with patch("ccbot.bot.config.runtime", "codex"):
            from ccbot.bot import _runtime_escape_description

            assert _runtime_escape_description() == "Send Escape to interrupt Codex"

    def test_build_bot_commands_uses_codex_menu(self):
        with patch("ccbot.bot.config.runtime", "codex"):
            from ccbot.bot import _build_bot_commands

            commands = {command.command: command.description for command in _build_bot_commands()}

        assert "plan" in commands
        assert "status" in commands
        assert "usage" not in commands
        assert "help" not in commands
        assert "memory" not in commands
        assert "model" not in commands
        assert "cost" not in commands
        assert commands["plan"] == "↗ Use plan mode for the next task"
        assert commands["status"] == "Show Codex status"

    def test_build_bot_commands_uses_claude_menu(self):
        with patch("ccbot.bot.config.runtime", "claude"):
            from ccbot.bot import _build_bot_commands

            commands = {command.command: command.description for command in _build_bot_commands()}

        assert "usage" in commands
        assert "status" not in commands
        assert "plan" not in commands
        assert commands["usage"] == "Show Claude Code usage remaining"
        assert commands["help"] == "↗ Show Claude Code help"
        assert commands["memory"] == "↗ Edit CLAUDE.md"
        assert commands["model"] == "↗ Switch Claude model"


class TestStartCommand:
    @pytest.mark.asyncio
    async def test_start_command_uses_codex_title(self):
        update = _make_update("/start")
        context = _make_context()

        with (
            patch("ccbot.bot.config.runtime", "codex"),
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            await start_command(update, context)

        assert "Codex Monitor" in mock_reply.await_args.args[1]
