"""Black-box tests for the migrated dagre rank/network_simplex layer (R256).

Exercises :mod:`minimax_code.dagre.layout.rank.network_simplex` through its
public entry point :func:`network_simplex` plus the private
``_init_low_lim_values`` / ``_dfs_assign_low_lim`` / ``_leave_edge`` /
``_is_tree_edge`` / ``_is_descendant`` / ``_update_ranks`` /
``_init_cut_values`` helpers on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state ``feasible_tree``
leaves behind, since ``network_simplex`` runs ``longest_path`` then
``feasible_tree`` internally). Covers:

* :func:`network_simplex` end-to-end on a single node, a linear chain
  (longest_path already optimal -> every tree-edge cut value >= 0 -> no
  pivot -> ranks copied back unchanged and stay negative), a diamond
  (verified feasible: every edge slack >= 0; its tight-tree cut values are
  all >= 0 too so no pivot fires either), the in-place mutation contract,
  and the empty-graph tolerance,
* ``_init_low_lim_values`` / ``_dfs_assign_low_lim`` DFS-numbering the tree
  with ``[low, lim]`` intervals and ``parent`` pointers (incl. an explicit
  ``root_`` override and the empty-tree ``""`` seed sentinel),
* ``_leave_edge`` returning the first negative-cut-value tree edge (and
  ``None`` when none are negative; a ``None`` cutvalue resolves to 0.0;
  an edgeless tree yields ``None``),
* ``_is_tree_edge`` membership and ``_is_descendant`` the ``[low, lim]``
  interval test (including the ``None`` -> 0 fallback),
* ``_update_ranks`` propagating ``parent_rank +/- minlen`` down the tree
  (the ``minlen as i32`` truncation toward zero; ``+`` when the graph edge
  points ``parent -> child`` -- flipped; ``-`` when it points
  ``child -> parent``),
* ``_init_cut_values`` writing ``cutvalue`` onto every non-root tree edge,
* the barrel surface contract (``network_simplex`` stays out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4; the ``rank``
  sub-package re-exports ``network_simplex`` alongside ``feasible_tree``
  and ``util``; ``network_simplex.__all__`` is ASCII-sorted).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.network_simplex import network_simplex
from minimax_code.dagre.layout.rank.util import slack
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty directed graph (the state ``network_simplex`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``rank`` runs on (the ``nesting_graph::run`` output). ``network_simplex``
    simplifies the graph first (R248 ``util.simplify``), so the compound /
    multigraph dressing is faithful but the algorithm itself operates on
    the simplified simple weighted DAG.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _make_tree() -> Graph:
    """Build the undirected tree ``network_simplex`` constructs internally.

    Mirrors grok's ``Graph::new(GraphOption { directed: false,
    multigraph: false, compound: false })`` -- the tight-edge spanning tree
    (built by ``feasible_tree``) that the network-simplex loop pivots on is
    undirected, non-multigraph, non-compound.
    """
    return Graph(GraphOption(directed=False, multigraph=False, compound=False))


# === network_simplex: end-to-end =========================================


def test_network_simplex_single_node() -> None:
    """A lone node keeps rank 0 (longest_path seeds it, no pivot fires)."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    network_simplex(g)
    assert g.node("a").rank == 0


