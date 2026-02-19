"""Contract tests for the AgentProvider protocol.

Every provider must pass these tests. The PROVIDER_FIXTURES list starts with
StubProvider and will grow as real providers are extracted (Claude, Codex, Gemini).
"""

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from ccbot.providers.base import (
    AgentMessage,
    AgentProvider,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers.claude import ClaudeProvider

# ── Stub provider (minimal conforming implementation) ────────────────────


class StubProvider:
    """Minimal provider that satisfies AgentProvider for contract testing."""

    _CAPS = ProviderCapabilities(
        name="stub",
        launch_command="stub-cli",
        supports_hook=True,
        supports_resume=True,
        supports_continue=True,
        supports_structured_transcript=True,
        transcript_format="jsonl",
        terminal_ui_patterns=("AskUserQuestion",),
        builtin_commands=("help", "clear"),
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        if resume_id and self._CAPS.supports_resume:
            return f"--resume {resume_id}"
        if use_continue and self._CAPS.supports_continue:
            return "--continue"
        return ""

    def parse_hook_payload(self, payload: dict[str, Any]) -> SessionStartEvent | None:
        sid = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        if not sid or not cwd:
            return None
        return SessionStartEvent(
            session_id=sid,
            cwd=cwd,
            transcript_path=payload.get("transcript_path", ""),
            window_key=payload.get("window_key", ""),
        )

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        if not line or not line.strip():
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        messages: list[AgentMessage] = []
        pending = dict(pending_tools)
        for entry in entries:
            msg_type = entry.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue
            content = entry.get("message", {}).get("content", "")
            text, pending = self._extract_content(content, pending)
            if text:
                messages.append(
                    AgentMessage(
                        session_id="stub-session",
                        text=text,
                        role=msg_type,
                        content_type="text",
                    )
                )
        return messages, pending

    @staticmethod
    def _extract_content(
        content: Any, pending: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if isinstance(content, str):
            return content, pending
        if not isinstance(content, list):
            return "", pending
        text = ""
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text += block.get("text", "")
            elif btype == "tool_use" and block.get("id"):
                pending[block["id"]] = block.get("name", "unknown")
            elif btype == "tool_result":
                pending.pop(block.get("tool_use_id", ""), None)
        return text, pending

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        if not pane_text or not pane_text.strip():
            return None
        return StatusUpdate(
            session_id="stub-session",
            raw_text=pane_text.strip(),
            display_label="…working",
        )

    def discover_commands(self, base_dir: str) -> list[str]:
        return list(self._CAPS.builtin_commands)


# ── Fixtures ─────────────────────────────────────────────────────────────

PROVIDER_FIXTURES: list[type] = [StubProvider, ClaudeProvider]


@pytest.fixture(params=PROVIDER_FIXTURES, ids=lambda cls: cls.__name__)
def provider(request: pytest.FixtureRequest) -> AgentProvider:
    return request.param()


# ── Contract tests ───────────────────────────────────────────────────────


class TestAgentProviderCapabilities:
    def test_required_fields(self, provider: AgentProvider) -> None:
        caps = provider.capabilities
        assert caps.name
        assert caps.launch_command

    def test_immutability(self, provider: AgentProvider) -> None:
        caps = provider.capabilities
        with pytest.raises(FrozenInstanceError):
            caps.name = "hacked"  # type: ignore[misc]


class TestMakeLaunchArgs:
    def test_fresh_session_no_flags(self, provider: AgentProvider) -> None:
        result = provider.make_launch_args()
        assert "--resume" not in result
        assert "--continue" not in result

    def test_resume_id_included_when_supported(self, provider: AgentProvider) -> None:
        caps = provider.capabilities
        result = provider.make_launch_args(resume_id="abc-123")
        if caps.supports_resume:
            assert "abc-123" in result
        else:
            assert "abc-123" not in result

    def test_continue_when_supported(self, provider: AgentProvider) -> None:
        caps = provider.capabilities
        result = provider.make_launch_args(use_continue=True)
        if caps.supports_continue:
            assert "--continue" in result


class TestParseHookPayload:
    def test_valid_payload_returns_event(self, provider: AgentProvider) -> None:
        payload = {
            "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "cwd": "/tmp/test",
            "transcript_path": "/tmp/test.jsonl",
            "window_key": "ccbot:@0",
        }
        event = provider.parse_hook_payload(payload)
        if provider.capabilities.supports_hook:
            assert event is not None
            assert isinstance(event, SessionStartEvent)
            assert event.session_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
            assert event.cwd == "/tmp/test"

    def test_invalid_payload_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_hook_payload({}) is None
        assert provider.parse_hook_payload({"session_id": ""}) is None
        assert provider.parse_hook_payload({"session_id": "x"}) is None


class TestParseTranscriptLine:
    def test_empty_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_transcript_line("") is None

    def test_whitespace_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_transcript_line("   ") is None

    def test_invalid_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_transcript_line("not json at all") is None

    def test_valid_returns_dict(self, provider: AgentProvider) -> None:
        line = json.dumps({"type": "assistant", "message": {"content": "hi"}})
        result = provider.parse_transcript_line(line)
        assert isinstance(result, dict)
        assert result["type"] == "assistant"


class TestParseTranscriptEntries:
    def test_empty_returns_empty(self, provider: AgentProvider) -> None:
        messages, pending = provider.parse_transcript_entries([], {})
        assert messages == []
        assert isinstance(pending, dict)

    def test_message_fields(self, provider: AgentProvider) -> None:
        entries = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        ]
        messages, _ = provider.parse_transcript_entries(entries, {})
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, AgentMessage)
        assert msg.text == "hello"
        assert msg.role == "assistant"

    def test_pending_carry_over(self, provider: AgentProvider) -> None:
        entries = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {},
                        }
                    ]
                },
            }
        ]
        _, pending = provider.parse_transcript_entries(entries, {})
        assert "t1" in pending

    def test_pending_resolved_on_result(self, provider: AgentProvider) -> None:
        entries = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                    ]
                },
            },
        ]
        _, pending = provider.parse_transcript_entries(entries, {})
        assert "t1" not in pending


class TestParseTerminalStatus:
    def test_empty_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_terminal_status("") is None

    def test_status_update_fields(self, provider: AgentProvider) -> None:
        result = provider.parse_terminal_status("✻ Reading files\n" + "─" * 30 + "\n")
        assert result is not None
        assert isinstance(result, StatusUpdate)
        assert isinstance(result.raw_text, str)
        assert isinstance(result.display_label, str)
        assert result.is_interactive is False


class TestDiscoverCommands:
    def test_returns_list_of_strings(self, provider: AgentProvider) -> None:
        result = provider.discover_commands("/tmp/nonexistent")
        assert isinstance(result, list)
        assert all(isinstance(c, str) for c in result)

    def test_builtins_included(self, provider: AgentProvider) -> None:
        result = provider.discover_commands("/tmp/nonexistent")
        for cmd in provider.capabilities.builtin_commands:
            assert cmd in result
