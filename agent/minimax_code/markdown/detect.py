"""Markdown structural-failure predicates (R37).

Ports the **pure string predicates** grok's ``xai-grok-markdown-core`` uses to
detect the two render-fidelity failures (:class:`~.stats.StructuralIssue`):

* :func:`fenced_block_is_unterminated` — a fenced code block whose source never
  closes, so it swallows the rest of the message.
* :func:`is_table_delimiter_line` / :func:`line_looks_like_header` — the raw
  line-shape tests that arm malformed-table detection (a delimiter row sitting
  under a pipe-bearing header that the parser did NOT turn into a table).

These work off **raw source text** only — the closure info for a fence and the
delimiter shape of a table are the one thing a token-stream parser cannot
recover, so they are computed before/independent of parsing. That makes them
host-agnostic and dependency-free: no markdown library is needed, and they can
run on any backend (or even the front end) without pulling in a parser.

What is NOT ported (parser-driven, wiring round): ``detect_malformed_tables``
needs the byte ranges of *real* tables/code blocks (the ``parsed_spans`` from
``pulldown-cmark``) to avoid flagging a ``|---|`` row that is legitimately
inside a parsed table or code block. That exclusion set comes from a parser,
so the full malformed-table detector is left to the wiring round that picks a
Python markdown parser; the raw-shape predicates it would call are here.
"""

from __future__ import annotations

__all__ = [
    "strip_block_prefix",
    "is_table_delimiter_line",
    "line_looks_like_header",
    "fenced_block_is_unterminated",
]


def strip_block_prefix(line: str) -> str:
    """Strip the container markers pulldown keeps on each source line.

    Removes leading ``>`` (blockquote), space, and tab characters. grok's
    ``trim_start_matches(['>', ' ', '\\t'])`` — ``lstrip`` with that same set
    is equivalent (it removes any leading run of those characters).
    """
    return line.lstrip("> \t")


def is_table_delimiter_line(line: str) -> bool:
    """A GFM table delimiter row: ``|``, ``-``, ``:`` and whitespace only, with
    at least one pipe and one dash.

    The pipe requirement rejects a bare ``---`` thematic break or a setext
    ``-----`` underline; the dash requirement rejects a ``|||``-only row.
    """
    stripped = line.strip()
    return (
        "|" in stripped
        and "-" in stripped
        and all(c in "|-: \t" for c in stripped)
    )


def line_looks_like_header(line: str) -> bool:
    """A line that could be a table header: non-empty, containing a column
    pipe, and not itself delimiter-shaped.

    Excluding delimiter-shaped lines prevents a ``|---|`` row arming the next
    line (which would chain one broken table into a duplicate flag per extra
    delimiter row).
    """
    stripped = line.strip()
    return bool(stripped) and "|" in stripped and not is_table_delimiter_line(stripped)


def fenced_block_is_unterminated(block_src: str) -> bool:
    """True iff no line after the opener is a closing fence matching the
    opener's character and length.

    Works off raw block source (the only thing carrying closure info), so a
    verbatim fence line in content could mask a real EOF — a rare,
    safe-direction (under-penalizing) miss we accept for simplicity. ``block_src``
    spans the opening fence through the close (or to EOF when unterminated),
    so its first line is the opener.
    """
    lines = iter(block_src.splitlines())
    try:
        open_line = strip_block_prefix(next(lines))
    except StopIteration:
        return False
    if not open_line or open_line[0] not in ("`", "~"):
        return False
    fence_char = open_line[0]
    open_len = len(open_line) - len(open_line.lstrip(fence_char))
    for line in lines:
        close = strip_block_prefix(line).rstrip()
        # ``open_len >= 3`` (CommonMark fence minimum), so the length check
        # implies ``close`` is non-empty before ``all`` runs over it.
        if len(close) >= open_len and all(c == fence_char for c in close):
            return False
    return True
