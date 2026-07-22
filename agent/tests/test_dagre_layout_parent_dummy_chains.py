"""Black-box tests for the migrated dagre parent_dummy_chains layer (R252).

Exercises :mod:`minimax_code.dagre.layout.parent_dummy_chains` through its
single public entry point (``parent_dummy_chains``) on a real compound
``Graph<GraphConfig, GraphNode, GraphEdge>`` whose long edges have been
pre-split into ``edge`` dummy chains (the state R250 ``normalize`` leaves
behind, hand-assembled here because the upstream ``nesting_graph`` stage that
produces the compound forest has not been migrated yet). Covers:

* the basic single-cluster re-parent (a dummy whose rank falls inside a
  compound node's ``[min_rank, max_rank]`` span is re-parented onto it),
* the ascending arm (a dummy whose rank exceeds a shallow compound node's
  ``max_rank`` advances the path cursor up to the deeper ancestor),
* the descending arm (a dummy whose rank meets a deeper compound node's
  ``min_rank`` advances the path cursor down into it),
* the flat-graph no-op (no compound ancestor -> dummy stays at the root),
* the ``dummy_chains is None`` / empty no-op,
* the private ``_postorder`` interval numbering (parent interval strictly
  contains every child's),
* the private ``_find_path`` LCA walk (``v -> lca -> w`` path stitched from
  both ancestor chains),
* the private ``_path_get`` fallback helper (out-of-range index -> LCA),
* in-place mutation,
* the barrel surface contract (``parent_dummy_chains`` stays out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.parent_dummy_chains import parent_dummy_chains
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.graphlib import Edge


def _make_compound_graph() -> Graph:
    """Build an empty compound directed graph with a ``GraphConfig`` label.

    ``compound=True`` because ``parent_dummy_chains`` re-parents dummies onto
    compound ancestors (a flat graph has no compound forest); ``multigraph=True``
    to mirror the production graph config. The default ``dummy_chains`` on the
    freshly minted ``GraphConfig`` is ``None`` until a test seeds it.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _wire_chain(
    g: Graph, head: str, v: str, w: str, rank: int
) -> None:
    """Install a single-dummy long-edge chain ``v -> head -> w``.

    The ``head`` node carries ``dummy="edge"`` + ``edge_obj`` pointing at the
    original endpoints (the exact shape R250 ``normalize`` leaves on a rank-2
    span), plus the two weight-default segments wiring it into the chain.
    Records ``head`` on ``GraphConfig.dummy_chains`` (the R250 ledger).
    """
    g.set_node(head, GraphNode(rank=rank, dummy="edge", edge_obj=Edge(v=v, w=w)))
    g.set_edge(v, head, GraphEdge(), None)
    g.set_edge(head, w, GraphEdge(), None)
    g.graph().dummy_chains = [head]


# === basic single-cluster re-parent =========================================


def test_basic_reparent_into_spanning_cluster() -> None:
    """A dummy whose rank sits inside ``cluster``'s span is re-parented onto it.

    ``cluster`` spans ``[0, 2]`` and contains ``a`` (rank 0) and ``c`` (rank 2);
    the rank-1 dummy ``d1`` (chain head of the ``a -> c`` long edge) starts at
    the root and must land inside ``cluster`` because its rank (1) is contained.
    """
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode(min_rank=0, max_rank=2))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_parent("a", "cluster")
    g.set_parent("c", "cluster")
    _wire_chain(g, "d1", "a", "c", rank=1)

    parent_dummy_chains(g)

    assert g.parent("d1") == "cluster"


# === ascending arm ==========================================================


def test_ascending_advances_past_shallow_subgraph() -> None:
    """A dummy whose rank exceeds ``a_sub``'s ``max_rank`` advances to the root.

    ``a`` lives in ``a_sub`` (``max_rank == 0``); the rank-1 dummy outgrows
    ``a_sub`` (``max_rank 0 < 1``), so the ascending loop advances the cursor
    past ``a_sub`` up to ``root`` (the LCA), and the dummy is re-parented onto
    ``root`` -- exercising the ``max_rank < node_rank`` branch.
    """
    g = _make_compound_graph()
    g.set_node("root", GraphNode(min_rank=0, max_rank=5))
    g.set_node("a_sub", GraphNode(min_rank=0, max_rank=0))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=5))
    g.set_parent("a_sub", "root")
    g.set_parent("a", "a_sub")
    g.set_parent("c", "root")
    _wire_chain(g, "d1", "a", "c", rank=1)

    parent_dummy_chains(g)

    assert g.parent("d1") == "root"


