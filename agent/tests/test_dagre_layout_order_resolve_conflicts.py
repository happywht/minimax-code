"""Black-box tests for the migrated dagre order/resolve_conflicts layer (R261).

Exercises :mod:`minimax_code.dagre.layout.order.resolve_conflicts` through
its public entry point :func:`resolve_conflicts` (and the
:class:`ResolvedBaryEntry` mutable projection) plus the three private
Kahn-sweep helpers (``_handle_in`` / ``_handle_out`` / ``_merge_entries``)
and the private :class:`ConflictEntry` bookkeeping type. The constraint
graph ``cg`` is a real ``Graph<GraphConfig, GraphNode, GraphEdge>``; the
``entries`` are R260 :class:`Barycenter` records. Covers:

* :func:`resolve_conflicts` end-to-end on an empty entry list (-> []),
  a single ``None``-barycenter entry unconstrained, a single defined
  barycenter unconstrained, an unconstrained multi-entry batch (the
  ``source_set`` LIFO pop order *reverses* the input -- a subtle but
  faithful port), a consistent constraint (``a.bary < b.bary`` with
  ``a -> b`` -> no merge, emitted in topological order), a conflicting
  constraint (``a.bary > b.bary`` with ``a -> b`` -> ``a`` coalesces
  into ``b``, ``vs = [a, b]``, barycenter = weighted mean ``3.5``), a
  ``None`` source barycenter (forces merge regardless), both-``None``
  barycenters (``sum / 0`` -> ``NaN`` reproduced faithfully, weight
  ``0.0``), a weighted two-entry merge (``(4*1 + 2*3) / 4 = 2.5``), a
  three-node chain (``a -> b -> c`` all confluent -> single ``vs = [a,
  b, c]``, barycenter ``3.0``, weight ``3.0``), constraint edges whose
  endpoints are absent from ``entries`` (skipped), and ``cg``
  immutability,
* ``_handle_in`` white-box: the merged-source short-circuit, the
  ``None`` ``u``-barycenter merge trigger, the ``None`` ``v``-barycenter
  merge trigger, the ``u >= v`` merge trigger, and the ``u < v``
  no-merge pass-through,
* ``_handle_out`` white-box: the ``ins`` append + ``indegree``
  decrement, the zero-indegree promotion onto ``source_set``, and the
  above-zero non-promotion,
* ``_merge_entries`` white-box: the weighted-mean barycenter, the
  ``source.vs ++ target.vs`` concatenation order, the ``min`` original
  index, the ``source.merged = True`` absorption flag, and the
  both-``None`` ``NaN`` reproduction,
* the barrel surface contract (``resolve_conflicts`` /
  ``ResolvedBaryEntry`` stay out of ``dagre.__all__``; the crate-root
  barrel count is unchanged at 4; the ``order`` sub-package barrel
  re-exports ``barycenter`` + ``cross_count`` + ``init_order`` +
  ``resolve_conflicts``; ``resolve_conflicts.__all__`` is ASCII-sorted
  with the class before the function).
"""

from __future__ import annotations

import math

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.barycenter import Barycenter
from minimax_code.dagre.layout.order.resolve_conflicts import (
    ConflictEntry,
    ResolvedBaryEntry,
    _handle_in,
    _handle_out,
    _merge_entries,
    resolve_conflicts,
)
from minimax_code.data_structures import Graph, GraphOption


def _make_cg() -> Graph:
    """Build an empty directed graph用作 constraint graph (the ``cg`` arg).

    ``resolve_conflicts`` reads ``cg.edges()`` only (each edge ``v -> w``
    encodes ``v`` must precede ``w``); the GraphConfig / compound /
    multigraph dressing mirrors the production pipeline faithfully but
    no node label is ever read off ``cg``.
    """
    cg: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    cg.set_graph(GraphConfig())
    return cg


def _bary(v: str, barycenter: float | None, weight: float | None) -> Barycenter:
    """Shorthand for a :class:`Barycenter` record (the R260 output shape)."""
    return Barycenter(v=v, barycenter=barycenter, weight=weight)


def _constraint(cg: Graph, v: str, w: str) -> None:
    """Add a ``v -> w`` constraint edge (``v`` must precede ``w``)."""
    cg.set_edge(v, w, GraphEdge(), None)


# === resolve_conflicts: end-to-end ====================================


def test_resolve_empty_entries_returns_empty() -> None:
    """No entries -> no survivors -> empty result list."""
    cg = _make_cg()
    assert resolve_conflicts([], cg) == []


