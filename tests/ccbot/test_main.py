"""Tests for CLI argument parsing in main.py."""

import pytest

from ccbot.main import _parse_forward_ports


def test_parse_forward_ports_empty() -> None:
    assert _parse_forward_ports([]) == []


def test_parse_forward_ports_multiple() -> None:
    ports = _parse_forward_ports(["--forward", "3000,5173", "--forward", "8080"])
    assert ports == [3000, 5173, 8080]


def test_parse_forward_ports_invalid() -> None:
    with pytest.raises(SystemExit):
        _parse_forward_ports(["--forward", "abc"])
