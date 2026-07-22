"""Black-box tests for the migrated dagre nesting_graph layer (R253).

Exercises :mod:`minimax_code.dagre.layout.nesting_graph` through its two public
entry points (``run`` / ``cleanup``) on a real compound
``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state R251 ``acyclic`` leaves
behind, since ``run_layout`` calls ``nesting_graph::run`` immediately after
``acyclic::run``). Covers:

* the ``_root`` dummy mint and the ``nesting_root`` / ``node_rank_factor``
  graph-label stamps,
* the ``node_sep = 2 * height + 1`` rank-multiplier for flat (height 0 -> 1.0),
  single-compound (height 1 -> 3.0) and two-level nested (height 2 -> 5.0)
  forests,
* every existing edge's ``minlen`` scaling by ``node_sep``,
* compound-node ``_bt`` / ``_bb`` border sentinel creation + ``set_parent``
  attachment + ``border_top`` / ``border_bottom`` label stamps,
* the ``top -> child_top`` / ``child_bottom -> bottom`` ``nesting_edge``
  stitching for a compound node's children,
* the ``_root -> leaf`` zero-weight link for leaf nodes (no ``nesting_edge``
  mark, so it is swept by ``remove_node(_root)`` rather than the
  ``nesting_edge`` walk),
* the ``_root -> top`` connector for top-level compound nodes (``parent`` is
  ``None``) that keeps the scaffolding connected,
* the leaf-child ``minlen = height - depth(parent) + 1`` stretch,
* the leaf-child doubled weight (``2 * weight``) vs the compound-child halved
  weight (``weight``),
* ``cleanup`` removing the ``_root`` node, clearing ``nesting_root``, dropping
  every ``nesting_edge`` edge, preserving original edges, and (faithful to
  grok) *keeping* the ``_bt`` / ``_bb`` border sentinels as orphans,
* the run-then-cleanup round-trip,
* the private ``_tree_depths`` level numbering and ``_sum_weights`` weight
  total,
* in-place mutation,
* the barrel surface contract (``run`` / ``cleanup`` stay out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.nesting_graph import cleanup, run
from minimax_code.data_structures import Graph, GraphOption


def _make_compound_graph() -> Graph:
    """Build an empty compound directed graph with a ``GraphConfig`` label.

    ``compound=True`` because :func:`run` erects the nesting scaffolding via
    ``set_parent`` (attaching ``_bt`` / ``_bb`` sentinels onto their compound
    owner), which is only meaningful on a compound graph; ``multigraph=True``
    to mirror the production graph config. The default ``nesting_root`` on the
    freshly minted ``GraphConfig`` is ``None`` until :func:`run` seeds it.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _nesting_edges(g: Graph) -> list[object]:
    """Collect every edge object whose label carries ``nesting_edge`` truthy."""
    out: list[object] = []
    for e in g.edges():
        label = g.edge_with_obj(e)
        if label is not None and label.nesting_edge:
            out.append(e)
    return out


# === run: _root dummy + graph-label stamps =================================


def test_run_creates_root_dummy_and_sets_nesting_root() -> None:
    """``run`` mints a ``_root`` dummy and records it on ``nesting_root``."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    run(g)
    root = g.graph().nesting_root
    assert root is not None
    assert root.startswith("_root")  # R248 add_dummy_node prefix
    root_node = g.node(root)
    assert root_node is not None
    assert root_node.dummy == "root"  # node_type stamp


def test_run_sets_node_rank_factor() -> None:
    """``run`` stores ``node_sep`` on ``node_rank_factor`` for later rank cleanup."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    assert g.graph().node_rank_factor is None
    run(g)
    # flat forest: heights all 1 -> height 0 -> node_sep = 2*0+1 = 1.0
    assert g.graph().node_rank_factor == 1.0


# === run: node_sep = 2 * height + 1 ========================================


def test_run_flat_forest_node_sep_is_one() -> None:
    """A flat forest (every node a root child, depth 1) yields ``node_sep = 1.0``."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    run(g)
    # depths: a=1, b=1 -> max=1 -> height=0 -> node_sep = 1.0
    assert g.graph().node_rank_factor == 1.0


def test_run_single_compound_node_sep_is_three() -> None:
    """A single-level compound forest (deepest depth 2) yields ``node_sep = 3.0``."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    # depths: cluster=1, a=2 -> max=2 -> height=1 -> node_sep = 3.0
    assert g.graph().node_rank_factor == 3.0


