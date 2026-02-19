"""Tests for provider registry, capability policy, and config integration."""

from typing import Any
from unittest.mock import patch

import pytest

from ccbot.providers.base import (
    AgentMessage,
    AgentProvider,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccbot.providers.policy import CapabilityPolicy
from ccbot.providers.registry import ProviderRegistry, UnknownProviderError, registry


class _StubProvider:
    """Minimal provider for registry/policy tests (no cross-file import)."""

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
        self, resume_id: str | None = None, use_continue: bool = False
    ) -> str:
        return ""

    def parse_hook_payload(self, payload: dict[str, Any]) -> SessionStartEvent | None:
        return None

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        return None

    def parse_transcript_entries(
        self, entries: list[dict[str, Any]], pending_tools: dict[str, Any]
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        return [], {}

    def parse_terminal_status(self, pane_text: str) -> StatusUpdate | None:
        return None

    def discover_commands(self, base_dir: str) -> list[str]:
        return list(self._CAPS.builtin_commands)


# ── Registry tests ──────────────────────────────────────────────────────


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        provider = reg.get("stub")
        assert provider.capabilities.name == "stub"

    def test_get_unknown_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(UnknownProviderError, match="nope"):
            reg.get("nope")

    def test_available_lists_registered(self) -> None:
        reg = ProviderRegistry()
        reg.register("bravo", _StubProvider)
        reg.register("alpha", _StubProvider)
        assert reg.available() == ["alpha", "bravo"]

    def test_available_empty(self) -> None:
        reg = ProviderRegistry()
        assert reg.available() == []

    def test_register_overwrites(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        reg.register("stub", _StubProvider)
        assert reg.available() == ["stub"]

    def test_get_returns_new_instance_each_call(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        a = reg.get("stub")
        b = reg.get("stub")
        assert a is not b

    def test_error_message_lists_available(self) -> None:
        reg = ProviderRegistry()
        reg.register("alpha", _StubProvider)
        reg.register("bravo", _StubProvider)
        with pytest.raises(UnknownProviderError, match="alpha, bravo"):
            reg.get("missing")


# ── Policy tests ────────────────────────────────────────────────────────


class TestCapabilityPolicy:
    @pytest.fixture()
    def full_caps(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="full",
            launch_command="full-cli",
            supports_hook=True,
            supports_resume=True,
            supports_continue=True,
            supports_structured_transcript=True,
            transcript_format="jsonl",
            terminal_ui_patterns=("AskUserQuestion", "ExitPlanMode"),
            builtin_commands=("help", "clear", "compact"),
        )

    @pytest.fixture()
    def minimal_caps(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="minimal",
            launch_command="min-cli",
        )

    def test_full_capabilities(self, full_caps: ProviderCapabilities) -> None:
        policy = CapabilityPolicy(full_caps)
        assert policy.can_hook is True
        assert policy.can_resume is True
        assert policy.can_continue is True
        assert policy.has_structured_transcript is True
        assert policy.transcript_format == "jsonl"

    def test_minimal_capabilities(self, minimal_caps: ProviderCapabilities) -> None:
        policy = CapabilityPolicy(minimal_caps)
        assert policy.can_hook is False
        assert policy.can_resume is False
        assert policy.can_continue is False
        assert policy.has_structured_transcript is False

    def test_has_interactive_ui(self, full_caps: ProviderCapabilities) -> None:
        policy = CapabilityPolicy(full_caps)
        assert policy.has_interactive_ui("AskUserQuestion") is True
        assert policy.has_interactive_ui("ExitPlanMode") is True
        assert policy.has_interactive_ui("Nonexistent") is False

    def test_has_command(self, full_caps: ProviderCapabilities) -> None:
        policy = CapabilityPolicy(full_caps)
        assert policy.has_command("help") is True
        assert policy.has_command("clear") is True
        assert policy.has_command("nonexistent") is False

    def test_from_provider(self) -> None:
        provider = _StubProvider()
        policy = CapabilityPolicy.from_provider(provider)
        assert policy.can_hook is True
        assert policy.has_command("help") is True


# ── Config integration tests ────────────────────────────────────────────


class TestConfigProviderSettings:
    def test_default_provider_name(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ALLOWED_USERS": "123",
            "HOME": "/tmp",
        }
        with patch.dict("os.environ", env, clear=True):
            from ccbot.config import Config

            cfg = Config()
            assert cfg.provider_name == "claude"
            assert cfg.provider_launch_command is None

    def test_override_provider_via_env(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ALLOWED_USERS": "123",
            "HOME": "/tmp",
            "CCBOT_PROVIDER": "codex",
            "CCBOT_PROVIDER_COMMAND": "/usr/local/bin/codex",
        }
        with patch.dict("os.environ", env, clear=True):
            from ccbot.config import Config

            cfg = Config()
            assert cfg.provider_name == "codex"
            assert cfg.provider_launch_command == "/usr/local/bin/codex"


# ── Integration: registry + policy wired together ───────────────────────


class TestModuleLevelRegistry:
    def test_singleton_exists_with_claude(self) -> None:
        assert isinstance(registry, ProviderRegistry)
        assert "claude" in registry.available()

    def test_stub_satisfies_protocol(self) -> None:
        assert isinstance(_StubProvider(), AgentProvider)


class TestRegistryPolicyIntegration:
    def test_registry_to_policy(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        provider = reg.get("stub")
        policy = CapabilityPolicy.from_provider(provider)
        assert policy.can_hook is True
        assert policy.can_resume is True
        assert policy.has_command("help") is True
        assert policy.has_interactive_ui("AskUserQuestion") is True
