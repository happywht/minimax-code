"""Black-box tests for the migrated dagre coordinate-system layer (R247).

Exercises :mod:`minimax_code.dagre.layout.coordinate_system` purely through the
two public entry points (``adjust`` / ``undo``) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>``. Covers:

* ``adjust`` rank-direction dispatch (``tb`` / ``bt`` no-op; ``lr`` / ``rl``
  swap ``width`` / ``height``),
* ``undo`` rank-direction dispatch (``bt`` / ``rl`` reverse y; ``lr`` / ``rl``
  swap x/y + swap width/height),
* the three private transforms -- width/height swap, y-axis reversal, x/y swap
  -- on both nodes and edges (including the edge ``points`` polyline),
* in-place mutation (the pipeline mutates the live graph, not a copy),
* the rankdir invariant assertion (grok ``unwrap`` -> Python ``AssertionError``
  on a missing graph label or ``None`` rankdir),
* the ``None`` points fallback (grok ``unwrap_or(vec![])`` -> empty list),
* the barrel surface contract (``adjust`` / ``undo`` stay out of
  ``dagre.__all__``, reachable only via ``dagre.layout.coordinate_system``).
"""

from __future__ import annotations

import pytest

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.dagre.layout.coordinate_system import adjust, undo
from minimax_code.data_structures import Graph, GraphOption


