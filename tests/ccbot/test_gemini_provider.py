"""Gemini-specific provider tests — behavior that differs from the generic contracts."""

import pytest

from ccbot.providers.gemini import GeminiProvider


@pytest.fixture
def gemini() -> GeminiProvider:
    return GeminiProvider()


class TestGeminiCapabilities:
    def test_name(self, gemini: GeminiProvider) -> None:
        assert gemini.capabilities.name == "gemini"

    def test_no_hook_support(self, gemini: GeminiProvider) -> None:
        assert gemini.capabilities.supports_hook is False

    def test_continue_not_supported(self, gemini: GeminiProvider) -> None:
        assert gemini.capabilities.supports_continue is False

    def test_no_terminal_ui_patterns(self, gemini: GeminiProvider) -> None:
        assert gemini.capabilities.terminal_ui_patterns == ()


class TestGeminiLaunchArgs:
    def test_resume_uses_flag(self, gemini: GeminiProvider) -> None:
        result = gemini.make_launch_args(resume_id="abc-123")
        assert result == "--resume abc-123"

    def test_invalid_resume_id_raises(self, gemini: GeminiProvider) -> None:
        with pytest.raises(ValueError, match="Invalid resume_id"):
            gemini.make_launch_args(resume_id="abc; rm -rf /")


class TestGeminiCommands:
    def test_returns_builtins(self, gemini: GeminiProvider) -> None:
        result = gemini.discover_commands("/tmp/nonexistent")
        assert len(result) == 11
        names = [c.name for c in result]
        assert "/clear" in names
        assert "/model" in names
        assert "/stats" in names
        assert "/resume" in names
        assert "/directories" in names
