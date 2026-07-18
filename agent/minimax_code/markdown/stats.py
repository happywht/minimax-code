"""Markdown analysis types (R37).

Ports the **type skeleton** of grok's ``xai-grok-markdown-core`` — the element
counts, the structural-issue discriminant, and the analysis container. These
are the host-agnostic data shapes a future render-fidelity analysis pass will
populate; the parser-driven ``analyze()`` loop itself depends on
``pulldown-cmark`` (a Rust markdown parser) and is left to a wiring round that
picks a Python parser.

What lives here (pure, no markdown-library dependency):

* :class:`MarkdownStats` — 21 element counters (headings per level, code
  kinds, emphasis/strong/strike, links/images, lists, math) plus the derived
  :meth:`headings` total and the :meth:`as_pairs` name→value projection.
* :class:`StructuralIssue` — the two render-fidelity failure modes grok
  detects (malformed table, unterminated code block); a unit enum whose
  string value is the stable log/metric key.
* :class:`MarkdownAnalysis` — the ``(stats, issues)`` container one parse pass
  produces.

Mapping
-------

* ``MarkdownStats`` / ``MarkdownAnalysis`` carry ``#[derive(Debug, Default,
  Clone, PartialEq, Eq)]`` — value-ish mutable aggregates with ``Default``.
  Per the R33 policy these map to **plain (non-frozen) dataclasses** (mutable,
  ``eq=True`` gives value equality); ``Default`` becomes ``field(default=…)`` /
  ``default_factory``. ``u32`` counters → ``int``.
* ``StructuralIssue`` is a **unit-only** enum (``#[derive(Debug, Clone, Copy,
  PartialEq, Eq)]``, no ``Hash``) → ``@unique enum.Enum`` per the R35 policy
  (singleton value semantics; Enum is hashable as a free side ability, which
  does not contradict grok's no-``Hash`` derive).

Product fusion
--------------

MiniMax Code's front end renders markdown in React, but the back end has no
render-fidelity story: when the model emits a table whose delimiter column
count mismatches the header, or a fenced code block missing its close fence,
the user sees silently-degraded output (a "table that didn't render" / a code
block swallowing the rest of the message). These types + the
:mod:`.detect` predicates are the analysis layer a future wiring round will
call right after the model stream completes, surfacing such failures as
structured issues rather than leaving them invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique

__all__ = [
    "MarkdownStats",
    "StructuralIssue",
    "MarkdownAnalysis",
]


@dataclass
class MarkdownStats:
    """Element counts from one markdown parse pass (grok ``MarkdownStats``).

    21 counters mirroring the ``pulldown-cmark`` event stream the renderer
    walks. grok marks the struct ``#[non_exhaustive]`` — treat the field set
    as open: adding a counter requires updating :meth:`as_pairs` too (the
    exhaustive list there is the single source of truth for name→value
    mapping, so downstream consumers cannot drift).
    """

    h1: int = 0
    h2: int = 0
    h3: int = 0
    h4: int = 0
    h5: int = 0
    h6: int = 0
    tables: int = 0
    fenced_code: int = 0
    indented_code: int = 0
    inline_code: int = 0
    strong: int = 0
    emphasis: int = 0
    strikethrough: int = 0
    links: int = 0
    images: int = 0
    blockquotes: int = 0
    thematic_breaks: int = 0
    inline_math: int = 0
    display_math: int = 0
    task_list_items: int = 0
    list_items: int = 0

    def headings(self) -> int:
        """Total heading count across all levels, derived from ``h1..=h6``."""
        return self.h1 + self.h2 + self.h3 + self.h4 + self.h5 + self.h6

    def as_pairs(self) -> list[tuple[str, int]]:
        """Stable name→value projection (22 entries, fixed order).

        Single source of truth for serialization/metrics: the ``headings``
        derived total is first, then the 21 raw counters in field-declaration
        order. Adding a counter to :class:`MarkdownStats` is a silent bug
        until it is appended here — keep the two in sync.
        """
        return [
            ("headings", self.headings()),
            ("h1", self.h1),
            ("h2", self.h2),
            ("h3", self.h3),
            ("h4", self.h4),
            ("h5", self.h5),
            ("h6", self.h6),
            ("tables", self.tables),
            ("fenced_code", self.fenced_code),
            ("indented_code", self.indented_code),
            ("inline_code", self.inline_code),
            ("strong", self.strong),
            ("emphasis", self.emphasis),
            ("strikethrough", self.strikethrough),
            ("links", self.links),
            ("images", self.images),
            ("blockquotes", self.blockquotes),
            ("thematic_breaks", self.thematic_breaks),
            ("inline_math", self.inline_math),
            ("display_math", self.display_math),
            ("task_list_items", self.task_list_items),
            ("list_items", self.list_items),
        ]


@unique
class StructuralIssue(Enum):
    """A render-fidelity failure: the model emitted markdown that does not
    render as the structure it clearly intended (grok ``StructuralIssue``).

    Distinct from :class:`MarkdownStats` counts: a count answers "how many
    tables", an issue answers "did a construct silently degrade". The string
    value is the stable snake_case key for logs, metrics, or FFI bindings.
    """

    #: A GFM table delimiter row (``|---|---|``) sits under a header line but
    #: the table did not parse (delimiter column count != header's), so the
    #: lines render as a paragraph — the "made a table but it didn't show" bug.
    MALFORMED_TABLE = "malformed_table"
    #: A fenced code block runs to EOF without a closing fence, swallowing the
    #: rest of the message.
    UNTERMINATED_CODE_BLOCK = "unterminated_code_block"

    def as_str(self) -> str:
        """Stable snake_case name (mirrors grok ``StructuralIssue::as_str``)."""
        return self.value


@dataclass
class MarkdownAnalysis:
    """Element counts plus any structural issues from one parse pass.

    grok ``#[derive(Debug, Default, Clone, PartialEq, Eq)]``; ``Default``
    gives empty stats + no issues.
    """

    stats: MarkdownStats = field(default_factory=MarkdownStats)
    issues: list[StructuralIssue] = field(default_factory=list)
