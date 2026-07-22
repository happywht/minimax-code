"""Black-box tests for the migrated dagre layout utility layer (R248).

Exercises :mod:`minimax_code.dagre.layout.util` purely through its 15 public
entry points on real ``Graph<GraphConfig, GraphNode, GraphEdge>`` instances.
Covers:

* ``unique_id`` monotonic sequence (AtomicUsize -> itertools.count analogue),
* ``add_dummy_node`` id minting + ``dummy`` tag + caller-data independence,
* ``simplify`` multi-edge aggregation (``weight`` sum, ``minlen`` max) + the
  fresh directed / non-multigraph / non-compound projection + deep-clone
  independence from the source graph,
* ``simplify_ref`` in-place ``None`` -> default normalisation (``weight=0.0``,
  ``minlen=1.0``),
* ``as_non_compound_graph`` leaf-only projection (compound parents dropped),
* ``transfer_node_edge_labels`` leaf + edge ferrying onto a destination graph,
* ``intersect_rect`` rectangle-ray intersection incl. the grok centre-point
  case ``(10, 20) -> (14, 20)``,
* ``build_layer_matrix`` per-rank bucketing ordered by ``node.order``,
* ``normalize_ranks`` minimum-to-zero shift,
* ``remove_empty_ranks`` empty-rank compaction under ``node_rank_factor``,
* ``add_border_node`` rank/order stamping + ``"border"`` dummy delegation,
* ``max_rank`` maximum across nodes (``None`` -> 0 fallback),
* ``partition`` predicate split into ``lhs`` / ``rhs``,
* the ``Rect`` / ``PartitionResponse`` struct shapes,
* the barrel surface contract (the 15 symbols stay out of ``dagre.__all__``,
  reachable only via ``dagre.layout.util``; crate-root barrel count unchanged).
"""

from __future__ import annotations

import pytest

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.dagre.layout.util import (
    PartitionResponse,
    Rect,
    add_border_node,
    add_dummy_node,
    as_non_compound_graph,
    build_layer_matrix,
    intersect_rect,
    max_rank,
    normalize_ranks,
    partition,
    remove_empty_ranks,
    simplify,
    simplify_ref,
    transfer_node_edge_labels,
    unique_id,
)
from minimax_code.data_structures import Graph, GraphOption


def _make_simple_graph() -> Graph:
    """Build a 2-node 1-edge directed graph with concrete ranks for rank tests.

    Node ``a`` at ``rank=0`` / ``order=0``, node ``b`` at ``rank=1`` / ``order=0``,
    one edge ``a->b``. Used by ``build_layer_matrix`` / ``max_rank`` /
    ``normalize_ranks`` / ``remove_empty_ranks``.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=0, order=0))
    g.set_node("b", GraphNode(rank=1, order=0))
    g.set_edge("a", "b", GraphEdge(), None)
    return g


def _make_compound_graph() -> Graph:
    """Build a compound graph: ``root`` parents two leaves ``a`` / ``b``.

    ``children("root") == ["a", "b"]`` and ``children("a") == []`` / ``children("b")
    == []``, so ``as_non_compound_graph`` / ``transfer_node_edge_labels`` drop
    ``root`` and keep only the leaves. Leaves carry ranks so the same graph also
    feeds the rank helpers.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("root", GraphNode())
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_parent("a", "root")
    g.set_parent("b", "root")
    g.set_edge("a", "b", GraphEdge(), None)
    return g