def test_resolve_single_none_barycenter_no_constraints() -> None:
    """A lone ``None``-barycenter node unconstrained passes through.

    ``source_set = [0]`` -> pop 0 -> no ins, no outs -> emit ``[a]``.
    """
    cg = _make_cg()
    result = resolve_conflicts([_bary("a", None, None)], cg)
    assert result == [ResolvedBaryEntry(vs=["a"], i=0, barycenter=None, weight=None)]


def test_resolve_single_defined_barycenter_no_constraints() -> None:
    """A lone defined-barycenter node unconstrained passes through."""
    cg = _make_cg()
    result = resolve_conflicts([_bary("a", 2.5, 1.0)], cg)
    assert result == [ResolvedBaryEntry(vs=["a"], i=0, barycenter=2.5, weight=1.0)]


def test_resolve_no_constraints_reverses_input_order() -> None:
    """An unconstrained run emits entries in ``source_set`` LIFO pop order.

    ``source_set`` collects ``[0, 1, 2]`` (all zero-indegree); ``pop``
    yields ``2, 1, 0``; nothing merges, so the result is the input
    *reversed*: ``[c, b, a]``. This is a faithful, if subtle, port --
    the Kahn LIFO traversal is what later ``sort`` / ``sort_subgraph``
    leaves rely on for their sweep order.
    """
    cg = _make_cg()
    entries = [
        _bary("a", 1.0, 1.0),
        _bary("b", 2.0, 1.0),
        _bary("c", 3.0, 1.0),
    ]
    result = resolve_conflicts(entries, cg)
    assert result == [
        ResolvedBaryEntry(vs=["c"], i=2, barycenter=3.0, weight=1.0),
        ResolvedBaryEntry(vs=["b"], i=1, barycenter=2.0, weight=1.0),
        ResolvedBaryEntry(vs=["a"], i=0, barycenter=1.0, weight=1.0),
    ]


def test_resolve_consistent_constraint_no_merge() -> None:
    """``a -> b`` with ``a.bary=1 < b.bary=3`` -> no merge, topological order.

    The constraint direction agrees with the barycentric ordering, so
    ``_handle_in`` skips the merge; both survive, emitted in pop order
    (``a`` popped first because it has zero indegree, then ``b``).
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    entries = [_bary("a", 1.0, 1.0), _bary("b", 3.0, 1.0)]
    result = resolve_conflicts(entries, cg)
    assert result == [
        ResolvedBaryEntry(vs=["a"], i=0, barycenter=1.0, weight=1.0),
        ResolvedBaryEntry(vs=["b"], i=1, barycenter=3.0, weight=1.0),
    ]


def test_resolve_conflicting_constraint_merges() -> None:
    """``a -> b`` with ``a.bary=5 > b.bary=2`` -> ``a`` coalesces into ``b``.

    The barycentric ordering says ``b`` before ``a`` but the constraint
    says ``a`` before ``b`` -- a conflict, so ``a`` merges into ``b``
    (the survivor). ``vs = source(a) ++ target(b) = [a, b]``, barycenter
    = ``(5*1 + 2*1) / 2 = 3.5``, weight = ``2.0``, ``i = min(0, 1) = 0``.
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    entries = [_bary("a", 5.0, 1.0), _bary("b", 2.0, 1.0)]
    result = resolve_conflicts(entries, cg)
    assert result == [ResolvedBaryEntry(vs=["a", "b"], i=0, barycenter=3.5, weight=2.0)]


def test_resolve_none_source_barycenter_forces_merge() -> None:
    """A ``None`` ``u``-barycenter forces merge regardless of ``v``.

    ``a -> b`` with ``a.bary=None``: ``_handle_in`` sees
    ``u_barycenter is None`` and merges ``a`` into ``b``. ``b`` keeps
    its own barycenter (``5*2 / 2 = 5.0``), weight ``2.0``; ``a``
    contributes nothing.
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    entries = [_bary("a", None, None), _bary("b", 5.0, 2.0)]
    result = resolve_conflicts(entries, cg)
    assert result == [ResolvedBaryEntry(vs=["a", "b"], i=0, barycenter=5.0, weight=2.0)]


def test_resolve_both_none_barycenters_yield_nan() -> None:
    """Two ``None`` barycenters under a constraint -> ``sum/0`` -> ``NaN``.

    grok computes ``Some(sum / weight)`` = ``Some(0.0 / 0.0_f64)`` =
    ``Some(NaN)``; Python ``0.0 / 0.0`` raises ``ZeroDivisionError``, so
    the port reproduces the ``NaN`` faithfully (the R260 widening). The
    aggregated ``weight`` is ``0.0``; ``vs = [a, b]``; ``i = 0``.
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    entries = [_bary("a", None, None), _bary("b", None, None)]
    result = resolve_conflicts(entries, cg)
    assert len(result) == 1
    assert result[0].vs == ["a", "b"]
    assert result[0].i == 0
    assert result[0].weight == 0.0
    assert math.isnan(result[0].barycenter)


