"""Coordinate assignment via Brandes-Kopf (R266, vendored ``third_party/dagre_rust``).

Mirrors the ``layout/position/bk.rs`` module of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
horizontal-coordinate-assignment stage of the layered layout pipeline --
the pass that runs after ``rank`` (R254-R257) assigns every node a rank and
``order`` (R258-R265) decides the left-to-right sequence within each rank.
``layout/mod.rs`` calls ``position`` between the order phase and the final
coordinate stamping; this module implements the Brandes-Kopf "Fast and
Simple Horizontal Coordinate Assignment" algorithm ( BRAND simplest ),
which assigns each node an x coordinate by:

1. marking type-1 conflicts (non-inner segments crossing inner segments --
   an inner segment is an edge whose both endpoints are ``dummy`` long-edge
   placeholders) and type-2 conflicts (edges crossing the ``border`` node
   bands R249's ``add_border_segments`` mints),
2. for each of the four extreme alignments (``ul`` / ``ur`` / ``dl`` /
   ``dr`` -- up/down vertical sweep x left/right horizontal bias),
   aligning nodes into vertical "blocks" via median neighbours
   (:func:`vertical_alignment`), then compacting the blocks to their
   tightest horizontal placement (:func:`horizontal_compaction`),
3. picking the smallest-width alignment, re-aligning the other three to
   share its min/max edge (:func:`align_coordinates`), and finally
   balancing each node's x to the median of its four placements
   (:func:`balance`).

This is the **first ``position`` leaf** (the ``position`` sub-package opens
here at 1/2; ``position/mod.rs`` lands in the next leaf as 2/2). It is the
**sixteenth zero-semantic-clone leaf** after R252-R265: every grok
``clone()`` is a ``String`` borrow, an ``f32`` ``Copy``, or a
``Vec`` ownership-transfer artefact (the single ``xss.clone()`` in
:func:`balance` becomes a per-map manual deep-copy because
:class:`OrderedHashMap` has no ``clone``; ``str`` ids and ``f32`` xs are
immutable so a per-key re-insert is the faithful ``Vec`` equivalent).
Same barrel policy as the sibling layout stages: every public symbol stays
out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.position.bk.<fn>``.

Migration notes
---------------
* ``block_g: Graph<GraphOption, String, f32>`` -> a bare :class:`Graph` with
  no options (directed/multigraph/compound default to True/False/False --
  the :class:`GraphOption` all-``None`` contract) and no default-label
  factories (node labels are ``None``; the block graph carries only
  topology + ``f32`` edge separations, never reading a node label).
* ``OrderedHashMap`` has no ``values_mut`` / ``clone``: the
  ``xs.values_mut().for_each(|x| *x += delta)`` sweep in
  :func:`align_coordinates` becomes a ``keys()`` snapshot + ``insert`` loop
  (``f32`` is immutable, so the in-place add maps to a re-insert at
  ``cur + delta``); the ``xss.clone()`` in :func:`balance` becomes a
  per-alignment manual deep-copy.
* ``conflicts.extend(find_type_2_conflicts(...))`` keeps grok's overwrite
  semantics verbatim -- :meth:`OrderedHashMap.extend` appends new keys and
  overwrites the value of existing keys in place, so a type-2 entry whose
  ``v`` already has a type-1 inner map replaces it (a faithful copy of the
  Rust ``HashMap::extend`` behaviour grok depends on).
* The four Rust nested ``fn``s (``visit_layer`` x2, ``scan``, ``iterate``,
  ``pass1``, ``pass2``) and the two ``Box<dyn Fn>`` neighbour closures
  become module-level private callables (``_visit_layer_type1`` /
  ``_scan_type2`` / ``_visit_layer_type2`` / ``_iterate`` / ``_pass1`` /
  ``_pass2`` / ``_predecessors_of`` / ``_successors_of``) -- explicit
  parameter passing stands in for the closure captures, and a plain ``def``
  avoids the E731 ``name = lambda`` trap.
"""

from __future__ import annotations

import math

from minimax_code.dagre import GraphConfig
from minimax_code.dagre.layout.util import build_layer_matrix
from minimax_code.dagre.lib import BorderTypeName
from minimax_code.data_structures import Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = [
    "add_conflict",
    "balance",
    "build_block_graph",
    "find_smallest_width_alignment",
    "find_type_2_conflicts",
    "has_conflict",
    "horizontal_compaction",
    "position_x",
    "vertical_alignment",
]


