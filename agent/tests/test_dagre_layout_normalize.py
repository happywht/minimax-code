"""Black-box tests for the migrated dagre normalize layer (R250).

Exercises :mod:`minimax_code.dagre.layout.normalize` purely through the two
public entry points (``run`` / ``undo``) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` flat graph. Covers:

* long-edge splitting (rank span > 1 -> one ``_d`` ``edge`` dummy per
  intermediate rank, chained with weight-preserving segments),
* the unit-length short-circuit (rank span == 1 -> edge untouched, only
  ``points`` cleared),
* the same-rank remove-and-recreate path (rank span == 0 -> no dummy),
* ``dummy_chains`` initialization (reset to a fresh list) + first-dummy-only
  recording (one entry per long edge, regardless of chain length),
* dummy node attributes (``dummy="edge"`` + ``rank`` + ``edge_label`` +
  ``edge_obj`` + independent ``edge_label`` deepcopy),
* the ``edge-label`` dummy at ``label_rank`` (``width`` / ``height`` /
  ``labelpos`` carried from the original edge label),
* weight propagation across every segment,
* ``undo`` round-trip (dummy chain collapsed, original edge restored,
  positioned dummy coordinates accumulated as ``points`` waypoints),
* ``undo`` edge-label geometry restoration (``x`` / ``y`` / ``width`` /
  ``height`` copied from the ``edge-label`` dummy),
* ``undo`` no-op guards (``dummy_chains is None`` / graph label ``None``) and
  the missing-head skip (a chain entry whose node was removed),
* in-place mutation,
* the barrel surface contract (``run`` / ``undo`` stay out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.normalize import run, undo
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty flat directed graph with a ``GraphConfig`` label.

    ``compound=False`` because normalize operates on the flat rank graph (the
    compound forest is handled by ``nesting_graph`` / ``parent_dummy_chains``);
    the default ``dummy_chains`` on the freshly minted ``GraphConfig`` is
    ``None`` until ``run`` resets it.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


# === long-edge splitting ====================================================


def test_run_splits_long_edge_into_dummy_chain() -> None:
    """An edge spanning 2 ranks splits into a -> _d1 -> c with one dummy."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    run(g)
    a_sucs = g.successors("a")
    assert a_sucs is not None and len(a_sucs) == 1
    dummy = a_sucs[0]
    assert dummy.startswith("_d")
    dummy_sucs = g.successors(dummy)
    assert dummy_sucs is not None and dummy_sucs == ["c"]
    # original a->c edge is gone (replaced by the two-segment chain)
    assert not g.has_edge("a", "c", None)


def test_two_dummies_for_three_rank_span() -> None:
    """An edge spanning 3 ranks yields 2 dummies but only 1 dummy_chains entry."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("d", GraphNode(rank=3))
    g.set_edge("a", "d", GraphEdge(), None)
    run(g)
    dummies = [v for v in g.nodes() if g.node(v).dummy is not None]
    assert len(dummies) == 2
    assert sorted(g.node(v).rank for v in dummies) == [1, 2]
    # only the first (head) dummy is recorded, regardless of chain length
    assert len(g.graph().dummy_chains) == 1
    assert g.graph().dummy_chains[0] in dummies


# === unit-length / same-rank short circuits ================================


def test_short_edge_unit_length_not_split() -> None:
    """An edge spanning exactly 1 rank is left in place (only ``points`` cleared)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    run(g)
    assert g.has_edge("a", "b", None)
    assert g.graph().dummy_chains == []
    edge = g.edge("a", "b", None)
    assert edge.points == []


def test_same_rank_edge_removed_and_recreated() -> None:
    """An edge with ``w_rank == v_rank`` is removed and recreated (no dummy).

    This is the grok path where ``w_rank != v_rank + 1`` (so the early return
    is skipped) but the ``while v_rank < w_rank`` loop body never runs (its
    condition is false from the start): the original edge is removed, then a
    fresh weight-preserving edge is set back. No dummies are minted.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(weight=3.0), None)
    run(g)
    assert g.has_edge("a", "b", None)
    assert g.edge("a", "b", None).weight == 3.0
    assert g.graph().dummy_chains == []


# === dummy_chains initialization ===========================================


def test_run_initializes_dummy_chains_list() -> None:
    """``run`` resets ``GraphConfig.dummy_chains`` from ``None`` to a fresh list."""
    g = _make_graph()
    assert g.graph().dummy_chains is None
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)  # short edge -> no dummy added
    run(g)
    assert g.graph().dummy_chains == []


# === dummy node attributes =================================================


def test_dummy_node_carries_edge_type_and_rank() -> None:
    """Each dummy carries ``dummy="edge"`` + its intermediate rank + label refs."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    run(g)
    dummy = g.graph().dummy_chains[0]
    node = g.node(dummy)
    assert node.dummy == "edge"
    assert node.rank == 1
    assert node.edge_label is not None
    assert node.edge_obj is not None


