"""Tests for adaptive timer throttle in status_polling.

Verifies that _should_send_status() correctly:
  - Always passes non-timer status lines
  - Sends timer updates every poll for the first 10 s
  - Throttles to ~5 s intervals between 10–60 s
  - Throttles to ~30 s intervals after 60 s
  - Resets when base text changes (different task)
  - Resets when status switches from timer to non-timer
"""

from unittest.mock import patch

import pytest

from ccbot.handlers.status_polling import (
    _TIMER_RE,
    _should_send_status,
    _timer_throttle,
)


@pytest.fixture(autouse=True)
def _clear_throttle_state():
    """Ensure throttle state is clean for each test."""
    _timer_throttle.clear()
    yield
    _timer_throttle.clear()


# ── Regex tests ──────────────────────────────────────────────────────────


class TestTimerRegex:
    """Verify _TIMER_RE matches expected timer suffixes."""

    @pytest.mark.parametrize(
        "text",
        [
            "Thinking… 5s",
            "Reading file.py 12s",
            "Bash echo hello 1m 30s",
            "Bash echo hello 1m30s",
            "Working 2m",
            "Thinking… 5s (Esc to interrupt)",
            "Bash ls 1m 30s (Esc to interrupt)",
            "Drizzling… (54s · ↓ 776 tokens)",
            "Coalescing… (25m 8s · ↓ 5.8k tokens · thought for 6s)",
            "Thinking… (3s)",
            "Thinking… (1m 5s · ↓ 12k tokens · thought for 3s)",
        ],
    )
    def test_timer_detected(self, text: str):
        assert _TIMER_RE.search(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "Reading file.py",
            "Writing to output",
            "Bash echo hello",
            "file2s",           # no preceding space
            "Processing items",
        ],
    )
    def test_non_timer_not_matched(self, text: str):
        assert _TIMER_RE.search(text) is None

    def test_base_text_extraction(self):
        text = "Thinking… 45s (Esc to interrupt)"
        m = _TIMER_RE.search(text)
        assert m is not None
        base = text[: m.start()].rstrip()
        assert base == "Thinking…"

    def test_base_text_bash(self):
        text = "Bash echo hello 1m 30s"
        m = _TIMER_RE.search(text)
        assert m is not None
        base = text[: m.start()].rstrip()
        assert base == "Bash echo hello"

    def test_base_text_parenthesized_timer(self):
        text = "Coalescing… (25m 8s · ↓ 5.8k tokens · thought for 6s)"
        m = _TIMER_RE.search(text)
        assert m is not None
        base = text[: m.start()].rstrip()
        assert base == "Coalescing…"

    def test_base_text_short_parenthesized(self):
        text = "Drizzling… (54s · ↓ 776 tokens)"
        m = _TIMER_RE.search(text)
        assert m is not None
        base = text[: m.start()].rstrip()
        assert base == "Drizzling…"


# ── Throttle logic tests ────────────────────────────────────────────────


