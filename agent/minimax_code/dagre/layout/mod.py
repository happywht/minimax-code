"""dagre layout top-level orchestrator (R268, vendored ``third_party/dagre_rust``).

Mirrors ``layout/mod.rs`` of the vendored ``dagre_rust`` 0.0.5 crate (upstream
``warpdotdev/mermaid-to-svg``, Apache-2.0): the **top-level orchestrator** of
the layered layout pipeline -- the ``layout()`` user entry point, the
``run_layout()`` 28-step pipeline that threads the whole stack together, and
the ``build_layout_graph()`` / ``update_input_graph()`` adapters that copy the
whitelisted layout-influencing attributes in and the computed coordinates out.
This is the **21st and final leaf** of the dagre layout stack (sibling to
``acyclic`` R251 / ``normalize`` R250 / ``coordinate_system`` R253 /
``nesting_graph`` R253 / ``util`` R248 / ``rank`` R254-R257 / ``order``
R258-R265 / ``position`` R266-R267 / ``parent_dummy_chains`` / ``add_border_segments``
R249): once every stage is in place, this leaf wires them into the canonical
Sugiyama-style pipeline (``make_space_for_edge_labels`` ->
``remove_self_edges`` -> ``acyclic`` -> ``nesting_graph`` -> ``rank`` ->
``order`` -> ``position`` -> ``coordinate_system`` -> ``translate_graph`` ->
``assign_node_intersects`` -> ``acyclic::undo``) and closes the migration at
100 %.

grok's ``layout::mod`` is the single public ``layout()`` entry point plus the
``run_layout()`` pipeline and a family of private stage helpers
(``make_space_for_edge_labels`` / ``inject_edge_label_proxies`` /
``assign_rank_min_max`` / ``translate_graph`` / ``assign_node_intersects`` /
``remove_edge_label_proxies`` / ``fixup_edge_label_coords`` /
``reverse_points_for_reversed_edges`` / ``remove_border_nodes`` /
``remove_self_edges`` / ``insert_self_edges`` / ``position_self_edges``) and
two default-stamper helpers (``set_graph_label_default_values`` /
``set_edge_label_default_values``). The 28-step pipeline is the literal
sequence ``run_layout`` calls in order; each step is a faithful translation of
the corresponding grok ``pub fn``.

Faithful translation choices (clone classification after R250 / R251 / R266 /
R267):

* **Semantic clones (deepcopy)** -- 8 sites where grok ``clone()`` isolates a
  label across a graph boundary or around a structural write-back, faithfully
  preserved as :func:`copy.deepcopy`:

  1. ``build_layout_graph`` ``graph_label`` -- the input ``GraphConfig`` is
     deep-copied onto the layout graph (cross-boundary + around ``set_graph``),
  2. ``build_layout_graph`` node ``_node`` -- each ``GraphNode`` is deep-copied
     onto the layout graph (cross-boundary; without it the layout graph would
     share node references with the input graph and ``run_layout``'s in-place
     mutations would leak back),
  3. ``build_layout_graph`` ``edge_label`` -- each ``GraphEdge`` is deep-copied
     onto the layout graph (cross-boundary),
  4. ``translate_graph`` ``graph_label`` -- the ``GraphConfig`` is deep-copied
     before the width/height write-back (around ``set_graph``),
  5. ``assign_node_intersects`` ``edge`` -- the edge label is deep-copied
     before the point insertion + ``set_edge_with_obj`` write-back,
  6. ``remove_self_edges`` ``edge_label`` -- the edge label is deep-copied
     onto ``node.self_edges`` before ``remove_edge_with_obj`` invalidates the
     graph reference,
  7. ``position_self_edges`` ``node`` -- the dummy node is deep-copied before
     the ``set_edge_with_obj`` + ``remove_node`` write-back,
  8. ``remove_border_nodes`` ``node`` -- the compound node is deep-copied
     before the width/height/x/y re-stamp + ``set_node`` write-back.

* **Borrow-checker clones (stripped)** -- grok ``clone()`` calls that exist
  only to release a ``&`` borrow so a ``&mut g`` can re-borrow, faithfully
  stripped on the Python side (Python has no borrow checker): the
  ``make_space_for_edge_labels`` ``graph_config`` block (read ``graph().rankdir``
  directly in the loop), the ``inject_edge_label_proxies`` ``v`` / ``w`` rank
  reads (read-only ``rank``), the ``assign_rank_min_max`` ``border_top`` /
  ``border_bottom`` node reads (read-only ``rank``), the ``assign_node_intersects``
  ``node_v`` / ``node_w`` reads (read-only ``x`` / ``y`` / ``width`` /
  ``height``), the ``remove_border_nodes`` ``t`` / ``b`` / ``l`` / ``r`` reads
  (read-only ``x`` / ``y``), the ``insert_self_edges`` ``self_edges`` snapshot
  (a shallow ``list()`` copy is enough -- the loop only reads width / height),
  and the ``position_self_edges`` ``node.label`` peel (``node`` is already a
  deepcopy, so ``graph_edge = node.label`` is an isolated reference).

* **unwrap -> assert** -- grok ``.unwrap()`` panics on a missing node / edge /
  config; Python mirrors the panic as ``assert x is not None  # grok: ...unwrap()``
  (the R248 ``util.py`` convention). The defensive guards hold because every
  node / edge a stage reads is guaranteed present by construction (the pipeline
  runs invariants forward).

Two integration details specific to this orchestrator:

* ``GraphElement`` enum -> ``_element_bounds(label, is_edge)`` helper: grok's
  ``GraphElement::Node(&GraphNode)`` / ``GraphElement::Edge(&GraphEdge)`` enum
  with ``x()`` / ``y()`` / ``width()`` / ``height()`` methods collapses to a
  module-level helper that returns ``(x, y, width, height)`` -- both variants
  read ``x`` / ``y`` directly (``GraphNode.x`` / ``GraphNode.y`` and
  ``GraphEdge.x`` / ``GraphEdge.y`` are non-Optional ``float``), the ``Node``
  variant reads ``width`` / ``height`` directly (``GraphNode`` non-Optional
  ``float``) and the ``Edge`` variant reads them through ``unwrap_or(0.0)``
  (``GraphEdge.width`` / ``GraphEdge.height`` are Optional). The companion
  ``_get_extremes(extremes, x, y, w, h)`` is the promoted grok nested ``fn``
  inside ``translate_graph`` (``*min_x = min_x.min(x - w/2)`` etc.).
* ``OrderedHashMap.keys()`` -> ``list(...)`` + ``sorted(...)``: grok's
  ``border_left.keys().cloned().collect::<Vec<i32>>(); l_keys.sort()`` becomes
  ``sorted(list(border_left.keys()))`` (Python ``int`` is immutable, no clone
  needed); ``border_left.get(&l_keys[len-1])`` stays ``border_left.get(l_keys[-1])``.

Visibility mirrors grok: ``layout`` / ``build_layout_graph`` /
``update_input_graph`` / ``run_layout`` are ``pub fn`` (the four public
entries); every stage helper and the two default-stampers are private (grok
marks them ``pub fn`` too but they are only ever called from ``run_layout`` /
``build_layout_graph`` / ``layout``; the migration keeps them module-level and
out of ``__all__``). Same barrel policy as the sibling layout stages: none of
these symbols earn a crate-root :class:`~minimax_code.dagre` ``__all__`` slot
(the R246 barrel count stays at 4); they are reachable only as
``minimax_code.dagre.layout.mod.<symbol>``. The dagre crate-root barrel count
of 4 is unchanged.
"""

