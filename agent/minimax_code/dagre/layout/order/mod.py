"""Order sweep dispatcher (R265, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/mod.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the within-rank
node-ordering orchestrator -- the crossing-minimization dispatcher that
runs after ``rank`` (R254-R257) assigns every node a rank and
``add_border_segments`` (R249) seeds the border dummies, and before
``position`` (unmigrated) computes coordinates. ``run_layout`` calls
:func:`order` at ``layout/mod.rs`` line 632, between ``add_border_segments``
(line 631) and the position phase. The algorithm (Gansner et al., "A
Technique for Drawing Directed Graphs", section 4) seeds an initial
layering via R258 :func:`init_order`, then sweeps up and down the ranks
re-sequencing each layer by the barycenter heuristic (R260 + R261 + R264),
scoring each candidate with R259 :func:`cross_count`, and keeping the
best-crossing-count ``layering`` across iterations. The sweep alternates
direction (``down`` on odd iterations via :attr:`GraphRelationship.IN_EDGES`,
``up`` on even iterations via :attr:`GraphRelationship.OUT_EDGES`) and the
barycenter tie-break (``bias_right`` flips every two iterations) to escape
local minima, and stops once four consecutive iterations fail to improve
the cross count.

This is the **ninth and final ``order`` leaf** (``order`` sub-package 9/9,
the milestone that closes the crossing-minimization stage) -- the
dispatcher every earlier ``order`` leaf feeds into. It depends on all
eight prior leaves (R258 :func:`init_order`, R259 :func:`cross_count`,
R260 :func:`barycenter` via R264, R261 :func:`resolve_conflicts` via R264,
R262 :func:`build_layer_graph` + :class:`GraphRelationship`, R263
:func:`add_subgraph_constraints`, R264 :func:`sort_subgraph` + :func:`sort`)
plus the R248 util primitives :func:`max_rank` + :func:`build_layer_matrix`,
so it lands only after every stage it calls is in place. The top-level
imports are strictly one-directional (this dispatcher consumes every
leaf; no leaf imports it back), so -- unlike the R264 ``sort`` /
``sort_subgraph`` load cycle -- no deferred import is needed here.

It is the **fifteenth zero-semantic-clone leaf** after R252 / R253 / R254
/ R255 / R256 / R257 / R258 / R259 / R260 / R261 / R262 / R263 / R264:
every grok ``clone()`` is a ``Vec<String>`` ownership-transfer artefact.
grok clones the ``layering`` matrix twice -- once to seed ``best``
(``mod.rs`` line 53) and once whenever a sweep improves the cross count
(line 68). Rust needs the clone because ``layering`` is later moved
(rebound) by ``build_layer_matrix``; Python's ``list`` is shared by
reference and :func:`build_layer_matrix` returns a fresh matrix each call,
so a naive ``best = layering`` would alias the saved snapshot to whatever
matrix ``layering`` next references. The port deep-copies the per-layer
lists (``[list(layer) for layer in layering]``) so a subsequent in-place
``node.order`` write on ``g`` cannot mutate the saved ``best`` -- the node
ids are immutable ``str`` so a per-layer shallow copy is the faithful
``Vec<Vec<String>>`` equivalent. The ``cross_count(...) as f64`` cast is a
Rust ``usize`` -> ``f64`` widening (R259 already returns ``float``, so no
cast is needed); the ``Graph::new(None)`` constraint-graph construction
maps to the R263 / R264 ``cg`` fixture pattern (directed, non-multigraph,
compound). The grok ``assign_order`` ``g.node_mut(v).unwrap()`` panic on a
missing node widens to a defensive skip (the R258 ``_init_order_dfs``
no-missing-label widening reused) so a ghost id in the matrix cannot crash
the final stamp. Same barrel policy: :func:`order` (the pub fn) /
``_sweep_layer_graphs`` / ``_assign_order`` (the private helpers) stay out
of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.mod.order``.
"""

