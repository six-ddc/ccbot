"""Tests for workspace-trust dialog detection and auto-answering.

Fixtures are real `tmux capture-pane` outputs from Claude Code 2.1.272.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import ccbot.tmux_manager as tm
from ccbot.terminal_parser import (
    extract_interactive_content,
    find_menu_option,
    is_prompt_ready,
)

FIXTURES = Path(__file__).parent / "fixtures" / "panes"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestDetection:
    def test_trust_dialog_detected(self):
        ui = extract_interactive_content(load("trust_dialog.txt"))
        assert ui is not None
        assert ui.name == "TrustDialog"
        assert "Yes, I trust this folder" in ui.content

    def test_trust_dialog_is_not_prompt_ready(self):
        assert not is_prompt_ready(load("trust_dialog.txt"))

    @pytest.mark.parametrize(
        "name",
        ["ready_bypass.txt", "ready_manual_mode.txt", "ready_labelled_separator.txt"],
    )
    def test_ready_prompt(self, name):
        pane = load(name)
        assert is_prompt_ready(pane)
        assert extract_interactive_content(pane) is None

    def test_option_is_one_below_cursor(self):
        # "❯ No, exit" is highlighted; the shell's "❯ claude" line above
        # must not be taken for the menu cursor.
        offsets = find_menu_option(load("trust_dialog.txt"), "Yes, I trust")
        assert offsets is not None
        cursor, option = offsets
        assert option - cursor == 1

    def test_missing_option(self):
        assert find_menu_option(load("trust_dialog.txt"), "nope") is None


class TestAutoAnswer:
    @pytest.fixture
    def mgr(self, monkeypatch):
        monkeypatch.setattr(tm.asyncio, "sleep", AsyncMock())
        m = tm.TmuxManager(session_name="ccbot")
        m.send_keys = AsyncMock(return_value=True)  # type: ignore[method-assign]
        return m

    @staticmethod
    def _moved(pane: str) -> str:
        return pane.replace(" ❯ No, exit", "   No, exit").replace(
            "   Yes, I trust this folder", " ❯ Yes, I trust this folder"
        )

    @pytest.mark.asyncio
    async def test_moves_down_verifies_then_enter(self, mgr):
        pane = load("trust_dialog.txt")
        mgr.capture_pane = AsyncMock(return_value=self._moved(pane))  # type: ignore[method-assign]
        assert await mgr.auto_answer_dialog("@1", pane) == "TrustDialog"
        keys = [c.args[1] for c in mgr.send_keys.await_args_list]
        assert keys == ["Down", "Enter"]

    @pytest.mark.asyncio
    async def test_does_not_confirm_if_cursor_did_not_move(self, mgr):
        pane = load("trust_dialog.txt")
        mgr.capture_pane = AsyncMock(return_value=pane)  # type: ignore[method-assign]
        assert await mgr.auto_answer_dialog("@1", pane) is None
        keys = [c.args[1] for c in mgr.send_keys.await_args_list]
        assert "Enter" not in keys

    @pytest.mark.asyncio
    async def test_ignores_other_panes(self, mgr):
        assert await mgr.auto_answer_dialog("@1", load("ready_bypass.txt")) is None
        mgr.send_keys.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wait_for_ready_answers_dialog(self, mgr):
        trust = load("trust_dialog.txt")
        mgr.capture_pane = AsyncMock(  # type: ignore[method-assign]
            side_effect=[trust, self._moved(trust), load("ready_bypass.txt")]
        )
        assert await mgr.wait_for_claude_ready("@1", timeout=5) is True
        keys = [c.args[1] for c in mgr.send_keys.await_args_list]
        assert keys == ["Down", "Enter"]