# === private helpers =====================================================


def _width(g: Graph, v: str) -> float:
    """Return node ``v``'s ``width`` (mirrors grok ``width``; 0.0 if absent)."""
    node = g.node(v)
    if node is None:
        return 0.0
    return node.width


def _find_other_inner_segment_node(g: Graph, v: str) -> str | None:
    """Return ``v``'s predecessor on an inner segment, or ``None`` (grok private).

    An inner segment is an edge whose both endpoints are ``dummy``; if ``v``
    is itself a dummy, the predecessor that is also a dummy is the other
    endpoint of ``v``'s inner segment. Returns ``None`` when ``v`` is not a
    dummy or has no dummy predecessor.
    """
    v_node = g.node(v)
    if v_node is not None and v_node.dummy is not None:
        for u in g.predecessors(v) or []:
            u_node = g.node(u)
            if u_node is not None and u_node.dummy is not None:
                return u
    return None


def add_conflict(
    conflicts: OrderedHashMap,
    v_: str,
    w_: str,
) -> None:
    """Record the ``(v, w)`` crossing as a conflict (mirrors grok ``add_conflict``).

    The endpoints are ordered ``v <= w`` so the conflict is canonical; the
    inner map for ``v`` is created on first sight (the entry API mirrors
    grok's ``get``-check-``insert``-``get_mut`` sequence without
    re-constructing an empty map when ``v`` already has an inner map).
    """
    v, w = v_, w_
    if v > w:
        v, w = w, v
    inner = conflicts.get_mut(v)
    if inner is None:
        inner = OrderedHashMap()
        conflicts.insert(v, inner)
    inner.insert(w, True)


def has_conflict(
    conflicts: OrderedHashMap,
    v_: str,
    w_: str,
) -> bool:
    """Return ``True`` if the ``(v, w)`` crossing is a recorded conflict (grok pub)."""
    v, w = v_, w_
    if v > w:
        v, w = w, v
    inner = conflicts.get(v)
    if inner is None:
        return False
    return w in inner


# === type-1 conflicts ====================================================


def _visit_layer_type1(
    g: Graph,
    prev_layer: list[str],
    layer: list[str],
    conflicts: OrderedHashMap,
) -> None:
    """Scan one layer against its predecessor for type-1 conflicts (grok nested).

    Walks ``layer`` left to right; at each node incident on an inner segment
    (or at the last node) it scans every predecessor of every node since
    the last such checkpoint and records a conflict for any predecessor
    whose position falls outside the ``[k0, k1]`` inner-segment span --
    unless both endpoints are dummies (a legitimate inner segment).
    """
    k0 = 0
    scan_pos = 0
    prev_layer_length = len(prev_layer)
    last_node = layer[-1]

    for i, v in enumerate(layer):
        w = _find_other_inner_segment_node(g, v)
        if w is not None:
            w_node = g.node(w)
            if w_node is not None and w_node.order is not None:
                k1 = w_node.order
            else:
                k1 = 0
        else:
            k1 = prev_layer_length

        if w is not None or v == last_node:
            for scan_node in layer[scan_pos : i + 1]:
                for u in g.predecessors(scan_node) or []:
                    u_label = g.node(u)
                    if u_label is None:
                        continue
                    u_pos = u_label.order if u_label.order is not None else 0
                    scan_node_label = g.node(scan_node)
                    scan_is_dummy = (
                        scan_node_label is not None and scan_node_label.dummy is not None
                    )
                    if (u_pos < k0 or k1 < u_pos) and not (
                        u_label.dummy is not None and scan_is_dummy
                    ):
                        add_conflict(conflicts, u, scan_node)
            scan_pos = i + 1
            k0 = k1


def _find_type_1_conflicts(
    g: Graph,
    layering: list[list[str]],
) -> OrderedHashMap:
    """Mark every non-inner segment crossing an inner segment (grok private).

    Scans each non-empty layer against its predecessor via
    :func:`_visit_layer_type1`; grok's ``filter``-then-``reduce`` over the
    layers maps to a flat ``for`` loop over adjacent non-empty pairs.
    """
    conflicts: OrderedHashMap = OrderedHashMap()
    non_empty = [layer for layer in layering if layer]
    for idx in range(1, len(non_empty)):
        _visit_layer_type1(g, non_empty[idx - 1], non_empty[idx], conflicts)
    return conflicts


