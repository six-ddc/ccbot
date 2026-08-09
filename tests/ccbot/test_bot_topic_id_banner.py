"""Tests for bot._topic_id_banner — the first-message thread_id banner.

Shown once on the first bot message in a newly-unbound topic (window
picker / directory browser) and preserved across in-place edits (folder
navigation, pagination, picker→browser transition) so it stays visible
for as long as CCBOT_TOPIC_ALLOWLIST / CCBOT_TOPIC_AUTO_CONFIRM setup
needs the thread_id.
"""

from ccbot.bot import _topic_id_banner


class TestTopicIdBanner:
    def test_contains_thread_id(self) -> None:
        assert "144" in _topic_id_banner(144)

    def test_ends_with_blank_line_separator(self) -> None:
        """Meant to be prepended directly to a message body."""
        banner = _topic_id_banner(299)
        assert banner.endswith("\n\n")

    def test_mentions_allowlist_env_var(self) -> None:
        assert "CCBOT_TOPIC_ALLOWLIST" in _topic_id_banner(299)
