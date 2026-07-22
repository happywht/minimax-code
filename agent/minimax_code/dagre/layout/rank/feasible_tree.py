"""Feasible-tree ranker (R255, vendored ``third_party/dagre_rust``).

Mirrors ``layout/rank/feasible_tree.rs`` of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). Constructs
a spanning tree of *tight* edges (edges whose rank span exactly matches
their ``minlen`` -- slack 0) and shifts the input graph's node ranks so the
tree becomes feasible. This is the seed the ``network_simplex`` ranker
pivots on, and the body of the ``tight-tree`` ranker strategy
(``rank::mod`` dispatches ``tight-tree`` to ``longest_path`` (R254)
followed by this function).

This is the ninth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains`` -> R253 ``nesting_graph`` ->
R254 ``rank/util`` -> R255 ``rank/feasible_tree``) and the **second leaf
of the ``rank`` sub-package**. ``run_layout`` calls ``rank`` at
``layout/mod.rs`` line 620; ``feasible_tree`` is the direct upstream of
``network_simplex`` (its ``use crate::layout::rank::feasible_tree``
dependency closes here; ``rank::network_simplex`` and the ``rank::mod``
dispatcher still land in later leaves).

Algorithm (Gansner, "A Technique for Drawing Directed Graphs")
--------------------------------------------------------------
1. Seed the tree ``t`` (undirected) with an arbitrary start node.
2. Repeat until ``t`` spans every node of ``g``:
   a. ``_tight_tree`` -- grow ``t`` via DFS, adding every node reachable
      across a slack-0 edge.
   b. If ``t`` already spans the graph, stop.
   c. ``_find_min_slack_edge`` -- pick the tree-border edge (exactly one
      endpoint in ``t``) with the smallest slack.
   d. ``_shift_ranks`` -- slide every node in ``t`` by ``delta`` so the
      chosen edge becomes tight: ``delta = slack`` if ``v`` is in ``t``,
      ``-slack`` if ``w`` is.
3. Return ``t`` (an undirected graph of tight edges).

Pre-conditions: the graph is a DAG, connected, has >= 1 node, every node
carries a ``rank`` consistent with incident-edge ``minlen`` (the
``longest_path`` seed satisfies this), and every edge carries a ``minlen``.

Borrow-checker clone artefacts
------------------------------
This is the **fourth zero-semantic-clone leaf** after R252 / R253 / R254
(the seventh reuse of the R250 / R251 classification framework). Every
grok ``clone()`` in ``feasible_tree.rs`` is a pure borrow / Copy artefact,
all stripped:

* ``g.nodes().first().cloned().unwrap_or("".to_string())`` -- an
  ``Option<&String> -> Option<String>`` that releases the borrow on ``g``
  before the mutable ``tight_tree`` / ``shift_ranks`` calls; Python reads
  the list element directly (a shared ``str`` reference).
* ``node_edge.v.clone()`` / ``w.clone()`` in ``tight_tree`` -- the edge
  endpoints are borrowed from the iterator but stored into ``t``; Python
  reads the ``str`` attributes directly.
* ``result.unwrap().0.clone()`` in ``find_min_stack_edge`` -- the winning
  ``Edge`` is borrowed from the iterator but returned by value; Python
  returns the shared reference.

The stage performs **no structural removal** (it only adds nodes/edges to
``t`` and mutates ``rank`` in place on ``g``), so no ``copy.deepcopy``
survives.

Notable grok quirks preserved
-----------------------------
* grok's private ``find_min_stack_edge`` is a typo for
  ``find_min_slack_edge`` (the dagre upstream JS is ``findMinSlackEdge``);
  this port uses the corrected spelling ``_find_min_slack_edge`` (the
  function selects on edge *slack*, never on a stack).
* ``t`` is built undirected (``GraphOption { directed: false }``) and
  ``tight_tree`` walks ``g.node_edges(v, None)`` (in-edges + out-edges) so
  the tree DFS treats edges as undirected; each edge's far endpoint is
  recovered by comparing ``node_edge.v == v``.
"""

from __future__ import annotations

from minimax_code.dagre import GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.util import slack
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.graphlib import Edge

__all__ = ["feasible_tree"]


