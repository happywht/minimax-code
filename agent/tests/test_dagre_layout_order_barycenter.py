"""Black-box tests for the migrated dagre order/barycenter layer (R260).

Exercises :mod:`minimax_code.dagre.layout.order.barycenter` through its
public entry point :func:`barycenter` (and the :class:`Barycenter`
frozen value type) on a real ``Graph<GraphConfig, GraphNode, GraphEdge>``
(the state ``order`` runs on, immediately after ``init_order`` stamps
every node with an ``order`` field). Covers:

* :func:`barycenter` end-to-end on a node with no in-edges (``None`` /
  ``None``), a single in-edge (the source ``order`` propagates), a
  weighted two-in-edge mean (``(0*1 + 4*3) / 4 = 3.0``), a ``None`` edge
  weight (counts as ``0.0`` -- the source contributes nothing), a
  ``None`` source ``order`` (counts as ``0``), the degenerate
  all-zero-weight case (``NaN`` -- grok's ``0.0 / 0.0`` reproduced
  faithfully rather than raising ``ZeroDivisionError``), an empty
  ``movable`` list, ``movable`` order preservation across a mixed batch,
  and graph immutability,
* the :class:`Barycenter` frozen dataclass contract (equality on
  identical fields; ``NaN`` does NOT equal itself, so the all-zero case
  is asserted via :func:`math.isnan`),
* the barrel surface contract (``barycenter`` / ``Barycenter`` stay out
  of ``dagre.__all__``; the crate-root barrel count is unchanged at 4;
  the ``order`` sub-package barrel re-exports ``barycenter`` +
  ``cross_count`` + ``init_order``; ``barycenter.__all__`` is
  ASCII-sorted with the class before the function).
"""

from __future__ import annotations

