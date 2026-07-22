"""Black-box tests for the migrated dagre order/build_layer_graph layer (R262).

Exercises :mod:`minimax_code.dagre.layout.order.build_layer_graph` through its
three public symbols (the :class:`GraphRelationship` enum + the
:func:`build_layer_graph` fn + the :func:`create_root_node` helper) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state ``order`` runs on,
immediately after ``add_border_segments`` (R249) seeds the per-rank border
sentinels and before ``sort`` / ``sort_subgraph`` sweep the layer). Covers:

* :func:`build_layer_graph` end-to-end with ``IN_EDGES``: an empty graph
  (no movable -> the result stays empty but the ``root`` label is set), a
  single parentless rank-0 node (re-parented under the synthetic root; the
  node label is the SAME live object as the source -- the zero-clone
  invariant), rank selectivity (only the movable rank projects), a linear
  ``a -> b`` chain built at rank 1 (the in-edge ``a -> b`` is copied with
  its weight; the non-movable source ``a`` is auto-created by ``set_edge``
  as a default node whose ``rank`` is ``None``, while the movable ``b``
  keeps its rank-1 label), a ``None`` edge weight (counts as ``0.0``), and
  multiple movable nodes on the same rank,
* :func:`build_layer_graph` with ``OUT_EDGES``: the relationship switch
  flips the incident-edge selection from in-edges to out-edges (the
  ``a -> b`` edge built at rank 0 copies as ``b -> a`` -- the
  ``u = e.w if e.v == v else e.v`` undirected-partner extraction),
* the compound-parent preservation (a node parented under a ``cluster``
  keeps that parent in the layer graph rather than being re-rooted),
* the multigraph weight aggregation (two parallel ``a -> b`` edges in the
  source collapse to one aggregated edge ``2.0 + 3.0 = 5.0`` -- the
  non-multigraph ``result`` updates the existing edge label rather than
  raising),
* the subgraph re-stamp (a node spanning the rank via ``min_rank`` /
  ``max_rank`` is re-stamped with a fresh default ``GraphNode`` carrying
  only the per-rank ``border_left_`` / ``border_right_`` sentinels; a
  rank outside the span is excluded; a ``None`` border map is tolerated),
* :func:`create_root_node` white-box (``_root{id}`` format; the minted id
  is absent from the source graph),
* the :class:`GraphRelationship` enum contract (two members, ``in_edges``
  / ``out_edges`` values),
* graph immutability (the source graph is unchanged after the call),
* the barrel surface contract (``build_layer_graph`` /
  ``create_root_node`` / ``GraphRelationship`` stay out of
  ``dagre.__all__``; the crate-root barrel count is unchanged at 4; the
  ``order`` sub-package barrel re-exports all five leaves; the submodule
  ``__all__`` is ASCII-sorted with the class before the two functions).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.build_layer_graph import (
    GraphRelationship,
    build_layer_graph,
    create_root_node,
)
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap


def _make_graph() -> Graph:
    """Build an empty directed compound graph (the state ``order`` runs on).

    ``compound=True`` + ``multigraph=True`` to mirror the production config
    ``order`` runs on (the post-``rank`` / post-``add_border_segments``
    graph). ``build_layer_graph`` reads ``node.rank`` / ``node.min_rank`` /
    ``node.max_rank`` + ``in_edges`` / ``out_edges`` + ``edge_with_obj`` +
    ``parent`` only; the GraphConfig dressing keeps the fixture faithful to
    the real pipeline. The ``multigraph=True`` lets the aggregation test
    mint two parallel ``a -> b`` edges in the source.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _edge(
    g: Graph, v: str, w: str, weight: float | None = 1.0, name: str | None = None
) -> None:
    """Add ``v -> w`` with the given weight + optional multigraph ``name``."""
    g.set_edge(v, w, GraphEdge(weight=weight), name)


# === build_layer_graph: end-to-end (IN_EDGES) ==========================


def test_build_layer_graph_empty_graph_sets_root_label_only() -> None:
    """An empty source yields an empty result; only the ``root`` label is set.

    No node projects (``g.nodes()`` is empty), so no ``set_node`` / no
    ``set_parent`` fires and the synthetic root is never auto-created in
    the result. ``create_root_node`` still mints a fresh id (it reads
    ``g.has_node`` only) and stamps it on ``result.graph().root``.
    """
    g = _make_graph()
    result = build_layer_graph(g, 0, GraphRelationship.IN_EDGES)
    assert list(result.nodes()) == []
    assert result.graph() is not None
    assert result.graph().root.startswith("_root")


