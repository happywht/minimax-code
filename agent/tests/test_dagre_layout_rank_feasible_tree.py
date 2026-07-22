"""Black-box tests for the migrated dagre rank/feasible_tree layer (R255).

Exercises :mod:`minimax_code.dagre.layout.rank.feasible_tree` through its
public entry point :func:`feasible_tree` plus the private ``_tight_tree`` /
``_tight_tree_dfs`` / ``_find_min_slack_edge`` / ``_shift_ranks` helpers on
a real ``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state
``longest_path`` leaves behind, since the ``tight-tree`` ranker strategy
runs ``longest_path`` then ``feasible_tree``). Covers:

* :func:`feasible_tree` end-to-end on a single node, a fully-tight linear
  chain (no shift needed), a diamond, a non-tight edge that forces a
  ``v``-in-tree positive-delta shift, a non-tight edge that forces a
  ``w``-in-tree negative-delta shift, the undirected-tree return contract,
  the empty-graph ``""`` seed sentinel, and the first-inserted-node start
  choice,
* ``_tight_tree`` growing across tight edges and skipping non-tight ones,
* ``_find_min_slack_edge`` picking the algebraically smallest-slack border
  edge and returning ``None`` when the tree spans (no border edge),
* ``_shift_ranks`` adding ``delta`` only to tree members and treating a
  ``None`` rank as 0,
* the barrel surface contract (``feasible_tree`` stays out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4; the ``rank``
  sub-package re-exports ``feasible_tree`` alongside ``util``;
  ``feasible_tree.__all__`` is ASCII-sorted).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.feasible_tree import feasible_tree
from minimax_code.dagre.layout.rank.util import longest_path, slack
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.graphlib import Edge


def _make_graph() -> Graph:
    """Build an empty directed graph (the state ``feasible_tree`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``rank`` runs on (the ``nesting_graph::run`` output). ``feasible_tree``
    reads only node ``rank`` and edge ``minlen`` (via :func:`slack`) and
    writes ``rank`` in place; the compound + GraphConfig dressing keeps the
    fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _make_tree() -> Graph:
    """Build the undirected tree ``feasible_tree`` constructs internally.

    Mirrors grok's ``Graph::new(GraphOption { directed: false,
    multigraph: false, compound: false })`` -- the tight-edge spanning tree
    is undirected, non-multigraph, non-compound.
    """
    return Graph(GraphOption(directed=False, multigraph=False, compound=False))


# === feasible_tree: end-to-end ============================================


def test_feasible_tree_single_node() -> None:
    """A lone node: t spans it immediately, the loop body never runs."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    t = feasible_tree(g)
    assert t.node_count() == 1
    assert t.has_node("a")
    assert g.node("a").rank == 0  # unchanged


def test_feasible_tree_tight_linear_chain_no_shift() -> None:
    """A fully-tight ``a -> b -> c`` (longest_path output) needs no shift.

    longest_path yields a=-2, b=-1, c=0 -- every edge already slack 0, so
    tight_tree covers the whole graph in one pass and shift_ranks never
    fires. The returned t spans all three nodes; ranks are untouched.
    """
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    longest_path(g)
    t = feasible_tree(g)
    assert t.node_count() == 3
    assert set(t.nodes()) == {"a", "b", "c"}
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_feasible_tree_shifts_to_make_edge_tight_v_in_tree() -> None:
    """A non-tight edge with ``v`` in the tree shifts by ``+slack``.

    ``a -> b`` both on rank 0 (minlen 1): slack = 0 - 0 - 1 = -1. start = "a"
    (first inserted), so v=a is in t -> delta = slack = -1; a drops to rank
    -1, the edge becomes tight, b is added on the next tight_tree pass.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    assert slack(g, Edge("a", "b")) == -1  # pre: not tight
    t = feasible_tree(g)
    assert t.node_count() == 2
    assert g.node("a").rank == -1  # shifted by slack (-1)
    assert g.node("b").rank == 0  # untouched (not in t when shift ran)
    assert slack(g, Edge("a", "b")) == 0  # now tight


def test_feasible_tree_shifts_when_w_in_tree() -> None:
    """A non-tight edge with ``w`` in the tree shifts by ``-slack``.

    Insert b first so start = "b" (the edge target). ``a -> b`` both rank 0:
    slack -1. Now w=b is in t -> delta = -slack = 1; b rises to rank 1, the
    edge becomes tight, a is added next.
    """
    g = _make_graph()
    g.set_node("b", GraphNode(rank=0))  # first inserted -> start node
    g.set_node("a", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    t = feasible_tree(g)
    assert t.node_count() == 2
    assert g.node("b").rank == 1  # shifted by -slack (1)
    assert g.node("a").rank == 0
    assert slack(g, Edge("a", "b")) == 0


def test_feasible_tree_returns_undirected_tree() -> None:
    """The returned tree is undirected, non-multigraph, non-compound."""
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    g.node_mut("a").rank = -2
    g.node_mut("b").rank = -1
    g.node_mut("c").rank = 0
    t = feasible_tree(g)
    assert not t.is_directed()
    assert not t.is_multigraph()
    assert not t.is_compound()


def test_feasible_tree_diamond_all_tight() -> None:
    """``a -> {b, c} -> d`` (longest_path output) is fully tight -> no shift."""
    g = _make_graph()
    for node_id in ("a", "b", "c", "d"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    g.set_edge("b", "d", GraphEdge(), None)
    g.set_edge("c", "d", GraphEdge(), None)
    longest_path(g)
    t = feasible_tree(g)
    assert t.node_count() == 4
    assert set(t.nodes()) == {"a", "b", "c", "d"}
    # ranks unchanged (every edge was already tight)
    assert g.node("a").rank == -2
    assert g.node("d").rank == 0


def test_feasible_tree_empty_graph_seeds_empty_string() -> None:
    """An empty graph violates the >= 1 node pre-condition but is tolerated.

    grok: start = "" (the ``unwrap_or`` sentinel), size = 0. t is seeded
    with "" and the loop exits immediately (``tight_tree < 0`` is False), so
    t carries just the "" seed node. Faithfully reproduced.
    """
    g = _make_graph()
    t = feasible_tree(g)
    assert t.node_count() == 1
    assert t.has_node("")


def test_feasible_tree_starts_from_first_inserted_node() -> None:
    """The seed is ``g.nodes()[0]`` -- the first-inserted node id.

    ``first`` / ``second`` both rank 0, joined by a minlen-1 edge (a connected
    graph -- the pre-condition; two isolated nodes would violate it and the
    loop would never terminate since ``_find_min_slack_edge`` would find no
    border edge). start = "first" (``g.nodes()[0]``), so v=first is in t ->
    first is shifted by ``slack`` (-1); second is added after the shift and
    keeps rank 0. The shift landing on *first* (not second) is the proof the
    seed was ``g.nodes()[0]``: had the seed been ``second``, the ``-slack``
    branch would have shifted *second* instead.
    """
    g = _make_graph()
    g.set_node("first", GraphNode(rank=0))
    g.set_node("second", GraphNode(rank=0))
    g.set_edge("first", "second", GraphEdge(), None)
    t = feasible_tree(g)
    assert t.has_node("first")  # g.nodes()[0] == "first" -> the seed
    assert g.node("first").rank == -1  # shifted by slack (proof start == "first")
    assert g.node("second").rank == 0  # untouched (added after the shift)


# === _tight_tree =========================================================


def test_tight_tree_grows_across_tight_edges() -> None:
    """A fully-tight chain grows to span the whole graph in one call."""
    from minimax_code.dagre.layout.rank.feasible_tree import _tight_tree

    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    longest_path(g)  # a=-2, b=-1, c=0 -- all edges slack 0
    t = _make_tree()
    t.set_node("a", GraphNode())
    count = _tight_tree(t, g)
    assert count == 3
    assert set(t.nodes()) == {"a", "b", "c"}


def test_tight_tree_skips_non_tight_edges() -> None:
    """A non-tight edge does not extend the tree."""
    from minimax_code.dagre.layout.rank.feasible_tree import _tight_tree

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)  # slack = -1 (not tight)
    t = _make_tree()
    t.set_node("a", GraphNode())
    count = _tight_tree(t, g)
    assert count == 1  # only a; b not added
    assert not t.has_node("b")


def test_tight_tree_is_monotonic_across_calls() -> None:
    """tight_tree only grows t; a second call on a spanned graph is a no-op."""
    from minimax_code.dagre.layout.rank.feasible_tree import _tight_tree

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=-1))
    g.set_edge("a", "b", GraphEdge(), None)  # slack = -1 - 0 - 1 = -2 (not tight)
    t = _make_tree()
    t.set_node("a", GraphNode())
    first = _tight_tree(t, g)
    second = _tight_tree(t, g)
    assert first == second == 1  # no growth either call


# === _find_min_slack_edge ================================================


def test_find_min_slack_edge_picks_smallest_slack() -> None:
    """Among border edges, the algebraically smallest slack wins."""
    from minimax_code.dagre.layout.rank.feasible_tree import _find_min_slack_edge

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)  # slack -1
    g.set_edge("a", "c", GraphEdge(minlen=2.0), None)  # slack 0 - 0 - 2 = -2
    t = _make_tree()
    t.set_node("a", GraphNode())  # a in t; b, c out -> both are border edges
    edge = _find_min_slack_edge(t, g)
    assert edge is not None
    assert (edge.v, edge.w) == ("a", "c")  # -2 < -1


def test_find_min_slack_edge_returns_none_when_tree_spans() -> None:
    """No border edge exists when every node is already in t."""
    from minimax_code.dagre.layout.rank.feasible_tree import _find_min_slack_edge

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    t = _make_tree()
    t.set_node("a", GraphNode())
    t.set_node("b", GraphNode())  # both endpoints in t
    assert _find_min_slack_edge(t, g) is None


def test_find_min_slack_edge_returns_none_on_edgeless_graph() -> None:
    """A graph with one node and no edges has no border edge."""
    from minimax_code.dagre.layout.rank.feasible_tree import _find_min_slack_edge

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    t = _make_tree()
    t.set_node("a", GraphNode())
    assert _find_min_slack_edge(t, g) is None


# === _shift_ranks ========================================================


def test_shift_ranks_adds_delta_to_tree_nodes_only() -> None:
    """Only nodes in t are shifted; out-of-tree nodes are untouched."""
    from minimax_code.dagre.layout.rank.feasible_tree import _shift_ranks

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=0))
    t = _make_tree()
    t.set_node("a", GraphNode())
    t.set_node("b", GraphNode())  # c deliberately absent from t
    _shift_ranks(t, g, 5)
    assert g.node("a").rank == 5
    assert g.node("b").rank == 5
    assert g.node("c").rank == 0  # untouched


def test_shift_ranks_treats_none_rank_as_zero() -> None:
    """A ``None`` rank contributes 0 before the delta (grok ``unwrap_or(0)``)."""
    from minimax_code.dagre.layout.rank.feasible_tree import _shift_ranks

    g = _make_graph()
    g.set_node("a", GraphNode())  # rank defaults to None
    assert g.node("a").rank is None
    t = _make_tree()
    t.set_node("a", GraphNode())
    _shift_ranks(t, g, 3)
    assert g.node("a").rank == 3  # 0 + 3


def test_shift_ranks_negative_delta() -> None:
    """A negative delta lowers ranks (the w-in-tree branch uses ``-slack``)."""
    from minimax_code.dagre.layout.rank.feasible_tree import _shift_ranks

    g = _make_graph()
    g.set_node("a", GraphNode(rank=10))
    t = _make_tree()
    t.set_node("a", GraphNode())
    _shift_ranks(t, g, -4)
    assert g.node("a").rank == 6


# === barrel surface contract =============================================


def test_feasible_tree_not_in_dagre_all() -> None:
    """``feasible_tree`` earns no crate-root barrel slot."""
    assert "feasible_tree" not in dagre.__all__


def test_feasible_tree_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "feasible_tree")


def test_feasible_tree_submodule_exports_feasible_tree() -> None:
    """The submodule exposes exactly ``feasible_tree`` (ASCII-sorted singleton)."""
    import minimax_code.dagre.layout.rank.feasible_tree as ft_mod

    assert ft_mod.__all__ == ["feasible_tree"]
    assert ft_mod.feasible_tree is feasible_tree


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R255 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_rank_subpackage_barrel_reexports_feasible_tree_and_util() -> None:
    """The ``rank`` barrel re-exports ``feasible_tree`` alongside ``util``."""
    import minimax_code.dagre.layout.rank as rank_pkg
    import minimax_code.dagre.layout.rank.feasible_tree as ft_mod
    import minimax_code.dagre.layout.rank.util as util_mod

    assert rank_pkg.__all__ == ["feasible_tree", "util"]  # ASCII-sorted
    assert rank_pkg.feasible_tree is ft_mod
    assert rank_pkg.util is util_mod


def test_feasible_tree_reachable_via_layout_rank() -> None:
    """Importing the ``rank`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.rank as rank_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.rank is rank_pkg
    assert rank_pkg.feasible_tree.feasible_tree is feasible_tree
