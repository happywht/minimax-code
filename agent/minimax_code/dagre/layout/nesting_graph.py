"""Compound-graph nesting scaffolding for rank assignment (R253, vendored).

Mirrors ``layout/nesting_graph.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). Implements Sander's
"Layout of Compound Directed Graphs": wraps the input compound DAG in a
nesting scaffolding -- a synthetic ``_root`` node plus ``_bt`` (border-top) /
``_bb`` (border-bottom) sentinels bracketing every compound node's rank span,
linked by ``nesting_edge`` edges -- so the downstream ``rank`` stage can assign
ranks that keep every subgraph's contents vertically compact and bordered.
``run_layout`` calls this as the **rank-preparation bracket**:
``nesting_graph::run`` at ``layout/mod.rs`` line 617 (the second stage, right
after ``acyclic::run`` and before the rank computation), and
``nesting_graph::cleanup`` at line 625 (right after ``remove_empty_ranks`` and
before ``normalize_ranks``) -- the nesting scaffolding is erected solely so
``rank`` / ``remove_empty_ranks`` can run on a connected, bordered compound
graph, then torn down the instant rank assignment is done.

This is the seventh layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains`` -> R253 ``nesting_graph``) and the
**first ``run`` / ``cleanup`` pair** (R250 ``normalize`` and R251 ``acyclic``
both use ``run`` / ``undo``). The name ``cleanup`` is preserved verbatim from
grok: unlike ``undo`` (which reverses a transformation to restore prior state),
``cleanup`` *deletes* the scaffolding it erected -- the ``_root`` dummy plus
every ``nesting_edge`` edge -- leaving the graph as it was before ``run``,
minus only the rank values ``rank`` wrote in between. It is the **producer of
the compound forest** that R252 ``parent_dummy_chains`` consumes: ``run``'s
``border_top`` / ``border_bottom`` stamps on compound node labels and the
``set_parent`` calls that attach ``_bt`` / ``_bb`` to their compound owner are
exactly the compound-forest structure R252 walks to re-parent long-edge
dummies. Migrating it closes the upstream dependency R252 depended on.

Algorithm (mirrors grok)
------------------------
* :func:`run` mints the ``_root`` dummy (via the R248
  :func:`~minimax_code.dagre.layout.util.add_dummy_node` helper), measures the
  compound forest depth of every node (:func:`_tree_depths`), derives a
  ``node_sep = 2 * height + 1`` rank-multiplier (``height`` = deepest nesting
  level minus one), records ``_root`` on ``GraphConfig.nesting_root``, scales
  every existing edge's ``minlen`` by ``node_sep`` (so real nodes never share a
  rank with a border sentinel), computes a ``weight`` larger than the graph's
  total edge weight, then DFS-walks the forest (:func:`_dfs`) erecting border
  sentinels and stitching them with ``nesting_edge`` edges. Finally it stores
  ``node_sep`` on ``GraphConfig.node_rank_factor`` so a later stage can purge
  the empty border ranks the multiplier created.
* :func:`_tree_depths` numbers every node with its compound-forest level (a
  root child is depth 1, its children depth 2, ...) via a nested DFS hoisted to
  module scope (:func:`_tree_depths_dfs`).
* :func:`_dfs` is the core border-creation walk. For a leaf node (no compound
  children) it links ``_root -> node`` with a zero-weight edge (unless the node
  IS ``_root``). For a compound node it mints ``_bt`` / ``_bb`` sentinels (via
  the R248 :func:`~minimax_code.dagre.layout.util.add_border_node` helper),
  parents them onto itself, records them on its ``border_top`` / ``border_bottom``
  label fields, then for every child stitches ``top -> child_top`` and
  ``child_bottom -> bottom`` nesting edges -- where ``child_top`` /
  ``child_bottom`` resolve to the child's own sentinels if it is itself
  compound (else the child id, marking a leaf). A leaf child gets a stretched
  ``minlen = height - depth(parent) + 1`` so it cannot land on its parent's
  border rank; a compound child gets a halved ``weight`` (its own nesting
  edges already keep it compact). A top-level compound node (``parent`` is
  ``None``) additionally gets a ``_root -> top`` edge so the scaffolding stays
  connected.
* :func:`_sum_weights` totals every edge's ``weight`` (skipping ``None``).
* :func:`cleanup` deletes the scaffolding: removes the ``_root`` node (which
  graphlib's :meth:`~minimax_code.data_structures.graphlib.Graph.remove_node`
  extends to all its incident edges -- the leaf links and ``_root -> top``
  connectors), clears ``nesting_root``, then walks every remaining edge and
  drops those marked ``nesting_edge`` (the ``top -> child_top`` /
  ``child_bottom -> bottom`` sentinels whose endpoints are NOT ``_root``).

Borrow-checker clone artefacts
------------------------------
This is the **second zero-semantic-clone leaf** after R252
``parent_dummy_chains`` (the fifth reuse of the R250 / R251 borrow-checker
classification framework). Every one of grok's 21 ``clone()`` calls here is a
pure ownership / immutable-reference artefact, and all are stripped:

* the ``f32`` / ``usize`` clones (``node_sep.clone()``, ``weight.clone()``,
  ``minlen.clone()``, ``this_weight.clone()``, ``height.clone()``) collapse to
  direct use -- Python's ``float`` / ``int`` are immutable value types with no
  borrow lifetime;
* the ``str`` clones (``root.clone()``, ``node_id.clone()``, ``child_id
  .clone()``, ``top.clone()`` / ``bottom.clone()`` for label storage and
  ``set_parent`` args, ``child_node.border_top.clone()`` / ``border_bottom
  .clone()`` reads) collapse to direct reference sharing -- ``str`` is
  immutable and the loop never mutates the shared value;
* the ``Option<String>`` clones (``border_top.clone().unwrap()`` /
  ``border_bottom.unwrap()``, ``graph_label.nesting_root.clone().unwrap()``)
  collapse to the live value behind an ``is not None`` guard;
* the ``depths.get(node_id).cloned().unwrap_or(0)`` reads collapse to
  ``depths.get(node_id) or 0`` (depths values are >= 1, so the falsy-zero trap
  never fires -- the R252 unwrap-or-strip pattern).

Unlike R250 ``normalize`` / R251 ``acyclic`` (each of which keeps two
*semantic* deep copies around an invalidating ``remove_edge`` /
``remove_node``), this stage performs **no structural removal that invalidates
a still-held reference**: ``cleanup``'s ``remove_node(nesting_root)`` reads
``nesting_root`` once and never touches the label again, and its
``remove_edge_with_obj`` reads ``nesting_edge`` then drops the label before
removal. The in-place ``edge_label.minlen = ...`` mutation in :func:`run` is a
direct attribute assignment (no clone needed). No ``copy.deepcopy`` survives.
"""

