"""Tests for Codex request_user_input handling in the Telegram bot."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.bot import callback_handler, handle_new_message, text_handler
from ccbot.handlers.callback_data import CB_CODEX_PROMPT_OPTION
from ccbot.session_monitor import NewMessage
from ccbot.transcript_parser import (
    CodexPromptOption,
    CodexPromptPayload,
    CodexPromptQuestion,
)


def _make_prompt() -> CodexPromptPayload:
    return CodexPromptPayload(
        questions=(
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
            ),
        )
    )


def _make_text_update(text: str, thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 1
    update.effective_chat = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 100
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    return update


def _make_callback_update(data: str, thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 1
    update.effective_chat = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 100
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.message_thread_id = thread_id
    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


class TestCodexPromptBotFlow:
    @pytest.mark.asyncio
    async def test_text_handler_uses_pending_notes_submission(self):
        update = _make_text_update("Inspect deployment scripts")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot.has_codex_prompt", return_value=True),
            patch("ccbot.bot.is_waiting_for_codex_notes_text", return_value=True),
            patch(
                "ccbot.bot.submit_codex_prompt_notes_text",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
            patch("ccbot.bot.session_manager") as mock_sm,
        ):
            mock_submit.return_value = (True, "Saved")

            await text_handler(update, context)

        mock_submit.assert_called_once_with(
            context.bot, 1, 42, "Inspect deployment scripts"
        )
        mock_reply.assert_not_called()
        mock_sm.get_window_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_handler_blocks_plain_text_while_prompt_pending(self):
        update = _make_text_update("hello")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch("ccbot.bot.has_codex_prompt", return_value=True),
            patch("ccbot.bot.is_waiting_for_codex_notes_text", return_value=False),
            patch("ccbot.bot.safe_reply", new_callable=AsyncMock) as mock_reply,
            patch("ccbot.bot.session_manager") as mock_sm,
        ):
            await text_handler(update, context)

        mock_reply.assert_called_once()
        assert "pending Codex question" in mock_reply.call_args.args[1]
        assert "Add notes" in mock_reply.call_args.args[1]
        mock_sm.get_window_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_handler_routes_codex_option(self):
        update = _make_callback_update(f"{CB_CODEX_PROMPT_OPTION}0:1:@5")
        context = _make_context()

        with (
            patch("ccbot.bot.is_user_allowed", return_value=True),
            patch(
                "ccbot.bot.advance_codex_prompt_with_option",
                new_callable=AsyncMock,
            ) as mock_advance,
            patch("ccbot.bot.session_manager") as mock_sm,
        ):
            mock_advance.return_value = (True, "Saved")

            await callback_handler(update, context)

        mock_sm.set_group_chat_id.assert_called_once_with(1, 42, 100)
        mock_advance.assert_called_once_with(
            context.bot,
            1,
            "@5",
            42,
            0,
            1,
        )
        update.callback_query.answer.assert_called_once_with("Saved", show_alert=False)

    @pytest.mark.asyncio
    async def test_handle_new_message_renders_codex_prompt_instead_of_queueing(self):
        bot = AsyncMock()
        prompt = _make_prompt()
        msg = NewMessage(
            session_id="session-1",
            text="**request_user_input**(Which scope?)",
            is_complete=False,
            content_type="tool_use",
            tool_use_id="call-1",
            tool_name="request_user_input",
            interactive_prompt=prompt,
        )

        with (
            patch("ccbot.bot.session_manager") as mock_sm,
            patch(
                "ccbot.bot.handle_codex_prompt",
                new_callable=AsyncMock,
            ) as mock_handle_prompt,
            patch("ccbot.bot.get_message_queue", return_value=None),
            patch(
                "ccbot.bot.enqueue_content_message",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_sm.find_users_for_session = AsyncMock(return_value=[(1, "@5", 42)])
            mock_sm.resolve_session_for_window = AsyncMock(
                return_value=SimpleNamespace(file_path=None)
            )
            mock_handle_prompt.return_value = True

            await handle_new_message(msg, bot)

        mock_handle_prompt.assert_called_once_with(
            bot,
            1,
            "@5",
            prompt,
            "call-1",
            42,
        )
        mock_enqueue.assert_not_called()
