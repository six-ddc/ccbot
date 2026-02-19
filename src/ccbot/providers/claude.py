"""Claude Code provider — wraps existing modules behind AgentProvider protocol.

Delegates to hook.py, transcript_parser.py, terminal_parser.py, and
cc_commands.py without changing any behavior. This is a thin adapter layer
that translates between the provider protocol and existing module APIs.
"""

import os
from pathlib import Path
from typing import Any

from ccbot.cc_commands import CC_BUILTINS, discover_cc_commands
from ccbot.hook import _UUID_RE
from ccbot.providers.base import (
    AgentMessage,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers.registry import registry
from ccbot.terminal_parser import (
    UI_PATTERNS,
    extract_interactive_content,
    format_status_display,
    parse_status_line,
)
from ccbot.transcript_parser import TranscriptParser


class ClaudeProvider:
    """AgentProvider implementation for Claude Code CLI."""

    _CAPS = ProviderCapabilities(
        name="claude",
        launch_command="claude",
        supports_hook=True,
        supports_resume=True,
        supports_continue=True,
        supports_structured_transcript=True,
        transcript_format="jsonl",
        terminal_ui_patterns=tuple(p.name for p in UI_PATTERNS),
        builtin_commands=tuple(CC_BUILTINS.keys()),
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        if resume_id:
            return f"--resume {resume_id}"
        if use_continue:
            return "--continue"
        return ""

    def parse_hook_payload(self, payload: dict[str, Any]) -> SessionStartEvent | None:
        session_id = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        transcript_path = payload.get("transcript_path", "")
        window_key = payload.get("window_key", "")

        if not session_id or not cwd:
            return None

        if not _UUID_RE.match(session_id):
            return None

        if not os.path.isabs(cwd):
            return None

        return SessionStartEvent(
            session_id=session_id,
            cwd=cwd,
            transcript_path=transcript_path,
            window_key=window_key,
        )

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        return TranscriptParser.parse_line(line)

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        parsed, remaining = TranscriptParser.parse_entries(entries, pending_tools)

        messages: list[AgentMessage] = []
        for entry in parsed:
            messages.append(
                AgentMessage(
                    session_id="",
                    text=entry.text,
                    role=entry.role,  # type: ignore[arg-type]
                    content_type=entry.content_type,  # type: ignore[arg-type]
                    tool_use_id=entry.tool_use_id,
                    tool_name=entry.tool_name,
                )
            )

        return messages, remaining

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        interactive = extract_interactive_content(pane_text)
        if interactive:
            return StatusUpdate(
                session_id="",
                raw_text=interactive.content,
                display_label=interactive.name,
                is_interactive=True,
                ui_type=interactive.name,
            )

        raw_status = parse_status_line(pane_text)
        if raw_status:
            return StatusUpdate(
                session_id="",
                raw_text=raw_status,
                display_label=format_status_display(raw_status),
            )

        return None

    def discover_commands(self, base_dir: str) -> list[str]:
        claude_dir = Path(base_dir) if base_dir else None
        commands = discover_cc_commands(claude_dir)
        return [cmd.name for cmd in commands]


registry.register("claude", ClaudeProvider)