def _make_multigraph() -> Graph:
    """Build a multigraph with two parallel ``a->b`` edges for ``simplify``.

    Edge ``e1`` carries ``weight=2.0`` / ``minlen=1.0``; edge ``e2`` carries
    ``weight=3.0`` / ``minlen=2.0``. ``simplify`` must collapse them into one
    edge with ``weight=5.0`` (sum) and ``minlen=2.0`` (max).
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=2.0, minlen=1.0), "e1")
    g.set_edge("a", "b", GraphEdge(weight=3.0, minlen=2.0), "e2")
    return g


# === unique_id (AtomicUsize -> itertools.count analogue) ====================


def test_unique_id_is_monotonic_and_starts_positive() -> None:
    """``unique_id`` returns a strictly increasing sequence (grok fetch_add +1).

    Only relative monotonicity is asserted (not absolute values): the counter is
    module-global and shared across the whole test session, mirroring grok's
    ``static UNIQUE_STARTER``.
    """
    values = [unique_id() for _ in range(5)]
    # ``strict=False`` because the two slices are intentionally off-by-one
    # (each value is compared against its successor).
    assert all(b > a for a, b in zip(values, values[1:], strict=False))
    assert values[0] >= 1


# === add_dummy_node =========================================================


def test_add_dummy_node_mints_prefix_id_and_stamps_dummy_tag() -> None:
    """``add_dummy_node`` returns ``f"{name}{unique_id()}"`` and stamps ``dummy``.

    The caller's ``data`` object is deep-cloned before stamping so the original
    is left untouched (grok ``data.clone()`` graph-independence contract).
    """
    g = _make_simple_graph()
    data = GraphNode(width=10.0, height=20.0)
    node_id = add_dummy_node(g, "test-type", data, "d-")

    assert node_id.startswith("d-")
    assert g.has_node(node_id)
    stored = g.node(node_id)
    assert stored is not None
    assert stored.dummy == "test-type"
    assert stored.width == 10.0 and stored.height == 20.0
    # Caller's original object is untouched (deep-copy independence).
    assert data.dummy is None


def test_add_dummy_node_mints_unique_ids() -> None:
    """Two calls mint two distinct node ids (mirrors grok collision-free loop)."""
    g = _make_simple_graph()
    id1 = add_dummy_node(g, "x", GraphNode(), "n-")
    id2 = add_dummy_node(g, "x", GraphNode(), "n-")
    assert id1 != id2
    assert g.has_node(id1) and g.has_node(id2)


# === simplify (multi-edge aggregation) ======================================


def test_simplify_collapses_parallel_edges_weight_sum_minlen_max() -> None:
    """``simplify`` aggregates parallel edges: ``weight`` sums, ``minlen`` maxes."""
    g = _make_multigraph()
    simplified = simplify(g)

    # The projection is a simple (non-multigraph) graph; the single a->b edge
    # carries the aggregated label.
    edge = simplified.edge("a", "b", None)
    assert edge is not None
    assert edge.weight == 5.0  # 2.0 + 3.0
    assert edge.minlen == 2.0  # max(1.0, 2.0)


def test_simplify_copies_node_labels_and_is_independent() -> None:
    """``simplify`` deep-clones node labels so the projection is independent."""
    g = _make_simple_graph()
    g.node("a").width = 42.0  # type: ignore[union-attr]
    simplified = simplify(g)

    src_a = g.node("a")
    dst_a = simplified.node("a")
    assert src_a is not None and dst_a is not None
    assert dst_a is not src_a  # deep-cloned, not shared
    assert dst_a.width == 42.0
    # Mutating the projection does not leak back into the source.
    dst_a.width = 99.0
    assert src_a.width == 42.0


def test_simplify_preserves_graph_label() -> None:
    """``simplify`` deep-clones the source graph label onto the projection."""
    g = _make_simple_graph()
    g.set_graph(GraphConfig(rankdir="lr"))
    simplified = simplify(g)

    config = simplified.graph()
    assert config is not None
    assert config.rankdir == "lr"
    # Independent clone (mutating projection label does not touch source).
    src_config = g.graph()
    assert src_config is not None
    config.rankdir = "tb"
    assert src_config.rankdir == "lr"


def test_simplify_default_weight_minlen_when_label_missing() -> None:
    """An edge whose parallel partner has no ``weight`` / ``minlen`` defaults to 0/1.

    Mirrors grok's ``unwrap_or`` fallback inside the aggregation: a ``None``
    ``weight`` contributes ``0.0`` and a ``None`` ``minlen`` contributes ``1.0``.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=None, minlen=None), "e1")
    simplified = simplify(g)
    edge = simplified.edge("a", "b", None)
    assert edge is not None
    assert edge.weight == 0.0
    assert edge.minlen == 1.0


# === simplify_ref (in-place None -> default) ================================


