"""Black-box tests for the migrated dagre add_border_segments layer (R249).

Exercises :mod:`minimax_code.dagre.layout.add_border_segments` purely through
the single public entry point (``add_border_segments``) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` compound graph. Covers:

* border map seeding (``border_left`` / ``border_right`` populated per rank),
* the ``[min_rank, max_rank + 1)`` rank range (including ``max_rank=None``),
* border sentinel construction (``dummy="border"`` + ``rank`` + ``border_type``
  + ``_bl`` / ``_br`` id prefixes),
* the weight-1 chain linking consecutive ranks,
* the ``rank - 1`` previous-node lookup (rank 0 has no predecessor -> no chain),
* ``set_parent`` wiring (each border node parented under its subgraph),
* the ``min_rank is None`` skip guard (plain + leaf nodes untouched),
* DFS bottom-up ordering over nested compound subgraphs,
* the ``border_left`` / ``border_right`` identity dispatch,
* in-place mutation,
* the barrel surface contract (``add_border_segments`` stays out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.add_border_segments import add_border_segments
from minimax_code.dagre.lib import BorderTypeName
from minimax_code.data_structures import Graph, GraphOption


def _make_compound_graph() -> Graph:
    """Build a compound graph: subgraph ``sg`` spanning ranks 0-1 with leaves a, b.

    ``sg`` carries ``min_rank=0`` / ``max_rank=1`` (the trigger for border
    injection); leaves ``a`` (rank 0) and ``b`` (rank 1) are parented under
    ``sg`` so ``children(sg)`` returns them and the DFS recurses into both
    before seeding ``sg``'s borders.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    g.set_node("sg", GraphNode(min_rank=0, max_rank=1))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=1))
    g.set_parent("a", "sg")
    g.set_parent("b", "sg")
    return g


# === border map seeding =====================================================


def test_seeds_border_left_and_right_maps() -> None:
    """``add_border_segments`` seeds both maps on ``sg`` covering ranks 0 and 1."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    assert sg.border_left is not None
    assert sg.border_right is not None
    assert list(sg.border_left.keys()) == [0, 1]
    assert list(sg.border_right.keys()) == [0, 1]


def test_single_rank_span_produces_one_border() -> None:
    """``min_rank == max_rank`` injects exactly one rank (``max_rank + 1`` is exclusive)."""
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    g.set_node("sg", GraphNode(min_rank=2, max_rank=2))
    add_border_segments(g)
    sg = g.node("sg")
    assert list(sg.border_left.keys()) == [2]
    assert list(sg.border_right.keys()) == [2]


def test_max_rank_none_falls_back_to_zero() -> None:
    """A ``None`` ``max_rank`` falls back to 0 (grok ``unwrap_or(0)``): range ``[min_rank, 1)``."""
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    g.set_node("sg", GraphNode(min_rank=0, max_rank=None))
    add_border_segments(g)
    sg = g.node("sg")
    assert list(sg.border_left.keys()) == [0]


# === border sentinel construction ===========================================


def test_border_nodes_are_dummy_with_rank_and_type() -> None:
    """Each border sentinel carries ``dummy="border"`` + its rank + ``border_type``."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    bl0 = g.node(sg.border_left.get(0))
    br1 = g.node(sg.border_right.get(1))
    assert bl0.dummy == "border"
    assert bl0.rank == 0
    assert bl0.border_type is BorderTypeName.BorderLeft
    assert br1.dummy == "border"
    assert br1.rank == 1
    assert br1.border_type is BorderTypeName.BorderRight


def test_border_node_ids_use_bl_and_br_prefixes() -> None:
    """Border ids are minted via ``add_dummy_node`` with ``_bl`` / ``_br`` prefixes."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    assert sg.border_left.get(0).startswith("_bl")
    assert sg.border_right.get(0).startswith("_br")
    assert sg.border_left.get(1).startswith("_bl")
    assert sg.border_right.get(1).startswith("_br")


# === weight-1 chain =========================================================


def test_consecutive_ranks_chained_with_weight_one() -> None:
    """Border nodes of consecutive ranks are linked by a weight-1 edge."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    bl0 = sg.border_left.get(0)
    bl1 = sg.border_left.get(1)
    edge = g.edge(bl0, bl1, None)
    assert edge is not None
    assert edge.weight == 1.0
    br0 = sg.border_right.get(0)
    br1 = sg.border_right.get(1)
    edge_r = g.edge(br0, br1, None)
    assert edge_r is not None
    assert edge_r.weight == 1.0