import math

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.barycenter import Barycenter, barycenter
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty directed compound graph (the state ``order`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``order`` runs on (the post-``init_order`` graph). ``barycenter`` reads
    ``in_edges`` + ``edge_with_obj`` + ``edge.weight`` + ``node.order``
    only; the GraphConfig dressing keeps the fixture faithful to the real
    pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _edge(g: Graph, v: str, w: str, weight: float | None = 1.0) -> None:
    """Add ``v -> w`` carrying the given weight (default 1.0, ``None`` allowed).

    ``barycenter`` consumes ``in_edges`` of the movable node ``w``, so
    ``_edge(g, "b", "a")`` makes ``b`` an in-edge source of ``a``.
    """
    g.set_edge(v, w, GraphEdge(weight=weight), None)


# === barycenter: end-to-end ============================================


def test_barycenter_no_in_edges_returns_none() -> None:
    """A node with no in-edge has no defined barycenter (``None`` / ``None``)."""
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    assert barycenter(g, ["a"]) == [Barycenter(v="a", barycenter=None, weight=None)]


def test_barycenter_single_in_edge_propagates_source_order() -> None:
    """A lone ``b -> a`` edge (source order 2, weight 1) yields barycenter 2.0.

    ``sum = 1 * 2``, ``weight = 1`` -> ``2.0 / 1.0 = 2.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=2))
    _edge(g, "b", "a")
    assert barycenter(g, ["a"]) == [Barycenter(v="a", barycenter=2.0, weight=1.0)]


def test_barycenter_weighted_mean_two_in_edges() -> None:
    """Two in-edges (order 0 w 1, order 4 w 3) -> ``(0*1 + 4*3) / 4 = 3.0``.

    ``sum = 0 + 12 = 12``, ``weight = 1 + 3 = 4`` -> ``12.0 / 4.0 = 3.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=0))
    g.set_node("c", GraphNode(order=4))
    _edge(g, "b", "a", weight=1.0)
    _edge(g, "c", "a", weight=3.0)
    assert barycenter(g, ["a"]) == [Barycenter(v="a", barycenter=3.0, weight=4.0)]


def test_barycenter_none_edge_weight_counts_zero() -> None:
    """A ``None`` edge weight counts as ``0.0`` (the source contributes nothing).

    ``b -> a`` (order 2, w 1) + ``c -> a`` (order 10, w ``None``): the
    ``c`` edge contributes ``0 * 10 = 0`` to ``sum`` and ``0`` to
    ``weight`` -> ``sum = 2``, ``weight = 1`` -> ``2.0 / 1.0 = 2.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=2))
    g.set_node("c", GraphNode(order=10))
    _edge(g, "b", "a", weight=1.0)
    _edge(g, "c", "a", weight=None)
    assert barycenter(g, ["a"]) == [Barycenter(v="a", barycenter=2.0, weight=1.0)]


def test_barycenter_none_source_order_counts_zero() -> None:
    """A ``None`` source ``order`` counts as ``0`` (grok ``unwrap_or(0)``).

    ``b -> a`` (order ``None``, w 1) + ``c -> a`` (order 5, w 1): the
    ``b`` source contributes ``1 * 0 = 0`` to ``sum`` -> ``sum = 5``,
    ``weight = 2`` -> ``5.0 / 2.0 = 2.5``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode())  # order None
    g.set_node("c", GraphNode(order=5))
    _edge(g, "b", "a", weight=1.0)
    _edge(g, "c", "a", weight=1.0)
    assert barycenter(g, ["a"]) == [Barycenter(v="a", barycenter=2.5, weight=2.0)]


def test_barycenter_all_zero_weight_is_nan() -> None:
    """Every in-edge ``weight = None`` -> aggregated ``0.0`` -> ``NaN``.

    grok computes ``(sum / 0.0_f64) as f32`` = ``NaN``; Python
    ``0.0 / 0.0`` raises ``ZeroDivisionError``, so the port reproduces
    the ``NaN`` faithfully (the defensive widening). The ``weight`` field
    still reports the aggregated ``0.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=3))
    g.set_node("c", GraphNode(order=5))
    _edge(g, "b", "a", weight=None)
    _edge(g, "c", "a", weight=None)
    result = barycenter(g, ["a"])
    assert len(result) == 1
    assert result[0].v == "a"
    assert result[0].weight == 0.0
    assert math.isnan(result[0].barycenter)


def test_barycenter_empty_movable_returns_empty() -> None:
    """An empty ``movable`` list yields an empty result list."""
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    assert barycenter(g, []) == []


def test_barycenter_preserves_movable_order() -> None:
    """The result list mirrors ``movable`` order (one entry per input id).

    ``movable = ["a", "b"]``: ``a`` has an in-edge (barycenter 2.0);
    ``b`` has none (``None`` / ``None``). The result preserves the
    input order regardless of each node's barycenter state.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=5))
    g.set_node("src", GraphNode(order=2))
    _edge(g, "src", "a")  # a has an in-edge; b does not
    result = barycenter(g, ["a", "b"])
    assert result == [
        Barycenter(v="a", barycenter=2.0, weight=1.0),
        Barycenter(v="b", barycenter=None, weight=None),
    ]


def test_barycenter_does_not_mutate_graph() -> None:
    """barycenter reads ``order`` + ``weight`` only; the graph is unchanged."""
    g = _make_graph()
    g.set_node("a", GraphNode(order=0))
    g.set_node("b", GraphNode(order=2))
    _edge(g, "b", "a")
    node_snapshot = {n: g.node(n).order for n in g.nodes()}
    edge_snapshot = {e: g.edge_with_obj(e).weight for e in g.edges()}
    barycenter(g, ["a"])
    assert {n: g.node(n).order for n in g.nodes()} == node_snapshot
    assert {e: g.edge_with_obj(e).weight for e in g.edges()} == edge_snapshot


# === barrel surface contract ===========================================


def test_barycenter_not_in_dagre_all() -> None:
    """``barycenter`` / ``Barycenter`` earn no crate-root barrel slot."""
    assert "barycenter" not in dagre.__all__
    assert "Barycenter" not in dagre.__all__


def test_barycenter_not_reachable_at_dagre_top_level() -> None:
    """Neither symbol is bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "barycenter")
    assert not hasattr(dagre, "Barycenter")


def test_barycenter_submodule_exports_symbols() -> None:
    """The submodule exposes the class + the fn (ASCII-sorted, class first)."""
    import minimax_code.dagre.layout.order.barycenter as bc_mod

    assert bc_mod.__all__ == ["Barycenter", "barycenter"]
    assert bc_mod.Barycenter is Barycenter
    assert bc_mod.barycenter is barycenter


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R260 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_three() -> None:
    """The ``order`` sub-package barrel re-exports ``barycenter`` + ``cross_count`` + ``init_order``.

    Synchronised at R260: the barrel now also re-exports ``barycenter``
    (the third ``order`` leaf, the barycentric heuristic weight), so
    ``__all__`` grew from ``["cross_count", "init_order"]`` to the
    ASCII-sorted ``["barycenter", "cross_count", "init_order"]``. Mirrors
    the R255 -> R254 / R257 -> R254 / R259 -> R258 barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.barycenter as bc_mod
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod

    assert order_pkg.__all__ == ["barycenter", "cross_count", "init_order"]
    assert order_pkg.barycenter is bc_mod
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod


def test_barycenter_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.barycenter.barycenter is barycenter
