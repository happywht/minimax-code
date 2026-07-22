"""``order`` sub-package barrel (R258+, vendored ``third_party/dagre_rust``).

Mirrors the ``layout/order`` directory of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
within-rank node-ordering stage of the layered layout pipeline -- the
crossing-minimization pass that runs after ``rank`` (R254-R257) assigns
every node a rank and before ``position`` (unmigrated) computes
coordinates. ``run_layout`` calls ``order`` at ``layout/mod.rs`` line 632,
between ``add_border_segments`` (line 631, R249) and the position phase --
once every node carries a rank and the long-edge / border dummies are in
place, ``order`` decides the left-to-right sequence of nodes within each
rank to minimize edge crossings (Gansner et al., "A Technique for Drawing
Directed Graphs", section 4).

grok's ``order`` module splits into nine files -- the largest layout
sub-package (``rank`` was four): ``order::init_order`` (the deterministic
seed ordering), ``order::cross_count`` (the Barth bilinear cross-counting
measure), ``order::barycenter`` (the barycentric heuristic weights),
``order::build_layer_graph`` (the per-layer sort graph + the
``GraphRelationship`` enum), ``order::sort_subgraph`` / ``order::sort`` /
``order::resolve_conflicts`` (the sweep sub-graph sorters), and
``order::add_subgraph_constraints`` (compound-graph constraints), all
orchestrated by ``order::mod`` (the ``order()`` dispatcher that sweeps up
and down the ranks, keeping the best-crossing-count layering). Leaves
migrate file-by-file; ``order::mod`` lands only after every stage it
calls is in place. This is the **second layout sub-package** (sibling to
``rank`` R254-R257); like ``rank`` it mirrors grok's
directory-of-files shape rather than a single-file module, because it
bundles an init ordering, a crossing measure, and a multi-pass sweep
heuristic behind one dispatcher.

First ``order`` leaf (R258): ``init_order`` -- the self-contained
deterministic seed ordering
(:func:`~minimax_code.dagre.layout.order.init_order.init_order`).
``order::mod`` calls it first (``layout/order/mod.rs`` line 41) to mint
the initial ``layering`` matrix every later sweep refines; with no
crossings yet computed it walks every leaf node's DFS successor tree,
placing each first-visited node into the layer of its rank. It is the
**seventh zero-semantic-clone leaf** after R252 / R253 / R254 / R255 /
R256 / R257 (every grok ``clone()`` is a ``String`` / ``i32`` / ``usize``
borrow-or-Copy artefact, stripped; the stage performs no structural
removal, so no ``copy.deepcopy`` survives). Same barrel policy as the
sibling layout stages: the symbol stays out of ``dagre.__all__``,
reachable only as
``minimax_code.dagre.layout.order.init_order.init_order``. The barrel
re-exports the ``init_order`` submodule (mirroring grok's ``pub mod
init_order`` visibility); the remaining eight ``order`` files land in
later leaves, re-exported here as they arrive.

Second ``order`` leaf (R259): ``cross_count`` -- the Barth bilinear
cross-counting measure
(:mod:`~minimax_code.dagre.layout.order.cross_count`). ``order::mod``'s
sweep loop calls :func:`cross_count` after every up/down pass to score
the candidate ``layering`` against the prior best, so this is the
**first direct downstream consumer** of the R258 ``init_order`` seed
ordering: ``init_order`` mints the initial matrix, ``cross_count``
measures how many edge crossings it has, and the barycenter sweep
(later leaves) keeps the best-crossing-count ``layering`` across
iterations. The algorithm (Barth, Mutzel & Yannakakis, "Bilayer Cross
Counting", JGAA 2004) collects every north -> south edge as a
``(south_position, weight)`` pair and accumulates them through a
complete binary tree, turning the naive O(k^2) pairwise comparison
into O(k log k). It is the **eighth zero-semantic-clone leaf** after
R252 / R253 / R254 / R255 / R256 / R257 / R258 (every grok ``clone()``
is a ``String`` / ``usize`` borrow-or-Copy artefact, stripped; the
stage performs no structural removal, so no ``copy.deepcopy``
survives). Same barrel policy: the symbol stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.cross_count.cross_count``. The barrel
now re-exports ``cross_count`` alongside ``init_order``.

Third ``order`` leaf (R260): ``barycenter`` -- the barycentric heuristic
weights (:mod:`~minimax_code.dagre.layout.order.barycenter`).
``order::mod`` calls :func:`barycenter` once per sweep pass to re-weight
the movable rank (the up/down sweep passes one rank at a time) before
:func:`cross_count` re-scores the candidate ``layering``: for each
movable node it takes the weighted mean of its in-edge source nodes'
within-rank ``order`` positions, the value ``order::sort_subgraph`` (a
later leaf) re-sequences the layer by. It is the **iterative partner**
of the R259 ``cross_count`` measure and the **direct upstream** of the
unmigrated ``sort`` / ``sort_subgraph`` phase -- together the three
form the Gansner et al. crossing-minimization loop (init -> barycenter
-> sort -> cross_count -> keep best). It is the **ninth
zero-semantic-clone leaf** after R252 / R253 / R254 / R255 / R256 /
R257 / R258 / R259 (every grok ``clone()`` is a ``String`` /
``Option<f32>`` / ``Option<i32>`` borrow-or-Copy artefact -- ``v``
immutable ``str`` read, ``edge.weight`` / ``node_u.order`` ``Copy``
type reads -- stripped; the stage performs no structural removal, so no
``copy.deepcopy`` survives). A defensive ``NaN`` reproduction covers
grok's ``0.0 / 0.0`` (Rust yields ``NaN`` where Python would raise
``ZeroDivisionError``). Same barrel policy: ``barycenter`` stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.barycenter.barycenter``. The barrel
now re-exports ``barycenter`` alongside ``cross_count`` + ``init_order``.

Fourth ``order`` leaf (R261): ``resolve_conflicts`` -- the Forster
constrained two-level crossing-reduction conflict resolver
(:mod:`~minimax_code.dagre.layout.order.resolve_conflicts`).
``order::sort_subgraph`` (a later leaf) feeds the R260 :func:`barycenter`
weights through :func:`resolve_conflicts` together with a constraint
graph ``cg`` (edges ``v -> w`` mean ``v`` must precede ``w``); when the
barycenters of two constrained nodes would violate the constraint
direction the two are coalesced into a single aggregated entry
(barycenter = weighted mean, weight = sum, vs = concatenation). The
algorithm is Forster, "A Fast and Simple Heuristic for Constrained
Two-Level Crossing Reduction": build a per-node :class:`ConflictEntry`
(Kahn bookkeeping -- ``indegree`` / ``ins`` / ``outs`` / ``merged``),
seed a ``source_set`` with every zero-indegree entry, sweep in LIFO pop
order calling :func:`_handle_in` (merge on barycenter violation --
``u`` into ``v`` when ``u``'s barycenter is ``None`` or not strictly
below ``v``'s) and :func:`_handle_out` (decrement indegree, promote on
zero), then emit the non-merged survivors in pop order. It is the
**direct upstream** of the unmigrated ``sort`` / ``sort_subgraph``
phase (``sort.rs`` line 3 imports ``ResolvedBaryEntry``;
``sort_subgraph.rs`` line 5 imports both), so landing it unblocks both
later leaves and closes the Gansner et al. crossing-minimization loop
(init -> barycenter -> resolve_conflicts -> sort -> cross_count ->
keep best). It is the **tenth zero-semantic-clone leaf** after R252 /
R253 / R254 / R255 / R256 / R257 / R258 / R259 / R260 (every grok
``clone()`` is a ``String`` borrow or a ``Vec`` ownership-transfer
artefact -- ``v`` immutable ``str`` HashMap key, ``ins`` / ``outs``
iterated without consumption, ``vs`` concatenated into a fresh list --
stripped; the stage mutates entries in place without structural
removal, so no ``copy.deepcopy`` survives). A defensive ``NaN``
reproduction covers grok's ``sum / weight`` when both barycenters are
``None`` (the R260 ``0.0 / 0.0`` widening reused). Same barrel policy:
``resolve_conflicts`` stays out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.resolve_conflicts.resolve_conflicts``.
The barrel now re-exports ``resolve_conflicts`` alongside ``barycenter``
+ ``cross_count`` + ``init_order``.

Fifth ``order`` leaf (R262): ``build_layer_graph`` -- the per-layer sort
graph + the :class:`GraphRelationship` enum
(:mod:`~minimax_code.dagre.layout.order.build_layer_graph`).
``order::mod`` calls :func:`build_layer_graph` once per sweep direction
(up then down) per rank before :func:`sort` / :func:`sort_subgraph`
re-sequence the layer: it projects the movable rank's nodes -- plus
their preserved compound hierarchy -- into a fresh directed compound
graph, attaches the incident edges selected by the
:class:`GraphRelationship` parameter (``IN_EDGES`` for the up sweep,
``OUT_EDGES`` for the down sweep), and roots every parentless movable
node under a synthetic ``_root{id}`` node stored in the graph label's
``root`` attribute. The result is the standalone sub-graph the sweep
sorts. It is the **direct structural upstream** of the unmigrated
``sort`` / ``sort_subgraph`` phase: both walk the layer graph's
compound hierarchy + aggregated edge weights (R260 + R261 feed the
weights; this leaf builds the graph they run on), and ``sort.rs`` /
``sort_subgraph.rs`` start the sweep from the layer graph's ``root``
attribute -- so landing it unblocks both later leaves. It is the
**eleventh zero-semantic-clone leaf** after R252 / R253 / R254 / R255 /
R256 / R257 / R258 / R259 / R260 / R261 (every grok ``clone()`` is a
``String`` borrow or a ``GraphNode`` borrow-release artefact -- ``v``
immutable ``str`` HashMap-key read; ``node.clone()`` the Rust
borrow-to-owned transfer ``g.node(v)`` -> ``&GraphNode`` -> ``set_node``
owns its label -- stripped; the stage builds a fresh graph, so no
``copy.deepcopy`` survives). The ``border_left`` / ``border_right``
``OrderedHashMap`` reads are ``.get(rank)`` value reads, not structural
clones. Same barrel policy: ``build_layer_graph`` / ``create_root_node``
/ ``GraphRelationship`` stay out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.build_layer_graph.build_layer_graph``.
The barrel now re-exports ``build_layer_graph`` alongside ``barycenter``
+ ``cross_count`` + ``init_order`` + ``resolve_conflicts``.
"""

from minimax_code.dagre.layout.order import (
    barycenter,
    build_layer_graph,
    cross_count,
    init_order,
    resolve_conflicts,
)

__all__ = [
    "barycenter",
    "build_layer_graph",
    "cross_count",
    "init_order",
    "resolve_conflicts",
]
