"""Tests for top-level ccbot CLI argument handling."""

from __future__ import annotations

import os
import sys
import types

import pytest

from ccbot.main import (
    _apply_global_cli_overrides,
    _build_codex_version_command,
    _ensure_runtime_requirements,
    _parse_version,
    main,
)


class TestApplyGlobalCliOverrides:
    def test_runtime_override_sets_env_and_returns_default_mode_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)

        argv = _apply_global_cli_overrides(["ccbot", "--run", "codex"])

        assert argv == ["ccbot"]
        assert os.environ["CCBOT_RUNTIME"] == "codex"
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)


class TestCodexVersionChecks:
    def test_build_codex_version_command_for_plain_codex(self) -> None:
        assert _build_codex_version_command("codex --no-alt-screen") == [
            "codex",
            "--version",
        ]

    def test_build_codex_version_command_for_npx_codex(self) -> None:
        assert _build_codex_version_command("npx codex --no-alt-screen") == [
            "npx",
            "codex",
            "--version",
        ]

    def test_parse_version(self) -> None:
        assert _parse_version("codex-cli 0.114.0") == (0, 114, 0)

    def test_ensure_runtime_requirements_rejects_old_codex(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ccbot.main.subprocess.run",
            lambda *args, **kwargs: types.SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.106.0\n",
                stderr="",
            ),
        )

        config = types.SimpleNamespace(
            runtime="codex",
            codex_command="codex --no-alt-screen",
        )

        with pytest.raises(ValueError, match="Please upgrade Codex"):
            _ensure_runtime_requirements(config)

    def test_ensure_runtime_requirements_accepts_supported_codex(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ccbot.main.subprocess.run",
            lambda *args, **kwargs: types.SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.114.0\n",
                stderr="",
            ),
        )

        config = types.SimpleNamespace(
            runtime="codex",
            codex_command="codex --no-alt-screen",
        )

        _ensure_runtime_requirements(config)

    def test_runtime_override_preserves_subcommand_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)

        argv = _apply_global_cli_overrides(
            ["ccbot", "--run", "codex", "hook", "--install"]
        )

        assert argv == ["ccbot", "hook", "--install"]
        assert os.environ["CCBOT_RUNTIME"] == "codex"
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)


class TestMainCliOverride:
    def test_main_uses_cli_runtime_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: dict[str, object] = {}

        class _Config:
            allowed_users = {12345}
            claude_projects_path = "/tmp/claude-projects"
            codex_home = "/tmp/.codex"
            codex_command = "codex --no-alt-screen"

            @property
            def runtime(self) -> str:
                return os.environ.get("CCBOT_RUNTIME", "")

        class _App:
            def run_polling(self, **kwargs) -> None:
                calls["polling_kwargs"] = kwargs

        def _create_bot() -> _App:
            calls["create_bot"] = True
            return _App()

        def _get_or_create_session() -> types.SimpleNamespace:
            calls["tmux_ready"] = True
            return types.SimpleNamespace(session_name="ccbot")

        fake_config_module = types.ModuleType("ccbot.config")
        fake_config_module.config = _Config()
        fake_tmux_module = types.ModuleType("ccbot.tmux_manager")
        fake_tmux_module.tmux_manager = types.SimpleNamespace(
            get_or_create_session=_get_or_create_session
        )
        fake_bot_module = types.ModuleType("ccbot.bot")
        fake_bot_module.create_bot = _create_bot

        monkeypatch.setitem(sys.modules, "ccbot.config", fake_config_module)
        monkeypatch.setitem(sys.modules, "ccbot.tmux_manager", fake_tmux_module)
        monkeypatch.setitem(sys.modules, "ccbot.bot", fake_bot_module)
        monkeypatch.setattr(sys, "argv", ["ccbot", "--run", "codex"])
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)
        monkeypatch.setattr(
            "ccbot.main.subprocess.run",
            lambda *args, **kwargs: types.SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.114.0\n",
                stderr="",
            ),
        )

        main()

        assert os.environ["CCBOT_RUNTIME"] == "codex"
        assert fake_config_module.config.runtime == "codex"
        assert calls["tmux_ready"] is True
        assert calls["create_bot"] is True
        assert calls["polling_kwargs"] == {
            "allowed_updates": ["message", "callback_query"]
        }
        monkeypatch.delenv("CCBOT_RUNTIME", raising=False)