def test_run_two_level_nested_node_sep_is_five() -> None:
    """A two-level nested compound forest (deepest depth 3) yields ``node_sep = 5.0``."""
    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    run(g)
    # depths: outer=1, inner=2, a=3 -> max=3 -> height=2 -> node_sep = 5.0
    assert g.graph().node_rank_factor == 5.0


# === run: edge minlen scaling =============================================


def test_run_scales_existing_edge_minlen_by_node_sep() -> None:
    """Every pre-existing edge's ``minlen`` is multiplied by ``node_sep``."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_parent("a", "cluster")
    g.set_parent("b", "cluster")
    g.set_edge("a", "b", GraphEdge(minlen=2.0), None)
    run(g)
    # node_sep = 3.0 (single compound) -> minlen = 2.0 * 3.0 = 6.0
    edge = g.edge("a", "b", None)
    assert edge is not None
    assert edge.minlen == 6.0


def test_run_defaults_unset_minlen_to_one_before_scaling() -> None:
    """An edge with ``minlen is None`` is treated as 1.0 before scaling."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_parent("a", "cluster")
    g.set_parent("b", "cluster")
    g.set_edge("a", "b", GraphEdge(), None)  # minlen unset
    run(g)
    edge = g.edge("a", "b", None)
    # grok: ``unwrap_or(1.0)`` -> 1.0 * 3.0 = 3.0
    assert edge is not None
    assert edge.minlen == 3.0


# === run: border sentinel creation ========================================


def test_run_creates_border_sentinels_for_compound_node() -> None:
    """A compound node gets ``_bt`` / ``_bb`` border sentinels stamped on its label."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    cluster = g.node("cluster")
    assert cluster.border_top is not None
    assert cluster.border_top.startswith("_bt")
    assert cluster.border_bottom is not None
    assert cluster.border_bottom.startswith("_bb")
    assert g.node(cluster.border_top).dummy == "border"
    assert g.node(cluster.border_bottom).dummy == "border"


def test_run_parents_border_sentinels_onto_compound_owner() -> None:
    """The ``_bt`` / ``_bb`` sentinels are parented onto their compound owner."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    cluster = g.node("cluster")
    assert g.parent(cluster.border_top) == "cluster"
    assert g.parent(cluster.border_bottom) == "cluster"


def test_run_does_not_stamp_border_on_leaf() -> None:
    """A leaf node (no compound children) gets no ``border_top`` / ``border_bottom``."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    leaf = g.node("a")
    assert leaf.border_top is None
    assert leaf.border_bottom is None


# === run: nesting-edge stitching ==========================================


def test_run_stitches_nesting_edges_around_leaf_child() -> None:
    """A compound node with one leaf child gets ``_bt -> child`` + ``child -> _bb``."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    cluster = g.node("cluster")
    # top -> child (= "a", leaf resolves to itself)
    top_edge = g.edge(cluster.border_top, "a", None)
    assert top_edge is not None
    assert top_edge.nesting_edge is True
    # child -> bottom
    bot_edge = g.edge("a", cluster.border_bottom, None)
    assert bot_edge is not None
    assert bot_edge.nesting_edge is True


def test_run_stitches_nesting_edges_around_compound_child() -> None:
    """A compound child resolves ``child_top`` / ``child_bottom`` to its own sentinels."""
    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    run(g)
    outer = g.node("outer")
    inner = g.node("inner")
    # outer.top -> inner.top (not -> "inner")
    top_edge = g.edge(outer.border_top, inner.border_top, None)
    assert top_edge is not None
    assert top_edge.nesting_edge is True
    # inner.bottom -> outer.bottom
    bot_edge = g.edge(inner.border_bottom, outer.border_bottom, None)
    assert bot_edge is not None
    assert bot_edge.nesting_edge is True


