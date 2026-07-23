"""Black-box tests for the migrated dagre position/mod orchestrator (R267).

Exercises :mod:`minimax_code.dagre.layout.position.mod` -- the
``position()`` orchestrator that closes the ``position`` sub-package at
2/2 and the private ``position_y()`` rank-sep stacking helper -- through
its public entry point :func:`position` plus the white-box
:func:`position_y`. The fixtures dress a bare
``Graph<GraphConfig, GraphNode, GraphEdge>`` directed non-multigraph
non-compound layer graph (the post-``order`` state every node carries a
``rank`` + ``order`` on; ``compound=False`` so
:func:`util.as_non_compound_graph` includes every node). Covers:

* :func:`position_y` white-box: a single node of ``height=10`` sits at
  ``y = 0 + 10/2 = 5.0``; two single-node layers under ``ranksep=50``
  stack 60 apart (``5.0`` / ``65.0``); grok's ``height as i32``
  truncation drops the fractional part (``height=10.7`` -> ``5.0`` not
  ``5.35``); the tallest node in a layer wins the centring
  (``[10, 24]`` -> both at ``12.0``); a rank gap (rank 0 -> rank 2,
  rank 1 empty) still advances ``prev_y`` by ``max_height + ranksep``
  (empty layer ``max_height=0`` -> ``c.y = 115.0``),
* :func:`position` end-to-end: a single ``a -> b -> c`` chain stamps
  both ``x`` and ``y`` back onto the original graph (``y`` stacked
  ``5.0`` / ``65.0`` / ``125.0``, ``x`` equal -- the chain has no
  horizontal spread); a two-layer bipartite ``a -> c`` / ``b -> d``
  separates horizontally (``a`` / ``c`` aligned, ``b`` / ``d`` aligned,
  at least ``nodesep`` apart) and stacks vertically (``a`` / ``b`` at
  ``5.0``, ``c`` / ``d`` at ``65.0``),
* the barrel surface contract: :func:`position` stays out of
  ``dagre.__all__`` and off the crate-root top level (the R246 barrel
  count stays at 4); ``mod.__all__`` is the single-symbol
  ``["position"]``; the ``position`` sub-package barrel re-exports both
  leaves (``["bk", "mod"]``); ``mod`` is reachable via
  ``dagre.layout.position``.
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.position.mod import position, position_y
from minimax_code.data_structures import Graph, GraphOption


def _make_position_graph(nodesep: float = 50.0, edgesep: float = 20.0) -> Graph:
    """Build an empty directed non-multigraph non-compound layer graph.

    ``multigraph=False`` + ``compound=False`` mirrors the post-``order``
    state ``position::mod`` runs on: every node already carries a
    ``rank`` + ``order``. ``compound=False`` means
    :func:`util.as_non_compound_graph` treats every node as a leaf (no
    children) and includes it in the projection. ``GraphConfig.ranksep``
    defaults to ``50.0`` (R246), so the stacking math is deterministic.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(nodesep=nodesep, edgesep=edgesep))
    return g


def _node(
    rank: int | None = None,
    order: int | None = None,
    width: float = 0.0,
    height: float = 0.0,
) -> GraphNode:
    """Stamp a layer-graph node with the coordinate fields ``position`` reads."""
    return GraphNode(rank=rank, order=order, width=width, height=height)


def _edge(g: Graph, v: str, w: str) -> None:
    """Add ``v -> w`` with the default edge label."""
    g.set_edge(v, w, GraphEdge(), None)


# === position_y: white-box =============================================


def test_position_y_single_layer_centers_node_at_half_height() -> None:
    """A single node of ``height=10`` sits at ``y = 0 + 10/2 = 5.0``."""
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, height=10.0))
    position_y(g)
    assert g.node("a").y == 5.0


def test_position_y_two_layers_stack_separated_by_ranksep() -> None:
    """Two single-node layers of ``height=10`` under ``ranksep=50`` stack 60 apart.

    layer 0: ``y = 0 + 5 = 5.0``, ``prev_y -> 0 + 10 + 50 = 60``;
    layer 1: ``y = 60 + 5 = 65.0``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, height=10.0))
    g.set_node("b", _node(rank=1, order=0, height=10.0))
    position_y(g)
    assert g.node("a").y == 5.0
    assert g.node("b").y == 65.0


def test_position_y_truncates_fractional_height_to_int() -> None:
    """grok ``height as i32`` truncates: ``height=10.7`` -> ``10``, ``y = 5.0``.

    A faithful fractional ``height`` (no truncation) would centre at
    ``5.35``; the ``int(height)`` carry-over of grok's pixel-rounding
    yields ``5.0``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, height=10.7))
    position_y(g)
    assert g.node("a").y == 5.0


