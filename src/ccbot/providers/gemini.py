"""Gemini CLI provider — Google's terminal agent behind AgentProvider protocol.

MVP implementation: Gemini CLI uses directory-scoped sessions with automatic
persistence. Resume uses ``--resume <id>`` flag syntax. No SessionStart hook —
session detection requires external wrapping. Terminal UI is a stable GUI-like
renderer with sticky headers; patterns TBD pending empirical characterization.
"""

from ccbot.providers.base import ProviderCapabilities
from ccbot.providers._jsonl import JsonlProvider

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


class GeminiProvider(JsonlProvider):
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

    _BUILTINS = _GEMINI_BUILTINS
