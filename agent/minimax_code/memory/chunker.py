"""Markdown-aware semantic chunking (R240, ``xai-grok-memory`` ``chunker.rs``).

Splits markdown content into chunks suitable for embedding and search.
Chunks respect markdown structure (headers, paragraphs, code blocks) and
include ancestor header context for self-containment.

**Fourth memory leaf (R240)** -- closes the pure-logic closure of the
``xai-grok-memory`` crate. Alongside :mod:`minimax_code.memory.mmr` (R237,
MMR re-rank), :mod:`minimax_code.memory.query_expansion` (R238, FTS keyword
extraction), and :mod:`minimax_code.memory.text_utils` (R239,
response-classification predicates), this ports the last zero-I/O leaf of
the memory subsystem's indexing pipeline. The remaining ``xai-grok-memory``
modules (``index.rs`` MemoryIndex, ``storage.rs`` MemoryStorage,
``embedding.rs``, ``dream.rs``, ``search.rs`` hybrid scoring, ``watcher.rs``,
``archive.rs``, ``backend.rs``) are heavy runtime stacks (rusqlite /
sqlite-vec / git2 / notify / reqwest) with no Python consumer -- only their
pure-logic leaves migrate.

Character counts proxy for token counts (chars / 4 ~= tokens), mirroring
grok's ``content.len()`` byte count. For ASCII content byte length and
Python :func:`len` code-point length are identical; for non-ASCII they
diverge, but ``max_chunk_chars`` is a soft budget so the difference is
immaterial and Python code-point counting is the natural unit for a Python
embedding pipeline.

blake3 -> hashlib.blake2b substitution
--------------------------------------
grok's :func:`chunk_hash` uses the ``blake3`` crate (32-byte / 64-hex digest).
Python's standard library has no ``blake3``; this leaf uses
:func:`hashlib.blake2b` with ``digest_size=32`` to reproduce the same wire
contract (deterministic + 64-char hex + distinct inputs map to distinct
outputs). The two algorithms are NOT interoperable -- a hash computed here
will not match a ``blake3`` hash of the same text -- but MiniMax Code has no
Rust runtime that reads memory indices produced by Python, so cross-language
hash consistency is YAGNI. If that ever becomes a requirement, swap
:func:`chunk_hash` to the third-party ``blake3`` PyPI package (drop-in:
``blake3.blake3(text.encode()).hexdigest()``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from minimax_code.config_types import MemoryIndexConfig


@dataclass(frozen=True, slots=True)
class Chunk:
    """A chunk of text extracted from a memory file.

    Mirrors grok ``Chunk`` (``#[derive(Debug, Clone, PartialEq, Eq)]``).
    Frozen + slots gives the same immutable-value semantics; ``frozen=True``
    additionally derives :func:`hash` (grok has no ``Hash`` derive, but a
    hashable Python value is harmless and occasionally useful for de-dup
    sets).
    """

    #: The chunk text, including ancestor header context.
    text: str
    #: 0-based start line in the source file.
    start_line: int
    #: 0-based end line (exclusive) in the source file.
    end_line: int


def chunk_hash(text: str) -> str:
    """Compute a digest of the chunk text, returned as a 64-char hex string.

    grok uses ``blake3::hash``; this leaf uses :func:`hashlib.blake2b` with
    ``digest_size=32`` (see module docstring for the substitution rationale).
    Both yield a 32-byte / 64-hex-char digest, so the wire-length contract is
    preserved.

    Args:
        text: Chunk text to hash.

    Returns:
        A 64-character lowercase hex string.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=32).hexdigest()


def chunk_markdown(content: str, config: MemoryIndexConfig) -> list[Chunk]:
    """Split markdown content into chunks, respecting structure.

    Strategy (mirrors grok ``chunk_markdown``):

    1. Split on ``##`` headers -- each section is a candidate chunk.
    2. If a section exceeds ``max_chunk_chars``, split on paragraph
       boundaries (``\\n\\n``).
    3. If a paragraph still exceeds ``max_chunk_chars``, split on line
       boundaries.
    4. Continuation chunks are prefixed with ancestor header context.

    When a section is split into multiple sub-chunks, each continuation chunk
    is prefixed with the last ``chunk_overlap_chars`` of the previous chunk
    for embedding continuity, plus ancestor header context.

    Args:
        content: Raw markdown text to chunk.
        config: Indexing config (``max_chunk_chars`` / ``chunk_overlap_chars``).

    Returns:
        A list of :class:`Chunk` objects (empty if ``content`` is empty).
    """
    if not content:
        return []

    max_chars = config.max_chunk_chars
    lines = content.splitlines()

    if not lines:
        return []

    # If the entire content fits in one chunk, return it directly.
    if len(content) <= max_chars:
        return [Chunk(text=content, start_line=0, end_line=len(lines))]

    # Split into sections by ## headers
    sections = _split_by_headers(lines)
    chunks: list[Chunk] = []

    for section in sections:
        section_text = "\n".join(section.lines)

        if len(section_text) <= max_chars:
            chunks.append(
                Chunk(
                    text=_add_header_context(section.header_context, section_text),
                    start_line=section.start_line,
                    end_line=section.start_line + len(section.lines),
                )
            )
        else:
            # Section too large -- split on paragraph boundaries
            sub_chunks = _split_section_by_paragraphs(
                section, max_chars, config.chunk_overlap_chars
            )
            chunks.extend(sub_chunks)

    return chunks