def feasible_tree(g: Graph) -> Graph:
    """Build a tight-edge spanning tree and shift ``g``'s ranks to make it feasible.

    Mirrors grok ``rank::feasible_tree::feasible_tree``. Returns a new
    undirected tree ``t`` (``directed=False, multigraph=False,
    compound=False``) spanning every node of ``g``, where each tree edge is
    tight (slack 0). As a side effect the ``rank`` of every node in ``g`` is
    shifted so the tight tree is feasible. Mutates ``g`` in place (writes
    ``GraphNode.rank``); returns ``t`` (a new graph).

    Pre-conditions: ``g`` is a connected DAG with >= 1 node, every node
    ranked consistently with incident-edge ``minlen`` (e.g. via
    :func:`~minimax_code.dagre.layout.rank.util.longest_path`).
    """
    t: Graph = Graph(GraphOption(directed=False, multigraph=False, compound=False))
    nodes = g.nodes()
    # grok: ``g.nodes().first().cloned().unwrap_or("".to_string())`` -- the
    # clone releases the borrow on g before the mutable calls below. An empty
    # graph yields the "" sentinel (a pre-condition violation, but carried
    # faithfully: t is seeded with "" and the loop exits immediately since
    # size == 0).
    start = nodes[0] if nodes else ""
    size = g.node_count()
    t.set_node(start, GraphNode())
    while _tight_tree(t, g) < size:
        edge = _find_min_slack_edge(t, g)
        if edge is not None:
            # delta slides the tree so the chosen border edge becomes tight.
            # If v is in t, raising the tree by +slack tightens v->w; if w is
            # in t, lowering by -slack does. (Exactly one of v/w is in t.)
            if t.has_node(edge.v):
                delta = slack(g, edge)
            else:
                delta = -1 * slack(g, edge)
            _shift_ranks(t, g, delta)
    return t


def _tight_tree(t: Graph, g: Graph) -> int:
    """Grow ``t`` with every tight-edge-reachable node; return its node count.

    Mirrors grok ``tight_tree``. DFS-walks from every node currently in ``t``,
    adding any node reachable across a slack-0 edge (plus the tree edge).
    ``t`` only grows -- no node or edge is ever removed -- so repeated calls
    are monotonic. Returns ``t.node_count()``.
    """
    nodes = t.nodes()
    for node_id in nodes:
        _tight_tree_dfs(node_id, t, g)
    return t.node_count()


def _tight_tree_dfs(v: str, t: Graph, g: Graph) -> None:
    """Depth-first tight-edge extension (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``tight_tree`` as a Rust nested ``fn`` (no
    closure capture, every parameter passed explicitly); Python hoists it to
    module scope as a private helper, paralleling the R249 / R251 / R253 /
    R254 nested-DFS-to-module-scope decision. Walks ``g``'s edges incident
    to ``v`` *undirected* (``node_edges(v, None)`` = in-edges + out-edges);
    for each incident edge whose far endpoint is not yet in ``t`` and whose
    slack is 0, adds the endpoint (and the tree edge) and recurses.
    """
    node_edges = g.node_edges(v, None) or []
    for node_edge in node_edges:
        # The tree is undirected: recover the far endpoint. node_edge.v is
        # the stored source; if v is the source, the far end is .w, else v
        # is the target and the far end is .v.
        if node_edge.v == v:
            w = node_edge.w
        else:
            w = node_edge.v
        if not t.has_node(w) and slack(g, node_edge) == 0:
            t.set_node(w, GraphNode())
            t.set_edge(v, w, GraphEdge(), None)
            _tight_tree_dfs(w, t, g)


def _find_min_slack_edge(t: Graph, g: Graph) -> Edge | None:
    """Return the smallest-slack edge crossing the tree border, or ``None``.

    Mirrors grok ``find_min_stack_edge`` (sic -- grok typo for ``slack``;
    corrected here). A tree-border edge has exactly one endpoint inside
    ``t``; among those, returns the one with the algebraically smallest
    slack (the most over-compressed border edge). Returns ``None`` when no
    edge crosses the border (``t`` already spans ``g``, or ``g`` is
    disconnected -- a pre-condition violation).
    """
    best: Edge | None = None
    best_slack: int | None = None
    for e in g.edges():
        # XOR: exactly one endpoint inside t.
        if t.has_node(e.v) != t.has_node(e.w):
            s = slack(g, e)
            if best_slack is None or s < best_slack:
                best = e
                best_slack = s
    return best


def _shift_ranks(t: Graph, g: Graph, delta: int) -> None:
    """Add ``delta`` to the rank of every node of ``g`` that sits in ``t``.

    Mirrors grok ``shift_ranks``. Walks ``t``'s nodes (the tree members) and
    bumps each one's ``rank`` on ``g`` by ``delta``; a ``None`` rank is
    treated as 0 (grok ``node.rank.unwrap_or(0)``). Nodes not in ``t`` are
    left untouched. Mutates ``g`` in place.
    """
    for node_id in t.nodes():
        node = g.node_mut(node_id)
        if node is not None:
            current = node.rank if node.rank is not None else 0
            node.rank = current + delta