def test_resolve_weighted_two_entry_merge() -> None:
    """A weighted merge: ``(4*1 + 2*3) / 4 = 2.5``.

    ``a -> b`` with ``a.bary=4, w=1`` and ``b.bary=2, w=3``: the
    constraint is violated (``4 > 2``), so ``a`` merges into ``b``;
    ``sum = 4*1 + 2*3 = 10``, ``weight = 1 + 3 = 4``, barycenter =
    ``10 / 4 = 2.5``.
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    entries = [_bary("a", 4.0, 1.0), _bary("b", 2.0, 3.0)]
    result = resolve_conflicts(entries, cg)
    assert result == [ResolvedBaryEntry(vs=["a", "b"], i=0, barycenter=2.5, weight=4.0)]


def test_resolve_three_node_chain_merges_all() -> None:
    """``a -> b -> c`` confluent -> single survivor ``vs = [a, b, c]``.

    Barycenters ``5 / 3 / 1`` strictly decrease along the chain, so
    every constraint is violated: ``a`` merges into ``b`` (``b`` ->
    barycenter ``4.0``, weight ``2``, vs ``[a, b]``); then the updated
    ``b`` (barycenter ``4.0`` > ``c.bary=1``) merges into ``c`` ->
    barycenter ``3.0``, weight ``3``, vs ``[a, b, c]``, ``i = 0``.
    """
    cg = _make_cg()
    _constraint(cg, "a", "b")
    _constraint(cg, "b", "c")
    entries = [
        _bary("a", 5.0, 1.0),
        _bary("b", 3.0, 1.0),
        _bary("c", 1.0, 1.0),
    ]
    result = resolve_conflicts(entries, cg)
    assert result == [
        ResolvedBaryEntry(vs=["a", "b", "c"], i=0, barycenter=3.0, weight=3.0)
    ]


def test_resolve_skips_constraint_edges_with_foreign_endpoints() -> None:
    """A constraint edge whose endpoint is absent from ``entries`` is skipped.

    ``a -> x`` and ``x -> a``: ``x`` is not in ``entries`` (not in
    ``id_to_idx``), so neither edge adjusts ``indegree`` or ``outs``.
    ``a`` stays zero-indegree and passes through unchanged.
    """
    cg = _make_cg()
    _constraint(cg, "a", "x")  # x not in entries
    _constraint(cg, "x", "a")  # x not in entries
    entries = [_bary("a", 1.0, 1.0)]
    result = resolve_conflicts(entries, cg)
    assert result == [ResolvedBaryEntry(vs=["a"], i=0, barycenter=1.0, weight=1.0)]


def test_resolve_does_not_mutate_constraint_graph() -> None:
    """resolve_conflicts reads ``cg.edges()`` only; the constraint graph is unchanged."""
    cg = _make_cg()
    _constraint(cg, "a", "b")
    edge_snapshot = {e: cg.edge_with_obj(e) for e in cg.edges()}
    entries = [_bary("a", 5.0, 1.0), _bary("b", 2.0, 1.0)]
    resolve_conflicts(entries, cg)
    assert {e: cg.edge_with_obj(e) for e in cg.edges()} == edge_snapshot


# === _handle_in: white-box ============================================


def _entry(
    vs: list[str],
    i: int,
    barycenter: float | None,
    weight: float | None,
    *,
    merged: bool = False,
    indegree: int = 0,
) -> ConflictEntry:
    """Build a :class:`ConflictEntry` with empty adjacency (white-box helper)."""
    return ConflictEntry(
        indegree=indegree,
        ins=[],
        outs=[],
        vs=vs,
        i=i,
        barycenter=barycenter,
        weight=weight,
        merged=merged,
    )


def test_handle_in_skips_merged_source() -> None:
    """A merged ``u`` short-circuits -- no merge is performed."""
    entries = [
        _entry(["a"], 0, 5.0, 1.0, merged=True),  # u already absorbed
        _entry(["b"], 1, 2.0, 1.0),  # v
    ]
    _handle_in(entries, v_idx=1, u_idx=0)
    assert entries[0].merged is True  # unchanged
    assert entries[1].vs == ["b"]  # v untouched


def test_handle_in_none_u_barycenter_merges() -> None:
    """A ``None`` ``u``-barycenter triggers merge regardless of ``v``."""
    entries = [
        _entry(["a"], 0, None, None),  # u, bary None
        _entry(["b"], 1, 5.0, 2.0),  # v
    ]
    _handle_in(entries, v_idx=1, u_idx=0)
    assert entries[0].merged is True
    assert entries[1].vs == ["a", "b"]


def test_handle_in_none_v_barycenter_merges() -> None:
    """A ``None`` ``v``-barycenter triggers merge regardless of ``u``."""
    entries = [
        _entry(["a"], 0, 1.0, 1.0),  # u
        _entry(["b"], 1, None, None),  # v, bary None
    ]
    _handle_in(entries, v_idx=1, u_idx=0)
    assert entries[0].merged is True
    assert entries[1].vs == ["a", "b"]


def test_handle_in_u_ge_v_merges() -> None:
    """``u.bary >= v.bary`` violates the ``u -> v`` constraint -> merge."""
    entries = [
        _entry(["a"], 0, 4.0, 1.0),  # u, bary 4
        _entry(["b"], 1, 4.0, 1.0),  # v, bary 4 (equal -> >= holds)
    ]
    _handle_in(entries, v_idx=1, u_idx=0)
    assert entries[0].merged is True
    assert entries[1].vs == ["a", "b"]


def test_handle_in_u_lt_v_no_merge() -> None:
    """``u.bary < v.bary`` agrees with the constraint -> no merge."""
    entries = [
        _entry(["a"], 0, 1.0, 1.0),  # u, bary 1
        _entry(["b"], 1, 3.0, 1.0),  # v, bary 3
    ]
    _handle_in(entries, v_idx=1, u_idx=0)
    assert entries[0].merged is False
    assert entries[1].vs == ["b"]  # unchanged


# === _handle_out: white-box ===========================================


def test_handle_out_appends_ins_and_decrements_indegree() -> None:
    """``_handle_out`` records ``v`` on ``w.ins`` and decrements ``w.indegree``.

    Above-zero indegree (2 -> 1) does NOT promote ``w`` onto
    ``source_set``.
    """
    entries = [
        _entry(["a"], 0, None, None),  # v
        _entry(["b"], 1, None, None, indegree=2),  # w, indeg 2
    ]
    source_set: list[int] = []
    _handle_out(entries, v_idx=0, w_idx=1, source_set=source_set)
    assert entries[1].ins == [0]
    assert entries[1].indegree == 1
    assert source_set == []


def test_handle_out_promotes_on_zero_indegree() -> None:
    """Indegree reaching zero promotes ``w`` onto ``source_set`` (Kahn emit)."""
    entries = [
        _entry(["a"], 0, None, None),  # v
        _entry(["b"], 1, None, None, indegree=1),  # w, indeg 1 -> 0
    ]
    source_set: list[int] = []
    _handle_out(entries, v_idx=0, w_idx=1, source_set=source_set)
    assert entries[1].indegree == 0
    assert source_set == [1]


# === _merge_entries: white-box ========================================


def test_merge_weighted_mean() -> None:
    """``(2*3 + 4*1) / 4 = 2.5``; weight aggregates to ``4.0``."""
    entries = [
        _entry(["a"], 0, 4.0, 1.0),  # source
        _entry(["b"], 1, 2.0, 3.0),  # target
    ]
    _merge_entries(entries, target_idx=1, source_idx=0)
    assert entries[1].barycenter == 2.5
    assert entries[1].weight == 4.0
    assert entries[0].merged is True
    assert entries[1].merged is False


def test_merge_vs_concatenates_source_first() -> None:
    """``vs = source.vs ++ target.vs`` (source first, mirroring grok)."""
    entries = [
        _entry(["x", "y"], 0, 4.0, 1.0),  # source
        _entry(["z"], 1, 2.0, 1.0),  # target
    ]
    _merge_entries(entries, target_idx=1, source_idx=0)
    assert entries[1].vs == ["x", "y", "z"]


def test_merge_takes_min_original_index() -> None:
    """``target.i = min(source.i, target.i)`` (lowest original index)."""
    entries = [
        _entry(["a"], 5, 4.0, 1.0),  # source i=5
        _entry(["b"], 2, 2.0, 1.0),  # target i=2
    ]
    _merge_entries(entries, target_idx=1, source_idx=0)
    assert entries[1].i == 2


def test_merge_marks_source_merged() -> None:
    """The absorbed ``source`` is flagged ``merged`` (filtered out at emit)."""
    entries = [
        _entry(["a"], 0, 4.0, 1.0),  # source
        _entry(["b"], 1, 2.0, 1.0),  # target
    ]
    _merge_entries(entries, target_idx=1, source_idx=0)
    assert entries[0].merged is True


def test_merge_both_none_yields_nan() -> None:
    """Both barycenters ``None`` -> ``sum=0, weight=0`` -> ``NaN`` (R260 widening)."""
    entries = [
        _entry(["a"], 0, None, None),  # source
        _entry(["b"], 1, None, None),  # target
    ]
    _merge_entries(entries, target_idx=1, source_idx=0)
    assert math.isnan(entries[1].barycenter)
    assert entries[1].weight == 0.0


# === barrel surface contract ==========================================


def test_resolve_conflicts_not_in_dagre_all() -> None:
    """``resolve_conflicts`` / ``ResolvedBaryEntry`` earn no crate-root barrel slot."""
    assert "resolve_conflicts" not in dagre.__all__
    assert "ResolvedBaryEntry" not in dagre.__all__


def test_resolve_conflicts_not_reachable_at_dagre_top_level() -> None:
    """Neither symbol is bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "resolve_conflicts")
    assert not hasattr(dagre, "ResolvedBaryEntry")