# === type-2 conflicts ====================================================


def _scan_type2(
    g: Graph,
    south: list[str],
    south_pos: int,
    south_end: int,
    prev_north_border: int,
    next_north_border: int,
    conflicts: OrderedHashMap,
) -> None:
    """Scan a south sub-range for border-band crossings (grok nested ``scan``).

    For each dummy ``v`` in ``south[south_pos:south_end]``, each dummy
    predecessor whose order falls outside ``[prev_north_border,
    next_north_border]`` is recorded as a type-2 conflict (it crosses the
    border band).
    """
    for i in range(south_pos, south_end):
        v = south[i]
        v_node = g.node(v)
        if v_node is None or v_node.dummy is None:
            continue
        for u in g.predecessors(v) or []:
            u_node = g.node(u)
            if u_node is None:
                continue
            u_order = u_node.order if u_node.order is not None else 0
            if u_node.dummy is not None and (
                u_order < prev_north_border or u_order > next_north_border
            ):
                add_conflict(conflicts, u, v)


def _visit_layer_type2(
    g: Graph,
    north: list[str],
    south: list[str],
    conflicts: OrderedHashMap,
) -> None:
    """Scan one south layer against its north predecessor (grok nested ``visit_layer``).

    Tracks the border band ``[prev_north_pos, next_north_pos]`` defined by
    successive ``border`` dummies; at each border dummy it scans the south
    sub-range since the last border, and after every node it scans the
    trailing sub-range up to the layer end -- so the final scan catches any
    node that crosses the last border band.
    """
    prev_north_pos = -1
    next_north_pos = -1
    south_pos = 0

    for south_lookahead, v in enumerate(south):
        v_node = g.node(v)
        if v_node is not None and v_node.dummy == "border":
            predecessors = g.predecessors(v) or []
            if predecessors:
                pred0_node = g.node(predecessors[0])
                if pred0_node is not None and pred0_node.order is not None:
                    next_north_pos = pred0_node.order
                else:
                    next_north_pos = 0
                _scan_type2(
                    g,
                    south,
                    south_pos,
                    south_lookahead,
                    prev_north_pos,
                    next_north_pos,
                    conflicts,
                )
                south_pos = south_lookahead
                prev_north_pos = next_north_pos
        _scan_type2(
            g,
            south,
            south_pos,
            len(south),
            next_north_pos,
            len(north),
            conflicts,
        )


def find_type_2_conflicts(
    g: Graph,
    layering: list[list[str]],
) -> OrderedHashMap:
    """Mark every edge crossing a border band (mirrors grok ``find_type_2_conflicts``).

    Iterates adjacent layer pairs (skipping a pair whose either layer is
    empty) and delegates to :func:`_visit_layer_type2`.
    """
    conflicts: OrderedHashMap = OrderedHashMap()
    for i in range(1, len(layering)):
        if not layering[i - 1] or not layering[i]:
            continue
        _visit_layer_type2(g, layering[i - 1], layering[i], conflicts)
    return conflicts


# === vertical alignment ==================================================


def _predecessors_of(graph: Graph, v: str) -> list[str]:
    """Neighbour closure: predecessors (grok ``|g, v| g.predecessors(v).unwrap_or(vec![])``)."""
    return graph.predecessors(v) or []


def _successors_of(graph: Graph, v: str) -> list[str]:
    """Neighbour closure: successors (grok ``|g, v| g.successors(v).unwrap_or(vec![])``)."""
    return graph.successors(v) or []