from __future__ import annotations

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.order.add_subgraph_constraints import (
    add_subgraph_constraints,
)
from minimax_code.dagre.layout.order.build_layer_graph import (
    GraphRelationship,
    build_layer_graph,
)
from minimax_code.dagre.layout.order.cross_count import cross_count
from minimax_code.dagre.layout.order.init_order import init_order
from minimax_code.dagre.layout.order.sort_subgraph import sort_subgraph
from minimax_code.dagre.layout.util import build_layer_matrix, max_rank
from minimax_code.data_structures.graphlib import GRAPH_NODE, Graph, GraphOption

__all__ = ["order"]


def order(g: Graph) -> None:
    """Minimize edge crossings + stamp the best ``order`` on every node.

    Seeds the initial layering via R258 :func:`init_order`, stamps it on
    ``g`` via :func:`_assign_order`, scores it via R259 :func:`cross_count`,
    then sweeps up and down the ranks (alternating direction every
    iteration, flipping ``bias_right`` every two) until four consecutive
    iterations fail to improve the cross count. Each sweep re-sequences
    every rank's nodes via :func:`_sweep_layer_graphs` (R262
    :func:`build_layer_graph` + R264 :func:`sort_subgraph` + R263
    :func:`add_subgraph_constraints`), and the best-crossing-count
    ``layering`` is kept; the winning matrix is finally stamped on ``g``.

    Mutates ``g`` in place: every node's ``order`` field is set to its
    within-rank position in the winning layering.

    Args:
        g: The layered graph (post-``rank`` -- every node carries a
            ``rank`` field; post-``add_border_segments`` -- border dummies
            seeded; the graph is the state ``run_layout`` passes at
            ``layout/mod.rs`` line 632).
    """
    # grok: ``down_layer_ranks = (1..=max_rank)`` -> [1, ..., max_rank];
    # ``up_layer_ranks = (0..max_rank).rev()`` -> [max_rank-1, ..., 0].
    # The local ``max_rank_value`` dodges shadowing the imported
    # :func:`max_rank` (grok shadows freely; Python would clobber the name).
    max_rank_value = max_rank(g)
    down_layer_ranks = list(range(1, max_rank_value + 1))
    up_layer_ranks = list(reversed(range(max_rank_value)))

    layering = init_order(g)
    _assign_order(g, layering)

    # Start with the init ordering as the best candidate. grok's note
    # (lines 44-50): the original dagre.js starts ``bestCC = Infinity`` so
    # the first sweep always replaces it, but seeding with the init cross
    # count keeps a minimal-crossing init from being discarded for a
    # same-count but visually worse biased sweep. ``init_cc`` is already
    # ``float`` (R259), so the grok ``as f64`` cast is a no-op here.
    init_cc = cross_count(g, layering)
    best_cc = init_cc
    # grok ``best = layering.clone()``: the matrix is later rebound by
    # ``build_layer_matrix``, so the snapshot is deep-copied per layer
    # (``str`` ids are immutable -> a per-layer shallow copy is the
    # ``Vec<Vec<String>>`` equivalent -- the fifteenth zero-semantic-clone
    # leaf).
    best: list[list[str]] = [list(layer) for layer in layering]

    iteration = 0
    last_best = 0
    while last_best < 4:
        if iteration % 2 != 0:
            _sweep_layer_graphs(
                g, down_layer_ranks, GraphRelationship.IN_EDGES, iteration % 4 >= 2
            )
        else:
            _sweep_layer_graphs(
                g, up_layer_ranks, GraphRelationship.OUT_EDGES, iteration % 4 >= 2
            )

        layering = build_layer_matrix(g)
        cc = cross_count(g, layering)
        if cc < best_cc:
            last_best = 0
            best = [list(layer) for layer in layering]
            best_cc = cc

        last_best += 1
        iteration += 1

    _assign_order(g, best)