def test_network_simplex_linear_chain_longest_path_optimal() -> None:
    """``a -> b -> c`` (default minlen 1) is already optimal: no pivot fires.

    longest_path yields a=-2, b=-1, c=0 -- every edge slack 0, so the tight
    tree spans the whole graph. The post-order cut-value walk then yields
    cut_value(c)=1.0 (its only incident edge is the tree edge b->c) and
    cut_value(b)=1.0 (a->b's weight 1 plus the c-side contribution folded
    in as a tree-edge term); both are >= 0, so ``_leave_edge`` returns
    ``None`` and the pivot loop never enters. Ranks are copied back from
    the simplified graph unchanged. They stay negative: ``network_simplex``
    does NOT normalize (``normalize_ranks`` is a later stage).
    """
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    network_simplex(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_network_simplex_diamond_produces_feasible_ranking() -> None:
    """``a -> {b, c} -> d`` yields a feasible ranking (every edge slack >= 0).

    The network-simplex invariant: the output ranking always satisfies
    ``rank(w) - rank(v) >= minlen`` for every edge. The diamond's tight tree
    is ``{a-b, a-c, b-d}`` and its post-order cut values are cut_value(d)=2.0,
    cut_value(b)=2.0, cut_value(c)=0.0 -- all >= 0, so ``_leave_edge``
    returns ``None`` and no pivot fires; the longest_path ranks
    (a=-2, b=c=-1, d=0) are copied back unchanged. Both the exact ranks and
    the feasibility invariant are asserted (slack >= 0 on every edge is the
    hard guarantee that survives any future pivot-order change).
    """
    g = _make_graph()
    for node_id in ("a", "b", "c", "d"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    g.set_edge("b", "d", GraphEdge(), None)
    g.set_edge("c", "d", GraphEdge(), None)
    network_simplex(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == -1
    assert g.node("d").rank == 0
    # The feasibility invariant (every edge slack >= 0) is the hard guarantee.
    for edge_obj in g.edges():
        assert slack(g, edge_obj) >= 0


def test_network_simplex_mutates_graph_in_place() -> None:
    """network_simplex writes ranks on the live graph object (returns None)."""
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    assert g.node("a").rank is None  # unset before
    result = network_simplex(g)
    assert result is None
    assert g.node("a").rank is not None  # same graph, now ranked


def test_network_simplex_empty_graph_is_tolerated() -> None:
    """An empty graph is simplified to nothing; the loop exits without crashing.

    simplify -> empty; longest_path -> no-op; feasible_tree -> the "" seed
    tree (R255); init_low_lim / init_cut_values -> no-op on a single "" node
    (the post-order pop strips the lone root); leave_edge -> ``None`` (no
    edges); the copy-back loop iterates no nodes. The graph stays empty.
    """
    g = _make_graph()
    network_simplex(g)  # must not raise
    assert g.node_count() == 0


# === _init_low_lim_values / _dfs_assign_low_lim =========================


def test_init_low_lim_values_numbers_linear_tree() -> None:
    """A ``a - b - c`` tree rooted at ``a`` gets ``[low, lim]`` intervals + parents.

    DFS from a (``t.nodes()[0]``): a enters at low 1, recurses b (low 1) ->
    c (low 1, lim 1, parent b) -> b (lim 2, parent a) -> a (lim 3). So
    a=[1,3,None], b=[1,2,"a"], c=[1,1,"b"]. The intervals back the
    ``_is_descendant`` LCA test.
    """
    from minimax_code.dagre.layout.rank.network_simplex import _init_low_lim_values

    t = _make_tree()
    for node_id in ("a", "b", "c"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    t.set_edge("b", "c", GraphEdge(), None)
    _init_low_lim_values(t, None)
    a, b, c = t.node("a"), t.node("b"), t.node("c")
    assert (a.low, a.lim, a.parent) == (1, 3, None)
    assert (b.low, b.lim, b.parent) == (1, 2, "a")
    assert (c.low, c.lim, c.parent) == (1, 1, "b")


def test_init_low_lim_values_respects_explicit_root() -> None:
    """Passing ``root_`` overrides the ``t.nodes()[0]`` default seed."""
    from minimax_code.dagre.layout.rank.network_simplex import _init_low_lim_values

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    _init_low_lim_values(t, "b")  # root at b, not the first-inserted a
    assert t.node("b").parent is None  # b is the root
    assert t.node("a").parent == "b"  # a descends from b


def test_init_low_lim_values_empty_tree_seeds_empty_string() -> None:
    """An empty tree seeds root ``""`` (the unwrap_or sentinel) without crashing.

    grok: ``root_ = tree.nodes().get(0).map(|n| n.0.clone()).unwrap_or(String::new())``
    -- an empty node list yields ``""``; the DFS marks ``""`` visited and
    finds no neighbors, so the call is a safe no-op.
    """
    from minimax_code.dagre.layout.rank.network_simplex import _init_low_lim_values

    t = _make_tree()
    _init_low_lim_values(t, None)  # nodes() == [] -> root "" -> no-op DFS


def test_dfs_assign_low_lim_marks_visited_and_returns_next_lim() -> None:
    """_dfs_assign_low_lim marks ``v`` visited and returns the post-increment ``next_lim``.

    On ``t: a - b`` (a root, b child): a enters at low 1, recurses b (low 1,
    lim 1), b returns 2; a then sets lim 2, increments to 3 and returns 3.
    Both endpoints are marked visited; the returned ``next_lim`` is the
    running counter past a's subtree (3 = 1 for a.low + 1 for b's subtree
    + 1 for a.lim).
    """
    from minimax_code.dagre.layout.rank.network_simplex import _dfs_assign_low_lim
    from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    visited: OrderedHashMap = OrderedHashMap()
    next_lim = _dfs_assign_low_lim(t, visited, 1, "a", None)
    assert "a" in visited
    assert "b" in visited
    assert next_lim == 3
    assert (t.node("a").low, t.node("a").lim) == (1, 2)
    assert (t.node("b").low, t.node("b").lim) == (1, 1)


# === _leave_edge =========================================================


def test_leave_edge_returns_first_negative_cutvalue_edge() -> None:
    """A tree edge with ``cutvalue < 0`` is the leave-edge candidate."""
    from minimax_code.dagre.layout.rank.network_simplex import _leave_edge

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(cutvalue=-1.0), None)
    edge = _leave_edge(t)
    assert edge is not None
    assert (edge.v, edge.w) == ("a", "b")


def test_leave_edge_returns_none_when_all_nonnegative() -> None:
    """No edge has a negative cut value -> no leave edge -> the pivot loop ends."""
    from minimax_code.dagre.layout.rank.network_simplex import _leave_edge

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(cutvalue=2.0), None)
    assert _leave_edge(t) is None


def test_leave_edge_none_cutvalue_treated_as_zero() -> None:
    """A ``None`` cutvalue resolves to 0.0 (never negative) via ``unwrap_or``.

    grok: ``edge.cutvalue.unwrap_or(0.0)``. A freshly minted ``GraphEdge()``
    has ``cutvalue = None`` (no manual-Default seeding for this field), so
    it folds to 0.0 and never trips the ``< 0`` test.
    """
    from minimax_code.dagre.layout.rank.network_simplex import _leave_edge

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)  # cutvalue defaults to None
    assert _leave_edge(t) is None  # None -> 0.0 -> not < 0


def test_leave_edge_returns_none_on_edgeless_tree() -> None:
    """A tree with nodes but no edges has no leave-edge candidate."""
    from minimax_code.dagre.layout.rank.network_simplex import _leave_edge

    t = _make_tree()
    t.set_node("a", GraphNode())
    assert _leave_edge(t) is None


# === _is_tree_edge / _is_descendant ======================================


def test_is_tree_edge_true_for_present_edge() -> None:
    """``_is_tree_edge`` mirrors ``tree.has_edge`` on the undirected tree."""
    from minimax_code.dagre.layout.rank.network_simplex import _is_tree_edge

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    assert _is_tree_edge(t, "a", "b") is True
    assert _is_tree_edge(t, "b", "a") is True  # undirected


def test_is_tree_edge_false_for_absent_edge() -> None:
    """An edge not in the tree returns ``False``."""
    from minimax_code.dagre.layout.rank.network_simplex import _is_tree_edge

    t = _make_tree()
    for node_id in ("a", "b", "c"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    assert _is_tree_edge(t, "a", "c") is False


def test_is_descendant_true_within_interval() -> None:
    """``v.lim`` inside ``[root.low, root.lim]`` -> descendant."""
    from minimax_code.dagre.layout.rank.network_simplex import _is_descendant

    root = GraphNode(low=1, lim=5)
    v = GraphNode(low=2, lim=3)
    assert _is_descendant(v, root) is True  # 1 <= 3 <= 5


def test_is_descendant_false_outside_interval() -> None:
    """``v.lim`` outside ``[root.low, root.lim]`` -> not a descendant."""
    from minimax_code.dagre.layout.rank.network_simplex import _is_descendant

    root = GraphNode(low=3, lim=5)
    v = GraphNode(low=1, lim=2)
    assert _is_descendant(v, root) is False  # 3 <= 2? no


def test_is_descendant_none_fields_treated_as_zero() -> None:
    """``None`` low / lim resolve to 0 (the ``unwrap_or`` fallback)."""
    from minimax_code.dagre.layout.rank.network_simplex import _is_descendant

    root = GraphNode()  # low None, lim None -> 0, 0
    v = GraphNode()  # lim None -> 0
    assert _is_descendant(v, root) is True  # 0 <= 0 <= 0


# === _update_ranks =======================================================


def test_update_ranks_propagates_parent_rank_plus_minlen() -> None:
    """A child whose graph edge points ``parent -> child`` ranks ``parent + minlen``.

    ``t: a - b`` (a root, b child of a). ``g: a -> b`` (minlen 1): the graph
    edge is ``parent -> child`` so ``g.edge(child, parent)`` is ``None``,
    ``flipped = True``, and ``delta = +int(minlen)``. With a on rank 0,
    b lands on rank 1. This is the rank ``longest_path`` would have given b
    -- ``_update_ranks`` re-derives it from the tree topology.
    """
    from minimax_code.dagre.layout.rank.network_simplex import (
        _init_low_lim_values,
        _update_ranks,
    )

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    _init_low_lim_values(t, None)  # stamps b.parent = "a"

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode())  # rank unset
    g.set_edge("a", "b", GraphEdge(minlen=1.0), None)
    _update_ranks(t, g)
    assert g.node("b").rank == 1  # 0 + int(1.0) (flipped, parent -> child)


def test_update_ranks_minlen_truncates_toward_zero() -> None:
    """``minlen as i32`` truncates toward zero; Python ``int()`` matches.

    grok: ``minlen as i32`` on an ``f64`` truncates toward zero (NOT rounding).
    ``g: a -> b`` with ``minlen = 1.9``: ``int(1.9) == 1`` (NOT 2), so b lands
    exactly one rank below a, faithfully reproducing the cast. The R254
    ``longest_path`` uses ``round()`` instead -- a deliberate asymmetry
    reproduced here.
    """
    from minimax_code.dagre.layout.rank.network_simplex import (
        _init_low_lim_values,
        _update_ranks,
    )

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    _init_low_lim_values(t, None)

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(minlen=1.9), None)
    _update_ranks(t, g)
    assert g.node("b").rank == 1  # int(1.9) == 1 (truncation, not rounding)


