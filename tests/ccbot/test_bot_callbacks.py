"""Tests for bot.py table-driven interactive key dispatch."""

import pytest

from ccbot.handlers.interactive_callbacks import (
    INTERACTIVE_KEY_MAP,
    INTERACTIVE_PREFIXES,
)
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
        assert set(INTERACTIVE_KEY_MAP.keys()) == expected

    @pytest.mark.parametrize(
        ("prefix", "expected_refresh"),
        [
            pytest.param(CB_ASK_ESC, False, id="esc-no-refresh"),
            pytest.param(CB_ASK_UP, True, id="up-refreshes"),
            pytest.param(CB_ASK_DOWN, True, id="down-refreshes"),
            pytest.param(CB_ASK_LEFT, True, id="left-refreshes"),
            pytest.param(CB_ASK_RIGHT, True, id="right-refreshes"),
            pytest.param(CB_ASK_ENTER, True, id="enter-refreshes"),
            pytest.param(CB_ASK_SPACE, True, id="space-refreshes"),
            pytest.param(CB_ASK_TAB, True, id="tab-refreshes"),
        ],
    )
    def test_key_refresh_behavior(self, prefix: str, expected_refresh: bool) -> None:
        _, refresh = INTERACTIVE_KEY_MAP[prefix]
        assert refresh is expected_refresh

    def test_refresh_in_prefixes_but_not_map(self) -> None:
        assert CB_ASK_REFRESH in INTERACTIVE_PREFIXES
        assert CB_ASK_REFRESH not in INTERACTIVE_KEY_MAP
