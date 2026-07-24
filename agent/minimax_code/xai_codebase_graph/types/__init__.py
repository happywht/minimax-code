"""Type layer for the codebase graph (direction (2), brick 1).

Mirrors grok ``xai-codebase-graph/src/types/mod.rs`` -- the crate's
``pub use`` barrel re-exports the leaf types (``FileEvent``, ``Location``,
``Position``, ``Range``) alongside the structural types defined in the same
file (``IndexStats`` / ``SymbolOccurrence`` / ``SymbolAlias`` / ``FileMeta``).

This barrel is the crate's public type surface: the structural types live
in :mod:`minimax_code.xai_codebase_graph.types.mod` (definitions) and the
leaves live in their own modules (:mod:`range`, :mod:`location`,
:mod:`file_event`). The barrel re-exports all nine public symbols so a caller
imports from ``minimax_code.xai_codebase_graph.types`` directly.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.types.file_event import FileEvent, FileEventKind
from minimax_code.xai_codebase_graph.types.location import Location
from minimax_code.xai_codebase_graph.types.mod import (
    FileMeta,
    IndexStats,
    SymbolAlias,
    SymbolOccurrence,
)
from minimax_code.xai_codebase_graph.types.range import Position, Range

__all__ = [
    # leaves (grok mod.rs ``pub use`` re-exports)
    "FileEvent",
    "Location",
    "Position",
    "Range",
    # structural types (defined in grok mod.rs body)
    "FileMeta",
    "IndexStats",
    "SymbolAlias",
    "SymbolOccurrence",
    # Pythonic discriminator for FileEvent's tagged-union port (no grok
    # counterpart: grok uses enum variants directly).
    "FileEventKind",
]
