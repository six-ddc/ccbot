"""Provider-specific tests for Codex and Gemini (JsonlProvider subclasses).

Tests behavior that differs from the generic contract tests: resume syntax,
builtin command sets, capability flags, and shared JSONL parsing edge cases.
"""

import pytest

from ccbot.providers._jsonl import extract_content_blocks, parse_jsonl_line
from ccbot.providers.codex import CodexProvider
from ccbot.providers.gemini import GeminiProvider

# ── Shared hookless-provider tests (parametrized) ────────────────────────

HOOKLESS_PROVIDERS = [CodexProvider, GeminiProvider]


@pytest.fixture(params=HOOKLESS_PROVIDERS, ids=lambda cls: cls.__name__)
def hookless(request: pytest.FixtureRequest):
    return request.param()


class TestHooklessCapabilities:
    def test_hookless_flags(self, hookless) -> None:
        caps = hookless.capabilities
        assert caps.supports_hook is False
        assert caps.supports_continue is False
        assert caps.terminal_ui_patterns == ()

    def test_invalid_resume_id_raises(self, hookless) -> None:
        with pytest.raises(ValueError, match="Invalid resume_id"):
            hookless.make_launch_args(resume_id="abc; rm -rf /")


# ── Codex-specific ───────────────────────────────────────────────────────


class TestCodexLaunchArgs:
    def test_resume_uses_subcommand(self) -> None:
        codex = CodexProvider()
        result = codex.make_launch_args(resume_id="abc-123")
        assert result == "exec resume abc-123"


class TestCodexCommands:
    def test_returns_builtins(self) -> None:
        codex = CodexProvider()
        result = codex.discover_commands("/tmp/nonexistent")
        names = [c.name for c in result]
        assert len(result) == len(codex.capabilities.builtin_commands)
        for cmd in ("/exit", "/model", "/status", "/mode"):
            assert cmd in names


# ── Gemini-specific ──────────────────────────────────────────────────────


class TestGeminiLaunchArgs:
    def test_resume_uses_flag(self) -> None:
        gemini = GeminiProvider()
        result = gemini.make_launch_args(resume_id="abc-123")
        assert result == "--resume abc-123"


class TestGeminiCommands:
    def test_returns_builtins(self) -> None:
        gemini = GeminiProvider()
        result = gemini.discover_commands("/tmp/nonexistent")
        names = [c.name for c in result]
        assert len(result) == len(gemini.capabilities.builtin_commands)
        for cmd in ("/clear", "/model", "/stats", "/resume", "/directories"):
            assert cmd in names


# ── JSONL parsing edge cases (extract_content_blocks) ────────────────────


class TestParseJsonlLine:
    def test_json_array_returns_none(self) -> None:
        assert parse_jsonl_line("[1, 2, 3]") is None

    def test_json_string_returns_none(self) -> None:
        assert parse_jsonl_line('"just a string"') is None

    def test_json_number_returns_none(self) -> None:
        assert parse_jsonl_line("42") is None


class TestExtractContentBlocks:
    def test_string_content(self) -> None:
        text, ct, pending = extract_content_blocks("hello world", {})
        assert text == "hello world"
        assert ct == "text"

    def test_non_list_non_string_returns_empty(self) -> None:
        text, ct, pending = extract_content_blocks(42, {})
        assert text == ""
        assert ct == "text"

    def test_none_content_returns_empty(self) -> None:
        text, ct, pending = extract_content_blocks(None, {})
        assert text == ""
        assert ct == "text"

    def test_non_dict_blocks_skipped(self) -> None:
        text, ct, pending = extract_content_blocks(["not a dict", 42], {})
        assert text == ""

    def test_tool_use_tracked_in_pending(self) -> None:
        blocks = [{"type": "tool_use", "id": "t1", "name": "Read"}]
        _, ct, pending = extract_content_blocks(blocks, {})
        assert ct == "tool_use"
        assert pending == {"t1": "Read"}

    def test_tool_result_clears_pending(self) -> None:
        blocks = [{"type": "tool_result", "tool_use_id": "t1"}]
        _, ct, pending = extract_content_blocks(blocks, {"t1": "Read"})
        assert ct == "tool_result"
        assert "t1" not in pending

    def test_tool_result_without_id_does_not_pop_empty(self) -> None:
        blocks = [{"type": "tool_result"}]
        pending = {"t1": "Read"}
        _, _, result = extract_content_blocks(blocks, pending)
        assert result == {"t1": "Read"}