def _sweep_layer_graphs(
    g: Graph,
    ranks: list[int],
    relationship: GraphRelationship,
    bias_right: bool,
) -> None:
    """Re-sequence every rank in ``ranks`` by the barycenter heuristic.

    For each rank: R262 :func:`build_layer_graph` projects the rank's
    movable nodes (+ their compound hierarchy + the edges selected by
    ``relationship``) into a fresh layer graph rooted at a synthetic
    ``_root{id}``; R264 :func:`sort_subgraph` sorts that sub-graph by
    barycenter (R260 weights -> R261 conflict resolution -> R264 sort),
    and the sorted sequence is stamped onto ``g`` as each node's ``order``;
    R263 :func:`add_subgraph_constraints` then propagates the compound
    hierarchy's left-to-right order onto the shared constraint graph ``cg``
    so the next sweep honors it. The constraint graph is fresh per call
    (one ``cg`` per sweep pass, shared across that pass's ranks).

    Mutates ``g`` in place (every rank's nodes get a new ``order``).

    Args:
        g: The layered graph being swept.
        ranks: The ranks to sweep, in sweep order (down sweep passes
            ``[1, 2, ..., max_rank]``; up sweep passes
            ``[max_rank-1, ..., 0]``).
        relationship: Which incident edges to project (``IN_EDGES`` for
            the down sweep, ``OUT_EDGES`` for the up sweep).
        bias_right: Tie-break direction for equal barycenters (flips every
            two iterations to escape local minima).
    """
    # grok ``Graph::new(None)``: a fresh constraint graph for this sweep
    # pass. Mirrors the R263 / R264 ``cg`` fixture (directed,
    # non-multigraph so ``set_edge`` aggregates rather than multiplies;
    # compound so the constraint edges compose with any parent chain).
    cg: Graph = Graph(
        GraphOption(directed=True, multigraph=False, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    cg.set_graph(GraphConfig())

    for rank in ranks:
        lg = build_layer_graph(g, rank, relationship)
        # grok ``lg.graph().root.clone().unwrap_or(GRAPH_NODE.to_string())``:
        # R262 :func:`build_layer_graph` always sets ``root`` to a fresh
        # ``_root{id}``, so the ``GRAPH_NODE`` fallback is almost never
        # hit -- but reproduced faithfully (the ``\x00`` sentinel from
        # ``graphlib``).
        graph_label = lg.graph()
        root = (
            graph_label.root
            if (graph_label is not None and graph_label.root is not None)
            else GRAPH_NODE
        )
        sorted_result = sort_subgraph(lg, root, cg, bias_right)
        for i, v in enumerate(sorted_result.vs):
            # grok ``if let Some(node) = g.node_mut(v)``: defensive -- a
            # ghost id in ``sorted_result.vs`` is skipped, not panicked.
            node = g.node_mut(v)
            if node is not None:
                node.order = i
        add_subgraph_constraints(lg, cg, sorted_result.vs)


def _assign_order(g: Graph, layering: list[list[str]]) -> None:
    """Stamp each node's within-rank position onto ``g``.

    For every layer in ``layering`` (a rank -> node-id list matrix), sets
    each node's ``order`` field to its index within its layer. Mirrors
    grok ``assign_order``; the grok ``g.node_mut(v).unwrap()`` panic on a
    missing node widens to a defensive skip (the R258 ``_init_order_dfs``
    no-missing-label widening reused) so a ghost id in the matrix cannot
    crash the stamp.

    Mutates ``g`` in place (every listed node's ``order`` is set).

    Args:
        g: The layered graph whose nodes get their ``order`` stamped.
        layering: The rank -> node-id list matrix (R258 :func:`init_order`
            output or R248 :func:`build_layer_matrix` output).
    """
    for layer in layering:
        for i, v in enumerate(layer):
            node = g.node_mut(v)
            if node is not None:
                node.order = i
