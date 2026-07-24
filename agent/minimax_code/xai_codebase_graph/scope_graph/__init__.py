"""ScopeGraph subpackage -- per-file symbol tracking (direction (2), brick 2).

Mirrors grok ``xai-codebase-graph/src/scope_graph/mod.rs``. A ScopeGraph
represents the symbols (definitions, references, imports) and their
relationships within a single source file.

R301 landed the node / edge type layer:

- :class:`EdgeKind` (from :mod:`edges`) -- the 5 relation labels.
- :class:`Symbol` / :class:`SymbolId` / :class:`LocalScope` /
  :class:`LocalDef` / :class:`LocalImport` / :class:`Reference` /
  :class:`NodeKind` / :class:`NodeKindKind` (from :mod:`nodes`) -- the node
  value types.

R305a extends the barrel with the pure-data foundation of the ``graph.rs``
runtime (from :mod:`graph`): :data:`NodeIndex`, :class:`QueryVersion`, and
:class:`Snippet`. These mirror grok ``scope_graph/mod.rs`` L11-L14, which
re-exports ``NodeIndex`` / ``QueryVersion`` / ``Snippet`` ahead of the heavier
``ScopeGraph`` / ``ScopeGraphIndex`` / ``ScopeStack`` graph algorithms and the
``extract_symbols_fast`` / ``scope_graph_from_definitions_query`` bridge
functions that land in R305b-d.

YAGNI: grok ``scope_graph/mod.rs`` also defines ``ScopeGraphResult`` and
``build_scope_graph`` (both depend on ``ScopeGraph``). All of these depend on
the graph algorithms / tree-sitter bridge (R305b-d); the barrel grows as they
land.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.scope_graph.edges import EdgeKind
from minimax_code.xai_codebase_graph.scope_graph.graph import (
    NodeIndex,
    QueryVersion,
    Snippet,
)
from minimax_code.xai_codebase_graph.scope_graph.nodes import (
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    NodeKindKind,
    Reference,
    Symbol,
    SymbolId,
)

__all__ = [
    "EdgeKind",
    "LocalDef",
    "LocalImport",
    "LocalScope",
    "NodeIndex",
    "NodeKind",
    "NodeKindKind",
    "QueryVersion",
    "Reference",
    "Snippet",
    "Symbol",
    "SymbolId",
]
