"""Long-edge normalization (R250, vendored ``third_party/dagre_rust``).

Mirrors ``layout/normalize/mod.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). Breaks every long edge
(one whose endpoints span more than one rank) into a chain of unit-length
segments joined by ``_d`` ``edge`` dummy nodes -- one per intermediate rank --
and records the head of each chain on ``GraphConfig.dummy_chains`` so
:func:`undo` can walk it back.

Pre-conditions: the input graph is a DAG and every node carries a ``rank``.
Post-conditions: every edge has length 1 and the dummy chains are recorded for
later denormalization. ``run_layout`` brackets the position phase with this
pair -- ``normalize::run`` at ``layout/mod.rs`` line 629 (before
``add_border_segments`` / ``position`` / ``order``) and ``normalize::undo`` at
line 638 (after).

This is the fourth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize``) and the first
stage with an explicit ``run`` / ``undo`` pair that ``run_layout`` brackets a
later phase with (R247 ``coordinate_system`` was adjust/undo around position
too, but normalize is the one whose ``run`` mutates structure -- splitting
edges -- and whose ``undo`` reverses that exact mutation). It reuses the R248
:func:`~minimax_code.dagre.layout.util.add_dummy_node` helper to mint the
``edge`` / ``edge-label`` sentinels and mutates the live graph in place.

Borrow-checker clone artefacts
------------------------------
Two clones survive the port because they carry semantic weight, not just Rust
ownership:

* ``_edge_label = copy.deepcopy(edge_label)`` before
  :meth:`~minimax_code.data_structures.graphlib.Graph.remove_edge_with_obj` --
  the in-graph label reference is invalidated by the removal, so the geometry
  each dummy's ``edge_label`` field must carry is captured first (grok
  ``edge_label.clone()``).
* ``node = copy.deepcopy(node_)`` in :func:`undo` -- ``remove_node`` invalidates
  the in-graph node reference, but the loop still needs to read the removed
  node's ``x`` / ``y`` / ``dummy`` to build the waypoint list.

Where grok clones purely for ownership -- ``attrs.edge_label = _edge_label`` in
the loop and ``orig_label = node.edge_label`` (rather than the grok
``.clone()`` on each) -- Python shares the live reference: ``add_dummy_node``
deep-clones the whole ``attrs`` before storing, and ``node`` is already an
independent deepcopy, so the independence invariant holds without the extra
copy (mirrors the R248 borrow-checker-clone-stripping decision).
"""

from __future__ import annotations

import copy

from minimax_code.dagre import GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.dagre.layout.util import add_dummy_node
from minimax_code.data_structures.graphlib import Edge, Graph

__all__ = ["run", "undo"]


def run(g: Graph) -> None:
    """Split every long edge into unit-length segments joined by dummy nodes.

    Mirrors grok ``normalize::run``: resets ``GraphConfig.dummy_chains`` to an
    empty list, snapshots every edge, then breaks each that spans more than one
    rank into a chain of ``_d`` dummies. Mutates ``g`` in place (no return).
    """
    graph_label = g.graph_mut()
    assert graph_label is not None  # grok: g.graph_mut() (graph always present)
    graph_label.dummy_chains = []
    edges = g.edges()
    for edge_obj in edges:
        _normalize_edge(g, edge_obj)