# === descending arm =========================================================


def test_descending_advances_into_deep_subgraph() -> None:
    """A dummy whose rank meets ``c_sub``'s ``min_rank`` descends into it.

    ``c`` lives in ``c_sub`` (``min_rank == 3``); the rank-4 dummy fits inside
    ``c_sub`` (``min_rank 3 <= 4``), so the descending loop advances the cursor
    down into ``c_sub`` and re-parents the dummy onto it -- exercising the
    ``min_rank <= node_rank`` advance branch.
    """
    g = _make_compound_graph()
    g.set_node("root", GraphNode(min_rank=0, max_rank=5))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c_sub", GraphNode(min_rank=3, max_rank=5))
    g.set_node("c", GraphNode(rank=5))
    g.set_parent("a", "root")
    g.set_parent("c_sub", "root")
    g.set_parent("c", "c_sub")
    _wire_chain(g, "d4", "a", "c", rank=4)

    parent_dummy_chains(g)

    assert g.parent("d4") == "c_sub"


def test_descending_does_not_advance_when_min_rank_too_high() -> None:
    """A dummy whose rank is below ``c_sub``'s ``min_rank`` stays at the LCA.

    The rank-1 dummy does not fit ``c_sub`` (``min_rank 3 > 1``), so the
    descending loop's advance condition fails immediately and the cursor stays
    on the LCA (``root``) -- exercising the ``min_rank <= node_rank`` false
    branch (the loop body's ``else: break``).
    """
    g = _make_compound_graph()
    g.set_node("root", GraphNode(min_rank=0, max_rank=5))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c_sub", GraphNode(min_rank=3, max_rank=5))
    g.set_node("c", GraphNode(rank=5))
    g.set_parent("a", "root")
    g.set_parent("c_sub", "root")
    g.set_parent("c", "c_sub")
    _wire_chain(g, "d1", "a", "c", rank=1)

    parent_dummy_chains(g)

    assert g.parent("d1") == "root"


# === flat-graph / empty-ledger no-ops =======================================


def test_flat_graph_keeps_dummy_at_root() -> None:
    """A compound graph with no compound nesting re-parents the dummy onto the root.

    With every node a direct child of ``GRAPH_NODE`` (no explicit ``set_parent``
    into a subgraph), both endpoints share no compound ancestor, so grok's LCA
    walk yields ``lca = None`` and the dummy is re-parented via
    ``set_parent(v, None)``. graphlib's ``parent()`` API normalises the
    synthetic ``GRAPH_NODE`` root to ``None`` (mirrors grok), so the re-parented
    dummy is observed as ``parent() is None`` -- effectively a no-op, since the
    dummy was already a root-level node.
    """
    g = _make_compound_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    _wire_chain(g, "d1", "a", "c", rank=1)

    parent_dummy_chains(g)  # must not raise

    # set_parent(v, None) maps onto GRAPH_NODE internally; parent() reports None.
    assert g.parent("d1") is None


def test_none_dummy_chains_is_noop() -> None:
    """``dummy_chains is None`` (never normalized) yields an empty walk."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_edge("a", "c", GraphEdge(), None)
    assert g.graph().dummy_chains is None

    parent_dummy_chains(g)  # must not raise


def test_empty_dummy_chains_is_noop() -> None:
    """An empty ``dummy_chains`` list walks nothing."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode(rank=0))
    g.graph().dummy_chains = []

    parent_dummy_chains(g)

    assert g.graph().dummy_chains == []


# === private helpers ========================================================


