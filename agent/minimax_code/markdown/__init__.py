"""Markdown analysis — fusion of Grok Build's ``xai-grok-markdown-core``.

Render-fidelity analysis for model markdown output. Where MiniMax Code's
front end *renders* markdown, this package lets the back end *audit* it:
count elements and flag the two silent-degradation failures (malformed table,
unterminated code block) that a token-stream renderer hides.

Scope of this package
---------------------

* :mod:`.stats` (R37) — element-count dataclass, structural-issue enum, and the
  analysis container. Pure type skeleton; the parser-driven ``analyze()`` loop
  that populates them is a wiring round (it needs a Python markdown parser;
  grok uses ``pulldown-cmark``).
* :mod:`.detect` (R37) — raw-source predicates for the two structural
  failures (fence-closure and table-delimiter/header line shape). Dependency-
  free; runnable anywhere without a markdown library.

No markdown parser is pulled in here — the types and predicates are the
host-agnostic core a future wiring round composes with a parser to produce a
full :class:`~.stats.MarkdownAnalysis`.
"""

from __future__ import annotations

from .detect import (
    fenced_block_is_unterminated,
    is_table_delimiter_line,
    line_looks_like_header,
    strip_block_prefix,
)
from .stats import MarkdownAnalysis, MarkdownStats, StructuralIssue

__all__ = [
    # stats (R37)
    "MarkdownStats",
    "StructuralIssue",
    "MarkdownAnalysis",
    # detect (R37)
    "strip_block_prefix",
    "is_table_delimiter_line",
    "line_looks_like_header",
    "fenced_block_is_unterminated",
]
