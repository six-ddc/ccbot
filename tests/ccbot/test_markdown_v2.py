"""Tests for Markdown → Telegram MarkdownV2 conversion."""

import pytest

from ccbot.markdown_v2 import _escape_mdv2, convert_markdown
from ccbot.transcript_parser import TranscriptParser

EXP_START = TranscriptParser.EXPANDABLE_QUOTE_START
EXP_END = TranscriptParser.EXPANDABLE_QUOTE_END


class TestEscapeMdv2:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            (
                "_*[]()~>#+\\-=|{}.!",
                "\\_\\*\\[\\]\\(\\)\\~\\>\\#\\+\\\\\\-\\=\\|\\{\\}\\.\\!",
            ),
            ("hello world 123", "hello world 123"),
            ("", ""),
        ],
        ids=["special-chars", "alphanumeric-unchanged", "empty-string"],
    )
    def test_escape(self, input_text: str, expected: str) -> None:
        assert _escape_mdv2(input_text) == expected


class TestConvertMarkdown:
    def test_plain_text(self) -> None:
        result = convert_markdown("hello world")
        assert "hello world" in result

    def test_bold(self) -> None:
        result = convert_markdown("**bold text**")
        assert "*bold text*" in result
        assert "**bold text**" not in result

    def test_code_block_preserved(self) -> None:
        result = convert_markdown("```python\nprint('hi')\n```")
        assert "```" in result
        assert "print" in result

    def test_expandable_quote_sentinels(self) -> None:
        text = f"{EXP_START}quoted content{EXP_END}"
        result = convert_markdown(text)
        assert EXP_START not in result
        assert EXP_END not in result
        # Telegram requires "**>" (not plain ">") to open an *expandable*
        # blockquote — otherwise the trailing "||" is an unmatched spoiler
        # entity and Telegram rejects the whole message.
        assert "**>quoted content||" in result

    def test_mixed_text_and_expandable_quote(self) -> None:
        text = f"before {EXP_START}inside quote{EXP_END} after"
        result = convert_markdown(text)
        assert EXP_START not in result
        assert EXP_END not in result
        assert "**>inside quote||" in result
        assert "before" in result
        assert "after" in result

    def test_multiline_expandable_quote_only_first_line_marked(self) -> None:
        text = f"{EXP_START}line one\nline two\nline three{EXP_END}"
        result = convert_markdown(text)
        assert "**>line one" in result
        assert "\n>line two" in result
        assert "\n>line three||" in result
        # Only the first line carries the "**" expandability marker.
        assert result.count("**>") == 1

    def test_expandable_quote_truncation_keeps_expandable_marker(self) -> None:
        text = f"{EXP_START}{'x' * 10000}{EXP_END}"
        result = convert_markdown(text)
        assert "**>" in result
        assert result.index("**>") < 10
        assert "\\(truncated\\)||" in result
        # Rendered quote block must still fit Telegram's message limit.
        assert len(result) < 4096
