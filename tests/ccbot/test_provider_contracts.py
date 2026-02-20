"""Contract tests for the AgentProvider protocol.

Every provider must pass these tests. The PROVIDER_FIXTURES list contains
StubProvider plus all real providers (Claude, Codex, Gemini).
"""

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from ccbot.providers.base import (
    AgentMessage,
    AgentProvider,
    DiscoveredCommand,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers._jsonl import JsonlProvider
from ccbot.providers.claude import ClaudeProvider
from ccbot.providers.codex import CodexProvider
from ccbot.providers.gemini import GeminiProvider

# ── Stub provider (minimal conforming implementation) ────────────────────


class StubProvider(JsonlProvider):
    """Minimal provider that satisfies AgentProvider for contract testing.

    Extends JsonlProvider with hook support and continue flag to exercise
    all contract test branches that real hookless providers skip.
    """

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

    _BUILTINS = {"help": "Show help", "clear": "Clear screen"}

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


# ── Fixtures ─────────────────────────────────────────────────────────────

PROVIDER_FIXTURES: list[type] = [
    StubProvider,
    ClaudeProvider,
    CodexProvider,
    GeminiProvider,
]


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
        resume_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = provider.make_launch_args(resume_id=resume_id)
        if caps.supports_resume:
            assert resume_id in result
        else:
            assert resume_id not in result

    def test_continue_when_supported(self, provider: AgentProvider) -> None:
        caps = provider.capabilities
        result = provider.make_launch_args(use_continue=True)
        if caps.supports_continue:
            assert "--continue" in result
        else:
            assert "--continue" not in result


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
        else:
            assert event is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"session_id": ""},
            {"session_id": "x"},
        ],
        ids=["empty", "empty_sid", "invalid_sid_no_cwd"],
    )
    def test_invalid_payload_returns_none(
        self, provider: AgentProvider, payload: dict[str, Any]
    ) -> None:
        assert provider.parse_hook_payload(payload) is None


class TestParseTranscriptLine:
    @pytest.mark.parametrize(
        "line",
        ["", "   ", "not json at all"],
        ids=["empty", "whitespace", "invalid"],
    )
    def test_invalid_returns_none(self, provider: AgentProvider, line: str) -> None:
        assert provider.parse_transcript_line(line) is None

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

    def test_interactive_ui_detected(self, provider: AgentProvider) -> None:
        pane = (
            "  Would you like to proceed?\n"
            "  ─────────────────────────────────\n"
            "  Yes     No\n"
            "  ─────────────────────────────────\n"
            "  ctrl-g to edit in vim\n"
        )
        result = provider.parse_terminal_status(pane)
        if result is not None and result.is_interactive:
            assert result.ui_type is not None
            assert isinstance(result.ui_type, str)


class TestExtractBashOutput:
    def test_returns_none_for_empty(self, provider: AgentProvider) -> None:
        assert provider.extract_bash_output("", "ls") is None

    def test_returns_none_when_command_not_found(self, provider: AgentProvider) -> None:
        assert provider.extract_bash_output("some text\nno command here", "ls") is None

    def test_returns_output_when_command_found(self, provider: AgentProvider) -> None:
        pane = "some text\n! ls -la\ntotal 42\n"
        result = provider.extract_bash_output(pane, "ls")
        assert result is not None
        assert result.startswith("! ls")


class TestIsUserTranscriptEntry:
    @pytest.mark.parametrize(
        "entry,expected",
        [
            ({"type": "user"}, True),
            ({"type": "assistant"}, False),
            ({}, False),
        ],
        ids=["user", "assistant", "empty"],
    )
    def test_detection(
        self, provider: AgentProvider, entry: dict[str, Any], expected: bool
    ) -> None:
        assert provider.is_user_transcript_entry(entry) is expected


class TestParseHistoryEntry:
    def test_non_message_returns_none(self, provider: AgentProvider) -> None:
        assert provider.parse_history_entry({"type": "summary"}) is None

    @pytest.mark.parametrize(
        "role,text",
        [("assistant", "hello world"), ("user", "my question")],
        ids=["assistant", "user"],
    )
    def test_message_parsed(
        self, provider: AgentProvider, role: str, text: str
    ) -> None:
        entry = {
            "type": role,
            "message": {"content": [{"type": "text", "text": text}]},
        }
        result = provider.parse_history_entry(entry)
        assert result is not None
        assert isinstance(result, AgentMessage)
        assert result.role == role
        assert result.text == text

    def test_empty_content_returns_none(self, provider: AgentProvider) -> None:
        entry = {
            "type": "assistant",
            "message": {"content": []},
        }
        assert provider.parse_history_entry(entry) is None


class TestDiscoverCommands:
    def test_returns_list_of_discovered_commands(self, provider: AgentProvider) -> None:
        result = provider.discover_commands("/tmp/nonexistent")
        assert isinstance(result, list)
        assert all(isinstance(c, DiscoveredCommand) for c in result)
        for c in result:
            assert c.name
            assert isinstance(c.description, str)
            assert isinstance(c.source, str)

    def test_builtins_included(self, provider: AgentProvider) -> None:
        result = provider.discover_commands("/tmp/nonexistent")
        names = [c.name for c in result]
        for cmd in provider.capabilities.builtin_commands:
            assert cmd in names
