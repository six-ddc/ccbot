"""Shared JSONL transcript parsing for providers with content-block format.

Codex and Gemini both use the same JSONL transcript structure (OpenAI-style
content blocks). This module extracts the common parsing logic so both
providers delegate here instead of duplicating ~100 lines each.

Shared helpers:
  - parse_jsonl_line: parse a single JSONL line
  - parse_jsonl_entries: parse a batch of entries into AgentMessages
  - extract_content_blocks: extract text + track tool_use/tool_result
  - parse_last_line_status: parse terminal pane last line as status
  - extract_bang_output: extract ``!`` command output from pane text
  - is_user_entry: check if entry is a human turn
  - parse_jsonl_history_entry: parse a single entry for history display
"""

import json
from typing import Any, cast

from ccbot.providers.base import (
    AgentMessage,
    ContentType,
    MessageRole,
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
