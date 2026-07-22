"""Black-box tests for the migrated dagre order/sort_subgraph layer (R264).

Exercises :mod:`minimax_code.dagre.layout.order.sort_subgraph` through its
public entry point :func:`sort_subgraph` and the :class:`SubgraphResult`
aggregate it returns, on a real ``Graph<GraphConfig, GraphNode, GraphEdge>``
compound layer graph (the state R262's :func:`build_layer_graph` produces,
carrying ``border_left_`` / ``border_right_`` per-rank labels on sub-graph
roots) plus a fresh constraint graph ``cg``. Covers:

* :func:`sort_subgraph` end-to-end: a three-movable-leaf root with no
  borders (the R260 barycenters score the leaves, R261
  :func:`resolve_conflicts` passes them through, :func:`sort`
  re-sequences them by ascending barycenter, no border bookending); an
  empty movable set (the root has no movable children -> an empty
  :class:`SubgraphResult`); a bordered root (``border_left_`` /
  ``border_right_`` bookend the sorted ``vs`` and, when both borders carry
  predecessors, their ``order`` positions fold into the result barycenter
  with a two-unit weight bump); a recursive compound child (a movable
  child that is itself a compound sub-graph is sorted recursively, its
  barycenter merged into the parent entry via
  :func:`_merge_barycenters`, then expanded into the parent's ``vs``),
* :class:`SubgraphResult` is a mutable slotted dataclass with list-factory
  defaults (the R236 trap dodged; two instances do not share the ``vs``
  list),
* the barrel surface contract (``sort_subgraph`` / ``SubgraphResult`` stay
  out of ``dagre.__all__``; the crate-root barrel count is unchanged at 4;
  the ``order`` sub-package barrel re-exports all eight ``order`` leaves;
  ``sort_subgraph.__all__`` is ASCII-sorted with the class before the fn).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.sort_subgraph import (
    SubgraphResult,
    sort_subgraph,
)
from minimax_code.data_structures import Graph, GraphOption


def _make_layer_graph() -> Graph:
    """Build an empty directed compound multigraph (the layer-graph state).

    ``compound=True`` + ``multigraph=True`` mirrors the R262
    :func:`build_layer_graph` output config. ``sort_subgraph`` reads
    ``children`` / ``node`` / ``predecessors`` + ``in_edges`` (via R260
    :func:`barycenter`) only; the ``GraphConfig`` dressing keeps the fixture
    faithful to the real layer graph the sweep runs on.
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


def _edge(g: Graph, v: str, w: str, weight: float = 1.0) -> None:
    """Add ``v -> w`` with the given weight (a barycenter in-edge)."""
    g.set_edge(v, w, GraphEdge(weight=weight), None)


def _src(g: Graph, name: str, order: int) -> None:
    """Stamp a barycenter source node with the given within-rank ``order``."""
    g.set_node(name, GraphNode(order=order))


# === sort_subgraph: end-to-end ===========================================


def test_sort_subgraph_three_movable_leaves_no_borders() -> None:
    """Three movable leaves under a border-less root sort by ascending barycenter.

    ``a_src`` (order 2), ``b_src`` (order 0), ``c_src`` (order 1) feed the
    R260 barycenters ``a=2.0`` / ``b=0.0`` / ``c=1.0``; R261
    :func:`resolve_conflicts` passes them through (no ``cg`` edges);
    :func:`sort` re-sequences to ``[b, c, a]`` (ascending barycenter). The
    aggregate is ``(2+0+1)/3 = 1.0`` (weight ``3.0``). No borders -> no
    bookending.
    """
    g = _make_layer_graph()
    g.set_node("root", GraphNode())
    g.set_parent("a", "root")
    g.set_parent("b", "root")
    g.set_parent("c", "root")
    _src(g, "a_src", 2)
    _src(g, "b_src", 0)
    _src(g, "c_src", 1)
    _edge(g, "a_src", "a")
    _edge(g, "b_src", "b")
    _edge(g, "c_src", "c")

    result = sort_subgraph(g, "root", _make_cg(), bias_right=False)

    assert result.vs == ["b", "c", "a"]
    assert result.barycenter == 1.0
    assert result.weight == 3.0


def test_sort_subgraph_empty_movable_returns_empty_result() -> None:
    """A root with no movable children yields an empty :class:`SubgraphResult`."""
    g = _make_layer_graph()
    g.set_node("root", GraphNode())

    result = sort_subgraph(g, "root", _make_cg(), bias_right=False)

    assert result.vs == []
    assert result.barycenter is None
    assert result.weight is None


def test_sort_subgraph_border_bookends_and_pred_order_fold() -> None:
    """Bordered root bookends ``vs`` + folds the border predecessors' order.

    ``root.border_left_="BL"`` / ``border_right_="BR"``; the two movable
    leaves ``a`` (barycenter ``0``) + ``b`` (barycenter ``2``) sort to
    ``[a, b]`` (barycenter ``1.0``, weight ``2.0``). Both borders carry a
    predecessor with ``order=0``, so the border-predecessor order fold
    fires: ``result.barycenter = (1.0*2.0 + 0 + 0) / (2.0 + 2.0) = 0.5``
    and ``result.weight = 4.0``. The flattened ``vs`` is
    ``[BL, a, b, BR]``.
    """
    g = _make_layer_graph()
    root = GraphNode()
    root.border_left_ = "BL"
    root.border_right_ = "BR"
    g.set_node("root", root)
    g.set_parent("a", "root")
    g.set_parent("b", "root")
    g.set_parent("BL", "root")
    g.set_parent("BR", "root")
    _src(g, "a_src", 0)
    _src(g, "b_src", 2)
    _edge(g, "a_src", "a")
    _edge(g, "b_src", "b")
    _src(g, "bl_pred", 0)
    _src(g, "br_pred", 0)
    _edge(g, "bl_pred", "BL")
    _edge(g, "br_pred", "BR")

    result = sort_subgraph(g, "root", _make_cg(), bias_right=False)

    assert result.vs == ["BL", "a", "b", "BR"]
    assert result.barycenter == 0.5
    assert result.weight == 4.0