def test_simplify_ref_normalises_none_weight_and_minlen_in_place() -> None:
    """``simplify_ref`` sets ``weight=0.0`` / ``minlen=1.0`` on every edge in place."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=None, minlen=None), None)

    simplify_ref(g)
    edge = g.edge("a", "b", None)
    assert edge is not None
    assert edge.weight == 0.0
    assert edge.minlen == 1.0


def test_simplify_ref_leaves_concrete_values_untouched() -> None:
    """``simplify_ref`` does not overwrite a concrete ``weight`` / ``minlen``."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=7.0, minlen=4.0), None)

    simplify_ref(g)
    edge = g.edge("a", "b", None)
    assert edge is not None
    assert edge.weight == 7.0
    assert edge.minlen == 4.0


# === as_non_compound_graph (leaf-only projection) ===========================


def test_as_non_compound_graph_drops_compound_parents_keeps_leaves() -> None:
    """``as_non_compound_graph`` copies only leaf nodes (``children`` empty).

    The compound parent ``root`` is dropped; leaves ``a`` / ``b`` and the edge
    ``a->b`` survive. Mirrors grok's ``g.children(&v).len() == 0`` guard.
    """
    g = _make_compound_graph()
    simplified = as_non_compound_graph(g)

    assert not simplified.has_node("root")
    assert simplified.has_node("a")
    assert simplified.has_node("b")
    assert simplified.edge("a", "b", None) is not None


def test_as_non_compound_graph_is_independent_deep_clone() -> None:
    """Labels in the projection are deep clones (mutating them is isolated)."""
    g = _make_compound_graph()
    simplified = as_non_compound_graph(g)

    src_a = g.node("a")
    dst_a = simplified.node("a")
    assert src_a is not None and dst_a is not None
    assert dst_a is not src_a
    dst_a.rank = 99
    assert src_a.rank == 0


def test_as_non_compound_graph_preserves_graph_label() -> None:
    """The projection inherits a deep clone of the source graph label."""
    g = _make_compound_graph()
    g.set_graph(GraphConfig(rankdir="lr"))
    simplified = as_non_compound_graph(g)
    config = simplified.graph()
    assert config is not None
    assert config.rankdir == "lr"


# === transfer_node_edge_labels ==============================================


def test_transfer_node_edge_labels_copies_leaves_and_edges() -> None:
    """``transfer_node_edge_labels`` ferries leaf labels + edges onto destination."""
    source = _make_compound_graph()
    destination: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )

    transfer_node_edge_labels(source, destination)
    assert not destination.has_node("root")  # parent dropped
    assert destination.has_node("a") and destination.has_node("b")
    assert destination.edge("a", "b", None) is not None


def test_transfer_node_edge_labels_is_independent() -> None:
    """Transferred labels are deep clones (mutating destination is isolated)."""
    source = _make_compound_graph()
    destination: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    transfer_node_edge_labels(source, destination)

    src_a = source.node("a")
    dst_a = destination.node("a")
    assert src_a is not None and dst_a is not None
    assert dst_a is not src_a
    dst_a.rank = 77
    assert src_a.rank == 0


# === intersect_rect (rectangle-ray intersection) ============================


def test_intersect_rect_center_point_returns_right_edge_midpoint() -> None:
    """A query at the rect centre exits at the right-edge midpoint (grok test case).

    Mirrors grok ``intersect_rect_returns_boundary_point_for_center_point``:
    rect ``(x=10, y=20, w=8, h=4)`` queried at its centre ``(10, 20)`` yields
    ``(14, 20)`` (the ``x + width/2`` point on the right edge).
    """
    rect = Rect(x=10.0, y=20.0, width=8.0, height=4.0)
    result = intersect_rect(rect, GraphEdgePoint(10.0, 20.0))
    assert result.x == pytest.approx(14.0)
    assert result.y == pytest.approx(20.0)


def test_intersect_rect_point_above_exits_top_edge() -> None:
    """A query directly above the centre exits at the top-edge midpoint.

    Point ``(10, 30)`` -> ``dy=10``, ``|dy|*w > |dx|*h`` -> top/bottom branch,
    ``dy > 0`` -> ``sy = +h`` -> exit ``(10, 22)``.
    """
    rect = Rect(x=10.0, y=20.0, width=8.0, height=4.0)
    result = intersect_rect(rect, GraphEdgePoint(10.0, 30.0))
    assert result.x == pytest.approx(10.0)
    assert result.y == pytest.approx(22.0)


