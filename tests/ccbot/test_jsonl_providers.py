"""Provider-specific tests for Codex and Gemini (JsonlProvider subclasses).

Tests behavior that differs from the generic contract tests: resume syntax,
builtin command sets, and capability flags specific to each provider.
"""

import pytest

from ccbot.providers.codex import CodexProvider
from ccbot.providers.gemini import GeminiProvider

# ── Shared hookless-provider tests (parametrized) ────────────────────────

HOOKLESS_PROVIDERS = [CodexProvider, GeminiProvider]


@pytest.fixture(params=HOOKLESS_PROVIDERS, ids=lambda cls: cls.__name__)
def hookless(request: pytest.FixtureRequest):
    return request.param()


class TestHooklessCapabilities:
    def test_no_hook_support(self, hookless) -> None:
        assert hookless.capabilities.supports_hook is False

    def test_continue_not_supported(self, hookless) -> None:
        assert hookless.capabilities.supports_continue is False

    def test_no_terminal_ui_patterns(self, hookless) -> None:
        assert hookless.capabilities.terminal_ui_patterns == ()

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
        assert len(result) == 4
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
        assert len(result) == 11
        for cmd in ("/clear", "/model", "/stats", "/resume", "/directories"):
            assert cmd in names
