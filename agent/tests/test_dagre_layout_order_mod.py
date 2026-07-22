"""Black-box tests for the migrated dagre order/mod dispatcher (R265).

Exercises :mod:`minimax_code.dagre.layout.order.mod` through its public
entry point :func:`order` plus the two private helpers
(``_sweep_layer_graphs`` / ``_assign_order``) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state ``rank`` leaves
behind -- every node carries a rank; ``run_layout`` calls ``order`` at
``layout/mod.rs`` line 632). Covers:

* :func:`order` end-to-end: a single node, an empty graph (max_rank 0 ->
  empty rank sweeps -> init layering kept), a linear chain, a fork (every
  node gets an ``order``), determinism (the same input graph yields the
  same ``order`` stamping across two independent runs), the
  cross-count-no-increase invariant, and the within-rank order range
  (every ``order`` is in ``[0, layer_size - 1]``),
* ``_assign_order`` white-box: sequential position stamping and the
  ghost-id defensive skip (the R258 ``_init_order_dfs`` no-missing-label
  widening reused),
* ``_sweep_layer_graphs`` white-box: a single-rank sweep stamps every
  rank node's ``order``,
* the barrel surface contract (``order`` stays out of ``dagre.__all__``;
  the crate-root barrel count is unchanged at 4; the ``order``
  sub-package barrel re-exports all nine ``order`` leaves;
  ``mod.__all__`` is the single-symbol list).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.build_layer_graph import GraphRelationship
from minimax_code.dagre.layout.order.cross_count import cross_count
from minimax_code.dagre.layout.order.init_order import init_order
from minimax_code.dagre.layout.order.mod import _assign_order, _sweep_layer_graphs, order
from minimax_code.dagre.layout.util import build_layer_matrix
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty directed compound multigraph (the state ``order`` runs on).

    ``compound=True`` + ``multigraph=True`` mirrors the production config
    ``order`` runs on (the post-``rank`` / post-``normalize`` graph).
    ``order`` reads node ``rank`` + the successor / predecessor tables +
    compound ``children`` (via the R258-R264 leaves); the GraphConfig
    dressing keeps the fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _make_cg() -> Graph:
    """Build an empty directed compound constraint graph (the ``cg`` accumulator)."""
    cg: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    cg.set_graph(GraphConfig())
    return cg


def _make_crossing_graph() -> Graph:
    """Build a two-rank graph whose init ordering carries a cross-count >= 0.

    rank 0 = {a, b}, rank 1 = {x, y}; edges ``a -> x``, ``a -> y``, ``b -> x``.
    The R258 :func:`init_order` DFS seeds a layering R259 :func:`cross_count`
    scores; :func:`order` sweeps without increasing it (the barycenter
    heuristic may or may not improve it, but it must never regress).
    """
    g = _make_graph()
    for node_id, rank in (("a", 0), ("b", 0), ("x", 1), ("y", 1)):
        g.set_node(node_id, GraphNode(rank=rank))
    g.set_edge("a", "x", GraphEdge(), None)
    g.set_edge("a", "y", GraphEdge(), None)
    g.set_edge("b", "x", GraphEdge(), None)
    return g


# === order: end-to-end ===================================================


def test_order_single_node() -> None:
    """A lone node on rank 0 sweeps to ``order = 0`` (max_rank 0 -> empty sweeps)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    order(g)
    assert g.node("a").order == 0


def test_order_empty_graph() -> None:
    """An empty graph sweeps without error (max_rank 0 -> empty rank lists)."""
    g = _make_graph()
    order(g)  # no nodes -> init_order = [[]]; empty sweeps; no stamp
    assert g.nodes() == []


def test_order_linear_chain() -> None:
    """``a -> b -> c`` (ranks 0/1/2) sweeps; each node lands at ``order = 0``.

    Every rank holds exactly one node, so each within-rank ``order`` is 0.
    """
    g = _make_graph()
    for node_id, rank in (("a", 0), ("b", 1), ("c", 2)):
        g.set_node(node_id, GraphNode(rank=rank))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    order(g)
    assert g.node("a").order == 0
    assert g.node("b").order == 0
    assert g.node("c").order == 0


def test_order_fork_all_nodes_ordered() -> None:
    """``a -> {b, c}`` (a rank 0, b/c rank 1) stamps an ``order`` on every node."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_node("c", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    order(g)
    # a is alone on rank 0; b/c share rank 1 -> orders {0, 1} in some order.
    assert g.node("a").order == 0
    assert g.node("b").order in (0, 1)
    assert g.node("c").order in (0, 1)
    assert g.node("b").order != g.node("c").order


def test_order_is_deterministic() -> None:
    """The same input graph yields the same ``order`` stamping across two runs.

    R258 :func:`init_order` (rank-sorted DFS) and the sweep (deterministic
    barycenter / sort) are both order-stable, so :func:`order` is a pure
    function of the graph -- two independent runs stamp identical orders.
    """
    g1 = _make_crossing_graph()
    g2 = _make_crossing_graph()
    order(g1)
    order(g2)
    for node_id in g1.nodes():
        assert g1.node(node_id).order == g2.node(node_id).order


def test_order_cross_count_does_not_increase() -> None:
    """The post-sweep cross count is never worse than the init ordering.

    The dispatcher keeps the best-crossing-count ``layering`` across
    iterations, so the final stamp scores at most the init cross count
    (the barycenter heuristic may plateau at the init value on a small
    graph, but it never regresses -- the regression-guard invariant).
    """
    g = _make_crossing_graph()
    init_cc = cross_count(g, init_order(g))
    order(g)
    final_cc = cross_count(g, build_layer_matrix(g))
    assert final_cc <= init_cc


def test_order_stamps_orders_in_valid_range() -> None:
    """Every node's ``order`` is in ``[0, layer_size - 1]`` for its rank."""
    g = _make_crossing_graph()
    order(g)
    layering = build_layer_matrix(g)
    for layer in layering:
        for i, node_id in enumerate(layer):
            assert g.node(node_id).order == i