from __future__ import annotations

from minimax_code.dagre import GraphEdge, GraphNode
from minimax_code.dagre.layout import util
from minimax_code.data_structures.graphlib import GRAPH_NODE, Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["cleanup", "run"]


def run(g: Graph) -> None:
    """Wrap ``g`` in the nesting scaffolding (mirrors grok ``nesting_graph::run``).

    Mints the synthetic ``_root`` node, measures compound-forest depths, scales
    every edge's ``minlen`` by ``node_sep`` so real nodes never share a rank
    with a border sentinel, then DFS-walks the forest erecting ``_bt`` /
    ``_bb`` border sentinels and the ``nesting_edge`` edges that chain them.
    Records ``_root`` on ``GraphConfig.nesting_root`` and ``node_sep`` on
    ``GraphConfig.node_rank_factor`` for :func:`cleanup` and the later
    empty-rank removal. Mutates ``g`` in place (no return).
    """
    root = util.add_dummy_node(g, "root", GraphNode(), "_root")
    depths = _tree_depths(g)
    height = 0
    for depth in depths.values():
        if depth > height:
            height = depth
    if height > 0:
        height -= 1

    node_sep = float(2 * height + 1)
    # grok: ``graph.graph_mut().nesting_root = Some(root.clone())`` -- the clone
    # is a borrow artefact (immutable str); the live value is stored directly.
    g.graph_mut().nesting_root = root

    # Multiply minlen by nodeSep to align nodes on non-border ranks.
    for edge_obj in g.edges():
        # grok: ``edge_mut_with_obj`` returns Option<&mut GraphEdge>; a None
        # means the edge carries no label and is skipped.
        edge_label = g.edge_mut_with_obj(edge_obj)
        if edge_label is None:
            continue
        # grok: ``edge_label.minlen.unwrap_or(1.0)`` -- default to 1.0 when unset.
        base = edge_label.minlen if edge_label.minlen is not None else 1.0
        edge_label.minlen = base * node_sep

    # Calculate a weight that is sufficient to keep subgraphs vertically compact.
    weight = _sum_weights(g) + 1.0

    # Create border nodes and link them up.
    for child_id in g.children(GRAPH_NODE):
        _dfs(g, root, node_sep, weight, height, depths, child_id)

    # Save the multiplier for node layers for later removal of empty border
    # layers.
    g.graph_mut().node_rank_factor = node_sep


