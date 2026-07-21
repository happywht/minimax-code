"""Tests for memory.chunker (R240, ``xai-grok-memory`` ``chunker.rs``).

Covers the migrated markdown-aware chunker: :class:`Chunk` (immutable chunk
record), :func:`chunk_markdown` (header / paragraph / line splitting strategy
with ancestor context + overlap tail), :func:`chunk_hash` (digest), and
:func:`header_level` (ATX header detection). Mirrors grok's ``chunker.rs``
tests plus Python-specific contract checks (str return type, 64-hex digest
length from the blake2b ``digest_size=32`` substitution, frozen-dataclass
immutability, overlap-tail continuation).
"""

from __future__ import annotations

import dataclasses

import pytest

from minimax_code.config_types import MemoryIndexConfig
from minimax_code.memory import Chunk, chunk_hash, chunk_markdown
from minimax_code.memory.chunker import header_level

# ---------------------------------------------------------------------------
# chunk_hash (mirrors grok test_chunk_hash_*).
# ---------------------------------------------------------------------------


def test_chunk_hash_deterministic() -> None:
    """Same input produces the same hash (grok test_chunk_hash_deterministic)."""
    assert chunk_hash("hello world") == chunk_hash("hello world")


def test_chunk_hash_different_inputs() -> None:
    """Different inputs produce different hashes (grok test_chunk_hash_different_inputs)."""
    assert chunk_hash("hello") != chunk_hash("world")


def test_chunk_hash_returns_str() -> None:
    """Return type is str (mirrors grok ``-> String``)."""
    assert isinstance(chunk_hash("x"), str)


def test_chunk_hash_is_64_hex_chars() -> None:
    """blake2b ``digest_size=32`` yields 64 hex chars (matches blake3's 32-byte digest).

    This is the wire-length contract preserved by the blake3 -> blake2b
    substitution documented in ``chunker.py``.
    """
    assert len(chunk_hash("test content")) == 64


def test_chunk_hash_is_lowercase_hex() -> None:
    """All chars are lowercase hex (blake2b ``hexdigest`` convention)."""
    h = chunk_hash("test content")
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# chunk_markdown (mirrors grok test_chunk_*).
# ---------------------------------------------------------------------------


def test_chunk_empty_content() -> None:
    """Empty content returns an empty list (grok test_chunk_empty_content)."""
    assert chunk_markdown("", MemoryIndexConfig.default()) == []


def test_chunk_small_content_single_chunk() -> None:
    """Content under ``max_chunk_chars`` returns one chunk spanning all lines."""
    content = "# Title\n\nSome text here."
    chunks = chunk_markdown(content, MemoryIndexConfig.default())
    assert len(chunks) == 1
    assert chunks[0].text == content
    assert chunks[0].start_line == 0
    assert chunks[0].end_line == 3  # ["# Title", "", "Some text here."]


def test_chunk_splits_on_headers() -> None:
    """Multiple ``##`` headers produce multiple chunks (grok test_chunk_splits_on_headers)."""
    config = MemoryIndexConfig(max_chunk_chars=80, chunk_overlap_chars=0)
    content = (
        "## Section 1\n\n"
        "Content for section 1 goes here with enough text to matter.\n\n"
        "## Section 2\n\n"
        "Content for section 2 is also significant enough to be a chunk."
    )
    chunks = chunk_markdown(content, config)
    assert len(chunks) >= 2
    assert "Section 1" in chunks[0].text
    assert "Section 2" in chunks[-1].text


def test_chunk_header_context_for_subsections() -> None:
    """A sub-section chunk is prefixed with ancestor header context."""
    config = MemoryIndexConfig(max_chunk_chars=60, chunk_overlap_chars=0)
    content = (
        "## Parent\n\n"
        "Intro.\n\n"
        "### Child\n\n"
        "Child content that is long enough to be its own chunk definitely."
    )
    chunks = chunk_markdown(content, config)
    child_chunks = [c for c in chunks if "[Context: ## Parent]" in c.text]
    assert len(child_chunks) >= 1


def test_chunk_large_section_splits_on_paragraphs() -> None:
    """A section over ``max_chunk_chars`` splits on paragraph boundaries."""
    config = MemoryIndexConfig(max_chunk_chars=150, chunk_overlap_chars=0)
    content = "## Big Section\n\n" + "A" * 100 + "\n\n" + "B" * 100
    chunks = chunk_markdown(content, config)
    assert len(chunks) >= 2


