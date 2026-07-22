"""Black-box tests for the migrated dagre order/cross_count layer (R259).

Exercises :mod:`minimax_code.dagre.layout.order.cross_count` through its
two public entry points (:func:`cross_count` + :func:`two_layer_cross_count`)
on a real ``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state ``order``
runs on, immediately after ``init_order`` mints the ``layering`` matrix).
Covers:

* :func:`two_layer_cross_count` end-to-end on a single edge (no crossing),
  parallel non-crossing edges, a single crossing, the K_{2,2} complete
  bipartite case (1 crossing), a fully-inverted three-edge set
  (C(3, 2) = 3 crossings -- the maximum), weighted edges (2 x 3 = 6),
  an edge whose south endpoint is absent from ``south_layer`` (skipped),
  a ``None`` edge weight (counts as 0.0), an empty north layer, an empty
  south layer, and graph immutability,
* :func:`cross_count` end-to-end on an empty / single-layer layering
  (0.0 -- no adjacent pair), a linear three-layer chain (no crossings),
  a three-layer matrix that accumulates one crossing per adjacent pair
  (2.0 total), and graph immutability,
* the Barth O(k log k) tree machinery indirectly: the fully-inverted
  three-edge case requires the complete binary tree to span positions
  0-2 (``first_index`` promoted to the next power of two), so its
  C(3, 2) = 3 result certifies the tree size / ``first_index`` math;
  the weighted case certifies the ``weight * weight_sum`` accumulation,
* the barrel surface contract (``cross_count`` / ``two_layer_cross_count``
  stay out of ``dagre.__all__``; the crate-root barrel count is unchanged
  at 4; the ``order`` sub-package barrel re-exports both ``cross_count``
  and ``init_order``; ``cross_count.__all__`` is ASCII-sorted).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.cross_count import cross_count, two_layer_cross_count
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty directed compound graph (the state ``order`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``order`` runs on (the post-``init_order`` graph). ``cross_count`` reads
    ``out_edges`` + ``edge_with_obj`` + ``edge.weight`` only; the GraphConfig
    dressing keeps the fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _edge(g: Graph, v: str, w: str, weight: float | None = 1.0) -> None:
    """Add ``v -> w`` carrying the given weight (default 1.0, ``None`` allowed)."""
    g.set_edge(v, w, GraphEdge(weight=weight), None)


# === two_layer_cross_count =============================================


def test_two_layer_single_edge_no_crossing() -> None:
    """A lone ``a -> b`` edge between singleton layers crosses nothing."""
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    _edge(g, "a", "b")
    assert two_layer_cross_count(g, ["a"], ["b"]) == 0.0


def test_two_layer_parallel_edges_no_crossing() -> None:
    """``a -> c`` + ``b -> d`` are parallel (left-to-left) -> 0 crossings."""
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "c")
    _edge(g, "b", "d")
    assert two_layer_cross_count(g, ["a", "b"], ["c", "d"]) == 0.0


def test_two_layer_one_crossing() -> None:
    """``a -> d`` + ``b -> c`` cross exactly once (a is left of b, d is right of c)."""
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "d")
    _edge(g, "b", "c")
    assert two_layer_cross_count(g, ["a", "b"], ["c", "d"]) == 1.0


def test_two_layer_complete_bipartite_k22() -> None:
    """K_{2,2} (all four edges) yields exactly one crossing (a-d vs b-c)."""
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "c")
    _edge(g, "a", "d")
    _edge(g, "b", "c")
    _edge(g, "b", "d")
    assert two_layer_cross_count(g, ["a", "b"], ["c", "d"]) == 1.0


def test_two_layer_fully_inverted_three_edges() -> None:
    """Three fully-inverted edges -> C(3, 2) = 3 crossings (the maximum).

    ``a -> f`` / ``b -> e`` / ``c -> d`` -- every pair crosses. The
    complete binary tree must span south positions 0-2, so ``first_index``
    promotes to the next power of two (4); the C(3, 2) = 3 result
    certifies the ``first_index`` / ``tree_size`` math.
    """
    g = _make_graph()
    for v in ("a", "b", "c", "d", "e", "f"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "f")
    _edge(g, "b", "e")
    _edge(g, "c", "d")
    assert two_layer_cross_count(g, ["a", "b", "c"], ["d", "e", "f"]) == 3.0


def test_two_layer_weighted_crossing() -> None:
    """Weighted crossing: ``a -> d`` (w=2) x ``b -> c`` (w=3) -> 2 * 3 = 6.

    The ``cc += weight * weight_sum`` accumulation: the second edge (w=3)
    sees the first edge's weight (2) already in the tree, so 3 * 2 = 6.
    """
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "d", weight=2.0)
    _edge(g, "b", "c", weight=3.0)
    assert two_layer_cross_count(g, ["a", "b"], ["c", "d"]) == 6.0


def test_two_layer_skips_edge_with_south_not_in_layer() -> None:
    """An edge whose south endpoint is absent from ``south_layer`` is skipped.

    ``a -> x`` does not cross the boundary into ``south_layer = [b]`` (x is
    not positioned), so it contributes nothing; only ``a -> b`` counts.
    """
    g = _make_graph()
    for v in ("a", "b", "x"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "b")
    _edge(g, "a", "x")
    # x is not in south_layer -> its edge is dropped; a single in-layer edge.
    assert two_layer_cross_count(g, ["a"], ["b"]) == 0.0


def test_two_layer_none_weight_counts_zero() -> None:
    """A ``None`` edge weight counts as 0.0 (grok ``.unwrap_or(0.0)``).

    Even though ``a -> d`` and ``b -> c`` would normally cross once, the
    ``a -> d`` edge has ``weight = None`` so it contributes 0.0; the
    ``b -> c`` edge sees weight 0 already in the tree, so 1 * 0 = 0.
    """
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "d", weight=None)
    _edge(g, "b", "c", weight=1.0)
    assert two_layer_cross_count(g, ["a", "b"], ["c", "d"]) == 0.0


def test_two_layer_empty_north_layer() -> None:
    """An empty north layer yields no entries -> 0.0."""
    g = _make_graph()
    g.set_node("b", GraphNode())
    assert two_layer_cross_count(g, [], ["b"]) == 0.0


def test_two_layer_empty_south_layer() -> None:
    """An empty south layer yields no positioned edges -> 0.0.

    ``first_index`` stays at 1 (the ``while first_index < 0`` loop never
    runs), ``tree_size`` is 1, and every edge's south endpoint is absent
    from the empty ``south_pos`` map, so all are skipped.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    _edge(g, "a", "b")
    assert two_layer_cross_count(g, ["a"], []) == 0.0


