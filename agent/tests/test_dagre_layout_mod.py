"""Black-box tests for the migrated dagre layout/mod orchestrator (R268).

Exercises :mod:`minimax_code.dagre.layout.mod` -- the **top-level orchestrator**
that closes the dagre layout stack at 21/21 (100 %): the ``layout()`` user entry
point, the ``build_layout_graph()`` / ``update_input_graph()`` adapters that copy
the whitelisted layout-influencing attributes in and the computed coordinates
out, the ``run_layout()`` 28-step pipeline, and the two default-stamper helpers
(``set_graph_label_default_values`` / ``set_edge_label_default_values``). The
fixtures dress a bare ``Graph<GraphConfig, GraphNode, GraphEdge>`` directed
non-multigraph non-compound input graph (``build_layout_graph`` builds its own
compound multigraph internally, so the input stays simple). Covers:

* :func:`build_layout_graph` white-box: the layout graph carries every input
  node + edge; every label is deep-copied so mutating the layout graph never
  leaks back (the ``copy.deepcopy`` isolation that ``run_layout``'s in-place
  mutations rely on); a config whose fields are explicitly ``None`` gets the
  defaults stamped (``ranksep = 50.0`` / ``rankdir = "tb"``),
* ``set_graph_label_default_values`` / ``set_edge_label_default_values``
  white-box: an all-``None`` config / edge label is filled (only ``None``
  fields -- an explicit caller value always wins),
* :func:`layout` end-to-end: the 28-step pipeline runs on a single
  ``a -> b -> c`` chain and stamps monotonically increasing ``y`` (top-to-
  bottom ``tb`` stacking) with equal ``x`` (a single chain has no horizontal
  spread); a two-layer bipartite ``a -> c`` / ``b -> d`` separates the two
  source nodes horizontally (``a.x != b.x``) and stacks the two ranks
  vertically (``a.y == b.y`` on rank 0, ``c.y == d.y`` on rank 1, rank 0
  above rank 1); a self-loop ``a -> a`` and an empty graph both run without
  raising (``remove_self_edges`` detaches the loop; the empty graph hits every
  stage's empty fast-path),
* the barrel surface contract: every ``mod`` symbol stays out of
  ``dagre.__all__`` and off the crate-root top level (the R246 barrel count
  stays at 4); ``mod.__all__`` is the ASCII-sorted four-symbol list; ``mod``
  is reachable via ``dagre.layout``.
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.mod import (
    build_layout_graph,
    layout,
    run_layout,
    set_edge_label_default_values,
    set_graph_label_default_values,
    update_input_graph,
)
from minimax_code.data_structures import Graph, GraphOption


def _make_input_graph() -> Graph:
    """Build an empty directed non-multigraph non-compound input graph.

    ``build_layout_graph`` constructs its own ``multigraph=True compound=True``
    layout graph internally, so the input graph stays a simple directed graph.
    ``set_graph(GraphConfig())`` dresses it with a default config (``ranksep``
    / ``nodesep`` / ``rankdir`` defaults come from ``GraphConfig`` itself);
    ``build_layout_graph`` deep-copies it onto the layout graph.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _node(width: float = 0.0, height: float = 0.0) -> GraphNode:
    """Stamp an input node with the geometry fields ``layout`` reads."""
    return GraphNode(width=width, height=height)


def _edge(g: Graph, v: str, w: str) -> None:
    """Add ``v -> w`` with the default edge label."""
    g.set_edge(v, w, GraphEdge(), None)


# === set_graph_label_default_values / set_edge_label_default_values ==========


def test_set_graph_label_default_values_fills_none_fields() -> None:
    """An all-``None`` config is filled with the canonical defaults."""
    label = GraphConfig(
        ranksep=None,
        edgesep=None,
        nodesep=None,
        rankdir=None,
        marginx=None,
        marginy=None,
    )
    set_graph_label_default_values(label)
    assert label.ranksep == 50.0
    assert label.edgesep == 20.0
    assert label.nodesep == 50.0
    assert label.rankdir == "tb"
    assert label.marginx == 0.0
    assert label.marginy == 0.0


