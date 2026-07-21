"""Tests for memory.text_utils (R239, ``xai-grok-memory`` ``text_utils.rs``).

Covers the two migrated pure-logic classification predicates:
:func:`has_markdown_headers` (structured-output guard) and :func:`is_no_reply`
(NO_REPLY convention). Mirrors grok's ``text_utils.rs`` 6 tests plus
Python-specific contract checks (str return type, empty-string edge cases,
alphanumeric-normalisation variants).
"""

from __future__ import annotations

from minimax_code.memory import has_markdown_headers, is_no_reply

# ---------------------------------------------------------------------------
# is_no_reply (mirrors grok test_is_no_reply).
# ---------------------------------------------------------------------------


def test_is_no_reply_underscore_variant() -> None:
    """``"NO_REPLY"`` (underscore) matches the convention."""
    assert is_no_reply("NO_REPLY") is True


def test_is_no_reply_space_variant() -> None:
    """``"no reply"`` (space) matches the convention."""
    assert is_no_reply("no reply") is True


def test_is_no_reply_hyphen_variant() -> None:
    """``"No-Reply"`` (hyphen, mixed case) matches the convention."""
    assert is_no_reply("No-Reply") is True


def test_is_no_reply_bare() -> None:
    """``"noreply"`` (bare, no separator) matches the convention."""
    assert is_no_reply("noreply") is True


def test_is_no_reply_rejects_extra_words() -> None:
    """``"no reply needed"`` does NOT match -- extra content after noreply."""
    assert is_no_reply("no reply needed") is False


def test_is_no_reply_rejects_real_content() -> None:
    """A real response to store does NOT match."""
    assert is_no_reply("I have things to store") is False


# ---------------------------------------------------------------------------
# has_markdown_headers (mirrors grok test_has_markdown_headers).
# ---------------------------------------------------------------------------


def test_has_markdown_headers_h2() -> None:
    """``"## Topic"`` is a header."""
    assert has_markdown_headers("## Topic") is True


def test_has_markdown_headers_h1_with_body() -> None:
    """``"# Title\\n\\nBody"`` is a header (h1 + body)."""
    assert has_markdown_headers("# Title\n\nBody") is True


def test_has_markdown_headers_preamble_then_h2() -> None:
    """A header after a preamble still counts."""
    assert has_markdown_headers("preamble\n\n## Topic") is True


def test_has_markdown_headers_rejects_plain_text() -> None:
    """Plain text without any ``# `` / ``## `` run is not a header."""
    assert has_markdown_headers("plain text without headers") is False


def test_has_markdown_headers_rejects_hashtag_without_space() -> None:
    """``"#hashtag"`` (no trailing space) is NOT an ATX header."""
    assert has_markdown_headers("#hashtag without space") is False


# ---------------------------------------------------------------------------
# Python-specific + contract checks.
# ---------------------------------------------------------------------------


def test_is_no_reply_returns_bool() -> None:
    """Return type is bool (mirrors grok ``-> bool``)."""
    assert isinstance(is_no_reply("noreply"), bool)
    assert isinstance(is_no_reply("real content"), bool)


def test_has_markdown_headers_returns_bool() -> None:
    """Return type is bool (mirrors grok ``-> bool``)."""
    assert isinstance(has_markdown_headers("## x"), bool)
    assert isinstance(has_markdown_headers("plain"), bool)


def test_is_no_reply_empty_string() -> None:
    """Empty string normalises to "" != "noreply" -> False."""
    assert is_no_reply("") is False


def test_has_markdown_headers_empty_string() -> None:
    """Empty string has no header run -> False."""
    assert has_markdown_headers("") is False


def test_is_no_reply_only_separators() -> None:
    """Only separators (no alphanumeric) -> "" != "noreply" -> False."""
    assert is_no_reply("- _ -") is False


def test_is_no_reply_extra_separators_around() -> None:
    """Leading/trailing separators are stripped; bare noreply still matches."""
    assert is_no_reply("--- no_reply ---") is True


def test_is_no_reply_uppercase_bare() -> None:
    """All-caps bare (no separator) lowercases to "noreply" -> True."""
    assert is_no_reply("NOREPLY") is True


def test_is_no_reply_rejects_partial_match() -> None:
    """"noreplyplease" is a different word, not the NO_REPLY convention."""
    assert is_no_reply("noreplyplease") is False


def test_has_markdown_headers_h3_only() -> None:
    """``"### Topic"`` is NOT recognised -- only ``#`` and ``##`` count.

    Mirrors grok's literal ``"## " in text || "# " in text`` check: ``"### "``
    contains ``"## "`` as a substring (positions 0-2), so this actually DOES
    match. Documents the substring semantics: any ``###``, ``####`` etc. also
    returns True because they all contain the ``"## "`` substring.
    """
    # "### Topic" contains "## " (chars 0,1,2) -> True by substring match.
    assert has_markdown_headers("### Topic") is True


def test_has_markdown_headers_hash_at_end_no_space() -> None:
    """``"end#"`` (hash but no trailing space) is not a header."""
    assert has_markdown_headers("end#") is False
