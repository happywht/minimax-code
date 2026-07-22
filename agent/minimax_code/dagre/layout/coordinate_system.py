"""dagre coordinate-system transforms (R247, vendored ``third_party/dagre_rust``).

Mirrors ``layout/coordinate_system.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the rank-direction
coordinate transforms applied around the position phase. :func:`adjust` runs
BEFORE ``position::position`` to swap the graph into the top-to-bottom
orientation the positioner expects; :func:`undo` runs AFTER to rotate / flip
the laid-out graph back into the user-requested ``rankdir`` (``tb`` / ``bt`` /
``lr`` / ``rl``).

This is the **second leaf of the ``dagre`` migration chain** (R246 type
foundation -> R247 ``coordinate_system``) and the **first leaf of the
``layout`` submodule**. It depends only on the R246 type layer
(:class:`~minimax_code.dagre.GraphConfig` /
:class:`~minimax_code.dagre.GraphNode` /
:class:`~minimax_code.dagre.GraphEdge`) + the now-complete graphlib
:class:`~minimax_code.data_structures.graphlib.Graph` surface (``graph`` /
``nodes`` / ``edges`` / ``node_mut`` / ``edge_mut_with_obj`` -- R241-R245). No
other ``layout/*`` module is needed, so this leaf is self-contained and
testable in isolation.

Visibility mirrors grok: ``coordinate_system`` is a public submodule
(``pub mod coordinate_system`` in ``layout/mod.rs``) and ``adjust`` / ``undo``
are ``pub fn``, but they are NOT re-exported at the crate root (``lib.rs``
does not ``pub use layout::*``). Mirroring R245's ``algo`` decision, the two
symbols stay out of the ``dagre`` barrel ``__all__`` and are reachable only as
``minimax_code.dagre.layout.coordinate_system.adjust`` / ``.undo``.

Rankdir invariant
-----------------
grok reads ``g.graph().rankdir.clone().unwrap()`` -- ``build_layout_graph``
calls ``set_graph_label_default_values`` which guarantees ``rankdir = Some("tb")``
by default, so the ``unwrap()`` never panics inside the layout pipeline. The
migration asserts the same invariant (a ``None`` graph label or ``None``
rankdir raises ``AssertionError``, the Python analogue of grok's panic). Every
layout caller -- and every test here -- seeds a ``GraphConfig`` with a concrete
``rankdir`` before calling :func:`adjust` / :func:`undo`.

Borrow-checker artefacts
------------------------
grok clones the edge ``points`` vec before mutating each point
(``edge_label.points.clone().unwrap_or(vec![])``) only to appease the borrow
checker -- ``edge_mut_with_obj`` already returns the live label. Python has no
such constraint, so the transforms mutate the point objects in place; the
``unwrap_or(vec![])`` fallback is preserved verbatim (a ``None`` points field
becomes an empty list, matching grok's ``Some(vec![])`` post-condition).
"""

from __future__ import annotations

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.data_structures.graphlib import Graph

__all__ = ["adjust", "undo"]


def adjust(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Pre-position rank-direction swap (mirrors grok ``coordinate_system::adjust``).

    For horizontal layouts (``rankdir`` ``"lr"`` or ``"rl"``) swaps every node's
    and edge's ``width`` / ``height`` so the positioner (which always lays out
    top-to-bottom) places nodes with the correct horizontal footprint.
    Vertical directions (``"tb"`` / ``"bt"``) are a no-op. Run before
    ``position::position``.
    """
    rank_dir = _rankdir(g)
    if rank_dir in ("lr", "rl"):
        _swap_width_height(g)


def undo(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Post-position rank-direction restore (mirrors grok ``coordinate_system::undo``).

    Reverses :func:`adjust` and rotates the laid-out graph back into the
    user-requested ``rankdir``:

    * ``"bt"`` / ``"rl"`` -> flip the y-axis (:func:`_reverse_y`).
    * ``"lr"`` / ``"rl"`` -> swap the x/y axes (:func:`_swap_x_y`) and swap the
      ``width`` / ``height`` back (:func:`_swap_width_height`).

    Run after ``position::position`` (and after ``fixup_edge_label_coords``).
    """
    rank_dir = _rankdir(g)
    if rank_dir in ("bt", "rl"):
        _reverse_y(g)

    if rank_dir in ("lr", "rl"):
        _swap_x_y(g)
        _swap_width_height(g)


def _rankdir(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> str:
    """Return the graph's ``rankdir`` (mirrors grok ``g.graph().rankdir.unwrap()``).

    Asserts the layout-pipeline invariant that a :class:`GraphConfig` label is
    set and carries a concrete ``rankdir`` (grok's ``unwrap``). Every layout
    caller seeds both via ``build_layout_graph`` +
    ``set_graph_label_default_values``.
    """
    config = g.graph()
    assert config is not None  # grok: g.graph() returns &GraphConfig (invariant)
    rank_dir = config.rankdir
    assert rank_dir is not None  # grok: .rankdir.unwrap() (set_graph_label_default_values)
    return rank_dir


def _swap_width_height(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Swap ``width`` / ``height`` on every node and edge (mirrors grok ``swap_width_height``)."""
    for v in g.nodes():
        node = g.node_mut(v)
        assert node is not None  # grok: node_mut(v).unwrap()
        node.width, node.height = node.height, node.width

    for e in g.edges():
        edge_label = g.edge_mut_with_obj(e)
        assert edge_label is not None  # grok: edge_mut_with_obj(&e).unwrap()
        edge_label.width, edge_label.height = edge_label.height, edge_label.width


def _reverse_y(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Negate the y-axis on every node and edge (mirrors grok ``reverse_y``).

    For each edge the ``points`` polyline is negated point-wise and the edge
    label's own ``y`` is negated. A ``None`` points field becomes an empty
    list (grok's ``unwrap_or(vec![])`` -> ``Some(vec![])`` post-condition).
    """
    for v in g.nodes():
        node = g.node_mut(v)
        assert node is not None
        node.y = -node.y

    for e in g.edges():
        edge_label = g.edge_mut_with_obj(e)
        assert edge_label is not None
        points = edge_label.points if edge_label.points is not None else []
        for point in points:
            point.y = -point.y
        edge_label.points = points
        edge_label.y = -edge_label.y


def _swap_x_y(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Swap the x/y axes on every node and edge (mirrors grok ``swap_x_y``).

    For each edge the ``points`` polyline is swapped point-wise and the edge
    label's own ``x`` / ``y`` are swapped. A ``None`` points field becomes an
    empty list (grok's ``unwrap_or(vec![])`` -> ``Some(vec![])`` post-condition).
    """
    for v in g.nodes():
        node = g.node_mut(v)
        assert node is not None
        node.x, node.y = node.y, node.x

    for e in g.edges():
        edge_label = g.edge_mut_with_obj(e)
        assert edge_label is not None
        points = edge_label.points if edge_label.points is not None else []
        for point in points:
            point.x, point.y = point.y, point.x
        edge_label.points = points

        edge_label.x, edge_label.y = edge_label.y, edge_label.x