def test_rank_zero_lookup_returns_none() -> None:
    """``border.get(rank - 1)`` for rank 0 returns None (no -1 key) -> no chain edge.

    This is the grok ``prev = border.get(&(rank - 1))`` + ``if prev.is_some()``
    guard: the rank-0 predecessor lookup misses because the border map only
    carries keys seeded by the ``[min_rank, max_rank + 1)`` loop.
    """
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    assert sg.border_left.get(-1) is None
    assert sg.border_right.get(-1) is None


# === set_parent wiring ======================================================


def test_border_nodes_parented_under_subgraph() -> None:
    """Each border sentinel is parented under its owning subgraph ``sg``."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    kids = g.children("sg")
    for rank in (0, 1):
        assert sg.border_left.get(rank) in kids
        assert sg.border_right.get(rank) in kids


# === min_rank skip guard ====================================================


def test_node_without_min_rank_is_skipped() -> None:
    """A node with ``min_rank=None`` gets no border maps (the DFS guard)."""
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    g.set_node("plain", GraphNode(min_rank=None, max_rank=5))
    add_border_segments(g)
    plain = g.node("plain")
    assert plain.border_left is None
    assert plain.border_right is None


def test_leaf_nodes_get_no_borders() -> None:
    """Leaf nodes ``a`` / ``b`` have ``min_rank=None`` -> left untouched."""
    g = _make_compound_graph()
    add_border_segments(g)
    assert g.node("a").border_left is None
    assert g.node("b").border_left is None
    assert g.node("a").border_right is None


# === DFS bottom-up over nested compound subgraphs ==========================


def test_dfs_borders_nested_subgraphs_bottom_up() -> None:
    """Nested compound subgraphs both get borders; the inner is recursed first.

    ``outer`` spans ranks 0-2 (3 border ranks); ``inner`` spans 0-1 (2 border
    ranks) and is parented under ``outer``. DFS recurses into ``inner`` before
    seeding ``outer``, so ``inner``'s border ids are minted first (lower
    ``unique_id`` suffix) -- the bottom-up guarantee grok relies on.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    g.set_node("outer", GraphNode(min_rank=0, max_rank=2))
    g.set_node("inner", GraphNode(min_rank=0, max_rank=1))
    g.set_parent("inner", "outer")
    add_border_segments(g)
    outer = g.node("outer")
    inner = g.node("inner")
    assert list(outer.border_left.keys()) == [0, 1, 2]
    assert list(inner.border_left.keys()) == [0, 1]
    # Bottom-up: inner border ids minted before outer's (monotonic unique_id).
    inner_n = int(inner.border_left.get(0)[len("_bl"):])
    outer_n = int(outer.border_left.get(0)[len("_bl"):])
    assert inner_n < outer_n


# === border_left / border_right identity dispatch ==========================


def test_border_left_goes_to_left_map() -> None:
    """``BorderLeft`` vs ``BorderRight`` sentinels land in distinct maps."""
    g = _make_compound_graph()
    add_border_segments(g)
    sg = g.node("sg")
    bl0 = sg.border_left.get(0)
    br0 = sg.border_right.get(0)
    assert bl0 != br0
    assert g.node(bl0).border_type is BorderTypeName.BorderLeft
    assert g.node(br0).border_type is BorderTypeName.BorderRight


# === in-place mutation ======================================================


def test_mutates_graph_in_place() -> None:
    """``add_border_segments`` mutates the live graph object (no copy/return)."""
    g = _make_compound_graph()
    sg = g.node("sg")
    assert sg.border_left is None
    add_border_segments(g)
    assert sg.border_left is not None  # same node object, now seeded


# === barrel surface contract ================================================


def test_add_border_segments_not_in_dagre_all() -> None:
    """``add_border_segments`` is NOT in the crate-root barrel (grok keeps it
    module-private to ``layout::add_border_segments``; only the four type
    structs earn a crate-root ``pub use``)."""
    assert "add_border_segments" not in dagre.__all__


def test_add_border_segments_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "add_border_segments")


def test_submodule_exports_only_add_border_segments() -> None:
    """The submodule exposes exactly ``add_border_segments`` (the single ``pub fn``)."""
    import minimax_code.dagre.layout.add_border_segments as abs_mod

    assert abs_mod.__all__ == ["add_border_segments"]
    assert abs_mod.add_border_segments is add_border_segments


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R249 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_add_border_segments_import() -> None:
    """Importing ``add_border_segments`` binds the ``layout`` subpackage on ``dagre``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.add_border_segments as abs_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.add_border_segments is abs_mod
