"""Tests for provider registry and config integration."""

from unittest.mock import patch

import pytest

from ccbot.providers.base import ProviderCapabilities
from ccbot.providers.registry import ProviderRegistry, UnknownProviderError
from test_provider_contracts import StubProvider as _StubProvider

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

    def test_register_overwrites(self) -> None:
        class _OtherProvider(_StubProvider):
            _CAPS = ProviderCapabilities(name="other", launch_command="other-cli")

        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        reg.register("stub", _OtherProvider)
        assert reg.get("stub").capabilities.name == "other"

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


# ── Integration: registry wired together ─────────────────────────────────


class TestModuleLevelRegistry:
    def test_singleton_exists_with_claude(self, monkeypatch) -> None:
        from ccbot.providers import _reset_provider, get_provider, registry

        _reset_provider()
        try:
            get_provider()
            assert isinstance(registry, ProviderRegistry)
            assert "claude" in sorted(registry._providers)
        finally:
            _reset_provider()

    def test_unknown_provider_falls_back_to_claude(self, monkeypatch) -> None:
        from ccbot.providers import _reset_provider, get_provider

        _reset_provider()
        monkeypatch.setenv("CCBOT_PROVIDER", "doesnotexist")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        try:
            provider = get_provider()
            assert provider.capabilities.name == "claude"
        finally:
            _reset_provider()

    def test_resolve_capabilities_unknown_falls_back(self) -> None:
        from ccbot.providers import _reset_provider, resolve_capabilities

        _reset_provider()
        try:
            caps = resolve_capabilities("nonexistent")
            assert caps.name == "claude"
        finally:
            _reset_provider()
