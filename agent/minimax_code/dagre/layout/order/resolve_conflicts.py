"""Constrained two-level crossing-reduction conflict resolution (R261).

Mirrors ``resolve_conflicts.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the Forster
constrained two-level crossing-reduction conflict resolver that
``order::sort_subgraph`` (a later leaf) feeds the R260 ``barycenter``
weights through. Given a list of ``{v, barycenter, weight}`` entries and
a constraint graph ``cg``, this resolves any conflict between the
constraint graph topology and the barycentric ordering: when the
barycenters of two constrained nodes would violate the constraint
direction, the two are coalesced into a single aggregated entry
(barycenter = weighted mean, weight = sum, vs = concatenation).

The algorithm (Forster, "A Fast and Simple Heuristic for Constrained
Two-Level Crossing Reduction") proceeds in three phases:

1. **Build**: map every :class:`~minimax_code.dagre.layout.order.barycenter.Barycenter`
   entry to a mutable :class:`ConflictEntry` (carrying the Kahn
   topological-sort bookkeeping -- ``indegree`` / ``ins`` / ``outs`` /
   ``merged``) and index it by node id; for every constraint edge
   ``v -> w`` (both endpoints present in the entries) increment
   ``w.indegree`` and record ``w`` as an out-neighbour of ``v``.

2. **Topological sweep (Kahn)**: seed ``source_set`` with every
   zero-indegree entry; pop one at a time (LIFO), and for each ``v``:

   * walk ``v.ins`` (the already-processed in-neighbours ``u``) calling
     :func:`_handle_in` -- if ``u``'s barycenter is ``None`` or not
     strictly below ``v``'s, merge ``u`` into ``v`` (the constraint
     ``u -> v`` is violated by the barycentric ordering, so coalesce);
   * walk ``v.outs`` (forward out-neighbours ``w``) calling
     :func:`_handle_out` -- append ``v`` to ``w.ins`` (the reverse
     adjacency, filled lazily as predecessors are processed), decrement
     ``w.indegree``, and promote ``w`` to ``source_set`` when its
     indegree hits zero.

3. **Emit**: filter out every merged (absorbed) entry and project the
   survivors to :class:`ResolvedBaryEntry`, preserving the
   ``source_set`` pop order (LIFO -- an unconstrained run therefore
   emits entries in reverse input order).

This is the **direct upstream** of the unmigrated ``sort`` /
``sort_subgraph`` phase (``sort.rs`` line 3 imports
``ResolvedBaryEntry``; ``sort_subgraph.rs`` line 5 imports both
``resolve_conflicts`` and ``ResolvedBaryEntry``), so landing it unblocks
both later leaves and closes the Gansner et al. crossing-minimization
loop (init -> barycenter -> resolve_conflicts -> sort -> cross_count ->
keep best).

It is the **tenth zero-semantic-clone leaf** after R252 / R253 / R254 /
R255 / R256 / R257 / R258 / R259 / R260: every grok ``clone()`` is a
``String`` borrow or a ``Vec`` ownership-transfer artefact -- ``v``
immutable ``str`` stored as a HashMap key / list element, ``ins`` /
``outs`` iterated without consumption (Python list iteration does not
move, so the grok borrow-release clone is stripped), ``vs``
concatenated into a fresh list (B-class ownership transfer reproduced
as ``list(...)``) -- and is stripped; the stage mutates entries in
place without structural removal, so no ``copy.deepcopy`` survives. A
defensive ``NaN`` reproduction covers grok's ``sum / weight`` when both
barycenters are ``None`` (Rust ``0.0 / 0.0_f64`` yields ``NaN`` where
Python ``0.0 / 0.0`` would raise ``ZeroDivisionError`` -- the R260
widening reused verbatim).
"""

from __future__ import annotations

from dataclasses import dataclass

from minimax_code.dagre.layout.order.barycenter import Barycenter
from minimax_code.data_structures.graphlib import Graph

__all__ = ["ResolvedBaryEntry", "resolve_conflicts"]


@dataclass(slots=True)
class ResolvedBaryEntry:
    """A post-resolution barycenter entry (mutable; consumed by ``sort``).

    Mirrors grok's ``ResolvedBaryEntry`` (``#[derive(Debug, Clone)]`` --
    non-``Copy`` because ``vs`` is a ``Vec``). ``sort_subgraph``'s
    ``expand_subgraphs`` mutates ``vs`` in place, so the dataclass is
    deliberately non-frozen (unlike the R260 :class:`Barycenter` value
    type, which is a frozen output).
    """

    vs: list[str]
    i: int
    barycenter: float | None
    weight: float | None


@dataclass(slots=True)
class ConflictEntry:
    """Mutable bookkeeping for the Kahn topological sweep (private).

    Carries the per-node constraint-graph adjacency (``ins`` reverse,
    ``outs`` forward, ``indegree`` remaining), the aggregated barycenter
    state (``vs`` / ``barycenter`` / ``weight`` / ``i``), and the
    ``merged`` absorption flag. All fields are mutated in place during
    the sweep, hence ``slots=True`` without ``frozen``.
    """

    indegree: int
    ins: list[int]
    outs: list[int]
    vs: list[str]
    i: int
    barycenter: float | None
    weight: float | None
    merged: bool


