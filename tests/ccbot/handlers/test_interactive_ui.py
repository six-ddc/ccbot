"""Tests for interactive_ui — handle_interactive_ui and keyboard layout."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.interactive_ui import (
    _codex_prompt_states,
    _build_interactive_keyboard,
    _find_codex_prompt_focus_index,
    advance_codex_prompt_with_option,
    arm_codex_prompt_notes_text,
    get_codex_prompt_state,
    handle_codex_prompt,
    handle_interactive_ui,
    submit_codex_prompt_notes_text,
)
from ccbot.handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)
from ccbot.transcript_parser import (
    CodexPromptOption,
    CodexPromptPayload,
    CodexPromptQuestion,
)


def _make_codex_prompt(*, question_count: int = 2) -> CodexPromptPayload:
    questions = [
        CodexPromptQuestion(
            header="Scope",
            question_id="scope",
            question="Which scope?",
            options=(
                CodexPromptOption(
                    label="Full (Recommended)",
                    description="Do the full thing",
                ),
                CodexPromptOption(label="Minimal", description="Do less"),
            ),
        )
    ]
    if question_count > 1:
        questions.append(
            CodexPromptQuestion(
                header="Mode",
                question_id="mode",
                question="Which mode?",
                options=(
                    CodexPromptOption(
                        label="Safe (Recommended)",
                        description="Conservative changes",
                    ),
                    CodexPromptOption(label="Fast", description="Move quickly"),
                ),
            )
        )
    return CodexPromptPayload(questions=tuple(questions))


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 999
    bot.send_message.return_value = sent_msg
    return bot


@pytest.fixture
def _clear_interactive_state():
    """Ensure interactive state is clean before and after each test."""
    from ccbot.handlers.interactive_ui import _interactive_mode, _interactive_msgs

    _interactive_mode.clear()
    _interactive_msgs.clear()
    _codex_prompt_states.clear()
    yield
    _interactive_mode.clear()
    _interactive_msgs.clear()
    _codex_prompt_states.clear()


@pytest.mark.usefixtures("_clear_interactive_state")
class TestHandleInteractiveUI:
    @pytest.mark.asyncio
    async def test_handle_settings_ui_sends_keyboard(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """handle_interactive_ui captures Settings pane, sends message with keyboard."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_sm.resolve_chat_id.return_value = 100

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is True
        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args
        assert call_kwargs.kwargs["chat_id"] == 100
        assert call_kwargs.kwargs["message_thread_id"] == 42
        assert call_kwargs.kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_handle_no_ui_returns_false(self, mock_bot: AsyncMock):
        """Returns False when no interactive UI detected in pane."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
            patch("ccbot.handlers.interactive_ui.session_manager"),
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value="$ echo hello\nhello\n$\n")

            result = await handle_interactive_ui(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

        assert result is False
        mock_bot.send_message.assert_not_called()


class TestKeyboardLayoutForSettings:
    def test_settings_keyboard_includes_all_nav_keys(self):
        """Settings keyboard includes Tab, arrows (not vertical_only), Space, Esc, Enter."""
        keyboard = _build_interactive_keyboard("@5", ui_name="Settings")
        # Flatten all callback data values
        all_cb_data = [
            btn.callback_data for row in keyboard.inline_keyboard for btn in row
        ]
        assert any(CB_ASK_TAB in d for d in all_cb_data if d)
        assert any(CB_ASK_SPACE in d for d in all_cb_data if d)
        assert any(CB_ASK_UP in d for d in all_cb_data if d)
        assert any(CB_ASK_DOWN in d for d in all_cb_data if d)
        assert any(CB_ASK_LEFT in d for d in all_cb_data if d)
        assert any(CB_ASK_RIGHT in d for d in all_cb_data if d)
        assert any(CB_ASK_ESC in d for d in all_cb_data if d)
        assert any(CB_ASK_ENTER in d for d in all_cb_data if d)


@pytest.mark.usefixtures("_clear_interactive_state")
class TestCodexPromptUI:
    def test_find_codex_prompt_focus_index_reads_current_selection(self):
        prompt = _make_codex_prompt(question_count=1)
        question = prompt.questions[0]
        pane = (
            "  Question 1/1 (1 unanswered)\n"
            "  Which scope?\n"
            "\n"
            "    1. Full (Recommended)\n"
            "  › 2. Minimal\n"
            "    3. None of the above\n"
            "\n"
            "  tab to add notes | enter to submit answer | esc to interrupt\n"
        )

        assert _find_codex_prompt_focus_index(pane, question) == 1

    @pytest.mark.asyncio
    async def test_handle_codex_prompt_sends_question_keyboard(
        self,
        mock_bot: AsyncMock,
    ):
        prompt = _make_codex_prompt()

        with patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm:
            mock_sm.resolve_chat_id.return_value = 100

            handled = await handle_codex_prompt(
                mock_bot,
                user_id=1,
                window_id="@5",
                prompt=prompt,
                tool_use_id="call-1",
                thread_id=42,
            )

        assert handled is True
        state = get_codex_prompt_state(1, 42)
        assert state is not None
        assert state.question_index == 0
        mock_bot.send_message.assert_called_once()
        kwargs = mock_bot.send_message.call_args.kwargs
        assert kwargs["chat_id"] == 100
        assert kwargs["message_thread_id"] == 42
        assert "Question 1/2" in kwargs["text"]
        buttons = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "Full (Recommended)" in buttons
        assert "None of the above" in buttons
        assert "Add notes" in buttons

    @pytest.mark.asyncio
    async def test_advance_codex_prompt_option_moves_to_next_question(
        self,
        mock_bot: AsyncMock,
    ):
        prompt = _make_codex_prompt()
        mock_bot.edit_message_text = AsyncMock()

        with (
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = 100
            mock_tmux.send_keys = AsyncMock(return_value=True)
            mock_tmux.capture_pane = AsyncMock(
                return_value=(
                    "  Question 1/2 (2 unanswered)\n"
                    "  Which scope?\n"
                    "\n"
                    "  › 1. Full (Recommended)\n"
                    "    2. Minimal\n"
                    "    3. None of the above\n"
                    "\n"
                    "  tab to add notes | enter to submit answer | esc to interrupt\n"
                )
            )

            await handle_codex_prompt(
                mock_bot,
                user_id=1,
                window_id="@5",
                prompt=prompt,
                tool_use_id="call-2",
                thread_id=42,
            )
            success, message = await advance_codex_prompt_with_option(
                mock_bot,
                user_id=1,
                window_id="@5",
                thread_id=42,
                question_index=0,
                option_index=1,
            )

        assert success is True
        assert message == "Saved"
        state = get_codex_prompt_state(1, 42)
        assert state is not None
        assert state.question_index == 1
        assert state.answers["scope"] == "Minimal"
        sent_keys = [call.args[1] for call in mock_tmux.send_keys.await_args_list]
        assert sent_keys == ["Down", "Enter"]
        mock_bot.edit_message_text.assert_called()

    @pytest.mark.asyncio
    async def test_arm_codex_prompt_notes_updates_message(
        self,
        mock_bot: AsyncMock,
    ):
        prompt = _make_codex_prompt()
        mock_bot.edit_message_text = AsyncMock()

        with patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm:
            mock_sm.resolve_chat_id.return_value = 100
            await handle_codex_prompt(
                mock_bot,
                user_id=1,
                window_id="@5",
                prompt=prompt,
                tool_use_id="call-3",
                thread_id=42,
            )
            success, message = await arm_codex_prompt_notes_text(
                mock_bot,
                user_id=1,
                window_id="@5",
                thread_id=42,
                question_index=0,
            )

        assert success is True
        assert message == "Send notes"
        state = get_codex_prompt_state(1, 42)
        assert state is not None
        assert state.awaiting_notes_text is True
        assert (
            "Send your next text message" in mock_bot.edit_message_text.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_submit_codex_prompt_notes_saves_note_without_submitting(
        self,
        mock_bot: AsyncMock,
    ):
        prompt = _make_codex_prompt(question_count=1)
        mock_bot.edit_message_text = AsyncMock()

        with (
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux,
        ):
            mock_sm.resolve_chat_id.return_value = 100
            mock_tmux.send_keys = AsyncMock(return_value=True)
            await handle_codex_prompt(
                mock_bot,
                user_id=1,
                window_id="@5",
                prompt=prompt,
                tool_use_id="call-4",
                thread_id=42,
            )
            success, message = await arm_codex_prompt_notes_text(
                mock_bot,
                user_id=1,
                window_id="@5",
                thread_id=42,
                question_index=0,
            )
            assert success is True

            success, message = await submit_codex_prompt_notes_text(
                mock_bot,
                user_id=1,
                thread_id=42,
                text="Inspect deployment scripts",
            )

        assert success is True
        assert message == "Notes saved"
        state = get_codex_prompt_state(1, 42)
        assert state is not None
        assert state.notes["scope"] == "Inspect deployment scripts"
        assert state.awaiting_notes_text is False
        mock_tmux.send_keys.assert_not_called()
        assert "Notes: Inspect deployment scripts" in mock_bot.edit_message_text.call_args.kwargs["text"]