def test_build_layer_graph_single_node_reparented_under_root() -> None:
    """A lone rank-0 node projects with the synthetic root as its parent.

    The movable ``a`` is copied verbatim (the live label reference -- the
    zero-clone invariant) and re-parented under ``root`` (it has no
    parent in ``g``). ``set_parent`` auto-creates ``root`` in the result,
    so the result holds exactly ``[a, root]``. ``result.node("a")`` IS
    ``g.node("a")`` -- Python passes the reference straight through (grok
    forces a ``node.clone()`` borrow-to-owned transfer; the eleventh
    zero-semantic-clone leaf).
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    result = build_layer_graph(g, 0, GraphRelationship.IN_EDGES)
    root = result.graph().root
    assert "a" in result.nodes()
    assert root in result.nodes()
    assert len(list(result.nodes())) == 2
    assert result.parent("a") == root
    assert result.node("a") is g.node("a")  # zero-clone: same live object
    assert result.node(root) is not None  # auto-created by set_parent


def test_build_layer_graph_rank_selectivity_excludes_other_ranks() -> None:
    """Only the movable rank projects; nodes on other ranks are excluded.

    ``a`` (rank 0) projects when building rank 0; ``b`` (rank 1) does
    not. ``b`` is absent from the result entirely (it is not a movable
    node and no copied edge auto-creates it).
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    result = build_layer_graph(g, 0, GraphRelationship.IN_EDGES)
    assert "a" in result.nodes()
    assert "b" not in result.nodes()
    root = result.graph().root
    assert result.parent("a") == root


def test_build_layer_graph_linear_chain_copies_in_edge_with_weight() -> None:
    """``a -> b`` built at rank 1 copies the in-edge ``a -> b`` with its weight.

    ``b`` (rank 1) is the movable node; its in-edge ``a -> b`` (weight
    2.5) is copied into the result. The undirected-partner extraction
    ``u = e.w if e.v == v else e.v`` yields ``u = a`` (``e.v == a != b``).
    The non-movable source ``a`` is auto-created by ``set_edge`` as a
    default node (``rank = None``); the movable ``b`` keeps its rank-1
    label -- ``set_node`` guards on KEY presence, so the ``set_node(b,
    None)`` that ``set_edge`` issues does NOT overwrite ``b``'s label.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "a", "b", weight=2.5)
    result = build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    root = result.graph().root
    # b is movable, projected verbatim (rank preserved -- set_node no-op).
    assert result.node("b") is g.node("b")
    assert result.node("b").rank == 1
    assert result.parent("b") == root
    # a is the auto-created in-edge source (default node, rank None).
    assert "a" in result.nodes()
    assert result.node("a") is not None
    assert result.node("a").rank is None
    # the copied edge carries the source weight.
    assert result.edge("a", "b", None) is not None
    assert result.edge("a", "b", None).weight == 2.5


def test_build_layer_graph_none_edge_weight_counts_zero() -> None:
    """A ``None`` source edge weight counts as ``0.0`` (the double unwrap_or).

    ``a -> b`` carries ``weight = None``; the copied edge aggregates
    ``0.0 (source) + 0.0 (first encounter) = 0.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "a", "b", weight=None)
    result = build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    assert result.edge("a", "b", None).weight == 0.0


def test_build_layer_graph_multiple_movable_same_rank() -> None:
    """Two movable nodes on the same rank both project, each under the root.

    ``a`` and ``b`` (both rank 1) project independently; the result holds
    ``[a, b, root]`` in source-discovery order. ``a``'s in-edge ``c -> a``
    is copied (``c`` auto-created); ``b`` has no in-edges.
    """
    g = _make_graph()
    g.set_node("c", GraphNode(rank=0))
    g.set_node("a", GraphNode(rank=1))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "c", "a")
    result = build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    root = result.graph().root
    assert "a" in result.nodes()
    assert "b" in result.nodes()
    assert root in result.nodes()
    assert result.parent("a") == root
    assert result.parent("b") == root
    assert result.edge("c", "a", None).weight == 1.0


# === build_layer_graph: OUT_EDGES relationship switch =================