def resolve_conflicts(
    entries: list[Barycenter],
    cg: Graph,
) -> list[ResolvedBaryEntry]:
    """Resolve constraint-graph vs barycenter conflicts (Forster heuristic).

    Args:
        entries: ``{v, barycenter, weight}`` records (R260
            :func:`~minimax_code.dagre.layout.order.barycenter.barycenter`
            output).
        cg: the constraint graph (an edge ``v -> w`` means ``v`` must
            precede ``w`` in the within-rank order).

    Returns:
        One :class:`ResolvedBaryEntry` per surviving (non-merged) entry,
        in ``source_set`` LIFO pop order (an unconstrained run therefore
        reverses the input order). Each ``vs`` is either a singleton or
        an aggregation ordered to respect the constraints, and ``i`` is
        the lowest original index of any aggregated element.
    """
    id_to_idx: dict[str, int] = {}
    mapped: list[ConflictEntry] = []

    for i, entry in enumerate(entries):
        id_to_idx[entry.v] = i
        mapped.append(
            ConflictEntry(
                indegree=0,
                ins=[],
                outs=[],
                vs=[entry.v],
                i=i,
                barycenter=entry.barycenter,
                weight=entry.weight,
                merged=False,
            )
        )

    for e in cg.edges():
        v_idx = id_to_idx.get(e.v)
        w_idx = id_to_idx.get(e.w)
        if v_idx is not None and w_idx is not None:
            mapped[w_idx].indegree += 1
            mapped[v_idx].outs.append(w_idx)

    source_set: list[int] = [
        idx for idx, entry in enumerate(mapped) if entry.indegree == 0
    ]

    entries_order: list[int] = []
    while source_set:
        v_idx = source_set.pop()
        entries_order.append(v_idx)

        # ins is built incrementally by _handle_out as predecessors are
        # processed; Python list iteration does not consume the list, so
        # the grok borrow-release clone (ins.clone() before into_iter)
        # is stripped -- reversed() walks a read-only view.
        for u_idx in reversed(mapped[v_idx].ins):
            _handle_in(mapped, v_idx, u_idx)

        for w_idx in mapped[v_idx].outs:
            _handle_out(mapped, v_idx, w_idx, source_set)

    result: list[ResolvedBaryEntry] = []
    for idx in entries_order:
        if mapped[idx].merged:
            continue
        entry = mapped[idx]
        result.append(
            ResolvedBaryEntry(
                vs=list(entry.vs),
                i=entry.i,
                barycenter=entry.barycenter,
                weight=entry.weight,
            )
        )
    return result


def _handle_in(entries: list[ConflictEntry], v_idx: int, u_idx: int) -> None:
    """Maybe merge predecessor ``u`` into ``v`` (constraint vs barycenter).

    If ``u`` is already merged (absorbed elsewhere) skip; otherwise merge
    when ``u`` has no barycenter, ``v`` has no barycenter, or ``u``'s
    barycenter is not strictly below ``v``'s (the constraint ``u -> v``
    is violated by the barycentric ordering, so coalesce ``u`` into
    ``v`` -- ``v`` is the survivor because it is the later-processed
    node).
    """
    if entries[u_idx].merged:
        return

    u_barycenter = entries[u_idx].barycenter
    v_barycenter = entries[v_idx].barycenter
    if (
        u_barycenter is None
        or v_barycenter is None
        or u_barycenter >= v_barycenter
    ):
        _merge_entries(entries, v_idx, u_idx)


def _handle_out(
    entries: list[ConflictEntry],
    v_idx: int,
    w_idx: int,
    source_set: list[int],
) -> None:
    """Record ``v`` as a processed predecessor of ``w``; maybe promote ``w``.

    Appends ``v`` to ``w.ins`` (the reverse adjacency, filled lazily as
    predecessors are processed -- this is what ``_handle_in`` later
    walks when ``w`` itself is popped), decrements ``w.indegree``, and
    when it reaches zero promotes ``w`` to ``source_set`` (Kahn
    emission).
    """
    entries[w_idx].ins.append(v_idx)
    entries[w_idx].indegree -= 1
    if entries[w_idx].indegree == 0:
        source_set.append(w_idx)


def _merge_entries(
    entries: list[ConflictEntry], target_idx: int, source_idx: int
) -> None:
    """Coalesce ``source`` into ``target`` (weighted-mean barycenter).

    Aggregates ``sum = tb*tw + sb*sw`` and ``weight = tw + sw`` over the
    defined (barycenter, weight) pairs, concatenates ``vs`` with source
    first (mirroring grok's ``source.vs.extend(target.vs)``), takes the
    min original index, and marks ``source`` merged. When both
    barycenters are ``None`` the aggregated weight is ``0.0`` and the
    mean is ``NaN`` (grok ``0.0 / 0.0_f64`` reproduced faithfully via
    ``float("nan")`` rather than raising ``ZeroDivisionError``).
    """
    sum_value = 0.0
    weight = 0.0

    target = entries[target_idx]
    source = entries[source_idx]
    if target.barycenter is not None and target.weight is not None:
        sum_value += target.barycenter * target.weight
        weight += target.weight
    if source.barycenter is not None and source.weight is not None:
        sum_value += source.barycenter * source.weight
        weight += source.weight

    vs = list(source.vs)
    vs.extend(target.vs)

    target.vs = vs
    target.barycenter = float("nan") if weight == 0.0 else sum_value / weight
    target.weight = weight
    target.i = min(source.i, target.i)

    source.merged = True