def vertical_alignment(
    g: Graph,
    layering: list[list[str]],
    conflicts: OrderedHashMap,
    neighbor_fn,
) -> tuple[OrderedHashMap, list[str]]:
    """Align nodes into vertical blocks via median neighbours (grok pub).

    Returns ``(root, align_keys)`` -- ``root[v]`` is the topmost node of
    ``v``'s block, and ``align_keys`` is the insertion-ordered key list of
    the ``align`` map (``align[v]`` is the node ``v`` aligns above). A node
    aligns with its median neighbour (the median of the neighbours that have
    a cached position) when that does not create a type-1/2 conflict and
    does not split an earlier block.
    """
    root: OrderedHashMap = OrderedHashMap()
    align: OrderedHashMap = OrderedHashMap()
    pos: OrderedHashMap = OrderedHashMap()

    for layer in layering:
        for order, v in enumerate(layer):
            root.insert(v, v)
            align.insert(v, v)
            pos.insert(v, order)

    for layer in layering:
        prev_idx = -1
        for v in layer:
            ws = [w for w in neighbor_fn(g, v) if w in pos]
            if not ws:
                continue
            ws.sort(key=lambda w: pos.get(w))
            mp = (len(ws) - 1) / 2.0
            i = int(mp)
            il = math.ceil(mp)
            while i <= il:
                w = ws[i]
                w_pos = pos.get(w)
                align_v = align.get(v)
                root_w = root.get(w)
                if (
                    align_v == v
                    and w_pos is not None
                    and prev_idx < w_pos
                    and not has_conflict(conflicts, v, w)
                ):
                    align.insert(w, v)
                    root.insert(v, root_w)
                    align.insert(v, root_w)
                    prev_idx = w_pos
                i += 1

    align_keys = list(align.keys())
    return root, align_keys


# === horizontal compaction ===============================================


def _sep(
    node_sep: float,
    edge_sep: float,
    reverse_sep: bool,
):
    """Build the block-separation function (grok ``sep`` closure factory).

    The separation between two adjacent block nodes is the sum of their
    half-widths plus the node/edge separation, with the label-position
    delta flipped when ``reverse_sep`` is set (the right-bias sweep).
    Returns a ``closure(g, v, w) -> float``.
    """

    def closure(g: Graph, v: str, w: str) -> float:
        v_label = g.node(v)
        w_label = g.node(w)
        if v_label is None or w_label is None:
            return 0.0
        total = 0.0
        delta = 0.0
        total += v_label.width / 2.0
        if v_label.labelpos == "l":
            delta = -v_label.width / 2.0
        elif v_label.labelpos == "r":
            delta = v_label.width / 2.0
        if delta != 0.0:
            total += delta if reverse_sep else -delta
        total += (edge_sep if v_label.dummy is not None else node_sep) / 2.0
        total += (edge_sep if w_label.dummy is not None else node_sep) / 2.0
        total += w_label.width / 2.0
        delta = 0.0
        if w_label.labelpos == "l":
            delta = w_label.width / 2.0
        elif w_label.labelpos == "r":
            delta = -w_label.width / 2.0
        if delta != 0.0:
            total += delta if reverse_sep else -delta
        return total

    return closure


def build_block_graph(
    g: Graph,
    layering: list[list[str]],
    root: OrderedHashMap,
    reverse_sep: bool,
) -> Graph:
    """Build the block graph (mirrors grok ``build_block_graph``).

    Each block root becomes a node; adjacent block roots within a layer get
    an edge labelled with the separation between them (the max of the
    computed separation and any prior label, so multi-layer adjacency keeps
    the widest separation). The block graph is a bare :class:`Graph` (no
    options, no factories) carrying only topology + ``f32`` edge labels.
    """
    block_graph: Graph = Graph()
    graph_label = g.graph()
    if graph_label is None:
        graph_label = GraphConfig()
    sep_fn = _sep(graph_label.nodesep, graph_label.edgesep, reverse_sep)

    for layer in layering:
        u: str | None = None
        for v in layer:
            v_root = root.get(v)
            block_graph.set_node(v_root, None)
            if u is not None:
                u_root = root.get(u)
                if u_root != v_root:
                    prev_max = block_graph.edge(u_root, v_root, None)
                    if prev_max is None:
                        prev_max = 0.0
                    sep_val = sep_fn(g, v, u)
                    block_graph.set_edge(u_root, v_root, max(sep_val, prev_max), None)
            u = v
    return block_graph


def _pass1(
    elem: str,
    xs: OrderedHashMap,
    block_g: Graph,
    _g: Graph,
    _border_type,
) -> None:
    """First compaction sweep: assign the smallest x (grok nested ``pass1``).

    ``xs[elem] = max(pred_x + sep)`` over the in-edges -- a block is placed
    just past the furthest predecessor.
    """
    in_edges = block_g.in_edges(elem, None) or []
    val = 0.0
    for e in in_edges:
        pred_x = xs.get(e.v)
        if pred_x is None:
            pred_x = 0.0
        sep = block_g.edge_with_obj(e)
        if sep is None:
            sep = 0.0
        ev = pred_x + sep
        if ev > val:
            val = ev
    xs.insert(elem, val)