def test_run_links_root_to_leaf_with_zero_weight() -> None:
    """A leaf node gets a ``_root -> leaf`` edge with ``weight == 0`` and no nesting mark."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    run(g)
    root = g.graph().nesting_root
    edge = g.edge(root, "a", None)
    assert edge is not None
    assert edge.weight == 0.0
    # The leaf link carries NO nesting_edge mark -- it is swept by
    # remove_node(_root) in cleanup, not by the nesting_edge walk.
    assert not edge.nesting_edge


def test_run_flat_forest_links_root_to_every_leaf() -> None:
    """A flat forest links ``_root`` to every leaf."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_node("c", GraphNode())
    run(g)
    root = g.graph().nesting_root
    for leaf in ("a", "b", "c"):
        edge = g.edge(root, leaf, None)
        assert edge is not None
        assert edge.weight == 0.0


def test_run_links_root_to_top_level_compound_top() -> None:
    """A top-level compound node (``parent is None``) gets a ``_root -> top`` connector."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    root = g.graph().nesting_root
    cluster = g.node("cluster")
    edge = g.edge(root, cluster.border_top, None)
    assert edge is not None
    assert edge.weight == 0.0
    assert edge.nesting_edge is True


def test_run_does_not_link_root_to_nested_compound_top() -> None:
    """A nested compound node (``parent`` set) gets NO ``_root -> top`` connector."""
    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    run(g)
    root = g.graph().nesting_root
    inner = g.node("inner")
    # inner has parent "outer" -> no _root -> inner.top connector
    assert g.edge(root, inner.border_top, None) is None


# === run: leaf-child minlen stretch + weight =============================


def test_run_leaf_child_minlen_is_height_minus_depth_plus_one() -> None:
    """A leaf child's nesting edge ``minlen = height - depth(parent) + 1``."""
    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    run(g)
    # heights: outer=1, inner=2, a=3 -> max=3 -> height=2
    # inner's leaf child a: minlen = 2 - depth(inner)=2 + 1 = 1
    inner = g.node("inner")
    edge = g.edge(inner.border_top, "a", None)
    assert edge is not None
    assert edge.minlen == 1.0


def test_run_leaf_child_nesting_weight_is_doubled() -> None:
    """A leaf child (no border) gets ``this_weight = 2 * weight``."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    # no pre-existing edges -> _sum_weights = 0 -> weight = 1.0
    # leaf child a: this_weight = 2 * 1.0 = 2.0
    cluster = g.node("cluster")
    edge = g.edge(cluster.border_top, "a", None)
    assert edge is not None
    assert edge.weight == 2.0


def test_run_compound_child_nesting_weight_is_halved() -> None:
    """A compound child (has border) gets ``this_weight = weight`` (halved)."""
    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    run(g)
    # weight = 1.0; inner is compound (has border_top) -> this_weight = weight = 1.0
    outer = g.node("outer")
    inner = g.node("inner")
    edge = g.edge(outer.border_top, inner.border_top, None)
    assert edge is not None
    assert edge.weight == 1.0


# === cleanup =============================================================


def test_cleanup_removes_root_and_clears_nesting_root() -> None:
    """``cleanup`` drops the ``_root`` node and clears ``nesting_root``."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    run(g)
    root = g.graph().nesting_root
    assert root is not None and g.node(root) is not None
    cleanup(g)
    assert g.node(root) is None  # _root removed
    assert g.graph().nesting_root is None


def test_cleanup_drops_every_nesting_edge() -> None:
    """``cleanup`` removes every ``nesting_edge``-marked edge."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    assert len(_nesting_edges(g)) > 0
    cleanup(g)
    assert _nesting_edges(g) == []


def test_cleanup_preserves_original_edges() -> None:
    """``cleanup`` keeps the original (non-nesting) edges intact."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=1.0), None)
    run(g)
    cleanup(g)
    assert g.has_edge("a", "b", None)
    edge = g.edge("a", "b", None)
    assert edge.weight == 1.0  # payload untouched


def test_cleanup_keeps_border_sentinels_as_orphans() -> None:
    """``cleanup`` does NOT remove the ``_bt`` / ``_bb`` border nodes (grok-faithful).

    grok's ``cleanup`` only removes the ``_root`` node and every ``nesting_edge``
    edge; the ``_bt`` / ``_bb`` sentinels are left behind as orphan nodes (their
    incident edges were the nesting edges, now gone). This is the faithful
    port -- later stages are expected to ignore or reclaim these border nodes.
    """
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("a", "cluster")
    run(g)
    cluster = g.node("cluster")
    top, bottom = cluster.border_top, cluster.border_bottom
    cleanup(g)
    assert g.node(top) is not None  # border sentinel survives
    assert g.node(bottom) is not None


