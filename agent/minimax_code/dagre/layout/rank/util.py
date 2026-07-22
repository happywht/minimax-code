"""Rank computation primitives (R254, vendored ``third_party/dagre_rust``).

Mirrors ``layout/rank/util.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). The foundational
rank helpers shared by every ranker strategy -- :func:`longest_path`
(the longest-path ranker and the seed for the tight-tree ranker) and
:func:`slack` (the edge-slack measure that drives ``network_simplex``'s
feasible-tree / edge-exchange loop). ``rank::mod`` dispatches to one of
three rankers based on ``GraphConfig.ranker``: ``network-simplex``
(the default), ``tight-tree`` (= longest_path + feasible_tree), or
``longest-path`` (= this module's :func:`longest_path` alone); the two
heavyweight strategies (``feasible_tree`` / ``network_simplex``) are not
yet migrated, so ``rank::mod`` lands only after them -- this module is
their self-contained foundation.

This is the eighth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains`` -> R253 ``nesting_graph`` ->
R254 ``rank/util``) and the **first leaf of the ``rank`` sub-package**
(grok nests ``rank/`` as a directory with ``mod`` + ``util`` +
``feasible_tree`` + ``network_simplex``; Python mirrors the sub-package).
``run_layout`` calls ``rank`` at ``layout/mod.rs`` line 620, between
``nesting_graph::run`` (line 617, R253 -- erects the compound-graph
nesting scaffolding so rank can keep subgraphs bordered) and
``nesting_graph::cleanup`` (line 625, R253 -- tears the scaffolding
down once ranks are written).

Algorithm (mirrors grok)
------------------------
* :func:`longest_path` pushes every node to the *lowest* rank it can
  occupy (Gansner, "A Technique for Drawing Directed Graphs"): for each
  source node it DFS-walks the DAG, assigning each node
  ``rank = min(child_rank - round(edge.minlen))`` over its out-edges --
  the longest path from a sink back to the node. It yields a poor
  (wide-bottom) ranking but is fast and serves as the seed for the
  tight-tree and network-simplex rankers. It does NOT normalize ranks (a
  later stage does).
* :func:`slack` returns ``w_rank - v_rank - round(edge.minlen)`` -- the
  slack of an edge is how much longer it is than its minimum length. A
  tight edge (slack 0) is one whose rank span exactly matches its
  ``minlen``; the feasible-tree / network-simplex loops drive edges
  toward slack 0.

Borrow-checker clone artefacts
------------------------------
This is the **third zero-semantic-clone leaf** after R252
``parent_dummy_chains`` and R253 ``nesting_graph`` (the sixth reuse of
the R250 / R251 borrow-checker classification framework). Every grok
``clone()`` here is a pure ownership / immutable-reference artefact, and
all are stripped:

* ``node_label.cloned().unwrap_or(GraphNode::default())`` in
  :func:`_longest_path_dfs` and :func:`slack` -- grok borrows the node
  label (``Option<&GraphNode>``) but then needs ``&mut g`` for
  ``out_edges`` / ``node_mut`` / ``edge_with_obj``, so it ``.cloned()``
  the label to release the borrow; Python reads the attribute directly
  (a node label is a shared reference, no borrow lifetime).
* ``edge_with_obj(&e).cloned().unwrap_or(GraphEdge::default())`` -- the
  same pattern for edge labels (reading ``minlen``); the clone releases
  the ``&GraphEdge`` borrow. Stripped.
* ``ranks.iter().min().cloned()`` -- an ``Option<&i32> -> Option<i32>``
  Copy artefact; Python uses ``min(ranks, default=0)``.
* ``rank.clone()`` on the ``Some(rank.clone())`` assignment -- ``i32`` is
  ``Copy``; Python assigns the int directly.

The stage performs **no structural removal that invalidates a still-held
reference** (longest_path only writes ``rank`` in place; slack is a pure
read), so no ``copy.deepcopy`` survives.

GraphEdge manual-Default minlen
-------------------------------
grok's ``GraphEdge`` has a hand-written ``Default`` impl (``lib.rs``) that
sets ``minlen = Some(1.0)`` (Python mirrors this: ``GraphEdge.minlen``
defaults to ``1.0``, not ``None``). This makes grok's
``.unwrap_or(0.0)`` (in :func:`longest_path`) and ``.unwrap_or(10.0)``
(in :func:`slack`) **dead code on the default-label path** -- a missing
edge label resolves to default ``minlen`` ``1.0``, never the fallback.
The fallbacks only fire when a *real* edge label carries ``minlen ==
None``; both are preserved verbatim below (longest_path -> 0, slack ->
10 -- note the asymmetric defaults, faithfully reproduced).

Round half-to-even caveat
-------------------------
grok's ``f32::round()`` rounds half *away from zero*; Python's
``round()`` rounds half *to even*. In practice every real ``minlen`` is
an integer (``1.0`` / ``3.0`` / ``node_sep``), so the two agree; the
divergence only surfaces on a synthetic ``x.5`` minlen, which no caller
passes.
"""

from __future__ import annotations

from minimax_code.data_structures.graphlib import Edge, Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["longest_path", "slack"]