def _pass2(
    elem: str,
    xs: OrderedHashMap,
    block_g: Graph,
    g: Graph,
    border_type,
) -> None:
    """Second compaction sweep: tighten to the greatest x (grok nested ``pass2``).

    ``xs[elem] = max(xs[elem], min(succ_x - sep))`` over the out-edges --
    a block is pulled up to the nearest successor when that does not cross
    a border of the swept ``border_type``.
    """
    out_edges = block_g.out_edges(elem, None) or []
    min_val = math.inf
    for e in out_edges:
        succ_x = xs.get(e.w)
        if succ_x is None:
            succ_x = 0.0
        sep = block_g.edge_with_obj(e)
        if sep is None:
            sep = 0.0
        ev = succ_x - sep
        if ev < min_val:
            min_val = ev
    if min_val != math.inf:
        node = g.node(elem)
        if node is not None and node.border_type != border_type:
            cur = xs.get(elem)
            if cur is None:
                cur = 0.0
            xs.insert(elem, max(cur, min_val))


def _iterate(
    set_xs_func,
    next_nodes_func,
    block_g: Graph,
    xs: OrderedHashMap,
    g: Graph,
    border_type,
) -> None:
    """Post-order DFS over the block graph (grok nested ``iterate``).

    The grok stack machine marks a node visited on first sight, re-pushes
    it after its successors/predecessors, and runs ``set_xs_func`` on the
    second pop -- so a block is placed only after every block it depends on.
    """
    stack = list(block_g.nodes())
    elem = stack.pop() if stack else None
    visited: OrderedHashMap = OrderedHashMap()
    while elem is not None:
        if elem in visited:
            set_xs_func(elem, xs, block_g, g, border_type)
        else:
            visited.insert(elem, True)
            stack.append(elem)
            stack.extend(next_nodes_func(block_g, elem))
        elem = stack.pop() if stack else None


def horizontal_compaction(
    g: Graph,
    layering: list[list[str]],
    root: OrderedHashMap,
    align: list[str],
    reverse_sep: bool,
) -> OrderedHashMap:
    """Assign x coordinates by compacting the block graph (grok pub).

    Builds the block graph (:func:`build_block_graph`), runs the smallest-x
    sweep (:func:`_pass1` via :func:`_iterate` over predecessors), then the
    greatest-x tightening sweep (:func:`_pass2` via :func:`_iterate` over
    successors), and finally stamps every aligned node with its root's x.
    """
    xs: OrderedHashMap = OrderedHashMap()
    block_g = build_block_graph(g, layering, root, reverse_sep)
    border_type = BorderTypeName.BorderLeft if reverse_sep else BorderTypeName.BorderRight

    _iterate(_pass1, _predecessors_of, block_g, xs, g, border_type)
    _iterate(_pass2, _successors_of, block_g, xs, g, border_type)

    for v in align:
        root_id = root.get(v)
        if root_id is None:
            continue
        root_x = xs.get(root_id)
        if root_x is None:
            continue
        xs.insert(v, root_x)
    return xs


# === width + balance =====================================================


def find_smallest_width_alignment(
    g: Graph,
    xss: OrderedHashMap,
) -> OrderedHashMap:
    """Return the narrowest of the four alignments (mirrors grok pub).

    Width is ``max(x + half_width) - min(x - half_width)`` over the
    alignment's nodes. The grok ``min_by`` double-comparator maps to a
    ``min(..., key=...)`` pre-computing each alignment's width; ties keep
    the first (insertion-order) alignment, matching Rust's stable
    ``min_by``.
    """

    def width_of(xs: OrderedHashMap) -> float:
        max_val = -math.inf
        min_val = math.inf
        for v, x in xs.iter():
            half_width = _width(g, v) / 2.0
            if x + half_width > max_val:
                max_val = x + half_width
            if x - half_width < min_val:
                min_val = x - half_width
        return max_val - min_val

    return min(xss.values(), key=width_of)


