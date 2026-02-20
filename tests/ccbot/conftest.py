"""Shared fixtures for ccbot unit tests.

Provides factories for building JSONL entries, content blocks,
and sample pane text for terminal parser tests.
"""

import time
from unittest.mock import MagicMock

import pytest

from ccbot.providers.base import AgentProvider, StatusUpdate


def make_mock_provider(
    *, has_status: bool = False, interactive: bool = False
) -> MagicMock:
    """Build a mock provider with parse_terminal_status configured.

    Plain helper (not a fixture) because callers need it inside ``patch()``
    context managers where fixture injection isn't available.
    """
    provider = MagicMock(spec=AgentProvider)
    if has_status:
        status = StatusUpdate(
            session_id="",
            raw_text="Working...",
            display_label="…working",
            is_interactive=interactive,
            ui_type="AskUserQuestion" if interactive else None,
        )
        provider.parse_terminal_status.return_value = status
    else:
        provider.parse_terminal_status.return_value = None
    return provider


# ── JSONL entry factories ────────────────────────────────────────────────


@pytest.fixture
def make_jsonl_entry():
    """Factory: build a raw JSONL dict (pre-parse_line)."""

    def _make(
        msg_type: str = "assistant",
        content: list | str = "",
        *,
        timestamp: str | None = None,
        session_id: str = "test-session-id",
        cwd: str = "/tmp/test",
    ) -> dict:
        entry: dict = {
            "type": msg_type,
            "message": {"content": content},
            "sessionId": session_id,
            "cwd": cwd,
        }
        if timestamp:
            entry["timestamp"] = timestamp
        else:
            entry["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return entry

    return _make


@pytest.fixture
def make_text_block():
    """Factory: build a text content block."""

    def _make(text: str) -> dict:
        return {"type": "text", "text": text}

    return _make


@pytest.fixture
def make_tool_use_block():
    """Factory: build a tool_use content block."""

    def _make(
        tool_id: str = "tool_1",
        name: str = "Read",
        input_data: dict | None = None,
    ) -> dict:
        return {
            "type": "tool_use",
            "id": tool_id,
            "name": name,
            "input": input_data or {},
        }

    return _make


@pytest.fixture
def make_tool_result_block():
    """Factory: build a tool_result content block."""

    def _make(
        tool_use_id: str = "tool_1",
        content: str | list = "result text",
        *,
        is_error: bool = False,
    ) -> dict:
        block: dict = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        return block

    return _make


@pytest.fixture
def make_thinking_block():
    """Factory: build a thinking content block."""

    def _make(thinking: str = "deep thoughts") -> dict:
        return {"type": "thinking", "thinking": thinking}

    return _make


# ── Sample pane text for terminal parser ─────────────────────────────────


@pytest.fixture
def sample_pane_exit_plan():
    return (
        "  Would you like to proceed?\n"
        "  ─────────────────────────────────\n"
        "  Yes     No\n"
        "  ─────────────────────────────────\n"
        "  ctrl-g to edit in vim\n"
    )


@pytest.fixture
def sample_pane_ask_user_multi_tab():
    return "  ←  ☐ Option A\n     ☐ Option B\n     ☐ Option C\n  Enter to select\n"


@pytest.fixture
def sample_pane_ask_user_single_tab():
    return "  ☐ Option A\n  ☐ Option B\n  Enter to select\n"


@pytest.fixture
def sample_pane_permission():
    return "  Do you want to proceed?\n  Some permission details\n  Esc to cancel\n"


@pytest.fixture
def sample_pane_status_line():
    sep = "─" * 30
    return f"Some output text here\nMore output\n✻ Reading file src/main.py\n{sep}\n"


@pytest.fixture
def sample_pane_no_ui():
    return "$ echo hello\nhello\n$\n"