def test_resolve_conflicts_submodule_exports_symbols() -> None:
    """The submodule exposes the class + the fn (ASCII-sorted, class first)."""
    import minimax_code.dagre.layout.order.resolve_conflicts as rc_mod

    assert rc_mod.__all__ == ["ResolvedBaryEntry", "resolve_conflicts"]
    assert rc_mod.ResolvedBaryEntry is ResolvedBaryEntry
    assert rc_mod.resolve_conflicts is resolve_conflicts


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R261 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_six() -> None:
    """The ``order`` sub-package barrel re-exports all five ``order`` leaves.

    Synchronised at R261 (``resolve_conflicts`` added) then R262
    (``build_layer_graph`` added): the barrel now re-exports all five
    ``order`` leaves, so ``__all__`` grew from ``["barycenter",
    "cross_count", "init_order"]`` -> ``["barycenter", "cross_count",
    "init_order", "resolve_conflicts"]`` -> the ASCII-sorted
    ``["barycenter", "build_layer_graph", "cross_count", "init_order",
    "resolve_conflicts"]``. Mirrors the R255 -> R254 / R257 -> R254 /
    R259 -> R258 / R260 -> R258+R259 barrel-sync pattern.
    """
    import minimax_code.dagre.layout.order as order_pkg
    import minimax_code.dagre.layout.order.add_subgraph_constraints as asc_mod
    import minimax_code.dagre.layout.order.barycenter as bc_mod
    import minimax_code.dagre.layout.order.build_layer_graph as blg_mod
    import minimax_code.dagre.layout.order.cross_count as cc_mod
    import minimax_code.dagre.layout.order.init_order as init_mod
    import minimax_code.dagre.layout.order.resolve_conflicts as rc_mod
    import minimax_code.dagre.layout.order.sort as sort_mod
    import minimax_code.dagre.layout.order.sort_subgraph as sort_subgraph_mod

    assert order_pkg.__all__ == [
        "add_subgraph_constraints",
        "barycenter",
        "build_layer_graph",
        "cross_count",
        "init_order",
        "resolve_conflicts",
        "sort",
        "sort_subgraph",
    ]
    assert order_pkg.add_subgraph_constraints is asc_mod
    assert order_pkg.barycenter is bc_mod
    assert order_pkg.build_layer_graph is blg_mod
    assert order_pkg.cross_count is cc_mod
    assert order_pkg.init_order is init_mod
    assert order_pkg.resolve_conflicts is rc_mod
    assert order_pkg.sort is sort_mod
    assert order_pkg.sort_subgraph is sort_subgraph_mod


def test_resolve_conflicts_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.resolve_conflicts.resolve_conflicts is resolve_conflicts
