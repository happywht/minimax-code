"""Pure text-classification helpers for the memory subsystem (R239,
``xai-grok-memory`` ``text_utils.rs``).

Two predicates shared by the memory-flush (``session::helpers::memory_flush``)
and dream-consolidation (``session::memory::dream``) response-processing paths.
In grok these live in the memory subsystem so ``dream`` no longer reaches *up*
into ``session::helpers::memory_flush`` for them -- which removes the
``dream`` <-> ``memory_flush`` module dependency cycle and is a prerequisite
for extracting the memory subsystem into its own crate.

:func:`has_markdown_headers` checks that the model produced structured output;
:func:`is_no_reply` checks the NO_REPLY convention so a flush/dream pass can
skip storing an empty response. Both are pure string predicates with no I/O.

**Third memory leaf (R239).** Sibling to :mod:`minimax_code.memory.mmr`
(R237, MMR re-rank) and :mod:`minimax_code.memory.query_expansion` (R238, FTS
keyword extraction). Unlike those two -- which share a word-splitting
primitive but consume it differently (set vs ordered list) -- this leaf is a
fully independent classifier pair: it touches no shared tokenizer, so it
introduces no coupling to either sibling.

Deliberately out of scope (YAGNI -- runtime layer): grok calls these from the
hybrid-scoring / flush / dream runtime paths (``index.rs`` MemoryIndex,
``storage.rs`` MemoryStorage, ``dream.rs``), which have no Python consumer
yet; only the pure-logic classification predicates migrate.
"""

from __future__ import annotations


def has_markdown_headers(text: str) -> bool:
    """Check if text contains at least one markdown header (``#`` or ``##``).

    Used by both flush and dream response processing to ensure the model
    produced structured output before persisting.

    Mirrors grok ``has_markdown_headers``. The trailing space is significant:
    ``"#hashtag"`` (no space) is not a header, only ``"# "`` (ATX header with
    a space after the hashes) counts. ``"## "`` is checked first because every
    ``## `` header also contains ``"# "`` as a substring -- checking ``"# "``
    alone would over-match, but the order of the two ``in`` checks does not
    affect the boolean result, so we mirror grok's literal order for
    traceability.

    Args:
        text: Raw model response text.

    Returns:
        ``True`` if the text contains ``"## "`` or ``"# "``.
    """
    return "## " in text or "# " in text


def is_no_reply(text: str) -> bool:
    """Check if the response matches the NO_REPLY convention.

    Strips all non-alphanumeric characters, lowercases, and checks if the
    remainder is exactly ``"noreply"``. This handles common separator
    variants: ``"no reply"``, ``"no_reply"``, ``"no-reply"``, ``"NO REPLY"``,
    etc.

    Mirrors grok ``is_no_reply``::

        text.to_lowercase().chars().filter(|c| c.is_alphanumeric()).collect()
            == "noreply"

    Rust ``char::is_alphanumeric`` maps to Python :meth:`str.isalnum`
    (both check Unicode ``Alphabetic`` or ``Numeric_Type``); the generator
    expression walks the lowered string once and ``str.join`` materialises
    it, mirroring ``chars().filter(...).collect::<String>()``.
    :meth:`str.lower` is the Unicode-aware equivalent of ``to_lowercase``
    (both lower-case ASCII input identically; rare locale-expansion cases
    like ``"İ"`` differ but never appear in the NO_REPLY convention).

    Args:
        text: Raw model response text.

    Returns:
        ``True`` if the alphanumeric-normalised, lower-cased text equals
        ``"noreply"``.
    """
    normalized = "".join(c for c in text.lower() if c.isalnum())
    return normalized == "noreply"