def _make_graph(rankdir: str) -> Graph:
    """Build a 2-node 1-edge layout graph with concrete geometry for transform tests.

    Node ``a`` sits at ``(10, 20)`` sized ``100 x 50``; node ``b`` at
    ``(30, 40)`` sized ``80 x 60``; the single edge ``a->b`` carries a 2-point
    polyline ``[(10,20), (30,40)]`` plus its own label position ``(15, 25)``
    and footprint ``5 x 3``. Every transform asserts against these exact
    coordinates so a swapped axis or sign flip is unambiguous.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(rankdir=rankdir))
    g.set_node("a", GraphNode(x=10.0, y=20.0, width=100.0, height=50.0))
    g.set_node("b", GraphNode(x=30.0, y=40.0, width=80.0, height=60.0))
    g.set_edge(
        "a",
        "b",
        GraphEdge(
            x=15.0,
            y=25.0,
            width=5.0,
            height=3.0,
            points=[GraphEdgePoint(10.0, 20.0), GraphEdgePoint(30.0, 40.0)],
        ),
        None,
    )
    return g


def _edge(g: Graph) -> GraphEdge:
    """Return the single ``a->b`` edge label (asserts it exists)."""
    label = g.edge("a", "b", None)
    assert label is not None
    return label


# === adjust: rank-direction dispatch ========================================


def test_adjust_tb_is_noop() -> None:
    """``adjust`` on the default ``"tb"`` direction leaves the graph untouched."""
    g = _make_graph("tb")
    adjust(g)
    assert g.node("a").x == 10.0 and g.node("a").width == 100.0
    assert g.node("b").y == 40.0 and g.node("b").height == 60.0
    assert _edge(g).width == 5.0 and _edge(g).height == 3.0


def test_adjust_bt_is_noop() -> None:
    """``adjust`` on ``"bt"`` is also a no-op (only ``lr`` / ``rl`` swap wh)."""
    g = _make_graph("bt")
    adjust(g)
    assert g.node("a").width == 100.0 and g.node("a").height == 50.0
    assert _edge(g).width == 5.0


@pytest.mark.parametrize("rankdir", ["lr", "rl"])
def test_adjust_horizontal_swaps_width_height(rankdir: str) -> None:
    """``adjust`` on ``lr`` / ``rl`` swaps node + edge ``width`` / ``height``.

    The positioner lays out top-to-bottom; horizontal directions swap the
    footprint so the placed width becomes the horizontal extent.
    """
    g = _make_graph(rankdir)
    adjust(g)
    # Node footprints swapped; x/y untouched.
    assert g.node("a").width == 50.0 and g.node("a").height == 100.0
    assert g.node("b").width == 60.0 and g.node("b").height == 80.0
    assert g.node("a").x == 10.0 and g.node("a").y == 20.0
    # Edge footprint swapped; x/y/points untouched.
    edge = _edge(g)
    assert edge.width == 3.0 and edge.height == 5.0
    assert edge.x == 15.0 and edge.y == 25.0
    assert edge.points[0].x == 10.0 and edge.points[1].y == 40.0


# === undo: rank-direction dispatch ==========================================


def test_undo_tb_is_noop() -> None:
    """``undo`` on ``"tb"`` is a no-op (the laid-out orientation matches)."""
    g = _make_graph("tb")
    undo(g)
    assert g.node("a").x == 10.0 and g.node("a").y == 20.0
    assert g.node("a").width == 100.0 and g.node("a").height == 50.0
    assert _edge(g).points[0].y == 20.0


def test_undo_bt_reverses_y_only() -> None:
    """``undo`` on ``"bt"`` flips the y-axis and nothing else.

    ``bt`` is bottom-to-top: the laid-out graph is flipped vertically so rank 0
    lands at the bottom. ``width`` / ``height`` and ``x`` are preserved.
    """
    g = _make_graph("bt")
    undo(g)
    # Nodes: y negated, x/width/height untouched.
    assert g.node("a").y == -20.0 and g.node("a").x == 10.0
    assert g.node("a").width == 100.0 and g.node("a").height == 50.0
    assert g.node("b").y == -40.0
    # Edge: points y negated, edge.y negated, x/width/height untouched.
    edge = _edge(g)
    assert edge.points[0].y == -20.0 and edge.points[1].y == -40.0
    assert edge.points[0].x == 10.0 and edge.points[1].x == 30.0
    assert edge.y == -25.0 and edge.x == 15.0
    assert edge.width == 5.0 and edge.height == 3.0


def test_undo_lr_swaps_xy_and_width_height() -> None:
    """``undo`` on ``"lr"`` swaps x/y axes and swaps width/height (no y reversal).

    ``lr`` is left-to-right: the laid-out graph is transposed so the horizontal
    rank axis becomes x. ``"lr"`` is NOT y-reversed (only ``"bt"`` / ``"rl"`` are).
    """
    g = _make_graph("lr")
    undo(g)
    # Nodes: x/y swapped, width/height swapped.
    assert g.node("a").x == 20.0 and g.node("a").y == 10.0
    assert g.node("a").width == 50.0 and g.node("a").height == 100.0
    assert g.node("b").x == 40.0 and g.node("b").y == 30.0
    # Edge: points x/y swapped, edge x/y swapped, width/height swapped.
    edge = _edge(g)
    assert edge.points[0].x == 20.0 and edge.points[0].y == 10.0
    assert edge.points[1].x == 40.0 and edge.points[1].y == 30.0
    assert edge.x == 25.0 and edge.y == 15.0
    assert edge.width == 3.0 and edge.height == 5.0


def test_undo_rl_reverses_y_swaps_xy_and_width_height() -> None:
    """``undo`` on ``"rl"`` applies all three transforms (reverse_y + swap_xy + swap_wh).

    ``"rl"`` is right-to-left: it is BOTH y-reversed (like ``"bt"``) AND
    transposed (like ``"lr"``). This is the only direction hitting both branches
    of grok's ``undo``.
    """
    g = _make_graph("rl")
    undo(g)
    # Apply reverse_y then swap_xy + swap_wh to the seed coords:
    # node a: (x=10,y=20) -> reverse_y -> (10,-20) -> swap_xy -> (-20,10).
    assert g.node("a").x == -20.0 and g.node("a").y == 10.0
    assert g.node("a").width == 50.0 and g.node("a").height == 100.0
    # Edge point (10,20) -> reverse_y -> (10,-20) -> swap_xy -> (-20,10).
    edge = _edge(g)
    assert edge.points[0].x == -20.0 and edge.points[0].y == 10.0
    # edge.y=25 -> -25 -> swap -> edge.x=-25; edge.x=15 (no reverse on x) -> swap -> edge.y=15.
    assert edge.x == -25.0 and edge.y == 15.0
    assert edge.width == 3.0 and edge.height == 5.0


# === private transform coverage (via the public dispatchers) =================


def test_swap_width_height_preserves_xy_and_points() -> None:
    """``_swap_width_height`` (via ``adjust`` on ``lr``) touches ONLY width/height.

    Geometry (``x`` / ``y``) and the edge polyline are invariant under the
    footprint swap -- guards against an accidental axis swap leaking in.
    """
    g = _make_graph("lr")
    adjust(g)
    assert g.node("a").x == 10.0 and g.node("a").y == 20.0
    edge = _edge(g)
    assert edge.points[0].x == 10.0 and edge.points[0].y == 20.0
    assert edge.x == 15.0 and edge.y == 25.0


def test_reverse_y_negates_node_edge_and_polyline() -> None:
    """``_reverse_y`` (via ``undo`` on ``bt``) negates every y on the graph."""
    g = _make_graph("bt")
    undo(g)
    assert g.node("a").y == -20.0 and g.node("b").y == -40.0
    edge = _edge(g)
    assert edge.y == -25.0
    assert [p.y for p in edge.points] == [-20.0, -40.0]
    # x is untouched.
    assert [p.x for p in edge.points] == [10.0, 30.0]


def test_swap_xy_transposes_node_edge_and_polyline() -> None:
    """``_swap_x_y`` (via ``undo`` on ``lr``, isolating the xy branch) transposes."""
    g = _make_graph("lr")
    undo(g)
    # node a (10,20) -> (20,10)
    assert g.node("a").x == 20.0 and g.node("a").y == 10.0
    edge = _edge(g)
    # points (10,20)->(20,10) and (30,40)->(40,30)
    assert (edge.points[0].x, edge.points[0].y) == (20.0, 10.0)
    assert (edge.points[1].x, edge.points[1].y) == (40.0, 30.0)
    # edge (15,25) -> (25,15)
    assert edge.x == 25.0 and edge.y == 15.0


# === in-place mutation ======================================================


def test_adjust_mutates_graph_in_place() -> None:
    """``adjust`` mutates the live graph object (no copy/return value)."""
    g = _make_graph("lr")
    node_a = g.node("a")
    adjust(g)
    # The same node object now carries the swapped footprint.
    assert node_a is not None
    assert node_a.width == 50.0 and node_a.height == 100.0


def test_undo_mutates_graph_in_place() -> None:
    """``undo`` mutates the live graph object (no copy/return value)."""
    g = _make_graph("bt")
    edge = g.edge("a", "b", None)
    undo(g)
    assert edge is not None
    assert edge.y == -25.0


# === rankdir invariant (grok unwrap -> Python AssertionError) ===============


def test_rankdir_none_raises_assertion_error() -> None:
    """A ``None`` rankdir violates the layout invariant (grok ``unwrap`` panic)."""
    g: Graph = Graph(
        GraphOption(),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(rankdir=None))
    with pytest.raises(AssertionError):
        adjust(g)
    with pytest.raises(AssertionError):
        undo(g)


def test_no_graph_label_raises_assertion_error() -> None:
    """A missing graph label (no ``set_graph``) also violates the invariant."""
    g: Graph = Graph(
        GraphOption(),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode())
    with pytest.raises(AssertionError):
        adjust(g)
    with pytest.raises(AssertionError):
        undo(g)


# === None points fallback (grok unwrap_or(vec![])) ==========================


def test_reverse_y_turns_none_points_into_empty_list() -> None:
    """An edge with ``points=None`` gets ``[]`` after ``reverse_y`` (grok ``Some(vec![])``)."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(rankdir="bt"))
    g.set_node("a", GraphNode(y=10.0))
    g.set_node("b", GraphNode(y=20.0))
    g.set_edge("a", "b", GraphEdge(y=5.0, points=None), None)
    undo(g)
    edge = g.edge("a", "b", None)
    assert edge is not None
    assert edge.points == []  # None -> [] (grok unwrap_or(vec![]) post-condition)
    assert edge.y == -5.0


