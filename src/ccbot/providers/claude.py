"""Claude Code provider — wraps existing modules behind AgentProvider protocol.

Delegates to hook.py, transcript_parser.py, terminal_parser.py, and
cc_commands.py without changing any behavior. This is a thin adapter layer
that translates between the provider protocol and existing module APIs.
"""

import os
from pathlib import Path
from typing import Any, cast

from ccbot.cc_commands import CC_BUILTINS, discover_cc_commands
from ccbot.hook import UUID_RE
from ccbot.providers.base import (
    AgentMessage,
    ContentType,
    DiscoveredCommand,
    MessageRole,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)

from ccbot.terminal_parser import (
    UI_PATTERNS,
    extract_bash_output,
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
        """Build Claude Code CLI args string for launching or resuming a session."""
        if resume_id:
            if not UUID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        if use_continue:
            return "--continue"
        return ""

    def parse_hook_payload(self, payload: dict[str, Any]) -> SessionStartEvent | None:
        """Parse a Claude Code SessionStart hook payload.

        Validates session_id (UUID format), cwd (absolute path), and
        rejects payloads missing required fields.
        """
        session_id = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        transcript_path = payload.get("transcript_path", "")
        window_key = payload.get("window_key", "")

        if not session_id or not cwd:
            return None

        if not UUID_RE.match(session_id):
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
        """Delegate to TranscriptParser.parse_line."""
        return TranscriptParser.parse_line(line)

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        """Parse JSONL entries via TranscriptParser and wrap as AgentMessages."""
        parsed, remaining = TranscriptParser.parse_entries(entries, pending_tools)

        messages = [
            AgentMessage(
                text=e.text,
                role=cast(MessageRole, e.role),
                content_type=cast(ContentType, e.content_type),
                tool_use_id=e.tool_use_id,
                tool_name=e.tool_name,
                timestamp=e.timestamp,
            )
            for e in parsed
        ]

        return messages, remaining

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        """Parse pane text; interactive UI takes precedence over status line."""
        interactive = extract_interactive_content(pane_text)
        if interactive:
            return StatusUpdate(
                raw_text=interactive.content,
                display_label=interactive.name,
                is_interactive=True,
                ui_type=interactive.name,
            )

        raw_status = parse_status_line(pane_text)
        if raw_status:
            return StatusUpdate(
                raw_text=raw_status,
                display_label=format_status_display(raw_status),
            )

        return None

    def extract_bash_output(self, pane_text: str, command: str) -> str | None:
        return extract_bash_output(pane_text, command)

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool:
        return TranscriptParser.is_user_message(entry)

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None:
        """Parse a single transcript entry for history display."""
        raw_role = entry.get("type", "assistant")
        if raw_role not in ("user", "assistant"):
            return None
        parsed = TranscriptParser.parse_message(entry)
        if parsed is None or not parsed.text:
            return None
        role = cast(MessageRole, raw_role)
        # "user"/"assistant" message_type maps to "text"; others pass through.
        raw_ct = (
            "text"
            if parsed.message_type in ("user", "assistant")
            else parsed.message_type
        )
        content_type = cast(ContentType, raw_ct)
        return AgentMessage(
            text=parsed.text,
            role=role,
            content_type=content_type,
            tool_name=parsed.tool_name,
            timestamp=TranscriptParser.get_timestamp(entry),
        )

    def discover_commands(self, base_dir: str) -> list[DiscoveredCommand]:
        claude_dir = Path(base_dir) if base_dir else None
        commands = discover_cc_commands(claude_dir)
        return [
            DiscoveredCommand(
                name=cmd.name, description=cmd.description, source=cmd.source
            )
            for cmd in commands
        ]