def test_chunk_line_numbers() -> None:
    """Single-chunk content reports start=0, end=line_count (grok test_chunk_line_numbers)."""
    content = "line 0\nline 1\nline 2\nline 3\nline 4"
    chunks = chunk_markdown(content, MemoryIndexConfig.default())
    assert len(chunks) == 1
    assert chunks[0].start_line == 0
    assert chunks[0].end_line == 5


def test_chunk_preserves_code_blocks() -> None:
    """Code fences survive chunking intact when content fits one chunk."""
    content = (
        "## Code\n\n"
        "```rust\n"
        "fn main() {\n"
        '    println!("hello");\n'
        "}\n"
        "```\n\n"
        "Some text."
    )
    chunks = chunk_markdown(content, MemoryIndexConfig.default())
    assert len(chunks) == 1
    assert "```rust" in chunks[0].text
    assert "fn main()" in chunks[0].text


def test_chunk_markdown_returns_list_of_chunks() -> None:
    """Return type is ``list[Chunk]``."""
    chunks = chunk_markdown("# Hi", MemoryIndexConfig.default())
    assert isinstance(chunks, list)
    assert all(isinstance(c, Chunk) for c in chunks)


def test_chunk_markdown_consumes_memory_index_config() -> None:
    """``chunk_markdown`` reads ``max_chunk_chars`` to decide splitting."""
    # A tiny max forces splitting even on short content.
    config = MemoryIndexConfig(max_chunk_chars=5, chunk_overlap_chars=0)
    chunks = chunk_markdown("aaaa\n\nbbbb", config)
    assert len(chunks) >= 2


def test_chunk_overlap_tail_continuation() -> None:
    """When ``chunk_overlap_chars > 0``, a paragraph-split continuation starts
    with the previous chunk's tail.

    Content ``"abcdefghij\\n\\nklmnop"`` with ``max=5, overlap=3``: the first
    paragraph flushes at the blank line (10 > 5), and the continuation is
    seeded with the last 3 chars of ``"abcdefghij"`` -> ``"hij"``.
    """
    config = MemoryIndexConfig(max_chunk_chars=5, chunk_overlap_chars=3)
    chunks = chunk_markdown("abcdefghij\n\nklmnop", config)
    assert len(chunks) >= 2
    assert chunks[1].text.startswith("hij")


# ---------------------------------------------------------------------------
# header_level (mirrors grok test_header_level_detection).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# Title", 1),
        ("## Section", 2),
        ("### Subsection", 3),
        ("#### Deep", 4),
        ("Not a header", None),
        ("#hashtag", None),  # no space after hashes -> not a header
        ("", None),
    ],
)
def test_header_level_detection(line: str, expected: int | None) -> None:
    """header_level detects ATX levels and rejects non-headers (7 grok cases)."""
    assert header_level(line) == expected


def test_header_level_indented() -> None:
    """Leading whitespace is stripped before detection (grok ``trim_start``)."""
    assert header_level("   ## Indented") == 2


def test_header_level_bare_hash_eol() -> None:
    """A bare ``#`` with nothing after is a valid level-1 header (rest empty)."""
    assert header_level("#") == 1


def test_header_level_returns_optional() -> None:
    """Return type is ``int | None`` (mirrors grok ``-> Option<usize>``)."""
    assert isinstance(header_level("# x"), int)
    assert header_level("nope") is None


# ---------------------------------------------------------------------------
# Chunk dataclass contract (Python-specific).
# ---------------------------------------------------------------------------


def test_chunk_is_frozen() -> None:
    """Chunk is immutable (frozen dataclass; mirrors grok ``Clone`` value semantics)."""
    c = Chunk(text="hi", start_line=0, end_line=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.text = "mutated"  # type: ignore[misc]


def test_chunk_equality() -> None:
    """Two Chunks with equal fields compare equal (grok ``PartialEq, Eq``)."""
    a = Chunk(text="hi", start_line=0, end_line=1)
    b = Chunk(text="hi", start_line=0, end_line=1)
    c = Chunk(text="bye", start_line=0, end_line=1)
    assert a == b
    assert a != c


def test_chunk_is_hashable() -> None:
    """``frozen=True`` derives ``__hash__`` -- useful for de-dup sets.

    grok has no ``Hash`` derive on ``Chunk``, but hashability is a harmless
    Python bonus and occasionally useful (e.g. deduplicating chunks).
    """
    a = Chunk(text="hi", start_line=0, end_line=1)
    b = Chunk(text="hi", start_line=0, end_line=1)
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