def test_swap_xy_turns_none_points_into_empty_list() -> None:
    """An edge with ``points=None`` gets ``[]`` after ``swap_x_y`` too."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(rankdir="lr"))
    g.set_node("a", GraphNode(x=1.0, y=2.0))
    g.set_node("b", GraphNode(x=3.0, y=4.0))
    g.set_edge("a", "b", GraphEdge(x=5.0, y=6.0, points=None), None)
    undo(g)
    edge = g.edge("a", "b", None)
    assert edge is not None
    assert edge.points == []
    assert edge.x == 6.0 and edge.y == 5.0  # swapped


# === barrel surface contract ================================================


def test_adjust_undo_not_in_dagre_all() -> None:
    """``adjust`` / ``undo`` are NOT in the crate-root barrel (grok keeps them
    module-private to ``layout::coordinate_system``; only the four type structs
    earn a crate-root ``pub use``)."""
    assert "adjust" not in dagre.__all__
    assert "undo" not in dagre.__all__


def test_adjust_undo_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level -- must
    import from ``dagre.layout.coordinate_system``."""
    assert not hasattr(dagre, "adjust")
    assert not hasattr(dagre, "undo")


def test_coordinate_system_submodule_all_exports() -> None:
    """The submodule exposes exactly ``adjust`` / ``undo`` (the two ``pub fn``)."""
    import minimax_code.dagre.layout.coordinate_system as cs

    assert cs.__all__ == ["adjust", "undo"]
    assert cs.adjust is adjust
    assert cs.undo is undo


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R247 adds no crate-root barrel symbols (``coordinate_system`` stays
    module-private); the barrel count set by R246 is unchanged at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_coordinate_system_import() -> None:
    """Importing ``coordinate_system`` binds the ``layout`` subpackage on ``dagre``,
    so ``dagre.layout.coordinate_system`` is reachable after the import."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.coordinate_system as cs

    assert dagre.layout is layout_pkg
    assert layout_pkg.coordinate_system is cs


# === integration: the four R246 types flow through coordinate_system =========


def test_coordinate_system_consumes_full_dagre_type_specialisation() -> None:
    """End-to-end type-level check: ``adjust`` / ``undo`` operate on the concrete
    ``Graph<GraphConfig, GraphNode, GraphEdge>`` specialisation -- reading the
    graph label's ``rankdir``, mutating ``GraphNode`` geometry and ``GraphEdge``
    points, all through the graphlib surface. A horizontal ``adjust`` followed
    by a vertical ``undo`` exercises both ``width`` / ``height`` swap branches
    and confirms the types compose without error."""
    g = _make_graph("lr")
    adjust(g)
    # After adjust(lr): width/height swapped on every node + edge.
    assert g.node("a").width == 50.0
    assert _edge(g).width == 3.0
    # Re-orient to tb and undo to confirm the y-reversal branch independently.
    g.graph().rankdir = "bt"
    undo(g)
    assert g.node("a").y == -20.0