def test_set_graph_label_default_values_keeps_explicit_values() -> None:
    """An explicit caller value wins (only ``None`` fields are filled)."""
    label = GraphConfig(ranksep=7.0, rankdir="lr")
    set_graph_label_default_values(label)
    assert label.ranksep == 7.0
    assert label.rankdir == "lr"


def test_set_edge_label_default_values_fills_none_fields() -> None:
    """An all-``None`` edge label is filled with the canonical defaults."""
    edge = GraphEdge(
        minlen=None,
        weight=None,
        width=None,
        height=None,
        labeloffset=None,
        labelpos=None,
    )
    set_edge_label_default_values(edge)
    assert edge.minlen == 1.0
    assert edge.weight == 1.0
    assert edge.width == 0.0
    assert edge.height == 0.0
    assert edge.labeloffset == 10.0
    assert edge.labelpos == "r"


def test_set_edge_label_default_values_keeps_explicit_values() -> None:
    """An explicit edge value wins (only ``None`` fields are filled)."""
    edge = GraphEdge(minlen=3.0, weight=5.0, labelpos="c")
    set_edge_label_default_values(edge)
    assert edge.minlen == 3.0
    assert edge.weight == 5.0
    assert edge.labelpos == "c"


# === build_layout_graph: white-box ==========================================


def test_build_layout_graph_copies_nodes_and_edges() -> None:
    """The layout graph carries every input node + edge."""
    g = _make_input_graph()
    g.set_node("a", _node(width=10.0))
    g.set_node("b", _node(width=10.0))
    _edge(g, "a", "b")
    lg = build_layout_graph(g)
    assert set(lg.nodes()) >= {"a", "b"}
    assert lg.has_edge("a", "b", None)


def test_build_layout_graph_isolates_node_labels() -> None:
    """Mutating a layout-graph node never leaks back (deepcopy isolation).

    ``run_layout`` mutates layout-graph labels in place; without the
    ``copy.deepcopy`` the layout graph would share node references with the
    input graph and the mutations would leak back.
    """
    g = _make_input_graph()
    g.set_node("a", _node(width=10.0))
    lg = build_layout_graph(g)
    lg_node = lg.node("a")
    assert lg_node is not None
    lg_node.width = 999.0
    assert g.node("a").width == 10.0


def test_build_layout_graph_isolates_graph_label() -> None:
    """The config is deep-copied onto the layout graph (cross-boundary clone)."""
    g = _make_input_graph()
    lg = build_layout_graph(g)
    lg_label = lg.graph()
    assert lg_label is not None
    lg_label.ranksep = 999.0
    assert g.graph().ranksep != 999.0


def test_build_layout_graph_stamps_config_defaults() -> None:
    """An all-``None`` input config is filled on the layout graph."""
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(
        GraphConfig(
            ranksep=None,
            edgesep=None,
            nodesep=None,
            rankdir=None,
            marginx=None,
            marginy=None,
        )
    )
    lg = build_layout_graph(g)
    label = lg.graph()
    assert label is not None
    assert label.ranksep == 50.0
    assert label.rankdir == "tb"


def test_build_layout_graph_stamps_edge_defaults() -> None:
    """A ``None``-free edge still gets the default ``minlen`` / ``weight`` stamp."""
    g = _make_input_graph()
    g.set_node("a", _node())
    g.set_node("b", _node())
    _edge(g, "a", "b")
    lg = build_layout_graph(g)
    edge = lg.edge("a", "b", None)
    assert edge is not None
    assert edge.minlen == 1.0
    assert edge.weight == 1.0
    assert edge.labelpos == "r"


# === layout: end-to-end ====================================================


def test_layout_single_chain_stamps_monotone_y_and_equal_x() -> None:
    """A single ``a -> b -> c`` chain stacks vertically with no horizontal spread.

    ``tb`` rank direction: every edge advances the rank, so ``position_y``
    stacks the three nodes top-to-bottom (``a.y < b.y < c.y``); a single chain
    has no horizontal spread, so ``position_x`` collapses all three to one
    ``x``. Exercises the full 28-step pipeline end-to-end.
    """
    g = _make_input_graph()
    g.set_node("a", _node(width=10.0, height=10.0))
    g.set_node("b", _node(width=10.0, height=10.0))
    g.set_node("c", _node(width=10.0, height=10.0))
    _edge(g, "a", "b")
    _edge(g, "b", "c")
    layout(g)
    a, b, c = g.node("a"), g.node("b"), g.node("c")
    assert a is not None and b is not None and c is not None
    assert a.y < b.y < c.y
    assert a.x == b.x == c.x


