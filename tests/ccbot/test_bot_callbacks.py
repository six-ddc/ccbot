"""Tests for bot.py table-driven interactive key dispatch."""

from ccbot.bot import _INTERACTIVE_KEY_MAP, _INTERACTIVE_PREFIXES
from ccbot.handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)


class TestInteractiveKeyMap:
    def test_all_ask_prefixes_in_map(self) -> None:
        expected = {
            CB_ASK_UP,
            CB_ASK_DOWN,
            CB_ASK_LEFT,
            CB_ASK_RIGHT,
            CB_ASK_ESC,
            CB_ASK_ENTER,
            CB_ASK_SPACE,
            CB_ASK_TAB,
        }
        assert set(_INTERACTIVE_KEY_MAP.keys()) == expected

    def test_esc_does_not_refresh(self) -> None:
        _, refresh = _INTERACTIVE_KEY_MAP[CB_ASK_ESC]
        assert refresh is False

    def test_navigation_keys_refresh(self) -> None:
        for prefix in (CB_ASK_UP, CB_ASK_DOWN, CB_ASK_LEFT, CB_ASK_RIGHT):
            _, refresh = _INTERACTIVE_KEY_MAP[prefix]
            assert refresh is True, f"{prefix} should refresh"

    def test_refresh_in_prefixes_but_not_map(self) -> None:
        assert CB_ASK_REFRESH in _INTERACTIVE_PREFIXES
        assert CB_ASK_REFRESH not in _INTERACTIVE_KEY_MAP