def test_build_layer_graph_out_edges_switches_incident_selection() -> None:
    """``OUT_EDGES`` projects out-edges; the partner is ``e.w`` when ``e.v == v``.

    Building rank 0 with ``OUT_EDGES``: ``a`` (rank 0) is movable, its
    out-edge ``a -> b`` (weight 1.5) projects. The undirected-partner
    extraction ``u = e.w if e.v == v else e.v`` yields ``u = b`` (``e.v
    == a == v``), so the copied edge is ``b -> a`` (the direction the
    down sweep sorts). ``b`` is auto-created; ``a`` is movable.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "a", "b", weight=1.5)
    result = build_layer_graph(g, 0, GraphRelationship.OUT_EDGES)
    root = result.graph().root
    assert result.node("a") is g.node("a")  # movable, projected verbatim
    assert result.parent("a") == root
    assert "b" in result.nodes()  # auto-created out-edge sink
    assert result.edge("b", "a", None).weight == 1.5


# === build_layer_graph: compound parent preservation ==================


def test_build_layer_graph_preserves_compound_parent() -> None:
    """A node parented under a cluster keeps that parent (not re-rooted).

    ``a`` (rank 1) is parented under ``cluster`` in ``g``; the layer
    graph re-parents ``a`` under ``cluster`` (not the synthetic root).
    ``cluster`` is auto-created by ``set_parent`` as a default node.
    """
    g = _make_graph()
    g.set_node("cluster", GraphNode(rank=0))
    g.set_node("a", GraphNode(rank=1))
    g.set_parent("a", "cluster")
    result = build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    assert result.parent("a") == "cluster"
    assert "cluster" in result.nodes()  # auto-created by set_parent
    assert result.node("cluster") is not None
    root = result.graph().root
    assert result.parent("a") != root  # cluster, not root


# === build_layer_graph: multigraph weight aggregation =================


def test_build_layer_graph_aggregates_parallel_edge_weights() -> None:
    """Two parallel ``a -> b`` edges collapse to one aggregated edge.

    The source is a multigraph with two ``a -> b`` edges (names ``e1`` /
    ``e2``, weights 2.0 / 3.0). ``b``'s in-edges are both copied; the
    non-multigraph ``result`` has only one ``(a, b)`` slot (``name =
    None``), so the second copy UPDATES the existing edge label rather
    than raising -- the weight aggregates ``2.0 + 3.0 = 5.0``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "a", "b", weight=2.0, name="e1")
    _edge(g, "a", "b", weight=3.0, name="e2")
    result = build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    assert result.edge("a", "b", None).weight == 5.0


# === build_layer_graph: subgraph re-stamp ==============================


def _border_map(rank: int, sentinel: str) -> OrderedHashMap:
    """Build a single-entry ``rank -> sentinel`` border OrderedHashMap."""
    m: OrderedHashMap = OrderedHashMap()
    m.insert(rank, sentinel)
    return m


def test_build_layer_graph_subgraph_restamps_border_sentinels() -> None:
    """A subgraph node spanning the rank is re-stamped with border sentinels.

    ``cluster`` (``min_rank=0``, ``max_rank=2``) spans rank 0; its
    ``border_left`` / ``border_right`` maps carry the per-rank sentinels
    ``add_border_segments`` (R249) seeded. The layer graph re-stamps
    ``cluster`` with a fresh default ``GraphNode`` carrying only
    ``border_left_ = "bl0"`` / ``border_right_ = "br0"``; every other
    field (``rank`` / ``min_rank`` / ...) is the default ``None``. The
    re-stamped node is NOT the source ``cluster`` object.
    """
    g = _make_graph()
    cluster = GraphNode(min_rank=0, max_rank=2)
    cluster.border_left = _border_map(0, "bl0")
    cluster.border_right = _border_map(0, "br0")
    g.set_node("cluster", cluster)
    result = build_layer_graph(g, 0, GraphRelationship.IN_EDGES)
    restamped = result.node("cluster")
    assert restamped is not None
    assert restamped.border_left_ == "bl0"
    assert restamped.border_right_ == "br0"
    assert restamped.rank is None  # fresh default, not the source min_rank
    assert restamped.min_rank is None
    assert restamped is not cluster  # a fresh GraphNode, not the source ref


def test_build_layer_graph_subgraph_rank_outside_span_excluded() -> None:
    """A subgraph node whose span excludes the rank is not projected.

    ``cluster`` (``min_rank=0``, ``max_rank=2``) does not span rank 5, so
    it is excluded; the result stays empty (only the ``root`` label).
    """
    g = _make_graph()
    g.set_node("cluster", GraphNode(min_rank=0, max_rank=2))
    result = build_layer_graph(g, 5, GraphRelationship.IN_EDGES)
    assert "cluster" not in result.nodes()
    assert list(result.nodes()) == []
    assert result.graph().root.startswith("_root")


