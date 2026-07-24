"""Codebase graph index (direction (2) -- xai-codebase-graph crate port).

grok ``xai-codebase-graph`` builds a tree-sitter-backed symbol index over a
workspace: it tracks file metadata, parses definitions / references, and
serves scope-graph queries for navigation and code review.

R300 lands the type foundation (``types/`` subpackage): ``Position`` /
``Range`` / ``Location`` / ``FileEvent`` / ``FileMeta`` / ``IndexStats`` /
``SymbolOccurrence`` / ``SymbolAlias``.

R301 adds the scope-graph node / edge type layer (``scope_graph/``
subpackage): ``Symbol`` / ``SymbolId`` / ``LocalScope`` / ``LocalDef`` /
``LocalImport`` / ``Reference`` / ``NodeKind`` (plus ``EdgeKind`` /
``NodeKindKind``, exported from the subpackage only -- see below).

R302 adds the string interner (``interner`` module): ``StringId`` /
``StringInterner`` -- dedup byte-string store backing symbol name storage.

R303 adds the per-language tree-sitter config type (``languages/`` subpackage):
``TSLanguageConfig`` -- bundles language ids / extensions / namespaces / the
definitions query string, resolves symbol-type names to ``SymbolId``, and
defers the tree-sitter runtime binding (``grammar`` optional) so the type layer
stays stdlib-clean.

R304 lands the language registry (grok ``languages/mod.rs``):
:class:`LanguageRegistry` -- preloaded with five language configs (python /
golang / javascript / rust / typescript factories) and serving lookup by
extension / id / file path, a same-language-family check, and a stable
``compute_query_hash`` (``hashlib.blake2b``) used to invalidate the index when
queries change. Mirrors grok ``lib.rs`` L88
``pub use languages::{LanguageRegistry, TSLanguageConfig};``.

R305a began the ``scope_graph/graph.rs`` runtime port (pure-data foundation
only): :class:`QueryVersion` -- the tree-sitter query version stamp that
drives index rebuilds.

R305b lands the :class:`ScopeGraph` runtime itself (the per-file scope/def/
ref/import graph, 22 methods) plus the :class:`ScopeGraphResult`
``{graph, aliases}`` container.

R305c lands the :class:`ScopeGraphIndex` runtime -- the cross-file symbol
index (~36 in-memory methods) that aggregates per-file :class:`ScopeGraph`
instances with interned names, alias resolution, and reverse file->symbol
indexes. Mirrors grok ``lib.rs`` L95-L98, which re-exports ``ScopeGraph`` /
``ScopeGraphResult`` / ``ScopeGraphIndex`` at the crate root. The SGIX binary
ser/de (R305d), the tree-sitter bridge (R305e), plus ``manager/`` and
``navigation``, follow.

The crate-root barrel mirrors grok ``lib.rs``: grok re-exports ``types``,
``scope_graph`` node symbols, and the ``interner`` pair at the crate root.
The Python port keeps them under their subpackages and re-exports them
here so both import paths work::

    from minimax_code.xai_codebase_graph import Position          # crate root
    from minimax_code.xai_codebase_graph.types import Position    # subpackage
    from minimax_code.xai_codebase_graph import Symbol, NodeKind  # crate root
    from minimax_code.xai_codebase_graph.scope_graph import Symbol  # subpackage
    from minimax_code.xai_codebase_graph import StringId          # crate root
    from minimax_code.xai_codebase_graph.interner import StringId  # module

Naming note: grok ``lib.rs`` does **not** re-export ``EdgeKind`` at the crate
root (it is only consumed inside ``scope_graph``). The Python port follows
the same surface: ``EdgeKind`` (and the Pythonic ``NodeKindKind``
discriminator, which has no grok counterpart) live under the
``scope_graph`` subpackage barrel only.

YAGNI: the ``types`` layer, the ``scope_graph`` node / edge type layer, the
``interner`` module, and the ``scope_graph/graph.py`` pure-data foundation
(``QueryVersion`` / ``Snippet`` / ``NodeIndex``) are public. The graph
algorithms (``ScopeGraph`` / ``ScopeGraphIndex``), ``manager`` /
``navigation`` modules do not exist yet -- the barrel grows as they land.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.interner import StringId, StringInterner
from minimax_code.xai_codebase_graph.languages import (
    LanguageRegistry,
    TSLanguageConfig,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    QueryVersion,
    Reference,
    ScopeGraph,
    ScopeGraphIndex,
    ScopeGraphResult,
    Symbol,
    SymbolId,
)
from minimax_code.xai_codebase_graph.types import (
    FileEvent,
    FileEventKind,
    FileMeta,
    IndexStats,
    Location,
    Position,
    Range,
    SymbolAlias,
    SymbolOccurrence,
)

__all__ = [
    "FileEvent",
    "FileEventKind",
    "FileMeta",
    "IndexStats",
    "LanguageRegistry",
    "LocalDef",
    "LocalImport",
    "LocalScope",
    "Location",
    "NodeKind",
    "Position",
    "QueryVersion",
    "Range",
    "Reference",
    "ScopeGraph",
    "ScopeGraphIndex",
    "ScopeGraphResult",
    "StringId",
    "StringInterner",
    "Symbol",
    "SymbolAlias",
    "SymbolId",
    "SymbolOccurrence",
    "TSLanguageConfig",
]
