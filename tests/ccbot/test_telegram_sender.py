"""Tests for telegram_sender.split_message."""

import pytest

from ccbot.telegram_sender import split_message


class TestSplitMessage:
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("hello world", ["hello world"], id="short_text"),
            pytest.param("", [""], id="empty_string"),
            pytest.param("a" * 4096, ["a" * 4096], id="exactly_4096_chars"),
        ],
    )
    def test_single_chunk_returned(self, text: str, expected: list[str]):
        assert split_message(text) == expected

    def test_split_on_newline_boundaries(self):
        line = "x" * 2000
        text = f"{line}\n{line}\n{line}"
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == f"{line}\n{line}"
        assert chunks[1] == line

    def test_single_long_line_force_split(self):
        text = "a" * 8192
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 4096
        assert chunks[1] == "a" * 4096

    def test_custom_max_length(self):
        text = "aaaa\nbbbb\ncccc"
        chunks = split_message(text, max_length=10)
        assert chunks == ["aaaa\nbbbb", "cccc"]

    def test_custom_max_length_force_split(self):
        text = "a" * 120
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 3
        assert chunks[0] == "a" * 50
        assert chunks[1] == "a" * 50
        assert chunks[2] == "a" * 20

    def test_trailing_newline_handling(self):
        text = "line1\nline2"
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 1
        assert chunks[0] == "line1\nline2"

    def test_mixed_lines_grouping(self):
        short = "short"
        long_line = "x" * 60
        text = f"{short}\n{long_line}\n{short}"
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 4
        assert chunks[0] == short
        assert chunks[1] == "x" * 50
        assert chunks[2] == "x" * 10
        assert chunks[3] == short

    @pytest.mark.parametrize(
        "text, max_len, expected_count",
        [
            pytest.param("a\nb\nc\nd\ne", 4, 3, id="many_short_lines"),
            pytest.param("ab\ncd\nef\ngh", 6, 2, id="pairs_of_lines"),
        ],
    )
    def test_chunk_count(self, text: str, max_len: int, expected_count: int):
        chunks = split_message(text, max_length=max_len)
        assert len(chunks) == expected_count

    def test_all_chunks_within_max_length(self):
        lines = [f"line-{i:04d} " + "x" * 40 for i in range(100)]
        text = "\n".join(lines)
        chunks = split_message(text, max_length=200)
        for chunk in chunks:
            assert len(chunk) <= 200