def test_intersect_rect_point_right_exits_right_edge() -> None:
    """A query directly right of the centre exits at the right-edge midpoint.

    Point ``(20, 20)`` -> ``dx=10``, ``|dy|*w <= |dx|*h`` -> left/right branch,
    ``dx > 0`` -> ``sx = +w`` -> exit ``(14, 20)``.
    """
    rect = Rect(x=10.0, y=20.0, width=8.0, height=4.0)
    result = intersect_rect(rect, GraphEdgePoint(20.0, 20.0))
    assert result.x == pytest.approx(14.0)
    assert result.y == pytest.approx(20.0)


def test_intersect_rect_diagonal_point_picks_dominant_axis() -> None:
    """A diagonal query ``(14, 22)`` exits at the corner ``(14, 22)``.

    ``dx=4`` / ``dy=2`` -> ``|dy|*w == |dx|*h`` (8 == 8), so the strict ``>``
    falls through to the left/right branch, exiting at ``(14, 22)``.
    """
    rect = Rect(x=10.0, y=20.0, width=8.0, height=4.0)
    result = intersect_rect(rect, GraphEdgePoint(14.0, 22.0))
    assert result.x == pytest.approx(14.0)
    assert result.y == pytest.approx(22.0)


def test_intersect_rect_point_left_exits_left_edge() -> None:
    """A query directly left of the centre exits at the left-edge midpoint.

    Point ``(0, 20)`` -> ``dx=-10``, left/right branch, ``dx < 0`` ->
    ``sx = -w`` -> exit ``(6, 20)``.
    """
    rect = Rect(x=10.0, y=20.0, width=8.0, height=4.0)
    result = intersect_rect(rect, GraphEdgePoint(0.0, 20.0))
    assert result.x == pytest.approx(6.0)
    assert result.y == pytest.approx(20.0)


# === build_layer_matrix =====================================================


def test_build_layer_matrix_buckets_by_rank_sorted_by_order() -> None:
    """``build_layer_matrix`` groups nodes into per-rank layers ordered by ``order``."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    # rank 0: order=1 "a", order=0 "b"  -> layer ["b", "a"] (sorted by order)
    # rank 1: order=0 "c"
    g.set_node("a", GraphNode(rank=0, order=1))
    g.set_node("b", GraphNode(rank=0, order=0))
    g.set_node("c", GraphNode(rank=1, order=0))

    matrix = build_layer_matrix(g)
    assert matrix == [["b", "a"], ["c"]]


def test_build_layer_matrix_skips_nodes_without_rank() -> None:
    """A node with ``rank=None`` is dropped from the matrix."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=0, order=0))
    g.set_node("b", GraphNode(rank=None, order=0))
    matrix = build_layer_matrix(g)
    assert matrix == [["a"]]


# === normalize_ranks ========================================================