def _tree_depths(g: Graph) -> OrderedHashMap:
    """Number every node with its compound-forest level (mirrors grok ``tree_depths``).

    A direct child of the synthetic ``GRAPH_NODE`` root is depth 1, its
    compound children depth 2, and so on. Leaf nodes share their parent's
    numbering pass (their depth is the value passed on entry). Returns an
    :class:`OrderedHashMap` mapping node id -> depth.
    """
    depths: OrderedHashMap = OrderedHashMap()
    for node_id in g.children(GRAPH_NODE):
        _tree_depths_dfs(node_id, 1, depths, g)
    return depths


def _tree_depths_dfs(
    node_id: str, depth: int, depths: OrderedHashMap, g: Graph
) -> None:
    """Depth-first compound-forest level numbering (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``tree_depths`` as a Rust nested ``fn``; Python
    hoists it to module scope (paralleling the R249 / R251 / R252 nested-DFS
    decision) under a distinct name (``_tree_depths_dfs``) to avoid clashing
    with the module-level :func:`_dfs` below. The shared ``depths`` map is
    mutated in place across the recursion (Python passes the boxed reference,
    matching grok's ``&mut`` parameter).
    """
    for child_id in g.children(node_id):
        _tree_depths_dfs(child_id, depth + 1, depths, g)
    # grok: ``depths.insert(node_id.clone(), depth)`` -- the clone is a borrow
    # artefact (immutable str key); the live id is inserted directly.
    depths.insert(node_id, depth)


def _dfs(
    g: Graph,
    root: str,
    node_sep: float,
    weight: float,
    height: int,
    depths: OrderedHashMap,
    node_id: str,
) -> None:
    """Erect border sentinels for ``node_id`` and stitch the nesting edges.

    Mirrors grok's module-level ``dfs`` (private). For a leaf (no compound
    children) links ``_root -> node_id`` with a zero-weight edge (unless
    ``node_id`` is ``_root`` itself). For a compound node mints ``_bt`` /
    ``_bb`` border sentinels, parents them onto itself, records them on its
    label, recurses into every child, and stitches ``top -> child_top`` /
    ``child_bottom -> bottom`` ``nesting_edge`` edges. A top-level compound
    node additionally gets a ``_root -> top`` connector so the scaffolding
    stays connected.
    """
    children = g.children(node_id)
    if not children:  # grok ``children.len() == 0``
        if node_id != root:
            graph_edge = GraphEdge()
            # grok: ``graph_edge.minlen = Some(node_sep.clone())`` -- f32 Copy.
            graph_edge.minlen = node_sep
            graph_edge.weight = 0.0
            g.set_edge(root, node_id, graph_edge, None)
        return

    top = util.add_border_node(g, "_bt", None, None)
    bottom = util.add_border_node(g, "_bb", None, None)

    # grok: ``set_parent(&top, Some(node_id.clone()))`` -- the clone is a borrow
    # artefact (immutable str); set_parent takes the id directly.
    g.set_parent(top, node_id)
    g.set_parent(bottom, node_id)

    label = g.node_mut(node_id)
    if label is not None:  # grok ``if let Some(label) = _label``
        # grok: ``label.border_top = Some(top.clone())`` -- immutable str.
        label.border_top = top
        label.border_bottom = bottom

    for child_id in children:
        _dfs(g, root, node_sep, weight, height, depths, child_id)

        child_node = g.node(child_id)
        if child_node is None:  # grok ``if _child_node.is_none() { continue }``
            continue

        # grok: ``child_node.border_top.clone()`` / ``.border_bottom.clone()``
        # -- immutable Option<str> reads; the live value is reused directly.
        border_top = child_node.border_top
        border_bottom = child_node.border_bottom

        child_top = child_id  # grok ``let mut child_top = child_id.clone()``
        child_bottom = child_id
        this_weight = 2.0 * weight  # grok ``2.0 * weight.clone()`` (f32 Copy)
        minlen = 1

        if border_top is not None:  # grok ``if border_top.is_some()``
            child_top = border_top  # grok ``border_top.clone().unwrap()``
        if border_bottom is not None:
            child_bottom = border_bottom  # grok ``border_bottom.unwrap()``
        if border_top is not None:
            this_weight = weight  # grok ``weight.clone()`` (f32 Copy)
        if child_top == child_bottom:
            # Leaf child (top == bottom == child_id): stretch the nesting edge
            # so the leaf cannot share a rank with its parent's border sentinel.
            # grok: ``depths.get(node_id).cloned().unwrap_or(0)`` -- depths
            # values are >= 1, so ``or 0`` never trips the falsy-zero trap.
            minlen = height - (depths.get(node_id) or 0) + 1

        # top -> child_top nesting edge.
        ct_graph_edge = GraphEdge()
        ct_graph_edge.minlen = float(minlen)  # grok ``minlen.clone() as f32``
        ct_graph_edge.weight = this_weight  # grok ``this_weight.clone()``
        ct_graph_edge.nesting_edge = True
        g.set_edge(top, child_top, ct_graph_edge, None)

        # child_bottom -> bottom nesting edge.
        cb_graph_edge = GraphEdge()
        cb_graph_edge.minlen = float(minlen)
        cb_graph_edge.weight = this_weight
        cb_graph_edge.nesting_edge = True
        g.set_edge(child_bottom, bottom, cb_graph_edge, None)

    if g.parent(node_id) is None:  # grok ``graph.parent(node_id).is_none()``
        # Top-level compound node: connect _root -> top so the scaffolding stays
        # a single connected component for the rank stage.
        graph_edge = GraphEdge()
        # grok: ``(depths.get(node_id).cloned().unwrap_or(0) + height) as f32``.
        graph_edge.minlen = float((depths.get(node_id) or 0) + height)
        graph_edge.weight = 0.0
        graph_edge.nesting_edge = True
        g.set_edge(root, top, graph_edge, None)


