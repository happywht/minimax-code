"""ScopeGraph subpackage -- per-file symbol tracking (direction (2), brick 2).

Mirrors grok ``xai-codebase-graph/src/scope_graph/mod.rs``. A ScopeGraph
represents the symbols (definitions, references, imports) and their
relationships within a single source file.

R301 lands the node / edge type layer only:

- :class:`EdgeKind` (from :mod:`edges`) -- the 5 relation labels.
- :class:`Symbol` / :class:`SymbolId` / :class:`LocalScope` /
  :class:`LocalDef` / :class:`LocalImport` / :class:`Reference` /
  :class:`NodeKind` / :class:`NodeKindKind` (from :mod:`nodes`) -- the node
  value types.

YAGNI (R301): grok ``scope_graph/mod.rs`` also re-exports the ``graph.rs``
runtime types (``ScopeGraph`` / ``ScopeGraphIndex`` / ``NodeIndex`` /
``QueryVersion`` / ``Snippet`` / ``extract_symbols_fast`` /
``scope_graph_from_definitions_query``) and defines ``ScopeGraphResult`` +
``build_scope_graph``. All of these depend on ``graph.rs`` (which itself
depends on ``interner.rs`` + the ``tree_sitter`` crate + ``languages/``);
they land with the ``graph.rs`` migration. The barrel will grow as they
land.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.scope_graph.edges import EdgeKind
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
    "NodeKind",
    "NodeKindKind",
    "Reference",
    "Symbol",
    "SymbolId",
]
