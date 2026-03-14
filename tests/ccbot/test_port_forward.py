"""Tests for port forwarding URL parsing and ssh command generation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.port_forward import PortForwardManager
from ccbot.port_forward import _LOCALHOST_RUN_URL_RE


def test_localhost_run_regex_ignores_admin_url() -> None:
    assert _LOCALHOST_RUN_URL_RE.search("https://admin.localhost.run/") is None


def test_localhost_run_regex_matches_random_subdomain() -> None:
    m = _LOCALHOST_RUN_URL_RE.search("tunnel: https://a1b2c3d4.localhost.run")
    assert m is not None
    assert m.group(0) == "https://a1b2c3d4.localhost.run"


@pytest.mark.asyncio
async def test_start_localhost_run_uses_non_n_ssh_command() -> None:
    manager = PortForwardManager([3000])
    fake_proc = SimpleNamespace(returncode=None)

    with (
        patch(
            "ccbot.port_forward.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as mock_create,
        patch.object(
            manager,
            "_wait_for_url",
            new_callable=AsyncMock,
            return_value="https://2cd35b9d853526.lhr.life",
        ),
    ):
        tunnel = await manager._start_localhost_run(3000)

    assert tunnel.public_url == "https://2cd35b9d853526.lhr.life"

    cmd = mock_create.await_args.args
    assert cmd[0] == "ssh"
    assert "-N" not in cmd
    assert "-R" in cmd
    assert "80:127.0.0.1:3000" in cmd
    assert cmd[-1] == "nokey@localhost.run"
