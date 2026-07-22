"""Initial within-rank node ordering (R258, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/init_order.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
deterministic seed ordering that opens the crossing-minimization stage.
``order::mod`` calls :func:`init_order` first (``layout/order/mod.rs``
line 41) to mint the initial ``layering`` matrix every later up/down sweep
refines; with no crossings yet measured it simply walks every leaf node's
DFS successor tree, placing each first-visited node into the layer of its
own rank. The result is a poor-but-valid ordering (no crossing
minimization) that the barycenter sweep loop (``order::mod``) then
optimizes -- keeping the best-crossing-count layering across iterations.

This is the twelfth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains`` -> R253 ``nesting_graph`` ->
R254-R257 ``rank`` sub-package -> R258 ``order/init_order``) and the
**first leaf of the ``order`` sub-package** -- the second layout
sub-package to mirror grok's directory-of-files shape (after ``rank``),
because ``order`` bundles an init ordering, a crossing measure, and a
multi-pass barycenter sweep behind one ``order::mod`` dispatcher.
``run_layout`` calls ``order`` at ``layout/mod.rs`` line 632, between
``add_border_segments`` (line 631, R249) and ``position`` (unmigrated) --
once every node carries a rank and the long-edge / border dummies are in
place, ``order`` decides the left-to-right sequence of nodes within each
rank.

Algorithm (mirrors grok)
------------------------
* Filter to ``simple_nodes`` -- nodes with no compound children (the
  leaves of the compound forest); compound / subgraph nodes are skipped
  as DFS seeds (their children seed the walk instead).
* Sort ``simple_nodes`` by ``rank`` (stable) so the DFS seeds fire in
  rank order -- lowest rank first.
* For each seed, DFS-walk the successor tree, placing each first-visited
  node into ``layers[node.rank]``. A shared ``visited`` map deduplicates
  across seeds (a node reached from one seed is not re-placed by a later
  seed), so a diamond graph places the shared sink exactly once.

Borrow-checker clone artefacts
------------------------------
This is the **seventh zero-semantic-clone leaf** after R252 / R253 /
R254 / R255 / R256 / R257 (the tenth reuse of the R250 / R251
borrow-checker classification framework). Every grok ``clone()`` here is
a pure ownership / immutable-reference artefact, and all are stripped:

* ``v.clone()`` on ``visited.insert`` / ``layer.push`` / the recursive
  ``dfs`` call -- ``String`` ownership / ``&String`` borrow; Python
  passes the immutable ``str`` by reference.
* ``node.rank.clone().unwrap_or(0)`` (x2, the ``max`` and the ``sort``)
  and ``node.rank.unwrap_or(0) as usize`` -- ``i32`` Copy / Option
  artefacts; Python reads the int directly.
* ``node_rank.clone()`` (x3, the ``get`` / ``insert`` / ``get_mut``
  triple) -- ``usize`` Copy; Python indexes with the int directly.

The stage performs **no structural removal that invalidates a still-held
reference** (it only reads ranks and appends to a fresh ``layers`` list),
so no ``copy.deepcopy`` survives.

Defensive ``None`` guards
-------------------------
grok's ``g.node(v).unwrap()`` assumes every DFS-reached node carries a
label (the call site runs after ``rank`` populated every node); Python
mirrors the R254 ``_longest_path_dfs`` decision and treats a missing
label as a no-op (mark visited, return without placing) rather than
raising -- a defensive widening that leaves the normal path (every node
labelled) byte-identical to grok.
"""

from __future__ import annotations

from minimax_code.data_structures.graphlib import Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["init_order"]


def init_order(g: Graph) -> list[list[str]]:
    """Assign an initial within-rank order via DFS (mirrors grok ``order::init_order``).

    Walks every leaf node's successor tree, placing each first-visited node
    into the layer of its rank. Yields a poor-but-valid ``layering`` matrix
    (a list of per-rank node-id lists, in first-visit order) that
    ``order::mod``'s sweep loop then refines. Does NOT mutate ``g`` (it
    reads ``node.rank`` and the successor table only).

    Pre-conditions: every node carries a ``rank`` (the stage runs after
    ``rank``). Leaf nodes (no compound children) seed the walk; compound /
    subgraph nodes are placed only if reached as a successor of a seed.
    """
    visited: OrderedHashMap = OrderedHashMap()
    # grok: ``g.nodes().into_iter().filter(|v| g.children(v).len() == 0)`` --
    # compound / subgraph nodes (those with children) are NOT seeds.
    simple_nodes: list[str] = [v for v in g.nodes() if not g.children(v)]

    def _rank_of(v: str) -> int:
        # grok: ``g.node(v).unwrap().rank.clone().unwrap_or(0)`` (x2, max +
        # sort). The unwrap / clone are borrow + Copy artefacts; a node
        # without a label or rank yields 0. The same closure backs both the
        # max and the sort (grok inlines the rank extraction twice).
        node = g.node(v)
        if node is None or node.rank is None:
            return 0
        return node.rank

    # grok: ``simple_nodes.iter().map(...).max().unwrap_or(0)`` -- the max
    # of the seed ranks seeds the layer count (default 0 for no seeds).
    max_rank = max((_rank_of(v) for v in simple_nodes), default=0)
    layers: list[list[str]] = [[] for _ in range(max_rank + 1)]

    # grok: ``simple_nodes.sort_by(|v1, v2| v1_rank.cmp(&v2_rank))`` -- a
    # stable rank sort so seeds fire lowest-rank-first.
    simple_nodes.sort(key=_rank_of)

    for v in simple_nodes:
        _init_order_dfs(v, g, visited, layers)

    return layers


def _init_order_dfs(
    v: str,
    g: Graph,
    visited: OrderedHashMap,
    layers: list[list[str]],
) -> None:
    """Depth-first within-rank placement (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``init_order`` as a Rust nested ``fn`` (no
    closure capture, every parameter passed explicitly); Python hoists it
    to module scope as a private helper, paralleling the R254
    ``_longest_path_dfs`` / R255 ``_tight_tree_dfs`` decision. Mutates the
    shared ``visited`` map and ``layers`` list in place across the
    recursion (Python passes the boxed references, matching grok's ``&mut``
    parameters).
    """
    # grok: ``visited.contains_key(v)`` -- key existence (the R243
    # OrderedHashMap None-value trap: a present key with a None value still
    # counts as visited). A re-visit is a silent no-op.
    if v in visited:
        return
    visited.insert(v, True)

    # grok: ``g.node(v).unwrap()`` -- assumes a labelled node. Python treats
    # a missing label as a no-op (mark visited, skip placement); the normal
    # path (every node labelled) is byte-identical to grok.
    node = g.node(v)
    if node is None:
        return

    # grok: ``node.rank.unwrap_or(0) as usize`` -- the rank picks the layer.
    node_rank = node.rank if node.rank is not None else 0

    # grok: ``if layers.get(node_rank).is_none() { layers.insert(node_rank,
    # vec![]) }`` -- a defensive grow for a rank beyond the pre-allocated
    # span. Rust's ``Vec::insert`` panics on a gap (node_rank > len); Python
    # extends to fill (append-once when node_rank == len, matching grok's
    # insert; fill-otherwise, a defensive widening the post-normalize call
    # graph never triggers).
    while len(layers) <= node_rank:
        layers.append([])
    layers[node_rank].append(v)

    # grok: ``g.successors(v).unwrap_or(vec![])`` -- None for an absent node
    # (here v is known-present); an empty successor list ends the walk.
    sucs = g.successors(v)
    if sucs is None:
        return
    for sv in sucs:
        _init_order_dfs(sv, g, visited, layers)
