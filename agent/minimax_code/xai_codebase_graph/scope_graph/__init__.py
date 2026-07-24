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

R305a extended the barrel with the pure-data foundation of the ``graph.rs``
runtime (from :mod:`graph`): :data:`NodeIndex`, :class:`QueryVersion`, and
:class:`Snippet`.

R305b lands the graph algorithms themselves: :class:`ScopeGraph` (the
per-file scope/def/ref/import graph, 22 methods), :class:`ScopeStack` (an
iterator walking enclosing scopes to root), and :class:`ScopeGraphResult`
(the ``{graph, aliases}`` container grok defines inline in ``mod.rs`` L20-L25).
These mirror grok ``scope_graph/mod.rs`` L11-L14, which re-exports ``NodeIndex``
/ ``QueryVersion`` / ``Snippet`` / ``ScopeGraph`` / ``ScopeStack``.

R305c lands :class:`ScopeGraphIndex` (from :mod:`index`) -- the cross-file
symbol index that aggregates many per-file :class:`ScopeGraph` instances,
interned symbol names, alias resolution, and reverse file->symbol indexes
for O(symbols-in-file) removal / rename. The in-memory runtime (~36 methods)
mirrors grok ``scope_graph/graph.rs`` ``ScopeGraphIndex``.

R305d lands the SGIX v1 binary ser/de (``save`` / ``load`` / ``write_to`` /
``read_from`` in :mod:`sgix`) -- the on-disk format for
:class:`ScopeGraphIndex`. These stay in their own module (not re-exported
here) because grok models them as ``ScopeGraphIndex`` methods reached via the
type, not as ``mod.rs`` re-exports.

R305e lands the tree-sitter bridge free functions (from :mod:`bridge`):
:func:`build_scope_graph`, :func:`extract_symbols_fast`, and
:func:`scope_graph_from_definitions_query`. These re-export here to mirror
grok ``mod.rs`` L13 (which re-exports the two ``graph.rs`` free functions) +
L30 (the inline ``build_scope_graph``). The bridge is the single module that
touches the ``tree_sitter`` runtime -- the deferred-binding兑现点 of range.py
L9-L14 and types.py L11-L24.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.scope_graph.bridge import (
    build_scope_graph,
    extract_symbols_fast,
    scope_graph_from_definitions_query,
)
from minimax_code.xai_codebase_graph.scope_graph.edges import EdgeKind
from minimax_code.xai_codebase_graph.scope_graph.graph import (
    NodeIndex,
    QueryVersion,
    ScopeGraph,
    ScopeGraphResult,
    ScopeStack,
    Snippet,
)
from minimax_code.xai_codebase_graph.scope_graph.index import ScopeGraphIndex
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
    "build_scope_graph",
    "extract_symbols_fast",
    "scope_graph_from_definitions_query",
    "LocalDef",
    "LocalImport",
    "LocalScope",
    "NodeIndex",
    "NodeKind",
    "NodeKindKind",
    "QueryVersion",
    "Reference",
    "ScopeGraph",
    "ScopeGraphIndex",
    "ScopeGraphResult",
    "ScopeStack",
    "Snippet",
    "Symbol",
    "SymbolId",
]
