"""Black-box tests for the migrated dagre position/bk layer (R266).

Exercises :mod:`minimax_code.dagre.layout.position.bk` -- the Brandes-Kopf
"Fast and Simple Horizontal Coordinate Assignment" four-alignment algorithm
-- through its public entry point :func:`position_x` plus the white-box
helpers (``_width`` / ``_find_other_inner_segment_node`` /
``vertical_alignment`` / ``build_block_graph`` / ``horizontal_compaction`` /
``find_smallest_width_alignment`` / ``balance``) and the public conflict
primitives (``add_conflict`` / ``has_conflict``). The fixtures dress a bare
``Graph<GraphConfig, GraphNode, GraphEdge>`` directed non-multigraph
non-compound layer graph (the post-``order`` state every node carries a
``rank`` + ``order`` on). Covers:

* :func:`position_x` end-to-end: an empty graph (no nodes -> an empty
  ``xs`` map, the ``all(not layer for layer in layering)`` early return);
  a single three-node chain ``a -> b -> c`` (no horizontal spread -> every
  node shares one ``x``); a two-layer bipartite ``a -> c`` / ``b -> d``
  (horizontal spread with ``a`` / ``c`` aligned and ``b`` / ``d`` aligned,
  the gap between the two blocks at least ``nodesep``),
* ``_width`` white-box: a present node returns its ``width``; an absent
  node returns ``0.0`` (the ``node is None`` guard),
* ``add_conflict`` / ``has_conflict`` white-box: the canonical ``v <= w``
  ordering (recording ``("b", "a")`` is equivalent to ``("a", "b")``) and
  the symmetric query (both directions hit); an unrecorded pair is ``False``,
* ``_find_other_inner_segment_node`` white-box: a non-dummy returns
  ``None``; a dummy with a dummy predecessor returns that predecessor; a
  dummy whose every predecessor is real returns ``None``,
* :func:`vertical_alignment` white-box: a single ``a -> b`` chain aligns
  ``b`` to ``a`` (``root = {a: a, b: a}``) and the returned ``align_keys``
  preserve the layering insertion order ``["a", "b"]``,
* :func:`build_block_graph` white-box: two adjacent non-dummy blocks of
  ``width=10`` under ``nodesep=50`` produce one separator edge labelled
  ``60.0`` (``width_b/2 + nodesep/2 + nodesep/2 + width_a/2``),
* :func:`horizontal_compaction` white-box: a single chain collapsed to
  one block places both nodes at ``x=0.0``,
* :func:`find_smallest_width_alignment` white-box: the narrowest of two
  candidate alignments wins (the nested ``width_of`` reads node widths),
* :func:`balance` white-box: a node whose four alignments are
  ``[0, 10, 20, 30]`` balances to the ``(xs_1 + xs_2) / 2 = 15.0`` median,
* the barrel surface contract: every ``bk`` symbol stays out of
  ``dagre.__all__`` and off the crate-root top level (the R246 barrel count
  stays at 4); the new ``position`` sub-package barrel re-exports the
  single ``bk`` leaf; ``bk.__all__`` is the ASCII-sorted nine-symbol list;
  the symbol is reachable via ``dagre.layout.position``.
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.position.bk import (
    _find_other_inner_segment_node,
    _predecessors_of,
    _width,
    add_conflict,
    balance,
    build_block_graph,
    find_smallest_width_alignment,
    has_conflict,
    horizontal_compaction,
    position_x,
    vertical_alignment,
)
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap


def _make_position_graph(nodesep: float = 50.0, edgesep: float = 20.0) -> Graph:
    """Build an empty directed non-multigraph non-compound layer graph.

    ``multigraph=False`` + ``compound=False`` mirrors the post-``order``
    state ``position::bk`` runs on (the non-compound projection
    ``position::mod`` will hand it in R267): every node already carries a
    ``rank`` + ``order``. ``bk`` reads ``node`` / ``predecessors`` /
``successors`` + ``graph`` only; the ``GraphConfig`` dressing keeps the
    fixture faithful to the real layer graph.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(nodesep=nodesep, edgesep=edgesep))
    return g


def _node(
    rank: int | None = None,
    order: int | None = None,
    width: float = 0.0,
    height: float = 0.0,
    dummy: str | None = None,
    labelpos: str | None = None,
) -> GraphNode:
    """Stamp a layer-graph node with the coordinate-assignment fields."""
    return GraphNode(
        rank=rank,
        order=order,
        width=width,
        height=height,
        dummy=dummy,
        labelpos=labelpos,
    )


def _edge(g: Graph, v: str, w: str) -> None:
    """Add ``v -> w`` with the default edge label."""
    g.set_edge(v, w, GraphEdge(), None)


# === _width: white-box ==================================================


def test_width_returns_node_width() -> None:
    """A present node returns its ``width`` field."""
    g = _make_position_graph()
    g.set_node("a", _node(width=10.0))
    assert _width(g, "a") == 10.0


