"""Compound-graph border-segment injection (R249, vendored ``third_party/dagre_rust``).

Mirrors ``layout/add_border_segments.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). For every compound
node that carries ``min_rank`` / ``max_rank`` bounds (a subgraph spanning
multiple ranks), injects a pair of sentinel ``border`` dummy nodes per rank --
``_bl`` (border-left) and ``_br`` (border-right) -- chained top-to-bottom with
weight-1 edges so the positioner can pad the subgraph's rank span. Walks the
compound forest depth-first so child subgraphs are bordered before their
parents.

This is the third layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments``) and the **first consumer of the R246
:class:`~minimax_code.dagre.lib.BorderTypeName` enum** (migrated into
``dagre.lib`` to break the lib/layout module cycle -- see the ``dagre.lib``
docstring). It reuses the R248 :func:`~minimax_code.dagre.layout.util.add_dummy_node`
helper to mint the border sentinels and mutates the live graph in place.
``run_layout`` calls it between ``parent_dummy_chains`` and ``order``
(``layout/mod.rs`` line 631).

The module-private ``_add_border_node`` has a deliberately different signature
from the R248 ``util.add_border_node`` (which builds a standalone border dummy
from just a prefix + optional rank/order). The leading underscore keeps the two
names from colliding when both are in scope; grok keeps its private helper
nested inside ``add_border_segments.rs`` where no such collision arises.
"""

from __future__ import annotations

from minimax_code.dagre.layout.util import add_dummy_node
from minimax_code.dagre.lib import BorderTypeName, GraphEdge, GraphNode
from minimax_code.data_structures.graphlib import GRAPH_NODE, Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["add_border_segments"]


def add_border_segments(g: Graph) -> None:
    """Inject border sentinels for every compound node spanning multiple ranks.

    Mirrors grok ``add_border_segments(g)``: walks the compound forest roots
    (``children(GRAPH_NODE)``) depth-first and, for each node whose
    ``min_rank`` is set, seeds ``border_left`` / ``border_right`` maps and
    inserts a ``_bl`` / ``_br`` dummy per rank in ``[min_rank, max_rank + 1)``.
    Mutates ``g`` in place (no return value, mirroring grok).
    """
    for v in g.children(GRAPH_NODE):
        _dfs(v, g)


def _dfs(v: str, g: Graph) -> None:
    """Depth-first border injection (mirrors grok's nested ``dfs`` closure).

    Recurses into ``v``'s children first (bottom-up), then -- if ``v`` itself
    spans ranks (``min_rank`` set) -- seeds its border maps and injects the
    per-rank ``_bl`` / ``_br`` sentinels. Children are snapshotted before the
    recursion so child-graph mutation (new dummy nodes) cannot perturb the
    iteration.
    """
    children = g.children(v)
    if children:
        for cv in children:
            _dfs(cv, g)
    node = g.node_mut(v)
    assert node is not None  # grok ``node_mut(v).unwrap()`` (v is a known node)
    if node.min_rank is not None:
        node.border_left = OrderedHashMap()
        node.border_right = OrderedHashMap()
        # grok: ``rank = min_rank.unwrap_or(0)`` -- but the guard above already
        # proved ``min_rank`` is ``Some``, so the ``unwrap_or(0)`` fallback is
        # unreachable; assign the proven-non-None value directly.
        rank = node.min_rank
        # grok: ``max_rank = node.max_rank.clone().unwrap_or(0) + 1`` -- a None
        # max_rank contributes 0, giving a half-open ``[min_rank, 1)`` range.
        max_rank = (node.max_rank or 0) + 1
        while rank < max_rank:
            _add_border_node(g, BorderTypeName.BorderLeft, "_bl", v, rank)
            _add_border_node(g, BorderTypeName.BorderRight, "_br", v, rank)
            rank += 1


def _add_border_node(
    g: Graph, prop: BorderTypeName, prefix: str, sg: str, rank: int
) -> None:
    """Inject one border sentinel and chain it to the previous rank (private).

    Mirrors grok ``add_border_node`` (private to ``add_border_segments.rs``;
    NOT the R248 ``util.add_border_node`` -- different signature, hence the
    ``_`` prefix). Builds a border dummy labelled with ``rank`` / ``border_type``,
    records it under ``sg``'s ``border_left`` / ``border_right`` map (chosen by
    ``prop``), links it to the previous-rank border node with a weight-1 edge
    when one exists, and parents it under ``sg``.
    """
    label = GraphNode()
    label.rank = rank
    label.border_type = prop
    curr = add_dummy_node(g, "border", label, prefix)
    sg_node = g.node_mut(sg)
    assert sg_node is not None  # grok ``node_mut(sg).unwrap()``
    # grok rebinds ``border`` to the right map via a ``match prop``; the
    # ternary preserves the identity-based dispatch (Enum matched by identity).
    border = (
        sg_node.border_right
        if prop is BorderTypeName.BorderRight
        else sg_node.border_left
    )
    assert border is not None  # grok ``as_mut().unwrap()`` (seeded by ``_dfs``)
    border.insert(rank, curr)
    # grok: ``let prev = border.get(&(rank - 1)); if prev.is_some() { ... }`` --
    # rank 0 looks up key -1 (absent) -> None -> no chain edge for the first rank.
    prev_v = border.get(rank - 1)
    if prev_v is not None:
        g.set_edge(prev_v, curr, GraphEdge(weight=1.0), None)
    g.set_parent(curr, sg)