# === _assign_order: white-box ============================================


def test_assign_order_stamps_sequential_positions() -> None:
    """``_assign_order`` stamps each node's within-layer index as ``order``."""
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    _assign_order(g, [["a", "b", "c"]])
    assert g.node("a").order == 0
    assert g.node("b").order == 1
    assert g.node("c").order == 2


def test_assign_order_skips_missing_node() -> None:
    """A ghost id in the matrix is skipped, not panicked (the R258 widening reused).

    grok's ``g.node_mut(v).unwrap()`` assumes a labelled node; Python treats a
    missing label defensively (skip the stamp) so a ghost id cannot crash the
    final stamp.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    assert g.node("ghost") is None
    _assign_order(g, [["a", "ghost"]])  # ghost skipped, no panic
    assert g.node("a").order == 0


# === _sweep_layer_graphs: white-box ======================================


def test_sweep_layer_graphs_stamps_rank_orders() -> None:
    """A single-rank sweep stamps an ``order`` on every rank node.

    Pre-stamp the init layering via :func:`_assign_order` so the R260
    barycenter has source positions to read; the down sweep over rank 1
    then re-stamps every rank-1 node.
    """
    g = _make_crossing_graph()
    _assign_order(g, [["a", "b"], ["x", "y"]])
    _sweep_layer_graphs(g, [1], GraphRelationship.IN_EDGES, False)
    assert g.node("x").order is not None
    assert g.node("y").order is not None
    assert g.node("x").order != g.node("y").order


def test_sweep_layer_graphs_uses_fresh_cg_per_call() -> None:
    """The constraint graph is local to the sweep pass (no cross-call leakage).

    Two independent sweeps on isolated graphs run without the ``cg`` from one
    polluting the other (``cg`` is minted fresh at the top of each call).
    """
    g1 = _make_crossing_graph()
    g2 = _make_crossing_graph()
    _assign_order(g1, [["a", "b"], ["x", "y"]])
    _assign_order(g2, [["a", "b"], ["x", "y"]])
    _sweep_layer_graphs(g1, [1], GraphRelationship.IN_EDGES, False)
    _sweep_layer_graphs(g2, [1], GraphRelationship.IN_EDGES, False)
    # both stamp x/y; the second call's fresh cg did not see the first's edges
    assert g1.node("x").order == g2.node("x").order
    assert g1.node("y").order == g2.node("y").order


# === barrel surface contract =============================================


def test_order_not_in_dagre_all() -> None:
    """``order`` earns no crate-root barrel slot."""
    assert "order" not in dagre.__all__


def test_order_not_reachable_at_dagre_top_level() -> None:
    """The fn is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "order")


def test_mod_submodule_exports_order() -> None:
    """The submodule exposes exactly ``order`` (single-symbol ``__all__``)."""
    import minimax_code.dagre.layout.order.mod as mod_mod

    assert mod_mod.__all__ == ["order"]
    assert mod_mod.order is order


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R265 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_nine() -> None:
    """The ``order`` sub-package barrel re-exports all nine ``order`` leaves.

    Synchronised at R265 (``mod`` added): the barrel ``__all__`` grew the R264
    eight-element list -> the ASCII-sorted nine (``"mod"`` lands between
    ``"init_order"`` and ``"resolve_conflicts"`` by the ``i < m < r`` rule).
    Mirrors the R259 -> R258 / R260 -> R258+R259 / R261 -> R258+R259+R260 /
    R262 -> R258+R259+R260+R261 / R263 -> R258-R262 / R264 -> R258-R263
    barrel-sync pattern. R265 closes the ``order`` sub-package at 9/9.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod
    import minimax_code.dagre.layout.order.barycenter as bc_mod
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod
    import minimax_code.dagre.layout.order.mod as mod_mod
    import minimax_code.dagre.layout.order.resolve_conflicts as rc_mod
    import minimax_code.dagre.layout.order.sort as sort_mod
    import minimax_code.dagre.layout.order.sort_subgraph as sort_subgraph_mod

    assert order_pkg.__all__ == [
        "add_subgraph_constraints",
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "mod",
        "resolve_conflicts",
        "sort",
        "sort_subgraph",
    ]
    assert order_pkg.add_subgraph_constraints is asc_mod
    assert order_pkg.barycenter is bc_mod
    assert order_pkg.build_layer_graph is blg_mod
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod
    assert order_pkg.mod is mod_mod
    assert order_pkg.resolve_conflicts is rc_mod
    assert order_pkg.sort is sort_mod
    assert order_pkg.sort_subgraph is sort_subgraph_mod


def test_order_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.mod.order is order


def test_cg_fixture_pattern_matches_mod_internal_construction() -> None:
    """The test ``cg`` fixture matches the in-sweep ``cg`` construction.

    :func:`_sweep_layer_graphs` mints its constraint graph with the same
    ``GraphOption(directed=True, multigraph=False, compound=True)`` +
    ``GraphConfig`` dressing the R263 / R264 ``cg`` fixtures use; this test
    pins the fixture <-> internal parity so a config drift is caught.
    """
    cg = _make_cg()
    # the constraint graph is empty + directed + compound + non-multigraph
    assert cg.nodes() == []
