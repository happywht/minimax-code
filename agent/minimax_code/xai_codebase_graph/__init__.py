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

Downstream slices (``scope_graph/graph.rs`` runtime, ``interner/``,
``languages/``, ``manager/``, ``navigation``) follow in R302+.

The crate-root barrel mirrors grok ``lib.rs``: grok re-exports ``types`` and
``scope_graph`` node symbols at the crate root. The Python port keeps them
under their subpackages and re-exports them here so both import paths work::

    from minimax_code.xai_codebase_graph import Position          # crate root
    from minimax_code.xai_codebase_graph.types import Position    # subpackage
    from minimax_code.xai_codebase_graph import Symbol, NodeKind  # crate root
    from minimax_code.xai_codebase_graph.scope_graph import Symbol  # subpackage

Naming note: grok ``lib.rs`` does **not** re-export ``EdgeKind`` at the crate
root (it is only consumed inside ``scope_graph``). The Python port follows
the same surface: ``EdgeKind`` (and the Pythonic ``NodeKindKind``
discriminator, which has no grok counterpart) live under the
``scope_graph`` subpackage barrel only.

YAGNI (R301): the ``types`` layer and the ``scope_graph`` node / edge type
layer are public. ``scope_graph/graph.rs`` runtime (``ScopeGraph`` etc.),
``manager`` / ``navigation`` modules do not exist yet -- the barrel will grow
as they land.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.scope_graph import (
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    Reference,
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
    "LocalDef",
    "LocalImport",
    "LocalScope",
    "Location",
    "NodeKind",
    "Position",
    "Range",
    "Reference",
    "Symbol",
    "SymbolAlias",
    "SymbolId",
    "SymbolOccurrence",
]