def test_layout_two_layer_bipartite_spreads_x_and_stacks_y() -> None:
    """A bipartite ``a -> c`` / ``b -> d`` separates horizontally and stacks vertically.

    rank 0: ``a``, ``b``; rank 1: ``c``, ``d``. ``position_y`` centres each
    rank's nodes at the same ``y`` (``a.y == b.y``, ``c.y == d.y``) with rank 0
    above rank 1 (``a.y < c.y``); ``position_x`` separates the two source
    blocks (``a.x != b.x``).
    """
    g = _make_input_graph()
    g.set_node("a", _node(width=10.0, height=10.0))
    g.set_node("b", _node(width=10.0, height=10.0))
    g.set_node("c", _node(width=10.0, height=10.0))
    g.set_node("d", _node(width=10.0, height=10.0))
    _edge(g, "a", "c")
    _edge(g, "b", "d")
    layout(g)
    a, b, c, d = g.node("a"), g.node("b"), g.node("c"), g.node("d")
    assert a is not None and b is not None and c is not None and d is not None
    assert a.y == b.y
    assert c.y == d.y
    assert a.y < c.y
    assert a.x != b.x


def test_layout_self_loop_does_not_crash() -> None:
    """A self-loop ``a -> a`` runs end-to-end (``remove_self_edges`` detaches it).

    ``rank`` cannot layer a self-loop, so ``remove_self_edges`` detaches it onto
    ``node.self_edges`` before ``acyclic`` / ``rank`` and ``insert_self_edges``
    re-inserts it as a ``selfedge`` dummy after ``order``; ``layout`` runs the
    full pipeline without raising and stamps a finite coordinate on ``a``.
    """
    g = _make_input_graph()
    g.set_node("a", _node(width=10.0, height=10.0))
    _edge(g, "a", "a")
    layout(g)
    a = g.node("a")
    assert a is not None
    assert isinstance(a.x, float)
    assert isinstance(a.y, float)


def test_layout_empty_graph_does_not_crash() -> None:
    """An empty graph (no nodes, no edges) runs every stage's empty fast-path."""
    g = _make_input_graph()
    layout(g)
    assert list(g.nodes()) == []
    assert list(g.edges()) == []


# === barrel surface contract ===============================================


def test_layout_symbols_not_in_dagre_all() -> None:
    """No ``mod`` symbol earns a crate-root barrel slot."""
    assert "layout" not in dagre.__all__
    assert "run_layout" not in dagre.__all__
    assert "build_layout_graph" not in dagre.__all__
    assert "update_input_graph" not in dagre.__all__


def test_layout_fn_not_promoted_over_subpackage() -> None:
    """The ``layout()`` fn is not promoted to the crate-root top level.

    ``minimax_code.dagre.layout`` is the **subpackage** (the directory of the
    layout stages), not the ``layout()`` orchestrator fn -- the fn lives at
    ``dagre.layout.mod.layout``. The name is occupied by the sub-package, so
    the fn is never callable at the top level; ``run_layout`` is not bound at
    all (it has no same-named sub-package to occupy the slot, but earns no
    crate-root slot either).
    """
    import types

    assert isinstance(dagre.layout, types.ModuleType)
    assert not callable(dagre.layout)
    assert not hasattr(dagre, "run_layout")


def test_mod_submodule_exports_four_symbols() -> None:
    """The submodule exposes the four public entries (ASCII-sorted ``__all__``)."""
    import minimax_code.dagre.layout.mod as mod_mod

    assert mod_mod.__all__ == [
        "build_layout_graph",
        "layout",
        "run_layout",
        "update_input_graph",
    ]
    assert mod_mod.layout is layout
    assert mod_mod.run_layout is run_layout
    assert mod_mod.build_layout_graph is build_layout_graph
    assert mod_mod.update_input_graph is update_input_graph


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R268 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_mod_reachable_via_dagre_layout() -> None:
    """``mod`` is reachable as ``dagre.layout.mod`` (mirrors grok ``pub mod mod``)."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.mod as mod_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.mod is mod_mod
    assert mod_mod.layout is layout
