"""Codex CLI provider — OpenAI's terminal agent behind AgentProvider protocol.

MVP implementation: Codex CLI uses a similar tmux-based launch model but differs
in hook mechanism (no SessionStart hook), resume syntax (subcommand not flag),
and terminal UI patterns (Rust TUI, patterns TBD). Unsupported capabilities are
explicitly declared so callers can gate behavior.
"""

from ccbot.providers.base import RESUME_ID_RE, ProviderCapabilities
from ccbot.providers._jsonl import JsonlProvider

# Codex CLI known slash commands.
_CODEX_BUILTINS: dict[str, str] = {
    "/exit": "Close session",
    "/model": "Switch model or reasoning level",
    "/status": "Show session ID",
    "/mode": "Switch approval mode",
}


class CodexProvider(JsonlProvider):
    """AgentProvider implementation for OpenAI Codex CLI."""

    _CAPS = ProviderCapabilities(
        name="codex",
        launch_command="codex",
        supports_hook=False,
        supports_resume=True,
        supports_continue=False,
        supports_structured_transcript=True,
        transcript_format="jsonl",
        terminal_ui_patterns=(),
        builtin_commands=tuple(_CODEX_BUILTINS.keys()),
    )

    _BUILTINS = _CODEX_BUILTINS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,  # noqa: ARG002 — protocol signature
    ) -> str:
        """Build Codex CLI args for launching or resuming a session.

        Resume uses ``exec resume <id>`` subcommand syntax (not a flag).
        """
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"exec resume {resume_id}"
        return ""