class TestShouldSendStatus:
    """Test adaptive throttle intervals."""

    def test_non_timer_always_passes(self):
        assert _should_send_status(1, 42, "Reading file.py") is True
        assert _should_send_status(1, 42, "Reading file.py") is True
        assert _should_send_status(1, 42, "Writing output") is True

    def test_first_timer_always_passes(self):
        assert _should_send_status(1, 42, "Thinking… 1s") is True

    def test_realtime_within_10s(self):
        """Timer updates within first 10s should all pass (1s interval)."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # 2 seconds later — still within 10s window
            mock_time.monotonic.return_value = t0 + 2
            assert _should_send_status(1, 42, "Thinking… 3s") is True

            # 1 second later — also passes
            mock_time.monotonic.return_value = t0 + 3
            assert _should_send_status(1, 42, "Thinking… 4s") is True

    def test_throttled_after_10s(self):
        """After 10s elapsed, interval becomes 5s."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            # First call
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # At 11s — first_seen=t0, elapsed=11s, since_sent=11s → passes (>= 5s)
            mock_time.monotonic.return_value = t0 + 11
            assert _should_send_status(1, 42, "Thinking… 12s") is True

            # At 13s — since_sent=2s → blocked (< 5s)
            mock_time.monotonic.return_value = t0 + 13
            assert _should_send_status(1, 42, "Thinking… 14s") is False

            # At 16s — since_sent=5s → passes (>= 5s)
            mock_time.monotonic.return_value = t0 + 16
            assert _should_send_status(1, 42, "Thinking… 17s") is True

    def test_throttled_after_60s(self):
        """After 60s elapsed, interval becomes 30s."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # At 61s — passes (since_sent=61s >= 30s)
            mock_time.monotonic.return_value = t0 + 61
            assert _should_send_status(1, 42, "Thinking… 1m 2s") is True

            # At 70s — blocked (since_sent=9s < 30s)
            mock_time.monotonic.return_value = t0 + 70
            assert _should_send_status(1, 42, "Thinking… 1m 11s") is False

            # At 91s — passes (since_sent=30s >= 30s)
            mock_time.monotonic.return_value = t0 + 91
            assert _should_send_status(1, 42, "Thinking… 1m 32s") is True

    def test_base_text_change_resets(self):
        """Changing base text (different task) resets the throttle."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # Jump to 65s (would be in 30s throttle zone)
            mock_time.monotonic.return_value = t0 + 65
            # Same base text — just record it as sent
            assert _should_send_status(1, 42, "Thinking… 1m 6s") is True

            # 2s later, different base text — should pass immediately
            mock_time.monotonic.return_value = t0 + 67
            assert _should_send_status(1, 42, "Bash echo hello 2s") is True

    def test_non_timer_clears_state(self):
        """Switching to non-timer status clears throttle state."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True
            assert (1, 42) in _timer_throttle

            # Non-timer clears state
            mock_time.monotonic.return_value = t0 + 5
            assert _should_send_status(1, 42, "Reading file.py") is True
            assert (1, 42) not in _timer_throttle

    def test_independent_per_user_thread(self):
        """Throttle state is independent per (user_id, thread_id)."""
        t0 = 1000.0
        with patch("ccbot.handlers.status_polling.time") as mock_time:
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True
            assert _should_send_status(1, 99, "Thinking… 1s") is True
            assert _should_send_status(2, 42, "Thinking… 1s") is True

            # Jump to 15s zone (5s interval)
            mock_time.monotonic.return_value = t0 + 15
            assert _should_send_status(1, 42, "Thinking… 16s") is True

            # 2s later — user 1/thread 42 blocked, but user 2/thread 42 passes
            mock_time.monotonic.return_value = t0 + 17
            assert _should_send_status(1, 42, "Thinking… 18s") is False
            assert _should_send_status(2, 42, "Thinking… 18s") is True

    def test_none_thread_id(self):
        """thread_id=None is treated as 0."""
        assert _should_send_status(1, None, "Thinking… 1s") is True
        assert (1, 0) in _timer_throttle

    def test_custom_intervals_from_config(self):
        """Intervals are read from config.status_throttle_intervals."""
        t0 = 1000.0
        # Set aggressive throttle: 2s / 10s / 60s
        with (
            patch("ccbot.handlers.status_polling.time") as mock_time,
            patch(
                "ccbot.handlers.status_polling.config.status_throttle_intervals",
                (2.0, 10.0, 60.0),
            ),
        ):
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # At t0+1 — within tier 1 (0-10s), but interval=2s → blocked
            mock_time.monotonic.return_value = t0 + 1
            assert _should_send_status(1, 42, "Thinking… 2s") is False

            # At t0+2 — since_sent=2s >= 2s → passes
            mock_time.monotonic.return_value = t0 + 2
            assert _should_send_status(1, 42, "Thinking… 3s") is True

    def test_disabled_throttle(self):
        """Setting all intervals to 1 effectively disables throttling."""
        t0 = 1000.0
        with (
            patch("ccbot.handlers.status_polling.time") as mock_time,
            patch(
                "ccbot.handlers.status_polling.config.status_throttle_intervals",
                (1.0, 1.0, 1.0),
            ),
        ):
            mock_time.monotonic.return_value = t0
            assert _should_send_status(1, 42, "Thinking… 1s") is True

            # At 65s elapsed, interval is still 1s → passes after 1s
            mock_time.monotonic.return_value = t0 + 65
            assert _should_send_status(1, 42, "Thinking… 1m 6s") is True

            mock_time.monotonic.return_value = t0 + 66
            assert _should_send_status(1, 42, "Thinking… 1m 7s") is True