def test_normalize_ranks_shifts_minimum_to_zero() -> None:
    """``normalize_ranks`` subtracts the minimum rank so the lowest becomes 0."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=2))
    g.set_node("b", GraphNode(rank=3))
    g.set_node("c", GraphNode(rank=5))

    normalize_ranks(g)
    assert g.node("a").rank == 0  # type: ignore[union-attr]
    assert g.node("b").rank == 1  # type: ignore[union-attr]
    assert g.node("c").rank == 3  # type: ignore[union-attr]


def test_normalize_ranks_skips_none_rank_nodes() -> None:
    """A ``rank=None`` node stays ``None`` but contributes ``0`` to the minimum.

    grok collects every node's rank with ``unwrap_or(0)`` (a ``None`` rank
    counts as ``0`` for the minimum), then the assignment loop skips nodes
    whose rank is ``None``. With ``a=5`` / ``b=-1`` / ``c=None`` the minimum
    is ``-1`` so every concrete rank shifts by ``+1``; ``c`` is never assigned
    and stays ``None``.
    """
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=5))
    g.set_node("b", GraphNode(rank=-1))
    g.set_node("c", GraphNode(rank=None))

    normalize_ranks(g)
    assert g.node("a").rank == 6  # 5 - (-1)  # type: ignore[union-attr]
    assert g.node("b").rank == 0  # -1 - (-1)  # type: ignore[union-attr]
    assert g.node("c").rank is None  # skipped in the assignment loop  # type: ignore[union-attr]


# === remove_empty_ranks =====================================================


def test_remove_empty_ranks_compacts_non_factor_empty_layers() -> None:
    """Empty ranks not aligned to ``node_rank_factor`` are compacted out.

    Graph ``a(rank=0)`` / ``b(rank=2)`` with ``node_rank_factor=2``:
    layers ``[[a], [], [b]]``. Layer 1 is empty and ``1 % 2 != 0`` -> ``delta``
    drops to ``-1``, so ``b.rank`` shifts ``2 -> 1``. Layer 0 (factor-aligned)
    and ``a`` are untouched.
    """
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=2))
    g.set_graph(GraphConfig(node_rank_factor=2.0))

    remove_empty_ranks(g)
    assert g.node("a").rank == 0  # type: ignore[union-attr]
    assert g.node("b").rank == 1  # type: ignore[union-attr]


def test_remove_empty_ranks_noop_when_factor_zero() -> None:
    """A non-positive ``node_rank_factor`` makes ``remove_empty_ranks`` a no-op."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=2))
    g.set_graph(GraphConfig(node_rank_factor=None))

    remove_empty_ranks(g)
    assert g.node("a").rank == 0  # type: ignore[union-attr]
    assert g.node("b").rank == 2  # type: ignore[union-attr]


def test_remove_empty_ranks_empty_graph_is_noop() -> None:
    """An empty graph short-circuits (grok ``if _nodes.is_empty() return``)."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(node_rank_factor=2.0))
    remove_empty_ranks(g)  # must not raise
    assert g.nodes() == []


# === add_border_node ========================================================


def test_add_border_node_stamps_rank_order_and_dummy_type() -> None:
    """``add_border_node`` injects a ``"border"`` dummy with rank/order set."""
    g = _make_simple_graph()
    node_id = add_border_node(g, "_bt", rank=2, order=1)

    assert node_id.startswith("_bt")
    stored = g.node(node_id)
    assert stored is not None
    assert stored.dummy == "border"
    assert stored.rank == 2
    assert stored.order == 1


def test_add_border_node_without_rank_order_leaves_them_unset() -> None:
    """Omitted ``rank`` / ``order`` stay at the default (``None``)."""
    g = _make_simple_graph()
    node_id = add_border_node(g, "_bt")

    stored = g.node(node_id)
    assert stored is not None
    assert stored.dummy == "border"
    assert stored.rank is None
    assert stored.order is None


# === max_rank ===============================================================


def test_max_rank_returns_maximum_rank() -> None:
    """``max_rank`` returns the largest ``rank`` across all nodes."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=2))
    g.set_node("b", GraphNode(rank=5))
    g.set_node("c", GraphNode(rank=3))
    assert max_rank(g) == 5