def test_update_ranks_child_to_parent_edge_subtracts_minlen() -> None:
    """A graph edge pointing ``child -> parent`` ranks ``parent - minlen``.

    ``t: a - b`` (a root). ``g: b -> a`` (minlen 1): ``g.edge(child=b,
    parent=a)`` exists, so ``flipped = False`` and ``delta = -int(minlen)``.
    With a on rank 0, b lands on rank -1 (the edge points the "wrong" way
    relative to the tree, so the child is ranked above its parent).
    """
    from minimax_code.dagre.layout.rank.network_simplex import (
        _init_low_lim_values,
        _update_ranks,
    )

    t = _make_tree()
    for node_id in ("a", "b"):
        t.set_node(node_id, GraphNode())
    t.set_edge("a", "b", GraphEdge(), None)
    _init_low_lim_values(t, None)

    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode())
    g.set_edge("b", "a", GraphEdge(minlen=1.0), None)  # child -> parent
    _update_ranks(t, g)
    assert g.node("b").rank == -1  # 0 - int(1.0) (not flipped)


# === _init_cut_values (smoke) ============================================


def test_init_cut_values_writes_cutvalue_on_tree_edges() -> None:
    """_init_cut_values stamps ``cutvalue`` on every non-root tree edge.

    Builds a real tight tree (longest_path + feasible_tree), numbers it
    (init_low_lim), then init_cut_values walks the post-order and writes a
    float ``cutvalue`` onto each tree edge. Smoke-level: verifies the
    recursion completes and every tree edge label carries a non-None
    cutvalue. On ``a -> b -> c`` the post-order yields cut_value(c)=1.0 and
    cut_value(b)=1.0 (both >= 0 -- the proof the linear chain needs no
    pivot, asserted in ``test_network_simplex_linear_chain_longest_path_optimal``).
    """
    from minimax_code.dagre.layout.rank.feasible_tree import feasible_tree
    from minimax_code.dagre.layout.rank.network_simplex import (
        _init_cut_values,
        _init_low_lim_values,
    )
    from minimax_code.dagre.layout.rank.util import longest_path

    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    longest_path(g)
    t = feasible_tree(g)
    _init_low_lim_values(t, None)
    _init_cut_values(t, g)
    for edge_obj in t.edges():
        label = t.edge_with_obj(edge_obj)
        assert label is not None
        assert label.cutvalue is not None  # written by the post-order walk