def _sum_weights(g: Graph) -> float:
    """Total every edge's ``weight`` (mirrors grok ``sum_weights``, private).

    Edges whose ``weight`` is ``None`` contribute nothing (grok's nested
    ``if let Some(weight)`` guard).
    """
    total_weights = 0.0
    for edge in g.edges():
        edge_label = g.edge_with_obj(edge)
        if edge_label is not None:
            if edge_label.weight is not None:
                total_weights += edge_label.weight
    return total_weights


def cleanup(g: Graph) -> None:
    """Tear down the nesting scaffolding (mirrors grok ``nesting_graph::cleanup``).

    Removes the ``_root`` node (graphlib's ``remove_node`` cascades to every
    incident edge -- the leaf links and ``_root -> top`` connectors), clears
    ``GraphConfig.nesting_root``, then walks every remaining edge and drops
    those marked ``nesting_edge`` (the ``top -> child_top`` /
    ``child_bottom -> bottom`` sentinels whose endpoints survived the
    ``_root`` removal). Mutates ``g`` in place (no return).
    """
    graph_label = g.graph()
    assert graph_label is not None  # grok ``graph.graph()`` (always present here)
    if graph_label.nesting_root is not None:
        # grok: ``graph_label.nesting_root.clone().unwrap()`` -- the clone
        # releases the &GraphConfig borrow so remove_node can take &mut graph;
        # Python reads the str once and never touches the label after removal.
        g.remove_node(graph_label.nesting_root)
    g.graph_mut().nesting_root = None

    # Remove every nesting edge. g.edges() returns a snapshot list, so
    # remove_edge_with_obj never disturbs the iteration (mirrors grok's
    # ``let edges = graph.edges()`` pre-collection before the mutation loop).
    for edge in g.edges():
        edge_label = g.edge_with_obj(edge)
        if edge_label is not None:
            # grok: ``edge_label.nesting_edge.clone().unwrap_or(false)`` -- the
            # clone is a Copy artefact; unwrap_or(false) is a plain truthy test
            # (None / False both skip), mirroring the R251 ``if edge.reversed``
            # simplification.
            if edge_label.nesting_edge:
                g.remove_edge_with_obj(edge)
