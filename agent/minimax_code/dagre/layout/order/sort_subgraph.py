"""Sub-graph sorter for the order sweep (R264, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/sort_subgraph.rs`` of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
recursive compound-hierarchy sorter ``order::mod`` drives the
crossing-minimization sweep through. For one compound node ``v`` it scores
every movable child by R260's :func:`barycenter`, recurses into every
child that is itself a compound sub-graph (merging the child's barycenter
into its entry via :func:`_merge_barycenters`), feeds the scored entries
through R261's :func:`resolve_conflicts` together with the constraint
graph ``cg``, expands each sub-graph entry's ``vs`` to its fully flattened
descendant list (:func:`_expand_subgraphs`), then hands the resolved
entries to :func:`sort` (this leaf's sibling, migrated alongside) which
re-sequences them by ascending barycenter. The sorted sequence is then
bookended by ``v``'s border sentinels (``border_left_`` /
``border_right_``, the per-rank labels R249 ``add_border_segments`` seeded)
and -- when both borders have predecessors -- the border predecessors'
``order`` positions are folded into the result barycenter (a two-unit
weight bump).

This is the **direct upstream caller** of :func:`sort`: ``sort_subgraph``
is the recursive driver, :func:`sort` is the per-level leaf sorter it
delegates to. The two form a tight mutual recursion (``sort`` returns a
:class:`SubgraphResult`; ``sort_subgraph`` calls :func:`sort`), so the two
leaves migrate as a pair (R264). Python's module-level import would
deadlock on the cycle (``sort_subgraph`` imports ``sort``;
``sort`` imports ``SubgraphResult`` from ``sort_subgraph`` -- whichever
module loads first, the other is only half-initialised), so
``sort_subgraph`` keeps the ``sort`` import out of module scope and pulls
it lazily inside :func:`sort_subgraph` at call time (the deferred binding
breaks the cycle; ``sort`` keeps its module-level ``SubgraphResult`` import
because ``SubgraphResult`` is defined at module scope here before any
function that uses it).

It is the **thirteenth zero-semantic-clone leaf** (the seventh ``order``
leaf, ``order`` sub-package 7/9) after R252 / R253 / R254 / R255 / R256 /
R257 / R258 / R259 / R260 / R261 / R262 / R263: every grok ``clone()`` is
a ``String`` borrow or a ``Vec`` ownership-transfer artefact (``v`` /
``bl_`` / ``br_`` immutable ``str`` reads; ``entry.v.clone()`` a
``String`` HashMap-key rebind; ``subgraph_result.clone()`` a grok
ownership artefact -- Rust's ``insert`` consumes the value but the caller
still reads ``barycenter`` afterwards, so it clones; Python's
``dict.__setitem__`` keeps the live reference, the read sees the same
object, the clone is stripped). The ``subgraph.vs.clone()`` in
:func:`_expand_subgraphs` is a ``Vec`` borrow-release under ``&`` (Python
extends from the live list). Same barrel policy: ``SubgraphResult`` /
``sort_subgraph`` stay out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.sort_subgraph.sort_subgraph``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from minimax_code.dagre.layout.order.barycenter import Barycenter, barycenter
from minimax_code.dagre.layout.order.resolve_conflicts import (
    ResolvedBaryEntry,
    resolve_conflicts,
)
from minimax_code.data_structures import Graph

__all__ = ["SubgraphResult", "sort_subgraph"]


@dataclass(slots=True)
class SubgraphResult:
    """Sorted sub-graph result (mirrors grok ``SubgraphResult``).

    grok derives ``Debug, Clone, Default``: ``vs`` defaults to ``vec![]``,
    ``barycenter`` / ``weight`` to ``None``. Python uses a slotted
    dataclass with ``field(default_factory=list)`` for ``vs`` (the R236
    mutable-default trap) and ``None`` for the two optionals. Mutable
    because :func:`sort_subgraph` rewrites ``vs`` / ``barycenter`` /
    ``weight`` after the border bookending (the result is built
    incrementally, not minted once).
    """

    vs: list[str] = field(default_factory=list)
    barycenter: float | None = None
    weight: float | None = None


def sort_subgraph(
    g: Graph, v: str, cg: Graph, bias_right: bool
) -> SubgraphResult:
    """Sort the sub-graph rooted at ``v`` (mirrors grok ``sort_subgraph``).

    Scores every movable child of ``v`` by R260 :func:`barycenter`,
    recurses into compound children (merging their barycenters via
    :func:`_merge_barycenters`), resolves constraint conflicts via R261
    :func:`resolve_conflicts`, expands sub-graph entries via
    :func:`_expand_subgraphs`, delegates the per-level ordering to
    :func:`sort`, then bookends the result with ``v``'s border sentinels.
    When both borders have predecessors, their ``order`` positions fold
    into the result barycenter (weight bumped by 2).

    Does NOT mutate ``g`` (reads ``children`` / ``node`` / ``predecessors``
    only -- the graph is the layer graph R262 :func:`build_layer_graph`
    built for this sweep rank).

    Args:
        g: The layer graph (compound, carries ``border_left_`` /
            ``border_right_`` per-rank labels on sub-graph nodes).
        v: The root of the sub-graph to sort (a compound node or the
            synthetic ``_root{id}`` R262 mints).
        cg: The constraint graph (edges ``a -> b`` mean ``a`` precedes
            ``b``; R263 :func:`add_subgraph_constraints` seeded it).
        bias_right: Tie-break direction for equal barycenters (the sweep
            alternates up/down).

    Returns:
        A :class:`SubgraphResult` carrying the flattened sorted ``vs``
        plus the aggregated ``barycenter`` / ``weight``.
    """
    # Lazy import: ``sort`` imports ``SubgraphResult`` from this module at
    # top level, and this module imports ``sort`` to delegate at call
    # time. A module-level binding would deadlock the load cycle
    # (whichever side loads first sees the other half-initialised); the
    # deferred binding resolves cleanly once both modules are registered.
    from minimax_code.dagre.layout.order.sort import sort

    movable = g.children(v)
    node = g.node(v)
    bl = node.border_left_ if node is not None else None
    br = node.border_right_ if node is not None else None
    subgraphs: dict[str, SubgraphResult] = {}
    if bl is not None and br is not None:
        movable = [w for w in movable if w != bl and w != br]
    barycenters = barycenter(g, movable)
    for entry in barycenters:
        if g.children(entry.v):
            subgraph_result = sort_subgraph(g, entry.v, cg, bias_right)
            subgraphs[entry.v] = subgraph_result
            if subgraph_result.barycenter is not None:
                _merge_barycenters(entry, subgraph_result)
    entries = resolve_conflicts(barycenters, cg)
    _expand_subgraphs(entries, subgraphs)
    result = sort(entries, bias_right)
    if bl is not None and br is not None:
        vs: list[str] = [bl]
        vs.extend(result.vs)
        vs.append(br)
        result.vs = vs
        bl_preds = g.predecessors(bl) or []
        if bl_preds:
            br_preds = g.predecessors(br) or []
            if br_preds:
                bl_pred = g.node(bl_preds[0])
                br_pred = g.node(br_preds[0])
                if bl_pred is not None and br_pred is not None:
                    bl_pred_order = (
                        bl_pred.order if bl_pred.order is not None else 0
                    )
                    br_pred_order = (
                        br_pred.order if br_pred.order is not None else 0
                    )
                    result_barycenter = (
                        result.barycenter if result.barycenter is not None else 0.0
                    )
                    result_weight = (
                        result.weight if result.weight is not None else 0.0
                    )
                    result.barycenter = (
                        result_barycenter * result_weight
                        + bl_pred_order
                        + br_pred_order
                    ) / (result_weight + 2.0)
                    result.weight = result_weight + 2.0
    return result


def _expand_subgraphs(
    entries: list[ResolvedBaryEntry], subgraphs: dict[str, SubgraphResult]
) -> None:
    """Flatten each entry's ``vs`` through the sub-graph map (mirrors grok).

    For every entry, walks its ``vs`` list: if a vertex names a recorded
    sub-graph, splices the sub-graph's flattened ``vs`` in its place;
    otherwise keeps the vertex. grok ``subgraph.vs.clone()`` is a ``Vec``
    borrow-release under ``&subgraphs`` (Python extends from the live
    list -- the thirteenth zero-semantic-clone leaf).
    """
    for entry in entries:
        vs: list[str] = []
        for w in entry.vs:
            subgraph = subgraphs.get(w)
            if subgraph is not None:
                vs.extend(subgraph.vs)
                continue
            vs.append(w)
        entry.vs = vs


def _merge_barycenters(target: Barycenter, other: SubgraphResult) -> None:
    """Fold ``other``'s barycenter into ``target`` (mirrors grok).

    Weighted-mean merge: if ``target`` already has a barycenter, the two
    are combined as ``(tb*tw + ob*ow) / (tw + ow)`` with summed weights;
    otherwise ``target`` adopts ``other``'s barycenter + weight. No-op if
    ``other`` lacks either field. Mutates ``target`` in place -- the
    R260 ``Barycenter`` is widened from ``frozen`` to mutable at R264 for
    this call site (grok ``target: &mut Barycenter``).
    """
    if other.barycenter is None or other.weight is None:
        return
    other_barycenter = other.barycenter
    other_weight = other.weight
    if target.barycenter is not None and target.weight is not None:
        tb = target.barycenter
        tw = target.weight
        target.barycenter = (tb * tw + other_barycenter * other_weight) / (
            tw + other_weight
        )
        target.weight = tw + other_weight
    else:
        target.barycenter = other_barycenter
        target.weight = other_weight
