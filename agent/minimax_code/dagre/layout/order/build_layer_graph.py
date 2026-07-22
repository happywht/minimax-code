"""Per-layer sort graph for the order sweep (R262, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/build_layer_graph.rs`` of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
per-layer sort graph ``order::sort_subgraph`` / ``order::sort`` (later leaves)
sweep over to re-sequence one rank at a time. ``order::mod`` calls
:func:`build_layer_graph` once per sweep direction (up then down) per rank:
it projects the movable rank's nodes -- plus their preserved compound
hierarchy -- into a fresh directed compound graph, attaches the incident
edges selected by the :class:`GraphRelationship` parameter (``IN_EDGES`` for
the up sweep, ``OUT_EDGES`` for the down sweep), and roots every parentless
movable node under a synthetic ``_root{id}`` node set in the graph label's
``root`` attribute. The result is the standalone sub-graph the sweep sorts.

This is the **direct structural upstream** of the unmigrated ``sort`` /
``sort_subgraph`` phase: both walk the layer graph's compound hierarchy +
aggregated edge weights to drive the barycenter re-sequence (R260 + R261
feed the weights; this leaf builds the graph they run on). ``sort.rs`` /
``sort_subgraph.rs`` start the sweep from the layer graph's ``root``
attribute, so landing it unblocks both later leaves.

It is the **eleventh zero-semantic-clone leaf** (the fifth ``order`` leaf,
``order`` sub-package 5/9) after R252 / R253 / R254 / R255 / R256 / R257 /
R258 / R259 / R260 / R261: every grok ``clone()`` is a ``String`` borrow or
a ``GraphNode`` borrow-release artefact (``v.clone()`` is an immutable
``str`` HashMap-key read; ``node.clone()`` is the Rust borrow-to-owned
transfer -- ``g.node(v)`` returns ``&GraphNode`` but ``set_node`` owns its
label, so Rust forces a ``clone()``; Python passes the live reference
straight through, no ``copy.deepcopy``). The ``border_left`` /
``border_right`` ``OrderedHashMap`` reads are ``.get(rank)`` value reads
(``Option<&str>`` -> ``str | None``), not structural clones. Same barrel
policy: ``build_layer_graph`` / ``create_root_node`` / ``GraphRelationship``
stay out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.build_layer_graph.build_layer_graph``.
The barrel now re-exports ``build_layer_graph`` alongside ``barycenter`` +
``cross_count`` + ``init_order`` + ``resolve_conflicts``.
"""

from __future__ import annotations

from enum import Enum

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.util import unique_id
from minimax_code.data_structures import Graph, GraphOption

__all__ = ["GraphRelationship", "build_layer_graph", "create_root_node"]


class GraphRelationship(Enum):
    """Which incident edges the layer graph projects (mirrors grok enum).

    grok's ``GraphRelationship::InEdges`` selects ``g.in_edges(v)`` (the up
    sweep -- sources below the movable rank); ``OutEdges`` selects
    ``g.out_edges(v)`` (the down sweep -- sinks above). The build pass uses
    it to filter which short edges are copied into the layer graph.
    """

    IN_EDGES = "in_edges"
    OUT_EDGES = "out_edges"


def build_layer_graph(
    g: Graph, rank: int, relationship: GraphRelationship
) -> Graph:
    """Construct the per-rank sort graph (mirrors grok ``build_layer_graph``).

    Walks every node of ``g``; for each node in the movable ``rank``
    (``rank == node.rank``) OR spanning it as a compound subgraph
    (``node.min_rank <= rank <= node.max_rank``), copies the node into a
    fresh directed compound ``result`` graph and re-parents it under its
    original ``g`` parent -- or under the synthetic root if parentless. Then
    copies every short incident edge selected by ``relationship``
    (``in_edges`` / ``out_edges``), aggregating weights where the
    non-multigraph result already has the edge. Subgraph nodes (those with
    ``min_rank``) are re-stamped with a fresh default ``GraphNode`` carrying
    only the ``border_left_`` / ``border_right_`` entries
    ``add_border_segments`` (R249) seeded for this rank -- the per-rank
    border sentinels the sweep honours.

    Pre-conditions mirror grok: the input graph is a DAG; base nodes carry
    ``rank``; subgraph nodes carry ``min_rank`` / ``max_rank``; edges carry
    ``weight``. The output graph preserves the movable rank's hierarchy,
    roots parentless movable nodes under ``result.graph().root``, and
    aggregates copied-edge weights (no multi-edges).

    Args:
        g: The layered graph (post-``rank`` / post-``add_border_segments``).
        rank: The movable rank to project.
        relationship: ``IN_EDGES`` (up sweep) or ``OUT_EDGES`` (down sweep).

    Returns:
        A fresh directed compound graph carrying the movable rank + its
        incident ``relationship`` edges, rooted under ``result.graph().root``.
    """
    root = create_root_node(g)
    result: Graph = Graph(
        GraphOption(directed=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    result.set_graph(GraphConfig())
    graph_label = result.graph_mut()
    if graph_label is not None:
        graph_label.root = root

    for v in g.nodes():
        node = g.node(v)
        if node is None:
            # grok ``g.node(v).unwrap()`` panics on a ghost; Python skips
            # the unlabelled vertex (the R258 defensive widening).
            continue
        parent = g.parent(v)

        in_rank = node.rank == rank
        min_rank = node.min_rank
        max_rank = node.max_rank
        in_subgraph_rank = (
            min_rank is not None
            and max_rank is not None
            and min_rank <= rank <= max_rank
        )

        relationship_edges = g.in_edges(v, None) or []
        if relationship == GraphRelationship.OUT_EDGES:
            relationship_edges = g.out_edges(v, None) or []

        if in_rank or in_subgraph_rank:
            # grok ``result.set_node(v.clone(), Some(node.clone()))``: the
            # ``node.clone()`` is the Rust borrow-to-owned transfer
            # (``g.node(v)`` -> ``&GraphNode``, ``set_node`` owns its label),
            # so Python passes the live reference -- the eleventh
            # zero-semantic-clone leaf (no ``copy.deepcopy``).
            result.set_node(v, node)
            if parent is not None:
                result.set_parent(v, parent)
            else:
                result.set_parent(v, root)

            # This assumes we have only short edges!
            for e in relationship_edges:
                u = e.w if e.v == v else e.v
                existing = result.edge(u, v, None)
                weight = (
                    existing.weight
                    if existing is not None and existing.weight is not None
                    else 0.0
                )
                source_edge = g.edge_with_obj(e)
                source_weight = (
                    source_edge.weight
                    if source_edge is not None and source_edge.weight is not None
                    else 0.0
                )
                edge_label = GraphEdge()
                edge_label.weight = source_weight + weight
                result.set_edge(u, v, edge_label, None)

            if min_rank is not None:
                # grok re-stamps subgraph nodes with a fresh default node
                # carrying only the per-rank border sentinels. The border
                # maps were seeded by ``add_border_segments`` (R249).
                graph_node = GraphNode()
                if node.border_left is not None:
                    graph_node.border_left_ = node.border_left.get(rank)
                if node.border_right is not None:
                    graph_node.border_right_ = node.border_right.get(rank)
                result.set_node(v, graph_node)

    return result


def create_root_node(g: Graph) -> str:
    """Mint a fresh ``_root{id}`` id absent from ``g`` (mirrors grok).

    Draws a monotonic id from :func:`unique_id` (the R248 util primitive)
    and retries while the candidate already names a node in ``g`` (the
    pathological collision case).
    """
    v = f"_root{unique_id()}"
    while g.has_node(v):
        v = f"_root{unique_id()}"
    return v