# === barrel surface contract =============================================


def test_network_simplex_not_in_dagre_all() -> None:
    """``network_simplex`` earns no crate-root barrel slot."""
    assert "network_simplex" not in dagre.__all__


def test_network_simplex_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "network_simplex")


def test_network_simplex_submodule_exports_network_simplex() -> None:
    """The submodule exposes exactly ``network_simplex`` (ASCII-sorted singleton)."""
    import minimax_code.dagre.layout.rank.network_simplex as ns_mod

    assert ns_mod.__all__ == ["network_simplex"]
    assert ns_mod.network_simplex is network_simplex


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R256 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_rank_subpackage_barrel_reexports_network_simplex() -> None:
    """The ``rank`` barrel re-exports ``network_simplex`` alongside its siblings.

    Mirrors grok's ``pub mod network_simplex`` visibility: the barrel exposes
    ``network_simplex`` next to ``feasible_tree`` and ``util`` so
    ``from minimax_code.dagre.layout.rank import network_simplex`` resolves
    once ``rank::mod`` is wired in a later leaf. The ``__all__`` is
    ASCII-sorted (``feasible_tree`` < ``network_simplex`` < ``util``).
    """
    import minimax_code.dagre.layout.rank as rank_pkg
    import minimax_code.dagre.layout.rank.network_simplex as ns_mod

    assert rank_pkg.__all__ == ["feasible_tree", "mod", "network_simplex", "util"]  # ASCII-sorted
    assert rank_pkg.network_simplex is ns_mod


def test_network_simplex_reachable_via_layout_rank() -> None:
    """Importing the ``rank`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.rank as rank_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.rank is rank_pkg
    assert rank_pkg.network_simplex.network_simplex is network_simplex