def test_run_then_cleanup_roundtrip_restores_original_edge_set() -> None:
    """run -> cleanup leaves the graph with only its original edges."""
    g = _make_compound_graph()
    g.set_node("cluster", GraphNode())
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_parent("a", "cluster")
    g.set_parent("b", "cluster")
    g.set_edge("a", "b", GraphEdge(), None)
    original = len(list(g.edges()))
    run(g)
    assert len(list(g.edges())) > original  # scaffolding added edges
    cleanup(g)
    # Only the original a -> b edge survives (border sentinels are orphaned,
    # carrying no edges).
    assert len(list(g.edges())) == original
    assert g.has_edge("a", "b", None)


def test_cleanup_noop_on_unrun_graph() -> None:
    """``cleanup`` on a graph that never ran ``run`` is a safe no-op."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    cleanup(g)  # nesting_root is None -> no _root removal; no nesting edges
    assert g.graph().nesting_root is None
    assert g.has_edge("a", "b", None)


# === private helpers =====================================================


def test_tree_depths_numbers_compound_levels() -> None:
    """``_tree_depths`` numbers every node with its compound-forest level."""
    from minimax_code.dagre.layout.nesting_graph import _tree_depths

    g = _make_compound_graph()
    g.set_node("outer", GraphNode())
    g.set_node("inner", GraphNode())
    g.set_node("a", GraphNode())
    g.set_parent("inner", "outer")
    g.set_parent("a", "inner")
    depths = _tree_depths(g)
    assert depths.get("outer") == 1  # root child -> depth 1
    assert depths.get("inner") == 2
    assert depths.get("a") == 3


def test_sum_weights_skips_unset_weights() -> None:
    """``_sum_weights`` totals every edge's ``weight`` and skips explicit ``None``.

    grok's ``GraphEdge`` carries a manual ``Default`` impl (``lib.rs`` lines
    112-131) that seeds ``weight = Some(1.0)``, so a freshly minted
    ``GraphEdge()`` already has a real weight (1.0) -- only an *explicit*
    ``None`` is skipped (mirrors grok's ``if let Some(weight) = edge_label
    .weight`` guard). This is the R246 "manual Default, not derived Default"
    contract: the all-``None`` default a derived ``Default`` would give is
    deliberately overridden so the layout pipeline sees real edge weights.
    """
    from minimax_code.dagre.layout.nesting_graph import _sum_weights

    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_node("c", GraphNode())
    g.set_node("d", GraphNode())
    g.set_edge("a", "b", GraphEdge(weight=2.0), None)
    g.set_edge("b", "c", GraphEdge(weight=3.0), None)
    # Explicit None weight -> skipped (a default GraphEdge() would weigh 1.0
    # and contribute, not skip).
    g.set_edge("c", "d", GraphEdge(weight=None), None)
    assert _sum_weights(g) == 5.0  # 2.0 + 3.0 + (None skipped)


# === in-place mutation ===================================================


def test_run_mutates_graph_in_place() -> None:
    """``run`` mutates the live graph object (no copy/return)."""
    g = _make_compound_graph()
    g.set_node("a", GraphNode())
    assert g.graph().nesting_root is None
    run(g)
    assert g.graph().nesting_root is not None  # same graph, now scaffolded


# === barrel surface contract =============================================


def test_nesting_graph_not_in_dagre_all() -> None:
    """Neither ``run`` nor ``cleanup`` earns a crate-root barrel slot."""
    assert "nesting_graph" not in dagre.__all__
    assert "cleanup" not in dagre.__all__
    assert "run" not in dagre.__all__


def test_nesting_graph_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "nesting_graph")
    assert not hasattr(dagre, "cleanup")
    assert not hasattr(dagre, "run")


def test_submodule_exports_run_and_cleanup() -> None:
    """The submodule exposes exactly ``run`` / ``cleanup`` (ASCII-sorted)."""
    import minimax_code.dagre.layout.nesting_graph as ng_mod

    assert ng_mod.__all__ == ["cleanup", "run"]
    assert ng_mod.run is run
    assert ng_mod.cleanup is cleanup


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R253 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_nesting_graph_import() -> None:
    """Importing ``nesting_graph`` binds the ``layout`` subpackage on ``dagre``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.nesting_graph as ng_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.nesting_graph is ng_mod