def test_width_missing_node_returns_zero() -> None:
    """An absent node hits the ``node is None`` guard -> ``0.0``."""
    g = _make_position_graph()
    assert _width(g, "ghost") == 0.0


# === add_conflict / has_conflict: white-box =============================


def test_add_conflict_canonicalises_endpoint_order() -> None:
    """Recording ``("b", "a")`` is equivalent to ``("a", "b")``.

    ``add_conflict`` swaps the endpoints when ``v > w`` so the conflict key
    is canonical; both query directions therefore hit.
    """
    conflicts = OrderedHashMap()
    add_conflict(conflicts, "b", "a")
    assert has_conflict(conflicts, "a", "b")
    assert has_conflict(conflicts, "b", "a")


def test_has_conflict_absent_pair_is_false() -> None:
    """An unrecorded pair is ``False``; recording then querying is ``True``."""
    conflicts = OrderedHashMap()
    assert not has_conflict(conflicts, "a", "b")
    add_conflict(conflicts, "a", "b")
    assert has_conflict(conflicts, "a", "b")
    assert not has_conflict(conflicts, "a", "c")


# === _find_other_inner_segment_node: white-box ==========================


def test_find_other_inner_segment_node_non_dummy_returns_none() -> None:
    """A non-dummy node is never on an inner segment -> ``None``."""
    g = _make_position_graph()
    g.set_node("a", _node())
    g.set_node("b", _node(dummy="edge"))
    _edge(g, "a", "b")
    assert _find_other_inner_segment_node(g, "a") is None


def test_find_other_inner_segment_node_dummy_with_dummy_pred() -> None:
    """A dummy whose predecessor is also a dummy returns that predecessor.

    Both endpoints dummy => the edge is an inner segment; the predecessor
    is the other endpoint.
    """
    g = _make_position_graph()
    g.set_node("d1", _node(dummy="edge"))
    g.set_node("d2", _node(dummy="edge"))
    _edge(g, "d1", "d2")
    assert _find_other_inner_segment_node(g, "d2") == "d1"


def test_find_other_inner_segment_node_dummy_with_real_pred_returns_none() -> None:
    """A dummy whose only predecessor is real has no inner segment -> ``None``."""
    g = _make_position_graph()
    g.set_node("real", _node())
    g.set_node("d", _node(dummy="edge"))
    _edge(g, "real", "d")
    assert _find_other_inner_segment_node(g, "d") is None


# === vertical_alignment: white-box ======================================


def test_vertical_alignment_single_chain_aligns_successor_to_predecessor() -> None:
    """A single ``a -> b`` chain aligns ``b`` to ``a``.

    ``root = {a: a, b: a}`` (``b`` joins ``a``'s block); the returned
    ``align_keys`` preserve the layering insertion order ``["a", "b"]``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0))
    g.set_node("b", _node(rank=1, order=0))
    _edge(g, "a", "b")
    layering = [["a"], ["b"]]
    root, align_keys = vertical_alignment(g, layering, OrderedHashMap(), _predecessors_of)
    assert root.get("a") == "a"
    assert root.get("b") == "a"
    assert align_keys == ["a", "b"]


# === build_block_graph: white-box =======================================


def test_build_block_graph_two_blocks_one_separator_edge() -> None:
    """Two adjacent non-dummy blocks of ``width=10`` produce one ``60.0`` edge.

    The separator is ``width_b/2 + nodesep/2 + nodesep/2 + width_a/2`` =
    ``5 + 25 + 25 + 5 = 60.0`` under ``nodesep=50`` (no ``labelpos`` delta).
    """
    g = _make_position_graph(nodesep=50.0)
    g.set_node("a", _node(width=10.0))
    g.set_node("b", _node(width=10.0))
    layering = [["a", "b"]]
    root = OrderedHashMap()
    root.insert("a", "a")
    root.insert("b", "b")
    block_g = build_block_graph(g, layering, root, False)
    assert set(block_g.nodes()) == {"a", "b"}
    assert block_g.has_edge("a", "b", None)
    assert block_g.edge("a", "b", None) == 60.0


# === horizontal_compaction: white-box ===================================


def test_horizontal_compaction_single_chain_both_nodes_at_zero() -> None:
    """A single chain collapsed to one block places both nodes at ``x=0.0``.

    ``root = {a: a, b: a}`` => ``a`` and ``b`` share block ``a``; the block
    graph has a single node and no edges, so both compaction sweeps leave
    ``xs[a] = 0.0`` and the root propagation copies it to ``b``.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, width=10.0))
    g.set_node("b", _node(rank=1, order=0, width=10.0))
    _edge(g, "a", "b")
    layering = [["a"], ["b"]]
    root = OrderedHashMap()
    root.insert("a", "a")
    root.insert("b", "a")
    xs = horizontal_compaction(g, layering, root, ["a", "b"], False)
    assert xs.get("a") == 0.0
    assert xs.get("b") == 0.0


# === find_smallest_width_alignment: white-box ===========================


