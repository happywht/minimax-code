"""Tests for memory.query_expansion (R238, ``xai-grok-memory`` ``query_expansion.rs``).

Covers the migrated FTS query keyword-extraction leaf: stop-word removal,
2-char minimum, pure-numeric filter, ordered de-duplication, and the empty /
punctuation edge cases. Mirrors grok's ``query_expansion.rs`` 14 tests plus
Python-specific contract checks (list[str] return type, frozenset module
constant, order preservation).
"""

from __future__ import annotations

from minimax_code.memory import extract_keywords
from minimax_code.memory.query_expansion import _STOP_WORDS

# ---------------------------------------------------------------------------
# Stop-word removal (mirrors grok tests).
# ---------------------------------------------------------------------------


def test_removes_stop_words() -> None:
    """Conversational query -> meaningful keywords only."""
    assert extract_keywords("that thing we discussed about the API") == [
        "discussed",
        "api",
    ]


def test_all_stop_words_returns_empty() -> None:
    """A query of all stop words returns [] (caller falls back to vector)."""
    assert extract_keywords("what is that?") == []


def test_preserves_meaningful_words() -> None:
    """Non-stop technical terms pass through unchanged, in order."""
    assert extract_keywords("rust programming async patterns") == [
        "rust",
        "programming",
        "async",
        "patterns",
    ]


def test_short_stop_words_filtered() -> None:
    """2-letter stop words (is/it/to/do/that) are removed, 'ok' survives."""
    assert extract_keywords("is it ok to do that") == ["ok"]


# ---------------------------------------------------------------------------
# Length / numeric filtering.
# ---------------------------------------------------------------------------


def test_filters_single_char_words() -> None:
    """Single-char tokens (i/a/x) dropped by the 2-char minimum."""
    assert extract_keywords("I a x language") == ["language"]


def test_preserves_short_meaningful_terms() -> None:
    """2-char meaningful terms (go/js) survive -- they are not stop words."""
    assert extract_keywords("Go and JS patterns") == ["go", "js", "patterns"]


def test_filters_pure_numbers() -> None:
    """Pure-numeric tokens (8080/443) dropped; surrounding words kept."""
    assert extract_keywords("port 8080 and 443 config") == ["port", "config"]


# ---------------------------------------------------------------------------
# De-duplication, punctuation, casing.
# ---------------------------------------------------------------------------


def test_deduplicates() -> None:
    """Repeated words collapse to first occurrence (order preserved)."""
    assert extract_keywords("rust rust rust programming") == ["rust", "programming"]


def test_handles_punctuation() -> None:
    """Punctuation splits tokens; stop words around 'solution'/'bug' removed."""
    assert extract_keywords("what's the solution for the bug?") == ["solution", "bug"]


def test_preserves_underscored_identifiers() -> None:
    """``\\w+`` keeps underscored identifiers as single tokens."""
    assert extract_keywords("the my_function variable") == ["my_function", "variable"]


def test_mixed_case() -> None:
    """Casing normalised via lower(); output always lower."""
    assert extract_keywords("Rust Programming ASYNC") == [
        "rust",
        "programming",
        "async",
    ]


# ---------------------------------------------------------------------------
# Empty / degenerate inputs.
# ---------------------------------------------------------------------------


def test_empty_query() -> None:
    assert extract_keywords("") == []


def test_only_punctuation() -> None:
    """No word chars -> no tokens -> []."""
    assert extract_keywords("??? !!! ...") == []


def test_real_conversational_queries() -> None:
    """End-to-end smoke against realistic user phrasing."""
    assert extract_keywords("how do I implement a binary search tree in rust") == [
        "implement",
        "binary",
        "search",
        "tree",
        "rust",
    ]
    assert extract_keywords("what was that function we used for parsing json") == [
        "function",
        "used",
        "parsing",
        "json",
    ]
    assert extract_keywords("can you remind me about the sqlite migration thing") == [
        "remind",
        "sqlite",
        "migration",
    ]


# ---------------------------------------------------------------------------
# Python-specific + contract checks.
# ---------------------------------------------------------------------------


def test_extract_keywords_returns_list_of_str() -> None:
    """Return type is list[str] (mirrors grok Vec<String>)."""
    result = extract_keywords("rust async patterns")
    assert isinstance(result, list)
    assert all(isinstance(w, str) for w in result)


def test_extract_keywords_preserves_order() -> None:
    """First-occurrence order is preserved, not set-randomised.

    Mirrors grok's ``seen.insert`` over an ordered iterator. Guards against a
    refactor that returns a set (which would lose order and break any FTS
    ranking that weights earlier keywords higher).
    """
    assert extract_keywords("alpha beta gamma delta") == [
        "alpha",
        "beta",
        "gamma",
        "delta",
    ]


def test_stop_words_is_frozenset() -> None:
    """``_STOP_WORDS`` is a frozenset (mirrors grok static HashSet immutability).

    A module-level frozenset is the Python equivalent of grok's
    ``LazyLock<HashSet>`` -- created once at import, immutable, O(1) lookup.
    Guards against a refactor that uses a mutable set or a list.
    """
    assert isinstance(_STOP_WORDS, frozenset)
    # Spot-check a known stop word from each linguistic group.
    assert "the" in _STOP_WORDS  # article
    assert "they" in _STOP_WORDS  # pronoun
    assert "is" in _STOP_WORDS  # verb
    assert "about" in _STOP_WORDS  # preposition
    assert "and" in _STOP_WORDS  # conjunction
    assert "thing" in _STOP_WORDS  # vague reference
    assert "today" in _STOP_WORDS  # time reference
    assert "please" in _STOP_WORDS  # request word
    assert "really" in _STOP_WORDS  # filler
