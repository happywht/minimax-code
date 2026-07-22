"""Black-box tests for the migrated dagre order/add_subgraph_constraints layer (R263).

Exercises :mod:`minimax_code.dagre.layout.order.add_subgraph_constraints`
through its public entry point :func:`add_subgraph_constraints` on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` compound source graph (the
layer graph R262's :func:`build_layer_graph` produces) and a fresh
constraint graph ``cg`` (the accumulator ``order::mod`` reuses across
sweep ranks). Covers:

* :func:`add_subgraph_constraints` end-to-end: a two-node ``vs`` whose
  ancestors diverge at an intermediate parent triggers a
  ``prev_child -> child`` constraint edge on ``cg``; a two-node ``vs``
  that shares the same intermediate parent adds nothing (the shared
  descendant would form a self-loop, suppressed by the
  ``_prev_child != child`` guard); the root-level path
  (``parent(child) is None``) records the parentless node in
  ``_root_prev`` and triggers a constraint between two distinct
  parentless roots; a shared parentless root adds nothing; an empty
  ``vs`` and a parentless ``v`` leave ``cg`` untouched; the constraint
  edge carries the constraint graph's default edge label
  (``edge_default_factory`` -> ``GraphEdge()`` with ``weight=1.0``); and
  the source graph ``g`` is read-only (``parent`` only, no mutation),
* the barrel surface contract: ``add_subgraph_constraints`` stays out
  of ``dagre.__all__`` and off the crate-root top level (the R246
  barrel count stays at 4); the ``order`` sub-package barrel re-exports
  all six ``order`` leaves (R263 grew ``__all__`` from the R262 five to
  the ASCII-sorted six, with ``add_subgraph_constraints`` first); the
  symbol is reachable via ``dagre.layout.order``.
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.add_subgraph_constraints import add_subgraph_constraints
from minimax_code.data_structures import Graph, GraphOption


def _make_g() -> Graph:
    """Build an empty directed compound multigraph (the layer-graph state).

    ``compound=True`` is mandatory -- :meth:`Graph.parent` (and
    :meth:`Graph.set_parent`) raise on a non-compound graph. ``multigraph=True``
    mirrors R262's :func:`build_layer_graph` output config. ``add_subgraph_constraints``
    reads ``g.parent`` only, so the GraphConfig dressing keeps the fixture
    faithful to the real layer-graph the sweep runs on.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _make_cg() -> Graph:
    """Build an empty directed compound constraint graph (the ``cg`` accumulator).

    ``order::mod`` reuses one constraint graph across sweep ranks; edges
    ``v -> w`` mean ``v`` must precede ``w``. ``multigraph=False`` because a
    constraint is a unique relation. ``add_subgraph_constraints`` calls
    ``cg.set_edge(prev_child, child, None, None)`` only -- the label defaults
    via ``edge_default_factory`` (``GraphEdge()`` -> ``weight=1.0``).
    """
    cg: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    cg.set_graph(GraphConfig())
    return cg


# === add_subgraph_constraints: end-to-end ====================================


def test_different_intermediate_parent_triggers_constraint() -> None:
    """Two nodes whose ancestors diverge at a shared grandparent -> one edge.

    ``vs = ["a", "b"]`` with ``parent(a)=X``, ``parent(b)=Y``,
    ``parent(X)=Z``, ``parent(Y)=Z``. Processing ``a`` records
    ``prev[Z]=X`` (and ``_root_prev=Z``); processing ``b`` sees
    ``_prev_child=prev[Z]=X`` != ``child=Y`` -> ``cg.set_edge("X", "Y")``.
    """
    g = _make_g()
    g.set_parent("a", "X")
    g.set_parent("b", "Y")
    g.set_parent("X", "Z")
    g.set_parent("Y", "Z")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a", "b"])

    assert cg.has_edge("X", "Y", None)
    assert len(cg.edges()) == 1


