"""Shared JSONL transcript parsing and base class for JSONL-based providers.

Codex and Gemini both use the same JSONL transcript structure (OpenAI-style
content blocks). This module extracts the common parsing logic and provides
``JsonlProvider`` — a concrete base class that both providers extend with
only their capabilities and launch args.

Shared helpers:
  - parse_jsonl_line: parse a single JSONL line
  - parse_jsonl_entries: parse a batch of entries into AgentMessages
  - extract_content_blocks: extract text + track tool_use/tool_result
  - parse_last_line_status: parse terminal pane last line as status
  - extract_bang_output: extract ``!`` command output from pane text
  - is_user_entry: check if entry is a human turn
  - parse_jsonl_history_entry: parse a single entry for history display

Base class:
  - JsonlProvider: hookless provider with JSONL transcripts
"""

import json
from typing import Any, cast

from ccbot.providers.base import (
    AgentMessage,
    ContentType,
    DiscoveredCommand,
    MessageRole,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL transcript line into a dict."""
    if not line or not line.strip():
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def extract_content_blocks(
    content: Any, pending: dict[str, Any]
) -> tuple[str, ContentType, dict[str, Any]]:
    """Extract text and track tool_use/tool_result from content blocks."""
    if isinstance(content, str):
        return content, "text", pending
    if not isinstance(content, list):
        return "", "text", pending

    text = ""
    content_type: ContentType = "text"
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text += block.get("text", "")
        elif btype == "tool_use" and block.get("id"):
            pending[block["id"]] = block.get("name", "unknown")
            content_type = "tool_use"
        elif btype == "tool_result":
            pending.pop(block.get("tool_use_id", ""), None)
            content_type = "tool_result"
    return text, content_type, pending


def parse_jsonl_entries(
    entries: list[dict[str, Any]],
    pending_tools: dict[str, Any],
) -> tuple[list[AgentMessage], dict[str, Any]]:
    """Parse JSONL entries into AgentMessages with tool tracking."""
    messages: list[AgentMessage] = []
    pending = dict(pending_tools)

    for entry in entries:
        msg_type = entry.get("type", "")
        if msg_type not in ("user", "assistant"):
            continue
        content = entry.get("message", {}).get("content", "")
        text, content_type, pending = extract_content_blocks(content, pending)
        if text:
            messages.append(
                AgentMessage(
                    text=text,
                    role=cast(MessageRole, msg_type),
                    content_type=content_type,
                )
            )
    return messages, pending


def parse_last_line_status(pane_text: str) -> StatusUpdate | None:
    """Parse the last non-empty line of pane text as a basic status update.

    MVP implementation for providers whose TUI patterns are not yet
    characterized — returns the last line as-is without interactive UI
    detection.
    """
    if not pane_text or not pane_text.strip():
        return None
    last_line = pane_text.strip().splitlines()[-1].strip()
    if not last_line:
        return None
    return StatusUpdate(
        raw_text=last_line,
        display_label=last_line,
    )


def extract_bang_output(pane_text: str, command: str) -> str | None:
    """Extract ``!`` command output from captured pane text.

    Looks for a line starting with ``! <command_prefix>`` and returns it.
    Exact format is assumed pending empirical verification.
    """
    if not pane_text or not command:
        return None
    cmd_prefix = command[:10]
    for line in pane_text.splitlines():
        if line.strip().startswith(f"! {cmd_prefix}"):
            return line.strip()
    return None


def is_user_entry(entry: dict[str, Any]) -> bool:
    """Return True if this entry represents a human turn."""
    return entry.get("type") == "user"


def parse_jsonl_history_entry(entry: dict[str, Any]) -> AgentMessage | None:
    """Parse a single JSONL transcript entry for history display."""
    msg_type = entry.get("type", "")
    if msg_type not in ("user", "assistant"):
        return None
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, list):
        text = "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    if not text:
        return None
    return AgentMessage(
        text=text,
        role=cast(MessageRole, msg_type),
        content_type="text",
    )


# ── Base class for hookless JSONL providers ──────────────────────────────


class JsonlProvider:
    """Base class for hookless providers that use JSONL transcripts.

    Subclasses must set ``_CAPS`` and ``_BUILTINS``, and override
    ``make_launch_args`` if their resume syntax differs from ``--resume <id>``.
    All transcript parsing, terminal status, and command discovery are shared.
    """

    _CAPS: ProviderCapabilities
    _BUILTINS: dict[str, str] = {}

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,  # noqa: ARG002 — protocol signature
    ) -> str:
        from ccbot.providers.base import RESUME_ID_RE

        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        return ""

    def parse_hook_payload(
        self,
        payload: dict[str, Any],  # noqa: ARG002 — protocol signature
    ) -> SessionStartEvent | None:
        return None

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        return parse_jsonl_line(line)

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        return parse_jsonl_entries(entries, pending_tools)

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
        return [
            DiscoveredCommand(name=name, description=desc, source="builtin")
            for name, desc in self._BUILTINS.items()
        ]