def test_postorder_interval_contains_children() -> None:
    """``_postorder`` gives every parent an interval that contains its children."""
    from minimax_code.dagre.layout.parent_dummy_chains import _postorder

    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_node("c", GraphNode())
    g.set_node("d1", GraphNode())
    g.set_parent("a", "cluster")
    g.set_parent("c", "cluster")

    nums = _postorder(g)

    a_pn = nums.get("a")
    c_pn = nums.get("c")
    cluster_pn = nums.get("cluster")
    d1_pn = nums.get("d1")
    assert a_pn is not None and c_pn is not None
    assert cluster_pn is not None and d1_pn is not None
    # cluster's interval strictly contains a's and c's (nested post-order)
    assert cluster_pn[0] <= a_pn[0] and a_pn[1] <= cluster_pn[1]
    assert cluster_pn[0] <= c_pn[0] and c_pn[1] <= cluster_pn[1]
    # d1 is a root sibling, so its interval is disjoint and after cluster's
    assert d1_pn[0] > cluster_pn[1]


def test_find_path_returns_lca_and_stitched_path() -> None:
    """``_find_path`` returns the LCA plus the ``v -> lca -> w`` node list."""
    from minimax_code.dagre.layout.parent_dummy_chains import _find_path, _postorder

    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_node("c", GraphNode())
    g.set_parent("a", "cluster")
    g.set_parent("c", "cluster")
    nums = _postorder(g)

    path, lca = _find_path(g, nums, "a", "c")

    assert lca == "cluster"
    # a and c share cluster as their direct parent -> the path is just [cluster]
    assert path == ["cluster"]


def test_find_path_lca_is_none_when_siblings_detached() -> None:
    """Two root-level siblings (no shared compound ancestor) yield ``lca = None``.

    grok's ``find_path`` ascends each endpoint's ``parent()`` chain; a root-level
    node has ``parent() == None`` (graphlib normalises ``GRAPH_NODE`` to
    ``None``), so the first ``v``-arm iteration pushes ``None`` onto ``v_path``
    and breaks with ``lca = None``. The ``w``-arm then hits ``parent == lca``
    (``None == None``) on its first step and breaks empty, leaving
    ``path == [None]``.
    """
    from minimax_code.dagre.layout.parent_dummy_chains import _find_path, _postorder

    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("c", GraphNode())
    nums = _postorder(g)

    path, lca = _find_path(g, nums, "a", "c")

    assert lca is None
    assert path == [None]


def test_path_get_returns_slot_or_fallback() -> None:
    """``_path_get`` returns the in-range slot, else the LCA fallback."""
    from minimax_code.dagre.layout.parent_dummy_chains import _path_get

    assert _path_get(["x", "y"], 0, "fb") == "x"
    assert _path_get(["x", "y"], 1, "fb") == "y"
    assert _path_get(["x", "y"], 5, "fb") == "fb"  # out of range -> fallback
    assert _path_get([], 0, None) is None  # empty path -> fallback


# === in-place mutation ======================================================


def test_parent_dummy_chains_mutates_graph_in_place() -> None:
    """``parent_dummy_chains`` mutates the live graph (no copy/return)."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode(min_rank=0, max_rank=2))
    g.set_node("a", GraphNode(rank=0))
    g.set_node("c", GraphNode(rank=2))
    g.set_parent("a", "cluster")
    g.set_parent("c", "cluster")
    _wire_chain(g, "d1", "a", "c", rank=1)
    assert g.parent("d1") != "cluster"  # before: dummy not yet in cluster

    parent_dummy_chains(g)

    assert g.parent("d1") == "cluster"  # same graph, now re-parented


# === barrel surface contract ================================================


def test_parent_dummy_chains_not_in_dagre_all() -> None:
    """``parent_dummy_chains`` earns no crate-root barrel slot."""
    assert "parent_dummy_chains" not in dagre.__all__
    assert "parent_dummy_chains" not in dagre.__all__


def test_parent_dummy_chains_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "parent_dummy_chains")


def test_submodule_exports_parent_dummy_chains() -> None:
    """The submodule exposes exactly ``parent_dummy_chains`` (the single ``pub fn``)."""
    import minimax_code.dagre.layout.parent_dummy_chains as pdc_mod

    assert pdc_mod.__all__ == ["parent_dummy_chains"]
    assert pdc_mod.parent_dummy_chains is parent_dummy_chains


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R252 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_parent_dummy_chains_import() -> None:
    """Importing ``parent_dummy_chains`` binds the ``layout`` subpackage on ``dagre``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.parent_dummy_chains as pdc_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.parent_dummy_chains is pdc_mod
