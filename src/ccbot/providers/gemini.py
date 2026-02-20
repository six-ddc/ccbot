"""Gemini CLI provider — Google's terminal agent behind AgentProvider protocol.

MVP implementation: Gemini CLI uses directory-scoped sessions with automatic
persistence. Resume uses ``--resume <id>`` flag syntax. No SessionStart hook —
session detection requires external wrapping. Terminal UI is a stable GUI-like
renderer with sticky headers; patterns TBD pending empirical characterization.
"""

import logging
from typing import Any

from ccbot.providers.base import (
    AgentMessage,
    DiscoveredCommand,
    ProviderCapabilities,
    RESUME_ID_RE,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers._jsonl import (
    extract_bang_output,
    extract_content_blocks,
    is_user_entry,
    parse_jsonl_entries,
    parse_jsonl_history_entry,
    parse_jsonl_line,
    parse_last_line_status,
)

logger = logging.getLogger(__name__)

# Gemini CLI known slash commands.
_GEMINI_BUILTINS: dict[str, str] = {
    "/clear": "Clear screen and chat context",
    "/model": "Switch model mid-session",
    "/compress": "Summarize chat context to save tokens",
    "/copy": "Copy last response to clipboard",
    "/help": "Display available commands",
    "/commands": "Manage custom commands",
    "/mcp": "List MCP servers and tools",
    "/stats": "Show session statistics",
    "/resume": "Browse and select previous sessions",
    "/bug": "File issue or bug report",
    "/directories": "Manage accessible directories",
}


class GeminiProvider:
    """AgentProvider implementation for Google Gemini CLI."""

    _CAPS = ProviderCapabilities(
        name="gemini",
        launch_command="gemini",
        supports_hook=False,
        supports_resume=True,
        supports_continue=False,
        supports_structured_transcript=True,
        transcript_format="jsonl",
        terminal_ui_patterns=(),
        builtin_commands=tuple(_GEMINI_BUILTINS.keys()),
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,  # noqa: ARG002 — protocol signature
    ) -> str:
        """Build Gemini CLI args for launching or resuming a session.

        Resume uses ``--resume <id>`` flag syntax.
        Continue is not supported.
        """
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        return ""

    def parse_hook_payload(
        self,
        payload: dict[str, Any],  # noqa: ARG002 — protocol signature
    ) -> SessionStartEvent | None:
        """Gemini has no SessionStart hook — always returns None."""
        return None

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        return parse_jsonl_line(line)

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        return parse_jsonl_entries(entries, pending_tools)

    # Keep as static for direct access in tests that use the class method.
    _extract_content = staticmethod(extract_content_blocks)

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        return parse_last_line_status(pane_text)

    def extract_bash_output(self, pane_text: str, command: str) -> str | None:
        return extract_bang_output(pane_text, command)

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool:
        return is_user_entry(entry)

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None:
        return parse_jsonl_history_entry(entry)

    def discover_commands(
        self,
        base_dir: str,  # noqa: ARG002 — protocol signature
    ) -> list[DiscoveredCommand]:
        """Return Gemini built-in slash commands (no custom command discovery)."""
        return [
            DiscoveredCommand(name=name, description=desc, source="builtin")
            for name, desc in _GEMINI_BUILTINS.items()
        ]