def _align_coordinates(
    xss: OrderedHashMap,
    align_to: OrderedHashMap,
) -> None:
    """Re-align the three non-smallest alignments to the smallest (grok private).

    Left-biased alignments share the smallest's minimum x; right-biased
    share its maximum. The smallest itself (``xs == align_to``) is skipped.
    """
    align_to_vals = list(align_to.values())
    align_to_min = min(align_to_vals)
    align_to_max = max(align_to_vals)

    for vert in ["u", "d"]:
        for horiz in ["l", "r"]:
            alignment = vert + horiz
            xs = xss.get(alignment)
            if xs is None or xs == align_to:
                continue
            xs_vals = list(xs.values())
            if horiz == "l":
                delta = align_to_min - min(xs_vals)
            else:
                delta = align_to_max - max(xs_vals)
            if delta != 0.0:
                for k in list(xs.keys()):
                    cur = xs.get(k)
                    if cur is None:
                        cur = 0.0
                    xs.insert(k, cur + delta)


def balance(
    xss: OrderedHashMap,
    align: str | None,
) -> OrderedHashMap:
    """Balance each node's x to the median of its four placements (grok pub).

    When ``align`` is set the node takes the named alignment's x outright;
    otherwise it takes the mean of the two middle placements (``xs[1]`` and
    ``xs[2]`` of the four sorted placements). Operates on a deep copy of
    ``xss`` (the grok ``xss.clone()`` -- :class:`OrderedHashMap` has no
    ``clone``) reading the originals for the per-node computation.
    """
    xss_clone: OrderedHashMap = OrderedHashMap()
    for k, xs in xss.iter():
        xs_copy: OrderedHashMap = OrderedHashMap()
        for vk, vx in xs.iter():
            xs_copy.insert(vk, vx)
        xss_clone.insert(k, xs_copy)

    ul = xss_clone.get("ul")
    if ul is not None:
        for v in list(ul.keys()):
            if align is not None:
                align_xs = xss.get(align)
                if align_xs is None:
                    balance_val = 0.0
                else:
                    raw = align_xs.get(v)
                    balance_val = raw if raw is not None else 0.0
                ul.insert(v, balance_val)
            else:
                xs_vals: list[float] = []
                for _xs in xss.values():
                    raw = _xs.get(v)
                    xs_vals.append(raw if raw is not None else math.inf)
                xs_vals.sort()
                xs1 = xs_vals[1] if len(xs_vals) > 1 else 0.0
                xs2 = xs_vals[2] if len(xs_vals) > 2 else 0.0
                ul.insert(v, (xs1 + xs2) / 2.0)

    result = xss_clone.get("ul")
    assert result is not None  # position_x always inserts the "ul" alignment
    return result


def position_x(g: Graph) -> OrderedHashMap:
    """Assign an x coordinate to every node (mirrors grok ``position_x``).

    The public entry point: build the layer matrix, mark type-1 + type-2
    conflicts, compute all four extreme alignments, re-align them to the
    smallest, and balance. Returns ``{node_id: x}``.
    """
    layering = build_layer_matrix(g)
    if all(not layer for layer in layering):
        return OrderedHashMap()

    conflicts = _find_type_1_conflicts(g, layering)
    conflicts.extend(find_type_2_conflicts(g, layering))

    xss: OrderedHashMap = OrderedHashMap()
    adjusted_layering: list[list[str]] | None = None
    for vert in ["u", "d"]:
        if vert == "u":
            adjusted_layering = [list(layer) for layer in layering]
        else:
            adjusted_layering = [list(layer) for layer in layering]
            adjusted_layering.reverse()

        for horiz in ["l", "r"]:
            if horiz == "r":
                for inner in adjusted_layering:
                    inner.reverse()

            neighbor_fn = _predecessors_of if vert == "u" else _successors_of
            root, align_keys = vertical_alignment(
                g, adjusted_layering, conflicts, neighbor_fn
            )
            xs = horizontal_compaction(
                g, adjusted_layering, root, align_keys, horiz == "r"
            )
            if horiz == "r":
                neg = OrderedHashMap()
                for k, v in xs.iter():
                    neg.insert(k, -v)
                xs = neg
            xss.insert(vert + horiz, xs)

    smallest_width = find_smallest_width_alignment(g, xss)
    _align_coordinates(xss, smallest_width)
    graph_label = g.graph()
    align = graph_label.align if graph_label is not None else None
    return balance(xss, align)
