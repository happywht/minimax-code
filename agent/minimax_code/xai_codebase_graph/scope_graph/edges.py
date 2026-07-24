"""Edge types for the ScopeGraph.

Mirrors grok ``xai-codebase-graph/src/scope_graph/edges.rs`` (direction (2),
brick 2). ``EdgeKind`` labels the relation between two nodes in a per-file
scope graph (nested-scope links, definition / import / reference resolution).

YAGNI boundary (R301): grok derives ``Serialize`` / ``Deserialize`` on
``EdgeKind`` for cache persistence. The Python port keeps this module
stdlib-only (no serde); cache (de)serialization lands with the ``manager/``
migration, which is where it is consumed. The member *names* are preserved
verbatim from grok so a future serde layer can use them as externally-tagged
keys without renames.
"""

from __future__ import annotations

from enum import Enum


class EdgeKind(Enum):
    """Describes the relation between two nodes in the ScopeGraph.

    Mirrors grok ``EdgeKind`` (edges.rs L5-L22). grok derives
    ``Copy + PartialEq + Eq + Hash``; the Python port uses :class:`enum.Enum`
    -- member identity gives ``Eq`` / ``Hash`` for free, and ``Copy`` is
    meaningless in Python (bindings are by reference). Each member's value is
    its grok variant name, kept verbatim for serde compatibility.
    """

    SCOPE_TO_SCOPE = "ScopeToScope"
    """Edge from a nested scope to its parent scope."""

    DEF_TO_SCOPE = "DefToScope"
    """Edge from a definition to its definition scope."""

    IMPORT_TO_SCOPE = "ImportToScope"
    """Edge from an import to its definition scope."""

    REF_TO_DEF = "RefToDef"
    """Edge from a reference to its definition."""

    REF_TO_IMPORT = "RefToImport"
    """Edge from a reference to its import."""