def test_max_rank_falls_back_to_zero_when_no_ranks() -> None:
    """If no node carries a ``rank``, ``max_rank`` returns ``0`` (grok unwrap_or)."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_node("a", GraphNode(rank=None))
    g.set_node("b", GraphNode(rank=None))
    assert max_rank(g) == 0


def test_max_rank_empty_graph_returns_zero() -> None:
    """An empty graph yields ``0`` (grok ``max().unwrap_or(0)``)."""
    g: Graph = Graph(
        GraphOption(directed=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    assert max_rank(g) == 0


# === partition ==============================================================


def test_partition_splits_by_predicate() -> None:
    """``partition`` routes ``True`` -> ``lhs``, ``False`` -> ``rhs``."""
    result = partition([1, 2, 3, 4, 5], lambda x: x > 2)
    assert result.lhs == [3, 4, 5]
    assert result.rhs == [1, 2]


def test_partition_empty_collection() -> None:
    """An empty collection yields empty ``lhs`` / ``rhs`` lists."""
    result: PartitionResponse[int] = partition([], lambda x: bool(x))
    assert result.lhs == []
    assert result.rhs == []


def test_partition_all_true_or_all_false() -> None:
    """If the predicate is constant, one side collects everything."""
    all_true = partition([1, 2, 3], lambda _: True)
    assert all_true.lhs == [1, 2, 3] and all_true.rhs == []
    all_false = partition([1, 2, 3], lambda _: False)
    assert all_false.lhs == [] and all_false.rhs == [1, 2, 3]


# === struct shapes ==========================================================


def test_rect_carries_four_float_fields() -> None:
    """``Rect`` is a four-field mutable slotted dataclass (mirrors grok ``pub struct``)."""
    r = Rect(x=1.0, y=2.0, width=3.0, height=4.0)
    assert r.x == 1.0 and r.y == 2.0 and r.width == 3.0 and r.height == 4.0
    # Mutable (grok fields are ``pub``, no ``Default``).
    r.x = 10.0
    assert r.x == 10.0


def test_partition_response_carries_lhs_rhs() -> None:
    """``PartitionResponse`` carries two generic lists."""
    p: PartitionResponse[str] = PartitionResponse(lhs=["a"], rhs=["b"])
    assert p.lhs == ["a"]
    assert p.rhs == ["b"]


# === barrel surface contract ================================================


def test_util_symbols_not_in_dagre_all() -> None:
    """The 15 util symbols are NOT in the crate-root barrel (grok keeps them
    module-private to ``layout::util``; only the four type structs earn a
    crate-root ``pub use``)."""
    for symbol in (
        "simplify",
        "simplify_ref",
        "unique_id",
        "intersect_rect",
        "as_non_compound_graph",
        "transfer_node_edge_labels",
        "build_layer_matrix",
        "normalize_ranks",
        "remove_empty_ranks",
        "add_border_node",
        "add_dummy_node",
        "max_rank",
        "partition",
        "Rect",
        "PartitionResponse",
    ):
        assert symbol not in dagre.__all__


def test_util_symbols_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level -- must
    import from ``dagre.layout.util``."""
    for symbol in ("simplify", "unique_id", "intersect_rect", "Rect", "partition"):
        assert not hasattr(dagre, symbol)


def test_util_submodule_all_exports() -> None:
    """The submodule exposes exactly the 15 ``pub`` symbols (ASCII-sorted)."""
    import minimax_code.dagre.layout.util as util

    assert util.__all__ == [
        "PartitionResponse",
        "Rect",
        "add_border_node",
        "add_dummy_node",
        "as_non_compound_graph",
        "build_layer_matrix",
        "intersect_rect",
        "max_rank",
        "normalize_ranks",
        "partition",
        "remove_empty_ranks",
        "simplify",
        "simplify_ref",
        "transfer_node_edge_labels",
        "unique_id",
    ]
    assert util.simplify is simplify
    assert util.intersect_rect is intersect_rect
    assert util.Rect is Rect


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R248 adds no crate-root barrel symbols (``util`` stays module-private);
    the barrel count set by R246 and preserved through R247 is unchanged at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_util_import() -> None:
    """Importing ``util`` binds the ``layout`` subpackage on ``dagre``, so
    ``dagre.layout.util`` is reachable after the import."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.util as util

    assert dagre.layout is layout_pkg
    assert layout_pkg.util is util


# === integration: the four R246 types flow through util =====================


def test_util_consumes_full_dagre_type_specialisation() -> None:
    """End-to-end type-level check: the helpers operate on the concrete
    ``Graph<GraphConfig, GraphNode, GraphEdge>`` specialisation -- reading
    ``GraphConfig.node_rank_factor``, mutating ``GraphNode.rank`` / ``order`` /
    ``dummy`` and ``GraphEdge.weight`` / ``minlen``, all through the graphlib
    surface. ``simplify`` -> ``normalize_ranks`` -> ``build_layer_matrix``
    exercises the type composition end-to-end."""
    g = _make_multigraph()
    simplified = simplify(g)
    # Aggregated edge carries the concrete GraphEdge specialisation.
    edge = simplified.edge("a", "b", None)
    assert edge is not None
    assert edge.weight == 5.0 and edge.minlen == 2.0
    # Node ranks are mutable GraphNode fields.
    simplified.node_mut("a").rank = 0  # type: ignore[union-attr]
    simplified.node_mut("b").rank = 1  # type: ignore[union-attr]
    normalize_ranks(simplified)
    matrix = build_layer_matrix(simplified)
    assert matrix == [["a"], ["b"]]