def test_sort_subgraph_recurses_into_compound_child() -> None:
    """A movable compound child is sorted recursively + merged into the parent.

    ``root`` has ``leaf1`` (barycenter ``0``) + ``compound1`` (barycenter
    ``2``); ``compound1`` has one child ``sub_a`` (barycenter ``4``). The
    recursive call scores ``sub_a``; :func:`_merge_barycenters` folds
    ``sub_a``'s barycenter into ``compound1`` -> ``(2*1 + 4*1) / 2 = 3.0``
    (weight ``2.0``); ``leaf1`` (barycenter ``0``) sorts before
    ``compound1`` (barycenter ``3``), and ``compound1`` expands to
    ``[sub_a]``. The aggregate is ``(0*1 + 3*2) / 3 = 2.0`` (weight ``3.0``).
    """
    g = _make_layer_graph()
    g.set_node("root", GraphNode())
    g.set_parent("leaf1", "root")
    g.set_parent("compound1", "root")
    g.set_parent("sub_a", "compound1")
    _src(g, "leaf1_src", 0)
    _src(g, "compound1_src", 2)
    _src(g, "sub_a_src", 4)
    _edge(g, "leaf1_src", "leaf1")
    _edge(g, "compound1_src", "compound1")
    _edge(g, "sub_a_src", "sub_a")

    result = sort_subgraph(g, "root", _make_cg(), bias_right=False)

    assert result.vs == ["leaf1", "sub_a"]
    assert result.barycenter == 2.0
    assert result.weight == 3.0


# === SubgraphResult: dataclass contract ==================================


def test_subgraph_result_mutable_with_list_factory_defaults() -> None:
    """``SubgraphResult`` is a mutable slotted dataclass (R264 widening).

    Defaults: ``vs=[]`` (a fresh list per instance via
    ``field(default_factory=list)`` -- the R236 mutable-default trap
    dodged), ``barycenter`` / ``weight`` ``None``. Mutable because
    :func:`sort_subgraph` rewrites all three fields after the border
    bookending.
    """
    result = SubgraphResult()
    assert result.vs == []
    assert result.barycenter is None
    assert result.weight is None
    result.vs = ["x"]
    result.barycenter = 1.0
    result.weight = 2.0
    assert result.vs == ["x"]
    assert result.barycenter == 1.0
    assert result.weight == 2.0


def test_subgraph_result_two_instances_do_not_share_vs_list() -> None:
    """The ``vs`` default factory mints a fresh list per instance (R236 guard)."""
    first = SubgraphResult()
    second = SubgraphResult()
    first.vs.append("a")
    assert second.vs == []


# === barrel surface contract =============================================


def test_sort_subgraph_not_in_dagre_all() -> None:
    """``sort_subgraph`` earns no crate-root barrel slot."""
    assert "sort_subgraph" not in dagre.__all__


def test_subgraph_result_not_in_dagre_all() -> None:
    """``SubgraphResult`` earns no crate-root barrel slot."""
    assert "SubgraphResult" not in dagre.__all__


def test_sort_subgraph_not_reachable_at_dagre_top_level() -> None:
    """The fn is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "sort_subgraph")


def test_sort_subgraph_submodule_exports_symbols() -> None:
    """The submodule exposes the class + fn (two-symbol ``__all__``)."""
    import minimax_code.dagre.layout.order.sort_subgraph as sort_subgraph_mod

    assert sort_subgraph_mod.__all__ == ["SubgraphResult", "sort_subgraph"]
    assert sort_subgraph_mod.sort_subgraph is sort_subgraph
    assert sort_subgraph_mod.SubgraphResult is SubgraphResult


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R264 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_eight() -> None:
    """The ``order`` sub-package barrel re-exports all eight ``order`` leaves.

    Synchronised at R264 (``sort`` + ``sort_subgraph`` added): the barrel
    ``__all__`` grew the R263 six-element list -> the ASCII-sorted eight
    (``"sort"`` < ``"sort_subgraph"`` by the shorter-prefix-first rule,
    both placed after ``"resolve_conflicts"``). Mirrors the R259 -> R258 /
    R260 -> R258+R259 / R261 -> R258+R259+R260 / R262 -> R258+R259+R260+R261 /
    R263 -> R258+R259+R260+R261+R262 barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod
    import minimax_code.dagre.layout.order.barycenter as bc_mod
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod
    import minimax_code.dagre.layout.order.resolve_conflicts as rc_mod
    import minimax_code.dagre.layout.order.sort as sort_mod
    import minimax_code.dagre.layout.order.sort_subgraph as sort_subgraph_mod

    assert order_pkg.__all__ == [
        "add_subgraph_constraints",
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "resolve_conflicts",
        "sort",
        "sort_subgraph",
    ]
    assert order_pkg.add_subgraph_constraints is asc_mod
    assert order_pkg.barycenter is bc_mod
    assert order_pkg.build_layer_graph is blg_mod
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod
    assert order_pkg.resolve_conflicts is rc_mod
    assert order_pkg.sort_subgraph is sort_subgraph_mod
    assert order_pkg.sort is sort_mod


def test_sort_subgraph_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.sort_subgraph.sort_subgraph is sort_subgraph
