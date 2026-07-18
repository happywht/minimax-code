"""Tests for the markdown structural-failure predicates (R37).

These predicates run off raw source text — no markdown library needed. They
mirror grok's ``strip_block_prefix`` / ``is_table_delimiter_line`` /
``line_looks_like_header`` / ``fenced_block_is_unterminated`` exactly. Cases
are drawn from grok's own doc comments (the pipe-vs-dash requirements, the
under-penalizing verbatim-fence caveat) plus the CommonMark fence rules
(opener char + min length 3, closer must be ≥ opener length).
"""

from __future__ import annotations

import pytest

from minimax_code.markdown import (
    fenced_block_is_unterminated,
    is_table_delimiter_line,
    line_looks_like_header,
    strip_block_prefix,
)

# --- strip_block_prefix ----------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("> quote", "quote"),
        (">quote", "quote"),
        ("   indented", "indented"),
        ("\ttabbed", "tabbed"),
        ("> > nested", "nested"),
        ("> \t> mixed", "mixed"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_strip_block_prefix(line, expected):
    assert strip_block_prefix(line) == expected


# --- is_table_delimiter_line ----------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("|---|---|", True),
        ("| --- | --- |", True),
        ("|:---|:---:|---:|", True),
        ("|---|", True),
        ("  |---|  ", True),  # surrounding whitespace trimmed
        ("---", False),  # no pipe → thematic break, not delimiter
        ("-----", False),  # no pipe → setext underline
        ("|||", False),  # no dash
        ("|abc|", False),  # illegal char
        ("| - - |", True),  # pipes, dashes, spaces only
        ("", False),
    ],
)
def test_is_table_delimiter_line(line, expected):
    assert is_table_delimiter_line(line) is expected


# --- line_looks_like_header -----------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("| name | value |", True),
        ("|name|value|", True),
        ("|---|---|", False),  # delimiter-shaped → not a header
        ("plain text", False),  # no pipe
        ("", False),
        ("   ", False),  # empty after trim
        ("not-a-table", False),
    ],
)
def test_line_looks_like_header(line, expected):
    assert line_looks_like_header(line) is expected


# --- fenced_block_is_unterminated -----------------------------------------


def test_terminated_backtick_fence():
    assert fenced_block_is_unterminated("```\ncode\n```") is False


def test_unterminated_backtick_fence():
    """Opener with no matching closer → swallows the rest of the message."""
    assert fenced_block_is_unterminated("```\ncode") is True


def test_unterminated_with_trailing_newline():
    assert fenced_block_is_unterminated("```\ncode\n") is True


def test_terminated_fence_with_language():
    """Opener ```` ```py ```` — the language suffix does not block close detection."""
    assert fenced_block_is_unterminated("```py\ncode\n```") is False


def test_close_too_short_is_unterminated():
    """CommonMark: closer must be ≥ opener length. Opener=3 backticks, closer=2."""
    assert fenced_block_is_unterminated("```\ncode\n``") is True


def test_longer_opener_needs_equal_or_longer_closer():
    """4-backtick opener not closed by a 3-backtick line."""
    assert fenced_block_is_unterminated("````\ncode\n```") is True
    assert fenced_block_is_unterminated("````\ncode\n````") is False


def test_empty_block_is_not_unterminated():
    """No opener line → nothing to terminate."""
    assert fenced_block_is_unterminated("") is False


def test_non_fence_first_line_is_not_unterminated():
    """First char not backtick/tilde → not a fence at all."""
    assert fenced_block_is_unterminated("plain\ntext") is False


def test_indented_fence_opener_is_stripped():
    """Blockquote/indent prefix on the opener is stripped before fence check."""
    assert fenced_block_is_unterminated("> ```\ncode\n```") is False


def test_tilde_opener_closed_by_backtick_is_unterminated():
    """Closer must match the opener's fence character."""
    assert fenced_block_is_unterminated("~~~\ncode\n```") is True


def test_terminated_tilde_fence_is_false():
    assert fenced_block_is_unterminated("~~~\ncode\n~~~") is False