from __future__ import annotations

import copy
import math

from minimax_code.dagre import GraphConfig, GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.dagre.layout import (
    acyclic,
    coordinate_system,
    nesting_graph,
    normalize,
)
from minimax_code.dagre.layout.add_border_segments import add_border_segments
from minimax_code.dagre.layout.order.mod import order
from minimax_code.dagre.layout.parent_dummy_chains import parent_dummy_chains
from minimax_code.dagre.layout.position.mod import position
from minimax_code.dagre.layout.rank.mod import rank
from minimax_code.dagre.layout.util import (
    Rect,
    add_dummy_node,
    as_non_compound_graph,
    build_layer_matrix,
    intersect_rect,
    normalize_ranks,
    remove_empty_ranks,
    transfer_node_edge_labels,
)
from minimax_code.data_structures import Graph, GraphOption

__all__ = ["build_layout_graph", "layout", "run_layout", "update_input_graph"]

DEFAULT_RANK_SEP: float = 50.0


def _element_bounds(label, is_edge: bool) -> tuple[float, float, float, float]:
    """Read ``(x, y, width, height)`` off a node or edge label (GraphElement helper).

    Mirrors grok's ``GraphElement::x()`` / ``y()`` / ``width()`` / ``height()``
    methods: both variants read ``x`` / ``y`` directly (non-Optional ``float``
    on both ``GraphNode`` and ``GraphEdge``); the ``Node`` variant reads
    ``width`` / ``height`` directly (non-Optional) and the ``Edge`` variant
    reads them through ``unwrap_or(0.0)`` (Optional on ``GraphEdge``).
    """
    x = label.x
    y = label.y
    if is_edge:
        width = label.width if label.width is not None else 0.0
        height = label.height if label.height is not None else 0.0
    else:
        width = label.width
        height = label.height
    return x, y, width, height


