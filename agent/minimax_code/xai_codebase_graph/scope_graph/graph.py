"""ScopeGraph runtime -- per-file symbol graph (direction (2), brick 6, leaf a).

Mirrors grok ``xai-codebase-graph/src/scope_graph/graph.rs``. R305a lands the
pure-data foundation of the graph runtime:

- Type aliases: :data:`NodeIndex` (graph node handle),
  :data:`SymbolWithRange`, :data:`ReferenceWithDefinition`,
  :data:`ExtractedSymbols`.
- :class:`QueryVersion` -- tracks the tree-sitter query hash used to build an
  index, so stale indexes (``Legacy`` / mismatched version) trigger a rebuild.
- :class:`Snippet` -- a code fragment with its text, line range, and
  associated symbols.

R305a is **zero tree-sitter**: every symbol here is pure data / pure logic,
consuming only R300 :class:`~minimax_code.xai_codebase_graph.types.Range` and
R301 :class:`~minimax_code.xai_codebase_graph.scope_graph.nodes.Symbol`. The
``ScopeGraph`` / ``ScopeStack`` graph algorithms (R305b), the
``ScopeGraphIndex`` runtime + binary ser/de (R305c), and the two tree-sitter
bridge free functions (R305d) land in subsequent leaves.

Functional-clone decisions (not line-by-line):

- ``NodeIndex``: grok ``petgraph::graph::NodeIndex<u32>`` is a newtype around
  ``u32`` with no behavioural surface beyond indexing, so the Python port uses
  a bare ``int`` alias.
- ``Arc<str>``: Python ``str`` is already an immutable shared reference, so no
  ``Arc`` analogue is needed.
- ``QueryVersion``: grok's two-variant enum ``{ Legacy, Version(u64) }``
  collapses to a single optional field (``version is None`` == ``Legacy``).
  Behaviourally identical, friendlier to (de)serialise later.
- ``Snippet``: grok derives ``Clone`` (not ``Copy``), so the Python mirror
  stays a mutable (non-frozen) dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from minimax_code.xai_codebase_graph.scope_graph.nodes import Symbol
from minimax_code.xai_codebase_graph.types import Range

# === type aliases ==========================================================
# These mirror grok ``graph.rs`` L23-39 ``pub type`` aliases. They are public
# (grok ``pub``) but only ``NodeIndex`` / ``QueryVersion`` / ``Snippet`` are
# re-exported by ``scope_graph/mod.rs``; the three compound aliases stay
# internal to the crate (consumed by the bridge functions landing in R305d).

#: Graph node handle. grok ``petgraph::graph::NodeIndex<u32>`` -- a wrapped
#: ``u32`` indexing a node slot. Python uses a bare ``int``: petgraph's
#: newtype carries no behaviour beyond indexing.
NodeIndex: TypeAlias = int

#: A symbol name paired with its source range.
#: grok ``(Arc<str>, Range)`` -- Python ``str`` is already an immutable shared
#: reference, so no ``Arc`` analogue is needed.
SymbolWithRange: TypeAlias = tuple[str, Range]

#: A reference and the definition it resolved to (if any).
#: Format: ``(ref_name, ref_range, optional (def_name, def_range))``.
ReferenceWithDefinition: TypeAlias = tuple[str, Range, tuple[str, Range] | None]

#: Result of symbol extraction: ``(definitions, references, aliases)``.
#: Aliases are ``(alias_name, original_name)`` string pairs. grok uses
#: ``Arc<str>`` for alias halves to avoid extra allocation when merged into
#: the index; the Python port drops the ``Arc`` (``str`` suffices).
ExtractedSymbols: TypeAlias = tuple[
    "list[SymbolWithRange]",
    "list[SymbolWithRange]",
    "list[tuple[str, str]]",
]


# === QueryVersion ==========================================================


@dataclass(frozen=True)
class QueryVersion:
    """Version stamp tracking which tree-sitter queries built an index.

    Mirrors grok ``enum QueryVersion { #[default] Legacy, Version(u64) }`` as
    a frozen dataclass where ``version is None`` encodes ``Legacy`` and
    ``version == Some(v)`` encodes ``Version(v)``. This collapses the
    two-variant enum into a single optional field -- behaviourally identical
    (``None`` is the default, matching grok's ``#[default] Legacy``) and
    friendlier to serialise when the binary index format lands in R305c.

    A rebuild is needed when the index is ``Legacy`` (unknown provenance) or
    when the stamped version differs from the current query hash.
    """

    version: int | None = None

    def needs_rebuild(self, current_version: int) -> bool:
        """Return whether the index must be rebuilt for ``current_version``.

        Mirrors grok ``QueryVersion::needs_rebuild``:

        - ``Legacy`` (``version is None``) -> always rebuild (unknown queries).
        - ``Version(v)`` (``version == v``) -> rebuild iff ``v != current``.
        """
        return self.version is None or self.version != current_version


# === Snippet ===============================================================


@dataclass
class Snippet:
    """A code fragment with its text, line range, and associated symbols.

    Mirrors grok ``struct Snippet { data: String, line_range: Range<usize>,
    symbols: Vec<Symbol> }``. ``line_range`` is a half-open ``(start, end)``
    pair (grok ``std::ops::Range<usize>``). Not frozen -- grok derives
    ``Clone`` (not ``Copy``), so the Python mirror stays mutable to match.
    """

    data: str
    line_range: tuple[int, int]
    symbols: list[Symbol]


__all__ = [
    "ExtractedSymbols",
    "NodeIndex",
    "QueryVersion",
    "ReferenceWithDefinition",
    "Snippet",
    "SymbolWithRange",
]
