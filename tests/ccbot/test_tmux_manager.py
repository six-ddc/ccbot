"""Unit tests for tmux manager helpers."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbot.config import config
from ccbot.tmux_manager import TmuxManager


def test_build_claude_launch_command_unsets_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TmuxManager(session_name="test")
    monkeypatch.setattr(config, "claude_command", "claude")

    cmd = manager._build_claude_launch_command(Path("/tmp/my project"))

    assert cmd.startswith("cd '/tmp/my project' && ")
    assert "unset VIRTUAL_ENV UV_PROJECT UV_WORKING_DIRECTORY && " in cmd
    assert cmd.endswith("claude")


def test_build_claude_launch_command_preserves_custom_claude_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TmuxManager(session_name="test")
    monkeypatch.setattr(
        config,
        "claude_command",
        "IS_SANDBOX=1 claude --dangerously-skip-permissions",
    )

    cmd = manager._build_claude_launch_command(Path("/tmp/work"))

    assert "unset VIRTUAL_ENV UV_PROJECT UV_WORKING_DIRECTORY" in cmd
    assert cmd.endswith("IS_SANDBOX=1 claude --dangerously-skip-permissions")


async def _fake_proc(returncode: int = 0) -> SimpleNamespace:
    async def communicate() -> tuple[bytes, bytes]:
        return b"captured", b""

    return SimpleNamespace(returncode=returncode, communicate=communicate)


@pytest.mark.asyncio
async def test_capture_pane_plain_uses_scrollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TmuxManager(session_name="test")
    called: dict[str, list[str]] = {}

    async def _fake_exec(*args: str, **kwargs: object) -> SimpleNamespace:
        called["args"] = list(args)
        return await _fake_proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    result = await manager.capture_pane("@1", with_ansi=False)

    assert result == "captured"
    assert called["args"][:2] == ["tmux", "capture-pane"]
    assert "-S" in called["args"]
    assert f"-{manager.DEFAULT_CAPTURE_SCROLLBACK_LINES}" in called["args"]
    assert called["args"][-2:] == ["-t", "@1"]


@pytest.mark.asyncio
async def test_capture_pane_ansi_does_not_use_scrollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TmuxManager(session_name="test")
    called: dict[str, list[str]] = {}

    async def _fake_exec(*args: str, **kwargs: object) -> SimpleNamespace:
        called["args"] = list(args)
        return await _fake_proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    result = await manager.capture_pane("@2", with_ansi=True)

    assert result == "captured"
    assert called["args"][:2] == ["tmux", "capture-pane"]
    assert "-e" in called["args"]
    assert "-S" not in called["args"]
    assert called["args"][-2:] == ["-t", "@2"]