def _get_extremes(
    extremes: list[float],
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    """Fold one element's bounds into the running ``(min_x, max_x, min_y, max_y)``.

    Promoted from grok's nested ``fn get_extremes`` inside ``translate_graph``
    (``*min_x = min_x.min(x - w/2)`` etc.).
    """
    extremes[0] = min(extremes[0], x - w / 2.0)
    extremes[1] = max(extremes[1], x + w / 2.0)
    extremes[2] = min(extremes[2], y - h / 2.0)
    extremes[3] = max(extremes[3], y + h / 2.0)


def layout(g: Graph) -> None:
    """Run the full layered layout pipeline on ``g`` (mirrors grok ``layout``).

    Builds an isolated layout graph, runs the 28-step pipeline on it, then
    writes the computed coordinates back onto the input graph.
    """
    layout_graph = build_layout_graph(g)
    run_layout(layout_graph)
    update_input_graph(g, layout_graph)


def build_layout_graph(input_graph: Graph) -> Graph:
    """Build an isolated layout graph from ``input_graph`` (mirrors grok).

    Copies only the whitelisted layout-influencing attributes (the graph
    config, every node label + parent, every edge label) onto a fresh directed
    multigraph compound graph, stamping defaults on the config and edge labels.
    Every label is deep-copied so the layout graph never shares a reference
    with the input graph (``run_layout`` mutates labels in place).
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )

    input_graph_label = input_graph.graph()
    assert input_graph_label is not None  # grok: input_graph.graph().clone() (non-Option)
    graph_label = copy.deepcopy(input_graph_label)  # grok: .clone()
    set_graph_label_default_values(graph_label)
    g.set_graph(graph_label)

    for node_id in input_graph.nodes():
        _node = input_graph.node(node_id)
        if _node is None:
            continue
        g.set_node(node_id, copy.deepcopy(_node))  # grok: _node.cloned()
        g.set_parent(node_id, input_graph.parent(node_id))

    for edge_obj in input_graph.edges():
        _edge = input_graph.edge_with_obj(edge_obj)
        if _edge is None:
            continue
        edge_label = copy.deepcopy(_edge)  # grok: _edge.cloned().unwrap()
        set_edge_label_default_values(edge_label)
        g.set_edge_with_obj(edge_obj, edge_label)

    return g


def update_input_graph(input_graph: Graph, layout_graph: Graph) -> None:
    """Write the computed coordinates back onto ``input_graph`` (mirrors grok).

    Copies each node's ``x`` / ``y`` (and a compound node's ``width`` / ``height``)
    and each edge's ``points`` / ``x`` / ``y``, plus the graph ``width`` / ``height``,
    from the layout graph onto the input graph.
    """
    for v in input_graph.nodes():
        input_label = input_graph.node_mut(v)
        layout_label = layout_graph.node(v)
        assert layout_label is not None  # grok: layout_graph.node(&v).unwrap()

        if input_label is not None:
            input_label.x = layout_label.x
            input_label.y = layout_label.y

            if len(layout_graph.children(v)) > 0:
                input_label.width = layout_label.width
                input_label.height = layout_label.height

    for e in input_graph.edges():
        input_label = input_graph.edge_mut_with_obj(e)
        assert input_label is not None  # grok: edge_mut_with_obj(&e).unwrap()
        layout_label = layout_graph.edge_with_obj(e)
        assert layout_label is not None  # grok: edge_with_obj(&e).unwrap()

        input_label.points = copy.deepcopy(layout_label.points)  # grok: .clone()
        input_label.x = layout_label.x
        input_label.y = layout_label.y

    input_graph_label = input_graph.graph_mut()
    assert input_graph_label is not None  # grok: input_graph.graph_mut() (non-Option)
    layout_graph_label = layout_graph.graph()
    assert layout_graph_label is not None  # grok: layout_graph.graph() (non-Option)
    input_graph_label.width = layout_graph_label.width
    input_graph_label.height = layout_graph_label.height


def set_graph_label_default_values(graph_label: GraphConfig) -> None:
    """Stamp defaults on the graph config (mirrors grok ``set_graph_label_default_values``).

    Only ``None`` fields are filled (grok ``if x.is_none()`` guards), so an
    explicit caller value always wins.
    """
    if graph_label.ranksep is None:
        graph_label.ranksep = 50.0

    if graph_label.edgesep is None:
        graph_label.edgesep = 20.0

    if graph_label.nodesep is None:
        graph_label.nodesep = 50.0

    if graph_label.rankdir is None:
        graph_label.rankdir = "tb"

    if graph_label.marginx is None:
        graph_label.marginx = 0.0

    if graph_label.marginy is None:
        graph_label.marginy = 0.0


def set_edge_label_default_values(edge_label: GraphEdge) -> None:
    """Stamp defaults on the edge label (mirrors grok ``set_edge_label_default_values``).

    Only ``None`` fields are filled (grok ``if x.is_none()`` guards).
    """
    if edge_label.minlen is None:
        edge_label.minlen = 1.0

    if edge_label.weight is None:
        edge_label.weight = 1.0

    if edge_label.width is None:
        edge_label.width = 0.0

    if edge_label.height is None:
        edge_label.height = 0.0

    if edge_label.labeloffset is None:
        edge_label.labeloffset = 10.0

    if edge_label.labelpos is None:
        edge_label.labelpos = "r"


def make_space_for_edge_labels(graph: Graph) -> None:
    """Halve ``ranksep`` and grow labelled edges (mirrors grok).

    Halves the inter-rank ``ranksep`` (the label sits between ranks), then for
    every edge doubles its ``minlen`` (so the label spans two rank gaps) and,
    when the label is not centred (``labelpos != "c"``), grows the edge's
    ``width`` (``tb`` / ``bt`` rank direction) or ``height`` (``lr`` / ``rl``)
    by the ``labeloffset``.
    """
    graph_label = graph.graph_mut()
    assert graph_label is not None  # grok: graph.graph_mut() (non-Option in grok)
    rank_sep = graph_label.ranksep if graph_label.ranksep is not None else DEFAULT_RANK_SEP
    graph_label.ranksep = rank_sep / 2.0

    # grok wraps the loop in ``{ let graph_config = graph.graph().clone(); ... }``
    # only to release the immutable borrow so ``edge_mut_with_obj`` can reborrow
    # -- Python has no borrow checker, so the ``rankdir`` is read once into a
    # local before the loop (faithful to grok's single ``clone`` read).
    graph_config = graph.graph()
    assert graph_config is not None  # grok: graph.graph().clone() (non-Option)
    rankdir = graph_config.rankdir if graph_config.rankdir is not None else ""

    for e in graph.edges():
        edge = graph.edge_mut_with_obj(e)
        assert edge is not None  # grok: edge_mut_with_obj(&e).unwrap()

        minlen = edge.minlen if edge.minlen is not None else 1.0
        edge.minlen = minlen * 2.0

        labelpos = edge.labelpos if edge.labelpos is not None else ""
        if labelpos != "c":
            labeloffset = edge.labeloffset if edge.labeloffset is not None else 10.0
            if rankdir == "tb" or rankdir == "bt":
                edge.width = (edge.width if edge.width is not None else 0.0) + labeloffset
            else:
                edge.height = (edge.height if edge.height is not None else 0.0) + labeloffset


def inject_edge_label_proxies(graph: Graph) -> None:
    """Insert a rank-midpoint proxy dummy for every labelled edge (mirrors grok).

    For each edge with a non-zero ``width`` + ``height`` (i.e. a label), mints a
    dummy node at the integer midpoint rank between ``v`` and ``w``, sized to
    the label and tagged ``edge-proxy`` so ``remove_edge_label_proxies`` can
    reclaim it once ``order`` / ``position`` have placed it.
    """
    for e in graph.edges():
        edge = graph.edge_with_obj(e)
        assert edge is not None  # grok: edge_with_obj(&e).unwrap()
        edge_width = edge.width if edge.width is not None else 0.0
        edge_height = edge.height if edge.height is not None else 0.0
        if edge_width > 0.0 and edge_height > 0.0:
            v_node = graph.node(e.v)
            v_rank = v_node.rank if (v_node is not None and v_node.rank is not None) else 0
            w_node = graph.node(e.w)
            w_rank = w_node.rank if (w_node is not None and w_node.rank is not None) else 0

            label = GraphNode()
            label.rank = (w_rank - v_rank) // 2 + v_rank  # grok i32 truncation
            label.width = edge_width
            label.height = edge_height
            label.labelpos = edge.labelpos
            label.e = e
            add_dummy_node(graph, "edge-proxy", label, "_ep")


def assign_rank_min_max(graph: Graph) -> None:
    """Stamp ``min_rank`` / ``max_rank`` on compound nodes (mirrors grok).

    For every node carrying a ``border_top`` (the R249 ``add_border_segments``
    sentinel), copies the border sentinels' ranks onto the node's ``min_rank``
    / ``max_rank`` and tracks the running ``max_rank`` (a faithful carry-over
    of grok's local accumulator -- computed but, like grok, not returned).
    """
    max_rank = 0
    for v in graph.nodes():
        node = graph.node(v)
        assert node is not None  # grok: graph.node(&v).unwrap()
        border_top = node.border_top
        border_bottom = node.border_bottom
        if border_top is not None:
            top_node = graph.node(border_top)
            assert top_node is not None  # grok: node(&border_top.unwrap()).cloned().unwrap()
            _min_rank = top_node.rank if top_node.rank is not None else 0
            assert border_bottom is not None  # grok: border_bottom.unwrap()
            bottom_node = graph.node(border_bottom)
            assert bottom_node is not None  # grok: node(&border_bottom.unwrap()).cloned().unwrap()
            _max_rank = bottom_node.rank if bottom_node.rank is not None else 0

            _node = graph.node_mut(v)
            assert _node is not None  # grok: node_mut(&v).unwrap()
            _node.min_rank = _min_rank
            _node.max_rank = _max_rank

            max_rank = max(max_rank, _max_rank)  # grok: dead accumulator (unused)


def translate_graph(graph: Graph) -> None:
    """Translate the graph so its bounding box sits at the margin (mirrors grok).

    Computes the bounding box over every node and every labelled edge, subtracts
    the margin-adjusted ``min_x`` / ``min_y`` from every node / edge-point / label
    coordinate (shifting the graph onto the origin), and stamps the resulting
    ``width`` / ``height`` onto the graph config.
    """
    min_x = math.inf
    max_x = 0.0
    min_y = math.inf
    max_y = 0.0

    _graph_label = graph.graph()
    assert _graph_label is not None  # grok: g.graph().clone() (non-Option, around set_graph)
    graph_label = copy.deepcopy(_graph_label)  # grok: .clone()
    margin_x = graph_label.marginx if graph_label.marginx is not None else 0.0
    margin_y = graph_label.marginy if graph_label.marginy is not None else 0.0

    extremes = [min_x, max_x, min_y, max_y]

    for v in graph.nodes():
        node = graph.node(v)
        assert node is not None  # grok: g.node(&v).unwrap()
        x, y, w, h = _element_bounds(node, is_edge=False)
        _get_extremes(extremes, x, y, w, h)

    for e in graph.edges():
        edge = graph.edge_with_obj(e)
        assert edge is not None  # grok: g.edge_with_obj(&e).unwrap()
        edge_width = edge.width if edge.width is not None else 0.0
        edge_height = edge.height if edge.height is not None else 0.0
        if edge_width > 0.0 and edge_height > 0.0:
            x, y, w, h = _element_bounds(edge, is_edge=True)
            _get_extremes(extremes, x, y, w, h)

    min_x, max_x, min_y, max_y = extremes
    min_x -= margin_x
    min_y -= margin_y

    for v in graph.nodes():
        node = graph.node_mut(v)
        assert node is not None  # grok: g.node_mut(&v).unwrap()
        node.x -= min_x
        node.y -= min_y

    for e in graph.edges():
        edge = graph.edge_mut_with_obj(e)
        assert edge is not None  # grok: g.edge_mut_with_obj(&e).unwrap()
        if edge.points is not None:
            for p in edge.points:
                p.x -= min_x
                p.y -= min_y
        edge_width = edge.width if edge.width is not None else 0.0
        edge_height = edge.height if edge.height is not None else 0.0
        if edge_width > 0.0 and edge_height > 0.0:
            edge.x -= min_x
            edge.y -= min_y

    graph_label.width = max_x - min_x + margin_x
    graph_label.height = max_y - min_y + margin_y
    graph.set_graph(graph_label)


def assign_node_intersects(graph: Graph) -> None:
    """Insert the node-rectangle intersection points on every edge (mirrors grok).

    For each edge, ensures it has at least a two-point path, then prepends the
    intersection of the ``v`` rectangle with the first segment and appends the
    intersection of the ``w`` rectangle with the last segment (so edge endpoints
    touch the node borders, not the centres).
    """
    for e in graph.edges():
        edge = copy.deepcopy(graph.edge_mut_with_obj(e))  # grok: edge_mut_with_obj(&e).cloned().unwrap()
        assert edge is not None  # grok: .cloned().unwrap()
        node_v = graph.node(e.v)  # borrow clone stripped (read-only x/y/width/height)
        assert node_v is not None  # grok: g.node(&e.v).cloned().unwrap()
        node_w = graph.node(e.w)
        assert node_w is not None  # grok: g.node(&e.w).cloned().unwrap()

        if edge.points is None or len(edge.points) == 0:
            edge.points = []
            p1 = GraphEdgePoint(x=node_w.x, y=node_w.y)
            p2 = GraphEdgePoint(x=node_v.x, y=node_v.y)
        else:
            p1 = edge.points[0]
            p2 = edge.points[len(edge.points) - 1]

        points = edge.points
        points.insert(
            0,
            intersect_rect(
                Rect(
                    x=node_v.x,
                    y=node_v.y,
                    width=node_v.width,
                    height=node_v.height,
                ),
                p1,
            ),
        )
        points.append(
            intersect_rect(
                Rect(
                    x=node_w.x,
                    y=node_w.y,
                    width=node_w.width,
                    height=node_w.height,
                ),
                p2,
            ),
        )
        graph.set_edge_with_obj(e, edge)


def remove_edge_label_proxies(graph: Graph) -> None:
    """Reclaim the ``edge-proxy`` dummies onto their edges (mirrors grok).

    For every ``edge-proxy`` dummy, copies its rank onto the originating edge's
    ``label_rank`` (so the label is drawn at the proxy's rank) and removes the
    dummy node.
    """
    for v in graph.nodes():
        node = graph.node(v)
        assert node is not None  # grok: g.node(&v).unwrap()
        if node.dummy is not None and node.dummy == "edge-proxy":
            rank = node.rank if node.rank is not None else 0
            assert node.e is not None  # grok: node.e.clone().unwrap()
            graph_edge = graph.edge_mut_with_obj(node.e)
            if graph_edge is not None:
                graph_edge.label_rank = rank
            graph.remove_node(v)


def fixup_edge_label_coords(graph: Graph) -> None:
    """Offset left/right edge labels off the edge line (mirrors grok).

    For every placed edge label (``x != 0.0``) with a left/right ``labelpos``,
    shrinks the label ``width`` by the ``labeloffset`` and shifts the label
    ``x`` outwards by half the shrunk width plus the offset.
    """
    for e in graph.edges():
        edge = graph.edge_mut_with_obj(e)
        assert edge is not None  # grok: edge_mut_with_obj(&e).unwrap()
        if edge.x != 0.0:
            labelpos = edge.labelpos if edge.labelpos is not None else ""
            labeloffset = edge.labeloffset if edge.labeloffset is not None else 0.0
            if labelpos == "l" or labelpos == "r":
                edge.width = (edge.width if edge.width is not None else 0.0) - labeloffset
                if labelpos == "l":
                    edge.x -= (edge.width if edge.width is not None else 0.0) / 2.0 + labeloffset
                elif labelpos == "r":
                    edge.x += (edge.width if edge.width is not None else 0.0) / 2.0 + labeloffset


def reverse_points_for_reversed_edges(graph: Graph) -> None:
    """Reverse the point path of every reversed edge (mirrors grok).

    ``acyclic::run`` reversed some edges to break cycles (tagging them
    ``reversed``); after ``coordinate_system::undo`` restores the original
    direction, those edges' point paths must be flipped back to match.
    """
    for e in graph.edges():
        edge = graph.edge_mut_with_obj(e)
        assert edge is not None  # grok: edge_mut_with_obj(&e).unwrap()
        reversed_flag = edge.reversed if edge.reversed is not None else False
        if reversed_flag:
            if edge.points is not None:
                edge.points.reverse()


def remove_border_nodes(g: Graph) -> None:
    """Collapse compound-node borders back onto the node (mirrors grok).

    First pass: for every compound node (a node with children), if it carries
    the R249 ``add_border_segments`` border sentinels, re-stamps its ``width`` /
    ``height`` / ``x`` / ``y`` from the outermost border rectangles (so the
    compound node boxes its children). Second pass: removes every ``border``
    dummy node now that its geometry has been folded back in.
    """
    for v in g.nodes():
        if len(g.children(v)) > 0:
            node = copy.deepcopy(g.node(v))  # grok: g.node(&v).cloned().unwrap() (around set_node)
            assert node is not None  # grok: .cloned().unwrap()

            border_top = node.border_top
            if border_top is None:
                continue
            border_bottom = node.border_bottom
            if border_bottom is None:
                continue

            t = g.node(border_top)  # borrow clone stripped (read-only x/y)
            if t is None:
                continue
            b = g.node(border_bottom)
            if b is None:
                continue

            border_left = node.border_left
            if border_left is None:
                continue
            border_right = node.border_right
            if border_right is None:
                continue

            l_keys = sorted(list(border_left.keys()))
            if len(l_keys) == 0:
                continue
            r_keys = sorted(list(border_right.keys()))
            if len(r_keys) == 0:
                continue

            l_node_id = border_left.get(l_keys[len(l_keys) - 1])
            if l_node_id is None:
                continue
            r_node_id = border_right.get(r_keys[len(r_keys) - 1])
            if r_node_id is None:
                continue

            left = g.node(l_node_id)  # borrow clone stripped (read-only x)
            if left is None:
                continue
            right = g.node(r_node_id)
            if right is None:
                continue

            node.width = abs(right.x - left.x)
            node.height = abs(b.y - t.y)
            node.x = left.x + node.width / 2.0
            node.y = t.y + node.height / 2.0

            g.set_node(v, node)

    for v in g.nodes():
        node = g.node(v)
        assert node is not None  # grok: g.node(v).unwrap()
        if node.dummy is not None and node.dummy == "border":
            g.remove_node(v)


def remove_self_edges(graph: Graph) -> None:
    """Detach every self-loop onto its node's ``self_edges`` (mirrors grok).

    Self-loops (``v == w``) cannot be layered in the rank pipeline, so before
    ``acyclic`` / ``rank`` each is detached: its label is deep-copied onto
    ``node.self_edges`` (a list of ``(edge, label)`` tuples) and the edge itself
    is removed. ``insert_self_edges`` re-inserts them as ``selfedge`` dummies
    after ``order`` has fixed the within-rank sequence.
    """
    edge_objs = graph.edges()
    for edge_obj in edge_objs:
        if edge_obj.v == edge_obj.w:
            edge_label = copy.deepcopy(graph.edge_with_obj(edge_obj))  # grok: edge_with_obj(&edge_obj).cloned().unwrap()
            assert edge_label is not None  # grok: .cloned().unwrap()
            node = graph.node_mut(edge_obj.v)
            assert node is not None  # grok: node_mut(&edge_obj.v).unwrap()
            node.self_edges.append((edge_obj, edge_label))
            graph.remove_edge_with_obj(edge_obj)


def insert_self_edges(graph: Graph) -> None:
    """Re-insert detached self-loops as ``selfedge`` dummies (mirrors grok).

    Walks the per-rank layer matrix; for every node carrying ``self_edges``
    (populated by ``remove_self_edges``), stamps a fresh ``order`` (advancing an
    ``order_shift`` counter so each self-edge dummy gets its own slot) and mints
    a ``selfedge`` dummy node carrying the original edge + label.
    """
    layers = build_layer_matrix(graph)
    for layer in layers:
        order_shift = 0
        for i, v in enumerate(layer):
            node = graph.node_mut(v)
            assert node is not None  # grok: node_mut(v).unwrap()
            node.order = i + order_shift
            rank = node.rank  # borrow clone stripped (int immutable)

            self_edges = list(node.self_edges)  # grok: node.self_edges.clone() (snapshot for safe iteration)
            for edge, graph_edge in self_edges:
                _graph_node = GraphNode()
                _graph_node.width = graph_edge.width if graph_edge.width is not None else 0.0
                _graph_node.height = graph_edge.height if graph_edge.height is not None else 0.0
                _graph_node.rank = rank
                order_shift += 1
                _graph_node.order = i + order_shift
                _graph_node.e = edge
                _graph_node.label = copy.deepcopy(graph_edge)  # grok: graph_edge.clone()
                add_dummy_node(graph, "selfedge", _graph_node, "_se")


def position_self_edges(g: Graph) -> None:
    """Route each ``selfedge`` dummy as a 6-point loop curve (mirrors grok).

    For every ``selfedge`` dummy, computes the self-loop curve (six points
    arcing out from the node's right edge and back) from the originating node's
    geometry, writes the curve onto the original edge label, and removes the
    dummy node.
    """
    for v in g.nodes():
        node = copy.deepcopy(g.node(v))  # grok: g.node(&v).cloned().unwrap() (around set_edge_with_obj + remove_node)
        assert node is not None  # grok: .cloned().unwrap()
        if node.dummy is not None and node.dummy == "selfedge":
            assert node.e is not None  # grok: node.e.as_ref().unwrap()
            self_node = g.node(node.e.v)
            assert self_node is not None  # grok: g.node(&node.e.as_ref().unwrap().v).unwrap()

            x = self_node.x + self_node.width / 2.0
            y = self_node.y
            dx = node.x - x
            dy = self_node.height / 2.0

            assert node.label is not None  # grok: node.label.clone().unwrap()
            graph_edge = node.label  # borrow clone stripped (node already deep-copied)
            graph_edge.points = [
                GraphEdgePoint(x=x + 2.0 * dx / 3.0, y=y - dy),
                GraphEdgePoint(x=x + 2.0 * dx / 3.0, y=y - dy),
                GraphEdgePoint(x=x + 5.0 * dx / 6.0, y=y - dy),
                GraphEdgePoint(x=x + dx, y=y),
                GraphEdgePoint(x=x + 5.0 * dx / 6.0, y=y + dy),
                GraphEdgePoint(x=x + 2.0 * dx / 3.0, y=y + dy),
            ]
            graph_edge.x = node.x
            graph_edge.y = node.y
            g.set_edge_with_obj(node.e, graph_edge)
            g.remove_node(v)


def run_layout(g: Graph) -> None:
    """Run the canonical 28-step Sugiyama-style layout pipeline (mirrors grok).

    Threads the whole stack together: edge-label space -> self-edge detachment
    -> acyclic reversal -> nesting-graph rank gaps -> non-compound rank
    assignment -> edge-label proxy injection -> empty-rank removal -> nesting
    cleanup -> rank normalisation -> border rank min/max -> proxy removal ->
    normalisation -> parent dummy chains -> border segments -> within-rank
    ordering -> self-edge re-insertion -> coordinate-system adjust -> position
    -> self-edge routing -> border-node collapse -> normalisation undo ->
    label-coord fixup -> coordinate-system undo -> translate -> node-intersect
    insertion -> reversed-edge point flip -> acyclic undo.
    """
    make_space_for_edge_labels(g)
    remove_self_edges(g)
    acyclic.run(g)
    nesting_graph.run(g)
    nc_graph = as_non_compound_graph(g)
    rank(nc_graph)
    transfer_node_edge_labels(nc_graph, g)
    inject_edge_label_proxies(g)
    remove_empty_ranks(g)
    nesting_graph.cleanup(g)
    normalize_ranks(g)
    assign_rank_min_max(g)
    remove_edge_label_proxies(g)
    normalize.run(g)
    parent_dummy_chains(g)
    add_border_segments(g)
    order(g)
    insert_self_edges(g)
    coordinate_system.adjust(g)
    position(g)
    position_self_edges(g)
    remove_border_nodes(g)
    normalize.undo(g)
    fixup_edge_label_coords(g)
    coordinate_system.undo(g)
    translate_graph(g)
    assign_node_intersects(g)
    reverse_points_for_reversed_edges(g)
    acyclic.undo(g)
