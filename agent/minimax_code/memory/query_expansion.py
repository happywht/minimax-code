"""Query expansion for FTS-only search mode (R238, ``xai-grok-memory`` ``query_expansion.rs``).

When users ask conversational queries like *"that thing we discussed about
the API"*, FTS5 matches every word equally -- articles, pronouns, and vague
references dilute precision. This module extracts meaningful keywords by
removing stop words, so hybrid search can feed a tighter keyword set to the
full-text path.

Pipeline (mirrors grok)::

    query -> lowercase -> split on non-alphanumeric -> remove stop words
           -> drop < 2-char / pure-numeric tokens -> dedup -> keywords

When all words are stop words (e.g. "what is that?"), returns an empty list;
the caller (hybrid search runtime, deferred -- YAGNI) falls back to the
vector path in that case.

**Second memory leaf (R238).** Sibling to :mod:`minimax_code.memory.mmr`
(R237). Both share the same word-splitting primitive -- a maximal run of
alphanumeric-or-underscore characters -- but consume it differently:
:func:`minimax_code.memory.mmr.tokenize` returns a *deduplicated set* (Jaccard
similarity input, order-irrelevant); :func:`extract_keywords` walks tokens
*in order*, filtering and de-duplicating into an ordered keyword list. The
``_WORD_RE`` pattern below is intentionally a per-module private copy of
``mmr._TOKEN_RE`` rather than a cross-module import: the two leaves stay
self-contained, and the "same split primitive, different consumer" link is
documented rather than coupled.

Deliberately out of scope (YAGNI -- runtime layer): grok's
``extract_keywords`` is called by the hybrid-scoring search runtime
(``search.rs`` MemoryIndex + sqlite-vec FTS5), which has no Python consumer
yet; only this pure-logic keyword-extraction leaf migrates.
"""

from __future__ import annotations

import re

#: One or more word characters (Unicode alphanumeric + underscore). Same split
#: primitive as ``mmr._TOKEN_RE``: a token is a maximal run of
#: alphanumeric-or-underscore characters, mirroring grok's
#: ``split(|c| !c.is_alphanumeric() && c != '_')``. ``re`` is Unicode-aware by
#: default for ``str`` patterns. Kept as a per-module private copy (see module
#: docstring) so this leaf stays self-contained.
_WORD_RE = re.compile(r"\w+")

#: English stop words filtered out of conversational queries before FTS.
#:
#: Mirrors grok's ``static STOP_WORDS: LazyLock<HashSet<&'static str>>``. A
#: Python module-level ``frozenset`` is the faithful equivalent: created once
#: at import, immutable, and membership-tested in O(1) -- LazyLock only defers
#: the one-time HashSet allocation, which module-level initialisation already
#: gives us for free. Grouped by linguistic role per grok for readability
#: (the set itself is unordered).
_STOP_WORDS: frozenset[str] = frozenset({
    # Articles & determiners
    "a", "an", "the", "this", "that", "these", "those",
    # Pronouns
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "him", "her", "its", "them", "us",
    # Common verbs
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "can", "may", "might",
    # Prepositions
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    # Conjunctions
    "and", "or", "but", "if", "then", "because", "as", "while", "when",
    "where", "what", "which", "who", "how", "why",
    # Vague references
    "thing", "things", "stuff", "something", "anything", "everything",
    "one", "some", "any", "all", "each", "every", "both", "few", "more",
    # Time references
    "yesterday", "today", "tomorrow", "earlier", "later", "recently",
    "now", "just", "already", "still", "yet",
    # Request words
    "please", "help", "find", "show", "get", "tell", "give", "make",
    # Common filler
    "not", "no", "yes", "also", "too", "very", "really", "here", "there",
    "so", "up", "out", "like", "than", "other", "only",
})


def extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a conversational query.

    Removes stop words, single-character tokens, and pure-numeric tokens;
    returns keywords in order of first appearance, de-duplicated.

    The 2-character minimum preserves meaningful short terms like ``"go"``,
    ``"js"``, ``"ui"``, ``"db"``, ``"ai"``, ``"ml"`` while stop words handle
    the common 2-letter noise (``"is"``, ``"it"``, ``"do"``, ``"we"``).

    Returns an empty list when all words are stop words or the query contains
    no meaningful content -- the caller should fall back to vector search.

    Mirrors grok ``extract_keywords``. Splitting uses ``\\w+`` (Unicode
    alphanumeric + underscore), so underscored identifiers like
    ``"my_function"`` survive as single keywords. Pure-numeric filtering uses
    :meth:`str.isnumeric`, the Python equivalent of Rust ``char::is_numeric``
    (both check Unicode ``Numeric_Type``); in practice only ASCII digits are
    involved. De-duplication preserves first-occurrence order, mirroring
    grok's ``seen.insert`` over an ordered iterator.
    """
    lowered = query.lower()
    seen: set[str] = set()
    keywords: list[str] = []
    for word in _WORD_RE.findall(lowered):
        if len(word) < 2:
            continue
        if word in _STOP_WORDS:
            continue
        if word.isnumeric():
            continue
        if word in seen:
            continue
        seen.add(word)
        keywords.append(word)
    return keywords
