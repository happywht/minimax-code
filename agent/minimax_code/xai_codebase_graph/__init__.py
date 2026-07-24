"""Codebase graph index (direction (2) -- xai-codebase-graph crate port).

grok ``xai-codebase-graph`` builds a tree-sitter-backed symbol index over a
workspace: it tracks file metadata, parses definitions / references, and
serves scope-graph queries for navigation and code review.

R300 lands the type foundation only (``types/`` subpackage): ``Position`` /
``Range`` / ``Location`` / ``FileEvent`` / ``FileMeta`` / ``IndexStats`` /
``SymbolOccurrence`` / ``SymbolAlias``. Downstream slices (``scope_graph/``,
``interner/``, ``languages/``, ``manager/``, ``navigation``) follow in R301+.

The crate-root barrel mirrors grok ``lib.rs``: grok re-exports the ``types``
module's symbols at the crate root. The Python port keeps them under the
``types`` subpackage and re-exports them here so both import paths work::

    from minimax_code.xai_codebase_graph import Position          # crate root
    from minimax_code.xai_codebase_graph.types import Position    # subpackage

YAGNI (R300): only the type layer is public. ``scope_graph`` / ``manager`` /
``navigation`` modules do not exist yet -- the barrel will grow as they land.
"""

from __future__ import annotations

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
    "Location",
    "Position",
    "Range",
    "SymbolAlias",
    "SymbolOccurrence",
]