@dataclass(frozen=True, slots=True)
class _Section:
    """A section of the document delimited by headers (internal).

    Mirrors grok's private ``Section<'a>``. grok borrows lines via lifetime
    ``&'a str``; Python owns them as ``list[str]`` (the source ``lines``
    list is never mutated after a section captures it).
    """

    lines: list[str]
    start_line: int
    header_context: str


def _split_by_headers(lines: list[str]) -> list[_Section]:
    """Split lines into sections by ``##`` (or deeper) headers."""
    sections: list[_Section] = []
    current_lines: list[str] = []
    current_start = 0
    header_stack: list[tuple[int, str]] = []  # (level, text)

    for i, line in enumerate(lines):
        level = header_level(line)
        if level is not None:
            # Flush previous section
            if current_lines:
                sections.append(
                    _Section(
                        lines=current_lines,
                        start_line=current_start,
                        header_context=_format_header_context(header_stack),
                    )
                )
                current_lines = []
            current_start = i

            # Update header stack: pop headers at same or deeper level
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()
            header_stack.append((level, line))
        current_lines.append(line)

    # Flush final section
    if current_lines:
        sections.append(
            _Section(
                lines=current_lines,
                start_line=current_start,
                header_context=_format_header_context(header_stack),
            )
        )

    return sections


def _split_section_by_paragraphs(
    section: _Section, max_chars: int, overlap_chars: int
) -> list[Chunk]:
    """Split a large section into sub-chunks by paragraph boundaries.

    Continuation chunks are prefixed with the last ``overlap_chars`` of the
    previous chunk for embedding continuity.
    """
    chunks: list[Chunk] = []
    current_text = ""
    current_start = section.start_line
    line_offset = 0

    for i, line in enumerate(section.lines):
        is_blank = not line.strip()

        # Paragraph boundary: blank line AND accumulated text is non-empty
        if is_blank and current_text and len(current_text) + len(line) > max_chars:
            # Flush current chunk
            flushed = current_text.strip()
            chunks.append(
                Chunk(
                    text=_add_header_context(section.header_context, flushed),
                    start_line=current_start,
                    end_line=section.start_line + i,
                )
            )
            # Apply overlap: start next chunk with tail of previous.
            # grok: flushed.chars().rev().take(overlap_chars) -- the last
            # ``overlap_chars`` code points. Python's ``flushed[-n:]`` slice
            # is code-point-based and returns the whole string when
            # ``n >= len(flushed)`` (matching ``take`` semantics). The
            # ``overlap_chars > 0`` guard is mandatory because ``[-0:]``
            # degenerates to ``[0:]`` (the whole string) in Python.
            current_text = flushed[-overlap_chars:] if overlap_chars > 0 else ""
            current_start = section.start_line + i + 1
            line_offset = i + 1
            continue

        if current_text:
            current_text += "\n"
        current_text += line

        # If single line pushes us over max, flush what we have
        if len(current_text) > max_chars and i > line_offset:
            split_at = current_text.rfind("\n")
            if split_at == -1:
                split_at = len(current_text)
            keep = current_text[:split_at]
            remainder = current_text[split_at:]
            chunks.append(
                Chunk(
                    text=_add_header_context(section.header_context, keep.strip()),
                    start_line=current_start,
                    end_line=section.start_line + i,
                )
            )
            current_text = remainder.lstrip("\n")
            current_start = section.start_line + i
            line_offset = i

    # Flush remaining
    if current_text.strip():
        chunks.append(
            Chunk(
                text=_add_header_context(section.header_context, current_text.strip()),
                start_line=current_start,
                end_line=section.start_line + len(section.lines),
            )
        )

    return chunks


def header_level(line: str) -> int | None:
    """Detect markdown header level (1 for ``#``, 2 for ``##``, etc.).

    Returns ``None`` if the line is not a header. Mirrors grok
    ``header_level`` (``pub(crate)`` -- module-public here so the unit test
    can exercise it directly, but intentionally NOT barrel-exported).

    A valid header is a run of ``#`` followed by either a space or
    end-of-line: ``"#hashtag"`` (no space) is NOT a header.
    """
    trimmed = line.lstrip()
    if not trimmed.startswith("#"):
        return None
    # Count leading '#' chars (mirrors trimmed.chars().take_while(|c| c == '#'))
    level = len(trimmed) - len(trimmed.lstrip("#"))
    rest = trimmed[level:]
    if not rest or rest.startswith(" "):
        return level
    return None


def _format_header_context(stack: list[tuple[int, str]]) -> str:
    """Format header stack into a context string like ``"## A > ### B"``."""
    if len(stack) <= 1:
        return ""
    # Skip the last entry (it's the current section's own header)
    return " > ".join(text.strip() for _, text in stack[:-1])


def _add_header_context(context: str, text: str) -> str:
    """Prepend ancestor header context to chunk text (if non-empty)."""
    if not context:
        return text
    return f"[Context: {context}]\n\n{text}"