def test_same_intermediate_parent_adds_no_constraint() -> None:
    """Two nodes sharing the same intermediate parent add nothing.

    ``vs = ["a", "b"]`` with ``parent(a)=X``, ``parent(b)=X``,
    ``parent(X)=Z``. Processing ``b`` sees ``_prev_child=prev[Z]=X`` ==
    ``child=X`` -> the self-loop guard suppresses the edge; climbing to
    ``Z`` hits the root path where ``_prev_child=_root_prev=Z`` ==
    ``child=Z`` -> suppressed again. ``cg`` stays empty.
    """
    g = _make_g()
    g.set_parent("a", "X")
    g.set_parent("b", "X")
    g.set_parent("X", "Z")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a", "b"])

    assert len(cg.edges()) == 0


def test_root_level_constraint_between_parentless_roots() -> None:
    """Two nodes whose parents are themselves parentless -> root-level edge.

    ``vs = ["a", "b"]`` with ``parent(a)=TOP1``, ``parent(b)=TOP2``,
    ``parent(TOP1)=None``, ``parent(TOP2)=None``. Processing ``a`` records
    ``_root_prev=TOP1`` (the ``parent(child) is None`` branch); processing
    ``b`` sees ``_prev_child=_root_prev=TOP1`` != ``child=TOP2`` ->
    ``cg.set_edge("TOP1", "TOP2")``.
    """
    g = _make_g()
    g.set_parent("a", "TOP1")
    g.set_parent("b", "TOP2")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a", "b"])

    assert cg.has_edge("TOP1", "TOP2", None)
    assert len(cg.edges()) == 1


def test_same_parentless_root_adds_no_constraint() -> None:
    """Two nodes sharing the same parentless parent add nothing.

    ``vs = ["a", "b"]`` with ``parent(a)=M``, ``parent(b)=M``,
    ``parent(M)=None``. Processing ``a`` records ``_root_prev=M``;
    processing ``b`` sees ``_prev_child=_root_prev=M`` == ``child=M`` ->
    suppressed. ``cg`` stays empty.
    """
    g = _make_g()
    g.set_parent("a", "M")
    g.set_parent("b", "M")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a", "b"])

    assert len(cg.edges()) == 0


def test_empty_vs_leaves_cg_untouched() -> None:
    """An empty ``vs`` produces no constraint edges (the ``for`` body never runs)."""
    g = _make_g()
    g.set_parent("a", "X")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, [])

    assert len(cg.nodes()) == 0
    assert len(cg.edges()) == 0


def test_parentless_v_is_skipped() -> None:
    """A ``v`` with no parent never enters the ``while`` -> no propagation.

    ``g.parent("a")`` is ``None`` (a is a root), so the ``while child is not
    None`` body is skipped entirely; ``cg`` stays empty.
    """
    g = _make_g()
    g.set_node("a", GraphNode())
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a"])

    assert len(cg.edges()) == 0


def test_constraint_edge_carries_default_edge_label() -> None:
    """``cg.set_edge(prev_child, child, None, None)`` mints a ``GraphEdge()`` label.

    The ``None`` value triggers :meth:`Graph.default_edge_label` on first
    creation -> ``edge_default_factory()`` -> ``GraphEdge()`` with the manual
    ``Default`` impl (``weight=1.0``, ``minlen=1.0``). Mirrors R262's
    ``set_edge(..., GraphEdge(weight=w), None)`` contract from the caller side.
    """
    g = _make_g()
    g.set_parent("a", "X")
    g.set_parent("b", "Y")
    g.set_parent("X", "Z")
    g.set_parent("Y", "Z")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a", "b"])

    edges = cg.edges()
    assert len(edges) == 1
    label = cg.edge_with_obj(edges[0])
    assert isinstance(label, GraphEdge)
    assert label.weight == 1.0