def test_dummy_edge_label_is_independent_copy() -> None:
    """Each dummy's ``edge_label`` is an independent object (deepcloned by add_dummy_node)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("d", GraphNode(rank=3))
    g.set_edge("a", "d", GraphEdge(), None)
    run(g)
    dummies = [v for v in g.nodes() if g.node(v).dummy is not None]
    assert len(dummies) == 2
    el0 = g.node(dummies[0]).edge_label
    el1 = g.node(dummies[1]).edge_label
    assert el0 is not None and el1 is not None
    assert el0 is not el1  # independent deepcopies, not the same object


# === edge-label dummy ======================================================


def test_edge_label_dummy_at_label_rank() -> None:
    """When ``v_rank == label_rank`` the dummy is ``edge-label`` with geometry."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("d", GraphNode(rank=3))
    label = GraphEdge(label_rank=1, width=20.0, height=10.0, labelpos="l")
    g.set_edge("a", "d", label, None)
    run(g)
    edge_label_dummies = [v for v in g.nodes() if g.node(v).dummy == "edge-label"]
    assert len(edge_label_dummies) == 1
    el = g.node(edge_label_dummies[0])
    assert el.rank == 1
    assert el.width == 20.0
    assert el.height == 10.0
    assert el.labelpos == "l"


# === weight propagation ====================================================


def test_edge_weight_preserved_across_dummy_chain() -> None:
    """The original edge weight propagates to every segment of the dummy chain."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(weight=5.0), None)
    run(g)
    dummy = g.graph().dummy_chains[0]
    assert g.edge("a", dummy, None).weight == 5.0
    assert g.edge(dummy, "c", None).weight == 5.0


# === in-place mutation =====================================================


def test_run_mutates_graph_in_place() -> None:
    """``run`` mutates the live graph-label object (no copy/return)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    graph_label = g.graph()
    assert graph_label is not None
    assert graph_label.dummy_chains is None
    run(g)
    assert graph_label.dummy_chains is not None  # same label object, now populated


# === undo round-trip =======================================================


def test_undo_restores_original_long_edge() -> None:
    """``undo`` collapses the chain and accumulates dummy coords as waypoints."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    run(g)
    dummy = g.graph().dummy_chains[0]
    # position the dummy (the position phase would do this between run and undo)
    g.node(dummy).x = 10.0
    g.node(dummy).y = 20.0
    undo(g)
    assert g.has_edge("a", "c", None)
    assert g.node(dummy) is None  # dummy removed
    edge = g.edge("a", "c", None)
    assert edge.points is not None
    assert len(edge.points) == 1
    assert edge.points[0].x == 10.0
    assert edge.points[0].y == 20.0


def test_undo_restores_edge_label_geometry() -> None:
    """``undo`` copies ``x``/``y``/``width``/``height`` from the edge-label dummy."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("d", GraphNode(rank=3))
    label = GraphEdge(label_rank=1, width=20.0, height=10.0, labelpos="l")
    g.set_edge("a", "d", label, None)
    run(g)
    el_dummy = [v for v in g.nodes() if g.node(v).dummy == "edge-label"][0]
    g.node(el_dummy).x = 5.0
    g.node(el_dummy).y = 7.0
    undo(g)
    edge = g.edge("a", "d", None)
    assert edge.x == 5.0
    assert edge.y == 7.0
    assert edge.width == 20.0
    assert edge.height == 10.0


def test_run_then_undo_roundtrip_no_dummies() -> None:
    """``run`` + ``undo`` leaves no dummy nodes behind (full structural reversal)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("d", GraphNode(rank=3))
    g.set_edge("a", "d", GraphEdge(), None)
    run(g)
    undo(g)
    leftover = [v for v in g.nodes() if g.node(v).dummy is not None]
    assert leftover == []


# === undo no-op guards =====================================================


def test_undo_noop_when_dummy_chains_none() -> None:
    """``undo`` is a no-op when ``dummy_chains`` is ``None`` (never normalized)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    assert g.graph().dummy_chains is None
    undo(g)  # must not raise
    assert g.has_edge("a", "b", None)


def test_undo_noop_when_graph_label_none() -> None:
    """``undo`` is a no-op when the graph has no graph-level label at all."""
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    # no set_graph -> graph label is None
    undo(g)  # must not raise


def test_undo_skips_missing_chain_head() -> None:
    """``undo`` skips a ``dummy_chains`` entry whose node was removed (no panic)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    run(g)
    dummy = g.graph().dummy_chains[0]
    g.remove_node(dummy)  # corrupt the chain by removing its head
    undo(g)  # must skip the missing head instead of panicking on .unwrap()


# === barrel surface contract ===============================================


def test_normalize_not_in_dagre_all() -> None:
    """Neither ``run`` nor ``undo`` earns a crate-root barrel slot (grok keeps
    them module-private to ``layout::normalize``)."""
    assert "run" not in dagre.__all__
    assert "undo" not in dagre.__all__
    assert "normalize" not in dagre.__all__


def test_normalize_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "normalize")
    assert not hasattr(dagre, "run")


def test_submodule_exports_run_and_undo() -> None:
    """The submodule exposes exactly ``run`` / ``undo`` (the two ``pub fn``)."""
    import minimax_code.dagre.layout.normalize as norm_mod

    assert norm_mod.__all__ == ["run", "undo"]
    assert norm_mod.run is run
    assert norm_mod.undo is undo


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R250 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_normalize_import() -> None:
    """Importing ``normalize`` binds the ``layout`` subpackage on ``dagre``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.normalize as norm_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.normalize is norm_mod
