"""Per-level sorter for the order sweep (R264, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/sort.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the leaf sorter
:func:`sort_subgraph` (this leaf's sibling) delegates to once it has
resolved + expanded the entries for one level. The entries split into a
sortable half (those carrying a barycenter) and an unsortable half (those
without); the sortable half sorts by ascending barycenter (with a
``bias_right`` tie-break on equal barycenters -- ascending ``i`` or
descending ``i``), the unsortable half sorts by descending ``i``, and the
two are interleaved by ``i`` position via :func:`_consume_unsortable` --
each sortable entry sinks every unsortable entry whose ``i`` it has
reached. The result flattens the per-entry ``vs`` lists and aggregates
the barycenter as a weighted mean (skipped when the total weight is
zero, mirroring grok's ``if weight != 0.0`` guard).

This is the **direct downstream callee** of :func:`sort_subgraph`: the two
form a tight mutual recursion (see the ``sort_subgraph`` docstring) and
migrate as a pair (R264). It is the **fourteenth zero-semantic-clone
leaf** (the eighth ``order`` leaf, ``order`` sub-package 8/9) after
R252 / R253 / R254 / R255 / R256 / R257 / R258 / R259 / R260 / R261 /
R262 / R263 + ``sort_subgraph``: every grok ``clone()`` is a ``Vec``
ownership-transfer artefact (``parts.lhs.clone()`` / ``parts.rhs.clone()``
clone the partition halves because grok's ``parts`` outlives the
``sort_by``; ``entry.vs.clone()`` clones because ``vs: Vec<Vec<String>>``
owns its rows -- Python's :func:`partition` returns a fresh
:class:`~minimax_code.dagre.layout.util.PartitionResponse` whose ``lhs``
/ ``rhs`` are already independent lists, and ``vs.append`` takes the
live reference, so all clones collapse). Same barrel policy: ``sort``
stays out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.sort.sort``.
"""

from __future__ import annotations

from functools import cmp_to_key

from minimax_code.dagre.layout.order.resolve_conflicts import ResolvedBaryEntry
from minimax_code.dagre.layout.order.sort_subgraph import SubgraphResult
from minimax_code.dagre.layout.util import partition

__all__ = ["sort"]


def sort(entries: list[ResolvedBaryEntry], bias_right: bool) -> SubgraphResult:
    """Sort resolved entries by barycenter (mirrors grok ``sort``).

    Splits ``entries`` into sortable (carry a barycenter) and unsortable
    (do not); sorts the sortable half by ascending barycenter with a
    ``bias_right`` tie-break, the unsortable half by descending ``i``;
    interleaves them so each sortable entry sinks the unsortable entries
    whose ``i`` it has reached (:func:`_consume_unsortable`); flattens
    the per-entry ``vs`` and aggregates a weighted-mean barycenter
    (skipped when total weight is zero).

    Args:
        entries: The resolved barycenter entries for one level (R261
            :func:`resolve_conflicts` output, post-expansion).
        bias_right: Tie-break direction for equal barycenters.

    Returns:
        A :class:`~minimax_code.dagre.layout.order.sort_subgraph.SubgraphResult`
        with the flattened sorted ``vs`` + aggregated barycenter / weight.
    """
    parts = partition(entries, lambda val: val.barycenter is not None)
    sortable = parts.lhs
    unsortable = parts.rhs
    sortable.sort(key=cmp_to_key(lambda e1, e2: _compare_with_bias(e1, e2, bias_right)))
    unsortable.sort(key=lambda e: e.i, reverse=True)
    vs: list[list[str]] = []
    sum_value = 0.0
    weight = 0.0
    vs_index = 0
    vs_index = _consume_unsortable(vs, unsortable, vs_index)
    for entry in sortable:
        vs_index += len(entry.vs)
        vs.append(entry.vs)
        entry_weight = entry.weight if entry.weight is not None else 0.0
        sum_value += (
            entry.barycenter if entry.barycenter is not None else 0.0
        ) * entry_weight
        weight += entry_weight
        vs_index = _consume_unsortable(vs, unsortable, vs_index)
    result = SubgraphResult()
    result.vs = [node for group in vs for node in group]
    if weight != 0.0:
        result.barycenter = sum_value / weight
        result.weight = weight
    return result


def _consume_unsortable(
    vs: list[list[str]], unsortable: list[ResolvedBaryEntry], index: int
) -> int:
    """Sink every unsortable entry whose ``i`` has been reached (mirrors grok).

    Pops from the back of ``unsortable`` (the half is sorted by descending
    ``i``, so the back is the smallest ``i``) while the back entry's ``i``
    is at or below ``index``, appending its ``vs`` and advancing the
    index. Returns the updated index (unchanged when the back entry's
    ``i`` is still ahead of ``index`` or the half is empty).
    """
    while True:
        if not unsortable:
            return index
        last = unsortable[-1]
        if last.i > index:
            return index
        last = unsortable.pop()
        vs.append(last.vs)
        index += 1


def _compare_with_bias(
    entry_v: ResolvedBaryEntry, entry_w: ResolvedBaryEntry, bias_right: bool
) -> int:
    """Comparator for sortable entries (mirrors grok ``compare_with_bias``).

    Ascending by barycenter (``unwrap_or(0.0)`` -- but every sortable
    entry carries a barycenter by construction, so the default never
    fires); on equal barycenters, ``bias_right`` picks ascending ``i``
    (``False``) or descending ``i`` (``True``). Returns -1 / 0 / 1
    (``functools.cmp_to_key`` contract).
    """
    bv = entry_v.barycenter if entry_v.barycenter is not None else 0.0
    bw = entry_w.barycenter if entry_w.barycenter is not None else 0.0
    if bv < bw:
        return -1
    if bv > bw:
        return 1
    if not bias_right:
        if entry_v.i < entry_w.i:
            return -1
        if entry_v.i > entry_w.i:
            return 1
        return 0
    if entry_w.i < entry_v.i:
        return -1
    if entry_w.i > entry_v.i:
        return 1
    return 0