def _normalize_edge(g: Graph, e: Edge) -> None:
    """Break one edge ``e`` into unit-length segments (private, mirrors grok).

    For an edge whose endpoints are exactly one rank apart, only clears its
    ``points``; otherwise removes it and inserts one ``_d`` ``edge`` (or
    ``edge-label`` at the label rank) dummy per intermediate rank, chained with
    weight-preserving edges, recording the first dummy on ``dummy_chains``.
    """
    v = e.v
    w = e.w
    v_node = g.node(v)
    v_rank = v_node.rank if (v_node is not None and v_node.rank is not None) else 0
    w_node = g.node(w)
    w_rank = w_node.rank if (w_node is not None and w_node.rank is not None) else 0
    edge_label = g.edge_mut_with_obj(e)
    if edge_label is None:
        return
    edge_label.points = []
    # grok: ``let weight = edge_label.weight.clone()`` -- ``Option<f32>`` is
    # immutable in Python, so the live reference is reused across every dummy.
    weight = edge_label.weight
    label_rank = edge_label.label_rank if edge_label.label_rank is not None else 0

    if w_rank == v_rank + 1:
        return

    # grok: ``let _edge_label = edge_label.clone()`` -- a deep clone because the
    # ``remove_edge_with_obj`` below invalidates the in-graph label reference;
    # each dummy's ``edge_label`` field must keep its own copy of the geometry.
    _edge_label = copy.deepcopy(edge_label)
    g.remove_edge_with_obj(e)

    i = 0
    v_rank += 1
    while v_rank < w_rank:
        attrs = GraphNode()
        # grok: ``attrs.edge_label = Some(_edge_label.clone())`` -- the clone is
        # a borrow-checker artefact; add_dummy_node deep-clones ``attrs`` before
        # storing, so sharing the reference yields an independent copy per node.
        attrs.edge_label = _edge_label
        attrs.edge_obj = e
        attrs.rank = v_rank
        dummy_type = "edge"
        if v_rank == label_rank:
            attrs.width = _edge_label.width if _edge_label.width is not None else 0.0
            attrs.height = _edge_label.height if _edge_label.height is not None else 0.0
            attrs.labelpos = _edge_label.labelpos
            dummy_type = "edge-label"
        dummy = add_dummy_node(g, dummy_type, attrs, "_d")
        dummy_edge_label = GraphEdge()
        dummy_edge_label.weight = weight
        g.set_edge(v, dummy, dummy_edge_label, None)
        if i == 0:
            graph_label = g.graph_mut()
            assert graph_label is not None
            if graph_label.dummy_chains is None:
                graph_label.dummy_chains = []
            graph_label.dummy_chains.append(dummy)
        v = dummy
        i += 1
        v_rank += 1

    graph_edge = GraphEdge()
    graph_edge.weight = weight
    g.set_edge(v, w, graph_edge, None)


def undo(g: Graph) -> None:
    """Collapse dummy chains back into their original long edges (mirrors grok).

    Walks each head recorded on ``GraphConfig.dummy_chains``: for every dummy
    in the chain, records its positioned ``x`` / ``y`` as a waypoint on the
    original edge's ``points`` (and its label geometry when the dummy is an
    ``edge-label``), removes the dummy, then re-creates the original long edge
    with the accumulated waypoints. No-op when ``dummy_chains`` is ``None``.
    """
    graph_label = g.graph()
    if graph_label is None or graph_label.dummy_chains is None:
        return
    dummy_chains = graph_label.dummy_chains
    for v_ in dummy_chains:
        node_ = g.node(v_)
        if node_ is None:
            continue
        # grok: ``let mut node = node_.cloned().unwrap()`` -- a deep clone because
        # remove_node below invalidates the in-graph node reference; the local
        # copy keeps ``x`` / ``y`` / ``dummy`` readable after the node is gone.
        node = copy.deepcopy(node_)
        # grok: ``node.edge_label.clone()`` is a borrow-checker artefact; node is
        # already an independent deepcopy and is never mutated through edge_label
        # here, so the live reference is shared.
        orig_label = node.edge_label if node.edge_label is not None else GraphEdge()
        assert node.edge_obj is not None  # grok: node.edge_obj.unwrap()
        edge_obj = node.edge_obj
        v = v_
        while node.dummy is not None:
            sucs = g.successors(v) or []
            w = sucs[0] if sucs else ""
            g.remove_node(v)
            assert orig_label.points is not None  # grok: points.as_mut().unwrap()
            orig_label.points.append(GraphEdgePoint(x=node.x, y=node.y))
            if node.dummy == "edge-label":
                orig_label.x = node.x
                orig_label.y = node.y
                orig_label.width = node.width
                orig_label.height = node.height
            v = w
            next_node = g.node(v)
            assert next_node is not None  # grok: g.node(&v).cloned().unwrap()
            node = copy.deepcopy(next_node)
        g.set_edge_with_obj(edge_obj, orig_label)
