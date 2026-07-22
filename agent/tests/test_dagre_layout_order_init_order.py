"""Black-box tests for the migrated dagre order/init_order layer (R258).

Exercises :mod:`minimax_code.dagre.layout.order.init_order` through its
public entry point :func:`init_order` plus the private ``_init_order_dfs``
helper on a real ``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state
``rank`` leaves behind, since ``run_layout`` calls ``order`` immediately
after ``rank`` assigns every node a rank). Covers:

* :func:`init_order` end-to-end on a single node, a linear chain, a fork,
  a diamond (the shared sink placed exactly once via the shared
  ``visited`` map), two disjoint components, an empty graph, an
  all-rank-None graph, and a compound-forest graph that filters compound
  nodes out of the DFS seed set,
* the ``simple_nodes`` seed filter (compound / subgraph nodes with
  children are excluded; leaf nodes seed the walk),
* the stable rank-sort of the seeds (lowest-rank seed fires first),
* the shared ``visited`` deduplication across seeds,
* the ``max_rank`` default 0 for an empty seed set,
* that the graph is NOT mutated (ranks read only),
* ``_init_order_dfs`` white-box: the visited re-visit no-op, the
  missing-label defensive no-op, the missing-successors short-circuit,
  the rank-beyond-span layer growth, and the ``None`` rank -> layer 0,
* the barrel surface contract (``init_order`` stays out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4; the
  ``order`` sub-package re-exports ``init_order``;
  ``init_order.__all__`` is ASCII-sorted).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.init_order import _init_order_dfs, init_order
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap


def _make_graph() -> Graph:
    """Build an empty directed compound graph (the state ``order`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``order`` runs on (the post-``rank`` / post-``normalize`` graph).
    ``init_order`` reads node ``rank`` + the successor table + compound
    ``children`` (to filter seed nodes); the GraphConfig dressing keeps
    the fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


# === init_order: end-to-end =============================================


def test_init_order_single_node() -> None:
    """A lone node on rank 0 lands in the single layer as the sole entry."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    assert init_order(g) == [["a"]]


def test_init_order_linear_chain() -> None:
    """``a -> b -> c`` (ranks 0/1/2) places each node in its own rank layer.

    All three are leaf seeds (no compound children); sorted by rank they
    fire a, b, c. The DFS from a reaches b then c via successors, so each
    lands in its own rank layer. The later seeds (b, c) find their slots
    already filled (visited) and no-op.
    """
    g = _make_graph()
    for node_id, rank in (("a", 0), ("b", 1), ("c", 2)):
        g.set_node(node_id, GraphNode(rank=rank))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    assert init_order(g) == [["a"], ["b"], ["c"]]


def test_init_order_fork() -> None:
    """``a -> {b, c}`` (a rank 0, b/c rank 1) places both children in layer 1.

    DFS from a visits b first then c (insertion order of the a -> b / a -> c
    edges); both land in layer 1, in successor-discovery order.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_node("c", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    assert init_order(g) == [["a"], ["b", "c"]]


def test_init_order_diamond_shared_sink_placed_once() -> None:
    """``a -> {b, c} -> d`` places d exactly once (shared-sink dedup).

    DFS from a reaches b then d (b's successor); d is marked visited. The
    c -> d walk then finds d visited and skips it. d lands in layer 2 once.
    """
    g = _make_graph()
    for node_id, rank in (("a", 0), ("b", 1), ("c", 1), ("d", 2)):
        g.set_node(node_id, GraphNode(rank=rank))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    g.set_edge("b", "d", GraphEdge(), None)
    g.set_edge("c", "d", GraphEdge(), None)
    layers = init_order(g)
    assert layers == [["a"], ["b", "c"], ["d"]]
    # d appears exactly once across all layers (the dedup guarantee).
    assert sum(layer.count("d") for layer in layers) == 1


def test_init_order_two_disjoint_components() -> None:
    """Two disjoint chains seed independently; both land in their rank layers.

    Seeds sorted by rank: a(0), c(0), b(1), d(1). DFS(a) places a -> b;
    DFS(c) places c -> d. Layer 0 holds the two sources in seed order.
    """
    g = _make_graph()
    for node_id, rank in (("a", 0), ("b", 1), ("c", 0), ("d", 1)):
        g.set_node(node_id, GraphNode(rank=rank))
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("c", "d", GraphEdge(), None)
    assert init_order(g) == [["a", "c"], ["b", "d"]]


def test_init_order_empty_graph() -> None:
    """An empty graph yields a single empty layer (max_rank defaults to 0)."""
    g = _make_graph()
    assert init_order(g) == [[]]


def test_init_order_all_rank_none_uses_zero() -> None:
    """Nodes with no ``rank`` land in layer 0 (the unwrap_or(0) default).

    Every node carries ``rank = None`` (a fresh ``GraphNode()``); the
    ``_rank_of`` extraction yields 0 for each, so ``max_rank = 0`` and
    every placed node lands in the single layer 0.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())  # rank defaults to None
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    assert init_order(g) == [["a", "b"]]


def test_init_order_filters_compound_nodes_from_seeds() -> None:
    """Compound nodes (with children) are NOT DFS seeds.

    ``cluster`` parents ``a``; ``cluster`` has a compound child, so it is
    excluded from ``simple_nodes``. Only ``a`` seeds the walk; ``cluster``
    is placed only if reached as a successor (it is not, here), so it
    stays absent from every layer.
    """
    g = _make_graph()
    g.set_node("cluster", GraphNode(rank=0))
    g.set_node("a", GraphNode(rank=1))
    g.set_parent("a", "cluster")  # cluster now has child a
    assert g.children("cluster") == ["a"]  # compound parent
    assert g.children("a") == []  # leaf
    layers = init_order(g)
    # only "a" is a seed; it lands in layer 1 (rank 1). cluster has children
    # -> not a seed; no successor points to it -> not placed.
    assert layers == [[], ["a"]]
    assert "cluster" not in [v for layer in layers for v in layer]


def test_init_order_does_not_mutate_graph() -> None:
    """init_order reads ranks only; the graph is unchanged after the call."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    snapshot = {n: g.node(n).rank for n in g.nodes()}
    init_order(g)
    assert {n: g.node(n).rank for n in g.nodes()} == snapshot


# === _init_order_dfs: white-box ========================================


def test_init_order_dfs_revisit_is_noop() -> None:
    """A second call on an already-visited node places nothing new."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_edge("a", "b", GraphEdge(), None)
    visited: OrderedHashMap = OrderedHashMap()
    layers: list[list[str]] = [[], []]
    _init_order_dfs("a", g, visited, layers)
    # first call places a (rank 0) then recurses to b (rank 1)
    assert layers == [["a"], ["b"]]
    _init_order_dfs("a", g, visited, layers)  # revisit -> no-op
    assert layers == [["a"], ["b"]]


def test_init_order_dfs_missing_label_is_noop() -> None:
    """A ghost node (no label) is marked visited but not placed.

    grok's ``g.node(v).unwrap()`` assumes a labelled node; Python treats a
    missing label defensively (mark visited, skip placement) -- the R254
    ``_longest_path_dfs`` widening. The ghost is recorded in ``visited``
    so a later re-visit is still a no-op.
    """
    g = _make_graph()
    visited: OrderedHashMap = OrderedHashMap()
    layers: list[list[str]] = [[], []]
    assert g.node("ghost") is None
    _init_order_dfs("ghost", g, visited, layers)
    assert "ghost" in visited  # marked visited
    assert layers == [[], []]  # but not placed in any layer


def test_init_order_dfs_missing_successors_ends_walk() -> None:
    """A node whose successors table is empty ends the walk (no recursion).

    A real node (``a``) with no out-edges carries an empty successors list:
    ``set_node`` seeds the per-node ``_sucs`` table, so ``successors`` returns
    ``[]`` (not ``None``). The walk places ``a`` then the empty ``for sv in
    []`` loop ends the recursion. The ``if sucs is None`` branch in
    ``_init_order_dfs`` is defensive (matches grok's ``.unwrap_or(vec![])``)
    but unreachable on a real graph -- the upstream ``node is None`` guard
    already intercepts any vertex without a ``_sucs`` entry.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))  # no out-edges -> successors == []
    assert g.successors("a") == []
    visited: OrderedHashMap = OrderedHashMap()
    layers: list[list[str]] = [[]]
    _init_order_dfs("a", g, visited, layers)
    assert layers == [["a"]]  # placed, but no recursion


def test_init_order_dfs_grows_layers_beyond_span() -> None:
    """A rank beyond the pre-allocated layer span grows ``layers`` in place.

    grok's ``layers.insert(node_rank, vec![])`` grows on a rank past the
    end; Rust ``Vec::insert`` panics on a gap, but Python extends to fill
    (the defensive widening). A rank-3 node with a single pre-allocated
    layer grows ``layers`` to length 4.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=3))
    visited: OrderedHashMap = OrderedHashMap()
    layers: list[list[str]] = [[]]  # pre-allocated for rank 0 only
    _init_order_dfs("a", g, visited, layers)
    assert layers == [[], [], [], ["a"]]  # grown to length 4


def test_init_order_dfs_rank_none_uses_zero() -> None:
    """A ``None`` rank lands the node in layer 0 (the unwrap_or(0) default)."""
    g = _make_graph()
    g.set_node("a", GraphNode())  # rank None
    visited: OrderedHashMap = OrderedHashMap()
    layers: list[list[str]] = [[]]
    _init_order_dfs("a", g, visited, layers)
    assert layers == [["a"]]


# === barrel surface contract ===========================================


def test_init_order_not_in_dagre_all() -> None:
    """``init_order`` earns no crate-root barrel slot."""
    assert "init_order" not in dagre.__all__


def test_init_order_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "init_order")


def test_init_order_submodule_exports_init_order() -> None:
    """The submodule exposes exactly ``init_order`` (ASCII-sorted singleton)."""
    import minimax_code.dagre.layout.order.init_order as init_mod

    assert init_mod.__all__ == ["init_order"]
    assert init_mod.init_order is init_order


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R258 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_init_order() -> None:
    """The ``order`` sub-package barrel re-exports ``init_order``."""
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.init_order as init_mod

    assert order_pkg.__all__ == ["init_order"]
    assert order_pkg.init_order is init_mod


def test_init_order_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.init_order.init_order is init_order