def test_g_is_read_only() -> None:
    """``add_subgraph_constraints`` reads ``g.parent`` only -- ``g`` is unchanged.

    Snapshot the node set + every node's parent chain before the call and
    assert identity after: no node added, no parent rewritten, no edge touched.
    """
    g = _make_g()
    g.set_parent("a", "X")
    g.set_parent("b", "Y")
    g.set_parent("X", "Z")
    g.set_parent("Y", "Z")
    cg = _make_cg()
    nodes_before = set(g.nodes())
    parents_before = {n: g.parent(n) for n in g.nodes()}
    edges_before = list(g.edges())

    add_subgraph_constraints(g, cg, ["a", "b"])

    assert set(g.nodes()) == nodes_before
    assert {n: g.parent(n) for n in g.nodes()} == parents_before
    assert list(g.edges()) == edges_before


def test_break_exits_after_first_constraint_per_v() -> None:
    """The grok ``return ()`` early-exit maps to ``break`` -- one edge max per ``v``.

    ``vs = ["a"]`` with a deep chain ``parent(a)=L1``, ``parent(L1)=M``,
    ``parent(M)=R1``, plus a sibling ``parent(L2)=M`` reachable only via
    ``b`` -- but ``b`` is absent from ``vs``. Processing ``a`` alone walks
    ``L1 -> M -> R1`` recording ``prev[M]=L1`` then ``prev[R1]=M`` (R1's
    parent is None, so it also seeds ``_root_prev=R1``); no second descendant
    ever appears under any ancestor, so no constraint fires. The ``break``
    path is unreachable here; this guards that a single ``v`` with no
    divergent sibling cannot self-trigger.
    """
    g = _make_g()
    g.set_parent("a", "L1")
    g.set_parent("L1", "M")
    g.set_parent("M", "R1")
    cg = _make_cg()

    add_subgraph_constraints(g, cg, ["a"])

    assert len(cg.edges()) == 0


# === barrel surface contract ================================================


def test_add_subgraph_constraints_not_in_dagre_all() -> None:
    """``add_subgraph_constraints`` earns no crate-root barrel slot."""
    assert "add_subgraph_constraints" not in dagre.__all__


def test_add_subgraph_constraints_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "add_subgraph_constraints")


def test_add_subgraph_constraints_submodule_exports_symbol() -> None:
    """The submodule exposes the fn (single-symbol ``__all__``)."""
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod

    assert asc_mod.__all__ == ["add_subgraph_constraints"]
    assert asc_mod.add_subgraph_constraints is add_subgraph_constraints


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R263 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_six() -> None:
    """The ``order`` sub-package barrel re-exports all six ``order`` leaves.

    Synchronised at R263 (``add_subgraph_constraints`` added): the barrel now
    re-exports all six ``order`` leaves, so ``__all__`` grew the R262
    ``["barycenter", "build_layer_graph", "cross_count", "init_order",
    "resolve_conflicts"]`` -> the ASCII-sorted ``["add_subgraph_constraints",
    "barycenter", "build_layer_graph", "cross_count", "init_order",
    "resolve_conflicts"]`` (``"a"`` < ``"b"`` so the new leaf is first).
    Mirrors the R259 -> R258 / R260 -> R258+R259 / R261 -> R258+R259+R260 /
    R262 -> R258+R259+R260+R261 barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod
    import minimax_code.dagre.layout.order.barycenter as bc_mod
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod
    import minimax_code.dagre.layout.order.mod as mod_mod
    import minimax_code.dagre.layout.order.resolve_conflicts as rc_mod
    import minimax_code.dagre.layout.order.sort as sort_mod
    import minimax_code.dagre.layout.order.sort_subgraph as sort_subgraph_mod

    assert order_pkg.__all__ == [
        "add_subgraph_constraints",
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "mod",
        "resolve_conflicts",
        "sort",
        "sort_subgraph",
    ]
    assert order_pkg.add_subgraph_constraints is asc_mod
    assert order_pkg.barycenter is bc_mod
    assert order_pkg.build_layer_graph is blg_mod
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod
    assert order_pkg.mod is mod_mod
    assert order_pkg.resolve_conflicts is rc_mod
    assert order_pkg.sort is sort_mod
    assert order_pkg.sort_subgraph is sort_subgraph_mod


def test_add_subgraph_constraints_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.add_subgraph_constraints.add_subgraph_constraints is add_subgraph_constraints