def test_build_layer_graph_subgraph_none_border_map_defensive() -> None:
    """A subgraph with ``None`` border maps re-stamps with ``None`` sentinels.

    grok's ``node.border_left.as_ref().unwrap()`` would panic on a
    ``None`` map; the port widens defensively (``if node.border_left is
    not None``), so a ``None`` map yields a fresh node with both
    ``border_left_`` / ``border_right_`` left at the default ``None``.
    """
    g = _make_graph()
    g.set_node("cluster", GraphNode(min_rank=0, max_rank=2))  # no border maps
    result = build_layer_graph(g, 0, GraphRelationship.IN_EDGES)
    restamped = result.node("cluster")
    assert restamped is not None
    assert restamped.border_left_ is None
    assert restamped.border_right_ is None


# === create_root_node: white-box ======================================


def test_create_root_node_format() -> None:
    """The minted id follows the ``_root{id}`` shape (mirrors grok)."""
    g = _make_graph()
    assert create_root_node(g).startswith("_root")


def test_create_root_node_absent_from_source() -> None:
    """The minted id is guaranteed absent from the source graph.

    ``create_root_node`` retries while ``g.has_node(v)`` (the pathological
    collision case); on an empty graph the first candidate always wins.
    """
    g = _make_graph()
    root = create_root_node(g)
    assert g.has_node(root) is False


# === GraphRelationship enum ===========================================


def test_graph_relationship_enum_contract() -> None:
    """The enum mirrors grok's ``InEdges`` / ``OutEdges`` two-member shape."""
    assert len(list(GraphRelationship)) == 2
    assert GraphRelationship.IN_EDGES.value == "in_edges"
    assert GraphRelationship.OUT_EDGES.value == "out_edges"


# === graph immutability ===============================================


def test_build_layer_graph_does_not_mutate_source() -> None:
    """build_layer_graph reads the source only; the graph is unchanged."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    _edge(g, "a", "b", weight=2.0)
    node_snapshot = {n: g.node(n) for n in g.nodes()}
    edge_snapshot = {e: g.edge_with_obj(e) for e in g.edges()}
    parent_snapshot = {n: g.parent(n) for n in g.nodes()}
    build_layer_graph(g, 1, GraphRelationship.IN_EDGES)
    assert {n: g.node(n) for n in g.nodes()} == node_snapshot
    assert {e: g.edge_with_obj(e) for e in g.edges()} == edge_snapshot
    assert {n: g.parent(n) for n in g.nodes()} == parent_snapshot


# === barrel surface contract ==========================================


def test_build_layer_graph_not_in_dagre_all() -> None:
    """None of the three symbols earns a crate-root barrel slot."""
    assert "build_layer_graph" not in dagre.__all__
    assert "create_root_node" not in dagre.__all__
    assert "GraphRelationship" not in dagre.__all__


def test_build_layer_graph_not_reachable_at_dagre_top_level() -> None:
    """No symbol is bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "build_layer_graph")
    assert not hasattr(dagre, "create_root_node")
    assert not hasattr(dagre, "GraphRelationship")


def test_build_layer_graph_submodule_exports_three_symbols() -> None:
    """The submodule exposes the enum + the two fns (ASCII-sorted, class first)."""
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod

    assert blg_mod.__all__ == ["GraphRelationship", "build_layer_graph", "create_root_node"]
    assert blg_mod.GraphRelationship is GraphRelationship
    assert blg_mod.build_layer_graph is build_layer_graph
    assert blg_mod.create_root_node is create_root_node


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R262 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_six() -> None:
    """The ``order`` sub-package barrel re-exports all five ``order`` leaves.

    Synchronised across R258 -> R259 -> R260 -> R261 -> R262: the barrel
    grew ``["init_order"]`` -> ``["cross_count", "init_order"]`` ->
    ``["barycenter", "cross_count", "init_order"]`` -> ``["barycenter",
    "cross_count", "init_order", "resolve_conflicts"]`` -> the
    ASCII-sorted ``["barycenter", "build_layer_graph", "cross_count",
    "init_order", "resolve_conflicts"]``. Mirrors the R255 -> R254 /
    R257 -> R254 ``rank`` barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod

    assert order_pkg.__all__ == [
        "add_subgraph_constraints",
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "resolve_conflicts",
    ]
    assert order_pkg.add_subgraph_constraints is asc_mod
    assert order_pkg.build_layer_graph is blg_mod


def test_build_layer_graph_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.build_layer_graph.build_layer_graph is build_layer_graph
