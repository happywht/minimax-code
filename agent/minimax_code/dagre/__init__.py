"""dagre layout-stack barrel (R246+, vendored ``third_party/dagre_rust``).

Re-exports the pure-logic leaves migrated from grok's vendored
``third_party/dagre_rust`` crate (upstream ``warpdotdev/mermaid-to-svg``,
Apache-2.0) -- the layered graph layout engine that drives the mermaid render
pipeline. This package is the natural downstream of the now-complete graphlib
stack (R241-R245): ``dagre_rust`` depends on ``graphlib_rust`` (complete
``Graph<GL,N,E>`` + traversal) and ``ordered_hashmap`` (insertion-ordered
map), both migrated, so the dagre layout algorithms can migrate leaf-by-leaf
on top of a closed graphlib surface.

Mirroring grok, ``dagre`` is an **independent top-level package** (grok's
``dagre_rust`` is a standalone crate sibling to ``graphlib_rust`` /
``ordered_hashmap``), not nested under :mod:`minimax_code.data_structures`.
The package barrel exposes only the crate-root ``pub`` symbols -- the four
type-foundation structs (:class:`GraphNode` / :class:`GraphEdgePoint` /
:class:`GraphEdge` / :class:`GraphConfig`). ``BorderTypeName`` is defined in
the type-foundation module but NOT re-exported at the crate root (grok keeps
it module-private to ``layout::add_border_segments``; migrated early into
:mod:`minimax_code.dagre.lib` to break the lib/layout module cycle -- see that
module's docstring), so it stays out of ``__all__``.

First leaf (R246): ``lib`` -- the type foundation. The four crate-root value
objects + the ``BorderTypeName`` enum migrated early. Every ``layout/*``
module (``coordinate_system`` / ``order`` / ``rank`` / ``position`` /
``add_border_segments`` / ...) depends on these types, so this leaf is the
prerequisite for the whole layout stack and unblocks the downstream leaves.
"""

from __future__ import annotations

from minimax_code.dagre.lib import (
    GraphConfig,
    GraphEdge,
    GraphEdgePoint,
    GraphNode,
)

__all__ = [
    "GraphConfig",
    "GraphEdge",
    "GraphEdgePoint",
    "GraphNode",
]
