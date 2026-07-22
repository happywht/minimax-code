"""Black-box tests for the migrated dagre order/sort layer (R264).

Exercises :mod:`minimax_code.dagre.layout.order.sort` through its public
entry point :func:`sort` plus the two private helpers
(``_compare_with_bias`` / ``_consume_unsortable``) and the
:class:`SubgraphResult` aggregate it returns. The entries are R261
:class:`ResolvedBaryEntry` records (the post-``resolve_conflicts`` output,
post-``_expand_subgraphs``). Covers:

* :func:`sort` end-to-end on a fully-sortable batch (every entry carries
  a barycenter -> ascending-barycenter order + weighted-mean aggregate),
  a ``bias_right`` tie-break on equal barycenters (ascending ``i`` when
  ``False`` vs descending ``i`` when ``True``), a fully-unsortable batch
  (every barycenter ``None`` -> the half sorts by descending ``i`` then
  LIFO-pops into ascending-``i`` order; the aggregate stays ``None``), a
  mixed batch with interleaving (an unsortable entry whose ``i`` the
  first sortable sinks lands ahead of it), a zero-total-weight batch
  (aggregate skipped -> ``barycenter`` / ``weight`` stay ``None``), a
  multi-``vs`` entry flattening, and an empty batch (-> empty result),
* ``_compare_with_bias`` white-box: ascending barycenter, the
  ``bias_right`` ``i`` tie-break both directions, and equal entries,
* ``_consume_unsortable`` white-box: the LIFO ``pop`` drain order, the
  ``last.i > index`` short-circuit, and the empty-half no-op,
* the barrel surface contract (``sort`` stays out of ``dagre.__all__``;
  the crate-root barrel count is unchanged at 4; the ``order`` sub-package
  barrel re-exports all eight ``order`` leaves; ``sort.__all__`` is the
  single-symbol list).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre.layout.order.resolve_conflicts import ResolvedBaryEntry
from minimax_code.dagre.layout.order.sort import (
    _compare_with_bias,
    _consume_unsortable,
    sort,
)
from minimax_code.dagre.layout.order.sort_subgraph import SubgraphResult


def _entry(
    vs: list[str],
    i: int,
    barycenter: float | None,
    weight: float | None,
) -> ResolvedBaryEntry:
    """Build a ``ResolvedBaryEntry`` (mirrors the resolve_conflicts output)."""
    return ResolvedBaryEntry(vs=vs, i=i, barycenter=barycenter, weight=weight)


# === sort: end-to-end ====================================================


def test_sort_all_sortable_ascending_barycenter() -> None:
    """Three sortable entries reorder by ascending barycenter + weighted-mean."""
    entries = [
        _entry(["a"], 0, 2.0, 1.0),
        _entry(["b"], 1, 0.0, 1.0),
        _entry(["c"], 2, 1.0, 1.0),
    ]
    result = sort(entries, bias_right=False)
    assert isinstance(result, SubgraphResult)
    assert result.vs == ["b", "c", "a"]
    assert result.barycenter == 1.0
    assert result.weight == 3.0


def test_sort_bias_right_false_breaks_tie_ascending_i() -> None:
    """Equal barycenters + ``bias_right=False`` -> ascending ``i`` tie-break."""
    entries = [
        _entry(["a"], 0, 1.0, 1.0),
        _entry(["b"], 1, 1.0, 1.0),
    ]
    result = sort(entries, bias_right=False)
    assert result.vs == ["a", "b"]


def test_sort_bias_right_true_breaks_tie_descending_i() -> None:
    """Equal barycenters + ``bias_right=True`` -> descending ``i`` tie-break."""
    entries = [
        _entry(["a"], 0, 1.0, 1.0),
        _entry(["b"], 1, 1.0, 1.0),
    ]
    result = sort(entries, bias_right=True)
    assert result.vs == ["b", "a"]


def test_sort_all_unsortable_descending_i_then_lifo_pop() -> None:
    """Every barycenter ``None`` -> descending-``i`` sort, then LIFO pop.

    The unsortable half sorts by descending ``i`` -> ``[c(2), b(1), a(0)]``;
    ``_consume_unsortable`` pops from the back (smallest ``i`` first) so the
    flattened ``vs`` is ascending-``i`` ``[a, b, c]``. No sortable entries
    means the total weight stays ``0.0`` -> the aggregate is skipped
    (``barycenter`` / ``weight`` stay ``None``).
    """
    entries = [
        _entry(["a"], 0, None, None),
        _entry(["b"], 1, None, None),
        _entry(["c"], 2, None, None),
    ]
    result = sort(entries, bias_right=False)
    assert result.vs == ["a", "b", "c"]
    assert result.barycenter is None
    assert result.weight is None


def test_sort_mixed_interleaves_unsortable_before_sortable() -> None:
    """An unsortable entry with ``i=0`` sinks before the first sortable entry."""
    entries = [
        _entry(["u"], 0, None, None),
        _entry(["s"], 1, 1.0, 1.0),
    ]
    result = sort(entries, bias_right=False)
    assert result.vs == ["u", "s"]
    assert result.barycenter == 1.0
    assert result.weight == 1.0


def test_sort_zero_total_weight_skips_aggregate() -> None:
    """Sortable entries with all-zero weights -> aggregate stays ``None``.

    The ``if weight != 0.0`` guard skips the barycenter / weight write, so
    the result keeps the :class:`SubgraphResult` defaults even though the
    ``vs`` flattening still runs.
    """
    entries = [
        _entry(["a"], 0, 1.0, 0.0),
        _entry(["b"], 1, 2.0, 0.0),
    ]
    result = sort(entries, bias_right=False)
    assert result.vs == ["a", "b"]
    assert result.barycenter is None
    assert result.weight is None


def test_sort_empty_entries_returns_empty_result() -> None:
    """An empty batch yields an empty :class:`SubgraphResult`."""
    result = sort([], bias_right=False)
    assert result.vs == []
    assert result.barycenter is None
    assert result.weight is None


def test_sort_multi_vs_entry_flattens() -> None:
    """A sortable entry carrying ``vs=["x","y"]`` flattens into the result."""
    entries = [
        _entry(["x", "y"], 0, 1.0, 2.0),
    ]
    result = sort(entries, bias_right=False)
    assert result.vs == ["x", "y"]
    assert result.barycenter == 1.0
    assert result.weight == 2.0


# === _compare_with_bias: white-box =======================================


def test_compare_with_bias_ascending_barycenter() -> None:
    """Lower barycenter sorts first regardless of ``bias_right``."""
    low = _entry(["a"], 0, 1.0, 1.0)
    high = _entry(["b"], 1, 2.0, 1.0)
    assert _compare_with_bias(low, high, False) == -1
    assert _compare_with_bias(high, low, False) == 1
    assert _compare_with_bias(low, high, True) == -1
    assert _compare_with_bias(high, low, True) == 1


def test_compare_with_bias_equal_barycenter_ascending_i() -> None:
    """Equal barycenters + ``bias_right=False`` -> ascending ``i``."""
    first = _entry(["a"], 0, 1.0, 1.0)
    second = _entry(["b"], 1, 1.0, 1.0)
    assert _compare_with_bias(first, second, False) == -1
    assert _compare_with_bias(second, first, False) == 1


def test_compare_with_bias_equal_barycenter_descending_i() -> None:
    """Equal barycenters + ``bias_right=True`` -> descending ``i``."""
    first = _entry(["a"], 0, 1.0, 1.0)
    second = _entry(["b"], 1, 1.0, 1.0)
    assert _compare_with_bias(first, second, True) == 1
    assert _compare_with_bias(second, first, True) == -1


def test_compare_with_bias_equal_entries() -> None:
    """Same barycenter + same ``i`` -> 0 (no swap)."""
    a = _entry(["a"], 0, 1.0, 1.0)
    b = _entry(["b"], 0, 1.0, 1.0)
    assert _compare_with_bias(a, b, False) == 0
    assert _compare_with_bias(a, b, True) == 0


# === _consume_unsortable: white-box ======================================


def test_consume_unsortable_lifo_pop_drains_smallest_i_first() -> None:
    """The half is sorted by descending ``i``; ``pop`` drains smallest-``i`` first."""
    unsortable = [
        _entry(["c"], 2, None, None),
        _entry(["b"], 1, None, None),
        _entry(["a"], 0, None, None),
    ]
    vs: list[list[str]] = []
    index = _consume_unsortable(vs, unsortable, 0)
    assert [node for group in vs for node in group] == ["a", "b", "c"]
    assert index == 3
    assert unsortable == []


def test_consume_unsortable_short_circuits_when_i_ahead_of_index() -> None:
    """A back entry whose ``i`` exceeds ``index`` stops the drain."""
    unsortable = [_entry(["a"], 5, None, None)]
    vs: list[list[str]] = []
    index = _consume_unsortable(vs, unsortable, 0)
    assert vs == []
    assert index == 0
    assert len(unsortable) == 1


def test_consume_unsortable_empty_half_is_noop() -> None:
    """An empty half returns ``index`` unchanged."""
    vs: list[list[str]] = []
    index = _consume_unsortable(vs, [], 3)
    assert vs == []
    assert index == 3


# === barrel surface contract =============================================


def test_sort_not_in_dagre_all() -> None:
    """``sort`` earns no crate-root barrel slot."""
    assert "sort" not in dagre.__all__


def test_sort_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "sort")


def test_sort_submodule_exports_symbol() -> None:
    """The submodule exposes the fn (single-symbol ``__all__``)."""
    import minimax_code.dagre.layout.order.sort as sort_mod

    assert sort_mod.__all__ == ["sort"]
    assert sort_mod.sort is sort


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R264 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_order_subpackage_barrel_reexports_all_eight() -> None:
    """The ``order`` sub-package barrel re-exports all eight ``order`` leaves.

    Synchronised at R264 (``sort`` + ``sort_subgraph`` added): the barrel
    ``__all__`` grew the R263 six-element list -> the ASCII-sorted eight
    (``"sort"`` < ``"sort_subgraph"`` by the shorter-prefix-first rule,
    both placed after ``"resolve_conflicts"``). Mirrors the R259 -> R258 /
    R260 -> R258+R259 / R261 -> R258+R259+R260 / R262 -> R258+R259+R260+R261 /
    R263 -> R258+R259+R260+R261+R262 barrel-sync pattern.
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


def test_sort_reachable_via_layout_order() -> None:
    """Importing the ``order`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.order as order_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.order is order_pkg
    assert order_pkg.sort.sort is sort