def longest_path(g: Graph) -> None:
    """Assign ranks via the longest-path algorithm (mirrors grok ``rank::util::longest_path``).

    Pushes every node to the lowest rank it can occupy: for each source
    node, DFS-walks the DAG assigning ``rank = min(child_rank - minlen)``
    over the out-edges. Yields a wide-bottom, unnormalized ranking that
    seeds the tight-tree and network-simplex rankers. Mutates ``g`` in
    place (writes ``GraphNode.rank``; no return).

    Pre-conditions: the input graph is a DAG (cycles have no valid rank).
    """
    visited: OrderedHashMap = OrderedHashMap()
    for node_id in g.sources():
        _longest_path_dfs(node_id, g, visited)


def _longest_path_dfs(v: str, g: Graph, visited: OrderedHashMap) -> int:
    """Depth-first longest-path rank assignment (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``longest_path`` as a Rust nested ``fn`` (no
    closure capture, every parameter passed explicitly); Python hoists it
    to module scope as a private helper, paralleling the R249 / R251 /
    R253 nested-DFS-to-module-scope decision. Returns the rank assigned
    to ``v`` (an ``int``); the shared ``visited`` map is mutated in place
    across the recursion (Python passes the boxed reference, matching
    grok's ``&mut`` parameter).
    """
    node_label = g.node(v)
    if v in visited:
        # grok: ``visited.contains_key(v)`` -- key existence, NOT value-non-None
        # (the R243 OrderedHashMap None-value trap: a present key with a None
        # value must still count as visited). grok then reads
        # ``node_label.cloned().unwrap_or(GraphNode::default()).rank.unwrap_or(0)``
        # -- the clone is a borrow artefact; Python reads the rank directly. A
        # node with no label, or a label with no rank, yields 0.
        if node_label is None:
            return 0
        return node_label.rank if node_label.rank is not None else 0
    visited.insert(v, True)

    ranks: list[int] = []
    out_edges = g.out_edges(v, None) or []
    for e in out_edges:
        child_rank = _longest_path_dfs(e.w, g, visited)
        # grok: ``g.edge_with_obj(&e).cloned().unwrap_or(GraphEdge::default())
        # .minlen.unwrap_or(0.0).round() as i32``. The clone is a borrow
        # artefact (releasing &GraphEdge so the loop body can reborrow g);
        # Python reads minlen directly. GraphEdge::default().minlen ==
        # Some(1.0) (manual Default impl), so unwrap_or(0.0) only fires when a
        # real edge label carries minlen == None -- a missing edge label
        # yields default minlen 1.0.
        edge_label = g.edge_with_obj(e)
        if edge_label is None:
            minlen = 1  # default GraphEdge minlen = 1.0, round(1.0) = 1
        elif edge_label.minlen is None:
            minlen = 0  # grok ``.unwrap_or(0.0).round()``
        else:
            minlen = round(edge_label.minlen)
        ranks.append(child_rank - minlen)

    # grok: ``ranks.iter().min().cloned().unwrap_or(0) as i32`` -- the clone
    # is a Copy artefact (Option<&i32> -> Option<i32>); Python uses min(...).
    rank = min(ranks) if ranks else 0
    # grok: ``g.node_mut(v); node_label.rank = Some(rank.clone())`` -- the
    # clone is a Copy artefact (i32); Python assigns the int directly. A
    # source node with no label is skipped (node_label is None).
    if node_label is not None:
        node_label.rank = rank
    return rank


def slack(g: Graph, e: Edge) -> int:
    """Return the slack of edge ``e`` (mirrors grok ``rank::util::slack``).

    The slack is the difference between the edge's actual rank span and
    its minimum length: ``w_rank - v_rank - round(minlen)``. A tight edge
    has slack 0. NOTE the minlen default here is ``10`` (NOT ``0`` as in
    :func:`longest_path`) -- faithfully reproduced from grok's asymmetric
    ``.unwrap_or(10.0)``.
    """
    # grok: ``g.node(&e.w).cloned().unwrap_or(GraphNode::default()).rank
    # .unwrap_or(0)`` -- borrow artefacts stripped; a node without a label or
    # rank yields 0.
    w_node = g.node(e.w)
    w_rank = w_node.rank if (w_node is not None and w_node.rank is not None) else 0
    v_node = g.node(e.v)
    v_rank = v_node.rank if (v_node is not None and v_node.rank is not None) else 0
    # grok: ``g.edge_with_obj(e).cloned().unwrap_or(GraphEdge::default()).minlen
    # .unwrap_or(10.0).round() as i32`` -- NOTE default 10.0 (NOT 0.0 as in
    # longest_path). GraphEdge::default().minlen == Some(1.0) (manual Default
    # impl), so unwrap_or(10.0) only fires when a real edge label carries
    # minlen == None -- a missing edge label yields default minlen 1.0.
    edge_label = g.edge_with_obj(e)
    if edge_label is None:
        minlen = 1  # default GraphEdge minlen = 1.0, round(1.0) = 1
    elif edge_label.minlen is None:
        minlen = 10  # grok ``.unwrap_or(10.0).round()``
    else:
        minlen = round(edge_label.minlen)
    return w_rank - v_rank - minlen