def test_find_smallest_width_alignment_picks_narrowest() -> None:
    """The narrowest of two candidate alignments wins.

    Both nodes ``width=10``: the narrow alignment spans ``[-5, 10]``
    (width ``15``); the wide alignment spans ``[-5, 55]`` (width ``60``).
    ``find_smallest_width_alignment`` returns the narrow one.
    """
    g = _make_position_graph()
    g.set_node("a", _node(width=10.0))
    g.set_node("b", _node(width=10.0))
    narrow = OrderedHashMap()
    narrow.insert("a", 0.0)
    narrow.insert("b", 5.0)
    wide = OrderedHashMap()
    wide.insert("a", 0.0)
    wide.insert("b", 50.0)
    xss = OrderedHashMap()
    xss.insert("ul", narrow)
    xss.insert("ur", wide)
    result = find_smallest_width_alignment(g, xss)
    assert result is narrow


# === balance: white-box =================================================


def test_balance_returns_median_of_four_alignments() -> None:
    """A node whose four alignments are ``[0, 10, 20, 30]`` balances to ``15.0``.

    With ``align=None`` the median is ``(xs_1 + xs_2) / 2 = (10 + 20) / 2``
    over the four per-node coordinates sorted ascending.
    """
    g = _make_position_graph()
    g.set_node("a", _node(width=10.0))
    xss = OrderedHashMap()
    for key, xval in [("ul", 0.0), ("ur", 10.0), ("dl", 20.0), ("dr", 30.0)]:
        xs = OrderedHashMap()
        xs.insert("a", xval)
        xss.insert(key, xs)
    result = balance(xss, None)
    assert result.get("a") == 15.0


# === position_x: end-to-end =============================================


def test_position_x_empty_graph_returns_empty_map() -> None:
    """An empty graph hits the ``all(not layer ...)`` early return.

    ``build_layer_matrix`` of a nodeless graph yields an empty layering;
    ``all([])`` is ``True`` so ``position_x`` returns an empty map without
    running any alignment.
    """
    g = _make_position_graph()
    xs = position_x(g)
    assert list(xs.keys()) == []


def test_position_x_single_chain_no_horizontal_spread() -> None:
    """A single ``a -> b -> c`` chain has no horizontal spread.

    Each rank holds one node so every vertical alignment collapses to one
    block per node and every compaction places the block at ``x=0``; the
    four alignments agree and ``balance`` keeps them equal.
    """
    g = _make_position_graph()
    g.set_node("a", _node(rank=0, order=0, width=10.0))
    g.set_node("b", _node(rank=1, order=0, width=10.0))
    g.set_node("c", _node(rank=2, order=0, width=10.0))
    _edge(g, "a", "b")
    _edge(g, "b", "c")
    xs = position_x(g)
    assert xs.get("a") == xs.get("b") == xs.get("c")


def test_position_x_two_layer_bipartite_spreads_horizontally() -> None:
    """A two-layer bipartite ``a -> c`` / ``b -> d`` spreads horizontally.

    ``a`` aligns with ``c`` and ``b`` with ``d`` (no crossing edges); the
    two blocks separate by at least ``nodesep`` and the within-block pairs
    share an ``x``.
    """
    g = _make_position_graph(nodesep=50.0)
    g.set_node("a", _node(rank=0, order=0, width=10.0))
    g.set_node("b", _node(rank=0, order=1, width=10.0))
    g.set_node("c", _node(rank=1, order=0, width=10.0))
    g.set_node("d", _node(rank=1, order=1, width=10.0))
    _edge(g, "a", "c")
    _edge(g, "b", "d")
    xs = position_x(g)
    for node in ("a", "b", "c", "d"):
        assert node in xs
    assert xs.get("a") != xs.get("b")
    assert xs.get("b") - xs.get("a") >= 50.0


# === barrel surface contract ============================================


def test_position_x_not_in_dagre_all() -> None:
    """``position_x`` earns no crate-root barrel slot."""
    assert "position_x" not in dagre.__all__


def test_position_x_not_reachable_at_dagre_top_level() -> None:
    """The fn is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "position_x")


def test_bk_submodule_exports_symbols() -> None:
    """The submodule exposes the nine public symbols (ASCII-sorted ``__all__``)."""
    import minimax_code.dagre.layout.position.bk as bk_mod

    assert bk_mod.__all__ == [
        "add_conflict",
        "balance",
        "build_block_graph",
        "find_smallest_width_alignment",
        "find_type_2_conflicts",
        "has_conflict",
        "horizontal_compaction",
        "position_x",
        "vertical_alignment",
    ]
    assert bk_mod.position_x is position_x


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R266 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_position_subpackage_barrel_reexports_bk() -> None:
    """The ``position`` sub-package barrel re-exports ``bk`` (and R267's ``mod``)."""
    import minimax_code.dagre.layout.position as position_pkg
    import minimax_code.dagre.layout.position.bk as bk_mod

    assert position_pkg.__all__ == ["bk", "mod"]
    assert position_pkg.bk is bk_mod


def test_bk_reachable_via_layout_position() -> None:
    """Importing the ``position`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.position as position_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.position is position_pkg
    assert position_pkg.bk.position_x is position_x
