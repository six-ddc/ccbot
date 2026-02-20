"""Codex-specific provider tests — behavior that differs from the generic contracts."""

import pytest

from ccbot.providers.codex import CodexProvider


@pytest.fixture
def codex() -> CodexProvider:
    return CodexProvider()


class TestCodexCapabilities:
    def test_name(self, codex: CodexProvider) -> None:
        assert codex.capabilities.name == "codex"

    def test_no_hook_support(self, codex: CodexProvider) -> None:
        assert codex.capabilities.supports_hook is False

    def test_continue_not_supported(self, codex: CodexProvider) -> None:
        assert codex.capabilities.supports_continue is False

    def test_no_terminal_ui_patterns(self, codex: CodexProvider) -> None:
        assert codex.capabilities.terminal_ui_patterns == ()


class TestCodexLaunchArgs:
    def test_resume_uses_subcommand(self, codex: CodexProvider) -> None:
        result = codex.make_launch_args(resume_id="abc-123")
        assert result == "exec resume abc-123"

    def test_invalid_resume_id_raises(self, codex: CodexProvider) -> None:
        with pytest.raises(ValueError, match="Invalid resume_id"):
            codex.make_launch_args(resume_id="abc; rm -rf /")


class TestCodexCommands:
    def test_returns_builtins(self, codex: CodexProvider) -> None:
        result = codex.discover_commands("/tmp/nonexistent")
        assert len(result) == 4
        names = [c.name for c in result]
        assert "/exit" in names
        assert "/model" in names
        assert "/status" in names
        assert "/mode" in names
