"""Barycenter heuristic weights for the order sweep (R260, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/barycenter.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the barycenter
heuristic assigns each movable node a "center of mass" -- the weighted
average of its in-edge source nodes' within-rank ``order`` positions -- so
``order::sort_subgraph`` (a later leaf) can re-sequence a layer by ascending
barycenter to drive edge crossings down. ``order::mod`` calls it once per
sweep pass to re-weight the movable rank before :func:`cross_count` (R259)
re-scores the candidate ``layering``: ``init_order`` (R258) mints the seed
matrix, ``barycenter`` proposes a better sequence, ``cross_count`` measures
how many crossings it has, and the sweep keeps the best-crossing-count
``layering`` across iterations (Gansner et al., "A Technique for Drawing
Directed Graphs", section 4).

This is the **ninth zero-semantic-clone leaf** (the third ``order`` leaf,
``order`` sub-package 3/9) after R252 / R253 / R254 / R255 / R256 / R257 /
R258 / R259: every grok ``clone()`` is a ``String`` / ``Option<f32>`` /
``Option<i32>`` borrow-or-Copy artefact (``v.clone()`` is an immutable
``str`` read; ``edge.weight.clone()`` and ``node_u.order.clone()`` are
``Copy`` type reads), so the port strips them all -- no ``copy.deepcopy``
survives. Same barrel policy: ``barycenter`` stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.barycenter.barycenter``.
"""

from __future__ import annotations

from dataclasses import dataclass

from minimax_code.data_structures.graphlib import Graph

__all__ = ["Barycenter", "barycenter"]


@dataclass(frozen=True, slots=True)
class Barycenter:
    """Per-node barycenter heuristic result (mirrors grok ``Barycenter``).

    grok carries ``Option<f32>`` for both ``barycenter`` and ``weight``;
    Python uses ``float | None``. ``None`` mirrors the no-in-edges case
    (the node had no incident edges, so it has no defined center of mass).
    A ``NaN`` ``barycenter`` mirrors grok's ``sum / 0.0`` (every in-edge
    had ``weight = None`` -> aggregated to ``0.0``): Rust yields ``NaN``
    where Python would raise ``ZeroDivisionError``, so the port reproduces
    the ``NaN`` faithfully (see :func:`barycenter`). Frozen + slotted
    because a barycenter is a pure computed value (never mutated once
    minted); three fields dodge the R236 ``frozen + slots + fieldless``
    ``TypeError`` trap.
    """

    v: str
    barycenter: float | None
    weight: float | None


def barycenter(g: Graph, movable: list[str]) -> list[Barycenter]:
    """Compute the barycenter heuristic weight for each movable node.

    For each ``v`` in ``movable``: collect ``g.in_edges(v)``; if the node
    has no in-edge, it has no defined barycenter (``barycenter = weight =
    None``); otherwise accumulate ``sum += edge.weight * source.order``
    and ``weight += edge.weight`` across every in-edge, and the barycenter
    is ``sum / weight`` -- the weighted mean of the in-edge source nodes'
    within-rank ``order`` positions. Mirrors grok
    ``order::barycenter::barycenter`` exactly, including the ``unwrap_or``
    defaults (``edge.weight = None`` -> ``0.0``; ``source.order = None`` ->
    ``0``) and the ``f64`` -> ``f32`` cast on the result (Python ``float``
    is ``f64``; the integer-valued test cases are exact in both widths).
    A defensive ``NaN`` reproduction covers the all-zero-weight edge case
    (grok ``0.0 / 0.0 = NaN``; Python would raise ``ZeroDivisionError``).

    Does NOT mutate ``g`` (reads ``in_edges`` / ``edge_with_obj`` /
    ``node`` only -- the graph is the state ``order::mod`` sweeps over).

    Args:
        g: The layered graph (post-``init_order`` -- every node carries an
            ``order`` field set by a prior within-rank ordering pass).
        movable: The subset of node ids to score (typically one rank's
            worth; ``order::mod`` passes the up/down-sweep rank here).

    Returns:
        One :class:`Barycenter` per input id, in input order.
    """
    result: list[Barycenter] = []
    for v in movable:
        in_edges = g.in_edges(v, None) or []
        if not in_edges:
            result.append(Barycenter(v=v, barycenter=None, weight=None))
            continue
        sum_value = 0.0
        weight_total = 0.0
        for edge_obj in in_edges:
            edge = g.edge_with_obj(edge_obj)
            node_u = g.node(edge_obj.v)
            edge_weight = (
                edge.weight if (edge is not None and edge.weight is not None) else 0.0
            )
            source_order = (
                node_u.order if (node_u is not None and node_u.order is not None) else 0
            )
            sum_value += edge_weight * source_order
            weight_total += edge_weight
        if weight_total == 0.0:
            # grok: (sum / 0.0_f64) as f32 -> NaN; Python 0.0 / 0.0 raises
            # ZeroDivisionError. Faithful NaN reproduction keeps the sweep
            # pipeline crash-free on the degenerate all-zero-weight edge set.
            center = float("nan")
        else:
            center = sum_value / weight_total
        result.append(Barycenter(v=v, barycenter=center, weight=weight_total))
    return result