def test_two_layer_does_not_mutate_graph() -> None:
    """two_layer_cross_count reads edges only; the graph is unchanged."""
    g = _make_graph()
    for v in ("a", "b", "c", "d"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "d")
    _edge(g, "b", "c")
    snapshot = {n: g.node(n) for n in g.nodes()}
    edge_snapshot = {e: g.edge_with_obj(e) for e in g.edges()}
    two_layer_cross_count(g, ["a", "b"], ["c", "d"])
    assert {n: g.node(n) for n in g.nodes()} == snapshot
    assert {e: g.edge_with_obj(e) for e in g.edges()} == edge_snapshot


# === cross_count =======================================================


def test_cross_count_empty_layering() -> None:
    """An empty layering has no adjacent pair -> 0.0."""
    g = _make_graph()
    assert cross_count(g, []) == 0.0


def test_cross_count_single_layer() -> None:
    """A single layer has no adjacent pair -> 0.0."""
    g = _make_graph()
    g.set_node("a", GraphNode())
    assert cross_count(g, [["a"]]) == 0.0


def test_cross_count_linear_chain_no_crossing() -> None:
    """``a -> b -> c`` across three layers crosses nothing."""
    g = _make_graph()
    for v in ("a", "b", "c"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "b")
    _edge(g, "b", "c")
    assert cross_count(g, [["a"], ["b"], ["c"]]) == 0.0


def test_cross_count_accumulates_across_layers() -> None:
    """Two adjacent pairs each crossing once -> 2.0 total.

    Layer 0 -> 1: ``a -> d`` + ``b -> c`` (one crossing). Layer 1 -> 2:
    ``c -> f`` + ``d -> e`` (one crossing). ``cross_count`` sums both.
    """
    g = _make_graph()
    for v in ("a", "b", "c", "d", "e", "f"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "d")
    _edge(g, "b", "c")
    _edge(g, "c", "f")
    _edge(g, "d", "e")
    layering = [["a", "b"], ["c", "d"], ["e", "f"]]
    assert cross_count(g, layering) == 2.0


def test_cross_count_does_not_mutate_graph() -> None:
    """cross_count reads edges only; the graph is unchanged."""
    g = _make_graph()
    for v in ("a", "b", "c"):
        g.set_node(v, GraphNode())
    _edge(g, "a", "b")
    _edge(g, "b", "c")
    snapshot = {n: g.node(n) for n in g.nodes()}
    cross_count(g, [["a"], ["b"], ["c"]])
    assert {n: g.node(n) for n in g.nodes()} == snapshot


# === barrel surface contract ===========================================


def test_cross_count_not_in_dagre_all() -> None:
    """``cross_count`` earns no crate-root barrel slot."""
    assert "cross_count" not in dagre.__all__
    assert "two_layer_cross_count" not in dagre.__all__


def test_cross_count_not_reachable_at_dagre_top_level() -> None:
    """Neither symbol is bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "cross_count")
    assert not hasattr(dagre, "two_layer_cross_count")


def test_cross_count_submodule_exports_two_symbols() -> None:
    """The submodule exposes exactly the two pub fns (ASCII-sorted)."""
    import minimax_code.dagre.layout.order.cross_count as cc_mod

    assert cc_mod.__all__ == ["cross_count", "two_layer_cross_count"]
    assert cc_mod.cross_count is cross_count
    assert cc_mod.two_layer_cross_count is two_layer_cross_count


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R259 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_both() -> None:
    """The ``order`` sub-package barrel re-exports ``cross_count`` + ``init_order``.

    Synchronised at R260 (``barycenter`` added), R261
    (``resolve_conflicts`` added), then R262 (``build_layer_graph``
    added): the barrel now re-exports all five ``order`` leaves, so
    ``__all__`` grew ``["cross_count", "init_order"]`` -> ``["barycenter",
    "cross_count", "init_order"]`` -> ``["barycenter", "cross_count",
    "init_order", "resolve_conflicts"]`` -> the ASCII-sorted
    ``["barycenter", "build_layer_graph", "cross_count", "init_order",
    "resolve_conflicts"]``. This test pins ``cross_count`` +
    ``init_order`` (the two it reaches for); the full five-element
    ``__all__`` is locked here too. Mirrors the R255 -> R254 / R257 ->
    R254 / R259 -> R258 barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod

    assert order_pkg.__all__ == [
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "resolve_conflicts",
    ]
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod


def test_cross_count_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.cross_count.cross_count is cross_count