def test_position_y_takes_max_height_within_layer() -> None:
    """The tallest node in a layer wins the centring: ``[10, 24]`` -> ``24``.

    Both nodes centred at ``y = 0 + 24/2 = 12.0``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, height=10.0))
    g.set_node("b", _node(rank=0, order=1, height=24.0))
    position_y(g)
    assert g.node("a").y == 12.0
    assert g.node("b").y == 12.0


def test_position_y_empty_middle_layer_advances_prev_y() -> None:
    """A rank gap (rank 0 -> rank 2, rank 1 empty) still advances ``prev_y``.

    layer 0 ``[a]`` h=10: ``a.y = 5.0``, ``prev_y -> 60``;
    layer 1 ``[]``: ``max_height=0``, no nodes, ``prev_y -> 60 + 0 + 50 = 110``;
    layer 2 ``[c]`` h=10: ``c.y = 110 + 5 = 115.0``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, height=10.0))
    g.set_node("c", _node(rank=2, order=0, height=10.0))
    position_y(g)
    assert g.node("a").y == 5.0
    assert g.node("c").y == 115.0


# === position: end-to-end ==============================================


def test_position_stamps_x_and_y_on_single_chain() -> None:
    """``position()`` writes both ``x`` and ``y`` back onto the original graph.

    ``a -> b -> c``, each ``height=10`` under ``ranksep=50``: ``y``
    stacked ``5.0`` / ``65.0`` / ``125.0``; ``x`` equal (a single chain
    has no horizontal spread, R266 ``position_x`` collapses it).
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, width=10.0, height=10.0))
    g.set_node("b", _node(rank=1, order=0, width=10.0, height=10.0))
    g.set_node("c", _node(rank=2, order=0, width=10.0, height=10.0))
    _edge(g, "a", "b")
    _edge(g, "b", "c")
    position(g)
    assert g.node("a").y == 5.0
    assert g.node("b").y == 65.0
    assert g.node("c").y == 125.0
    assert g.node("a").x == g.node("b").x == g.node("c").x


def test_position_two_layer_bipartite_spreads_x_and_stacks_y() -> None:
    """A bipartite ``a -> c`` / ``b -> d`` separates horizontally and stacks vertically.

    rank 0: ``a``, ``b`` (order 0, 1); rank 1: ``c``, ``d`` (order 0, 1).
    ``y``: ``a`` / ``b`` at ``5.0`` (layer 0), ``c`` / ``d`` at ``65.0``
    (layer 1); ``x``: ``a`` aligns with ``c``, ``b`` with ``d``, the two
    blocks at least ``nodesep`` apart.
    """
    g = _make_position_graph(nodesep=50.0)
    g.set_node("a", _node(rank=0, order=0, width=10.0, height=10.0))
    g.set_node("b", _node(rank=0, order=1, width=10.0, height=10.0))
    g.set_node("c", _node(rank=1, order=0, width=10.0, height=10.0))
    g.set_node("d", _node(rank=1, order=1, width=10.0, height=10.0))
    _edge(g, "a", "c")
    _edge(g, "b", "d")
    position(g)
    assert g.node("a").y == 5.0
    assert g.node("b").y == 5.0
    assert g.node("c").y == 65.0
    assert g.node("d").y == 65.0
    assert g.node("a").x == g.node("c").x
    assert g.node("b").x == g.node("d").x
    assert g.node("b").x - g.node("a").x >= 50.0


# === barrel surface contract ===========================================


def test_position_not_in_dagre_all() -> None:
    """``position`` earns no crate-root barrel slot."""
    assert "position" not in dagre.__all__


def test_position_not_reachable_at_dagre_top_level() -> None:
    """The fn is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "position")


def test_mod_submodule_exports_position_only() -> None:
    """The submodule exposes just the public ``position`` (ASCII ``__all__``)."""
    import minimax_code.dagre.layout.position.mod as mod_mod

    assert mod_mod.__all__ == ["position"]
    assert mod_mod.position is position


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R267 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_position_subpackage_barrel_reexports_bk_and_mod() -> None:
    """The ``position`` sub-package barrel re-exports both leaves (``bk`` + ``mod``)."""
    import minimax_code.dagre.layout.position as position_pkg
    import minimax_code.dagre.layout.position.mod as mod_mod

    assert position_pkg.__all__ == ["bk", "mod"]
    assert position_pkg.mod is mod_mod


def test_mod_reachable_via_layout_position() -> None:
    """Importing ``position`` binds it (and ``mod``) on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.position as position_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.position is position_pkg
    assert position_pkg.mod.position is position
