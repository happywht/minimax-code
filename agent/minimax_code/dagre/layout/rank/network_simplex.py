"""Network-simplex ranker (R256, vendored ``third_party/dagre_rust``).

Mirrors ``layout/rank/network_simplex.rs`` of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). The
network simplex algorithm assigns ranks to each node in the input graph
and iteratively improves the ranking to reduce the length of edges. This
is dagre's default ranker (``rank::mod`` dispatches the unmatched /
``"network-simplex"`` arms here) and the heaviest single leaf of the
``rank`` sub-package.

This is the tenth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains`` -> R253 ``nesting_graph`` ->
R254 ``rank/util`` -> R255 ``rank/feasible_tree`` -> R256
``rank/network_simplex``) and the **third leaf of the ``rank``
sub-package**. ``run_layout`` calls ``rank`` at ``layout/mod.rs`` line
620; ``network_simplex`` consumes R254 ``util.longest_path`` /
``util.slack`` and R255 ``feasible_tree`` (its ``use
crate::layout::rank::feasible_tree`` / ``util::{longest_path, slack}``
dependencies close here) plus R248 ``util.simplify`` and R245
``data_structures.algo.postorder``. Only the ``rank::mod`` dispatcher
remains for a later leaf.

Algorithm (Gansner, "A Technique for Drawing Directed Graphs")
--------------------------------------------------------------
1. ``simplify`` the graph (strip self-loops / multi-edges into a simple
   weighted DAG) and seed ranks with ``longest_path`` (lowest position).
2. Build a feasible tight tree ``t`` via ``feasible_tree`` (R255).
3. ``_init_low_lim_values`` -- DFS-number the tree, assigning each node a
   ``[low, lim]`` interval and a ``parent`` (the LCA / descendant test
   hinges on these intervals).
4. ``_init_cut_values`` -- post-order walk (bar the root) computing each
   tree edge's cut value (the slack reduction obtained by cutting it).
5. While a tree edge has negative cut value (``_leave_edge``):
   a. ``_enter_edge`` -- pick the smallest-slack non-tree edge crossing
      the cut the leave-edge defines (the replacement that tightens most).
   b. ``_exchange_edges`` -- swap the leave edge out, the enter edge in,
      and re-run low-lim / cut-value / rank propagation
      (``_update_ranks`` writes ``rank`` from the tree topology).
6. Copy the optimized ranks off the simplified graph back onto ``g``.

Pre-conditions: the input graph is a DAG, connected, every node carries
an object value, every edge carries ``minlen`` and ``weight`` (the
``simplify`` + ``longest_path`` + ``feasible_tree`` pipeline establishes
these for a well-formed input).

Borrow-checker clone artefacts
------------------------------
This is the **fifth zero-semantic-clone leaf** after R252 / R253 / R254 /
R255 (the eighth reuse of the R250 / R251 classification framework).
Every grok ``clone()`` in ``network_simplex.rs`` falls into one of three
borrow / Copy artefact classes, all stripped:

* **A. Borrow release** (releases a ``g`` / ``t`` borrow before the next
  ``mut`` call): ``child_lab.parent.clone()`` in ``assign_cut_value`` /
  ``calc_cut_value``; ``graph_edge.cloned()`` in ``calc_cut_value``;
  ``e.w.clone()`` / ``e.v.clone()`` in ``calc_cut_value``;
  ``tree.nodes().first().cloned()`` in ``init_low_lim_values`` /
  ``update_ranks``; ``t.node(v).unwrap_or(...).parent.clone()`` and
  ``children.get(&parent).cloned()`` in ``update_ranks``;
  ``t.node(&v).cloned()`` in ``enter_edge``. Python reads the shared
  ``str`` / object reference directly.
* **B. Ownership** (gains ownership to store / return):
  ``visited.entry(v.clone())`` and ``parent.cloned().unwrap()`` in
  ``dfs_assign_low_lim``; ``find().cloned()`` in ``leave_edge``;
  ``edge.v.clone()`` / ``edge.w.clone()`` and ``candidates.min_by().
  cloned()`` in ``enter_edge``; ``e.v.clone()`` / ``e.w.clone()`` in
  ``exchange_edges``; ``vec![root.clone()]`` in ``update_ranks``.
* **C. Copy** (``usize`` / ``f32`` primitives): ``next_lim_.clone()`` in
  ``dfs_assign_low_lim``; ``v_label.lim.clone()`` in ``enter_edge``;
  ``low.clone()`` / ``lim.clone()`` in ``is_descendant``.

The stage performs **no structural removal on a graph whose identity is
shared with an iterator** (``exchange_edges`` calls ``remove_edge`` +
``set_edge`` on ``t`` but no ``g`` edge is removed while iterated), so
no ``copy.deepcopy`` survives. ``simplify`` (R248) already returns an
independent deep copy.

Notable grok quirks preserved
-----------------------------
* ``visited.entry(v.clone()).or_insert(true)`` is a 1:1 insert-if-absent;
  the migrated ``OrderedHashMap`` exposes a native ``entry().or_insert()``
  API, but we use the ``if v not in visited: visited.insert(v, True)``
  form already validated by R254 ``util._longest_path_dfs`` for
  consistency across the ``rank`` sub-package.
* ``graph_edge.cloned().unwrap_or(GraphEdge::default())`` -- a missing
  edge resolves to ``GraphEdge()`` whose manual ``Default`` (R246 / R253)
  seeds ``weight = 1.0`` (NOT ``None`` -> ``0.0``). So a None tree-edge
  label contributes **1.0** to the cut value, while a real label carrying
  ``weight = None`` contributes ``0.0``. The two-layer ``unwrap_or``
  chain is preserved exactly.
* ``minlen as i32`` truncates toward zero; Python ``int(minlen)``
  matches (positive ``minlen`` floors identically).
* ``vs.pop()`` in ``init_cut_values`` drops the post-order root; Rust's
  ``Vec::pop`` is a no-op on an empty vec, so the Python port guards with
  ``if vs:`` (a non-empty tree always yields a non-empty post-order, but
  the guard mirrors grok's tolerance).
* ``candidates.min_by(|e1, e2| slack(g, e1).cmp(&slack(g, e2)))`` --
  Rust's ``min_by`` keeps the **first** element on ties (the ``fold``
  accumulator only replaces on ``Ordering::Greater``); Python's ``min``
  / explicit ``<`` comparison is first-wins, matching dagre's JS lodash
  ``_.minBy`` lineage.
"""

from __future__ import annotations

from minimax_code.dagre import GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.feasible_tree import feasible_tree
from minimax_code.dagre.layout.rank.util import longest_path, slack
from minimax_code.dagre.layout.util import simplify
from minimax_code.data_structures import Graph
from minimax_code.data_structures.algo import postorder
from minimax_code.data_structures.graphlib import Edge
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["network_simplex"]


def network_simplex(g: Graph) -> None:
    """Rank ``g``'s nodes via the network simplex algorithm (mirrors grok).

    Mirrors grok ``rank::network_simplex::network_simplex``. Simplifies
    ``g`` into an independent simple weighted DAG, seeds it with
    :func:`~minimax_code.dagre.layout.rank.util.longest_path`, builds a
    feasible tight tree, then iteratively swaps negative-cut-value tree
    edges for smaller-slack non-tree edges until no cut value is
    negative. Finally copies the optimized ranks off the simplified graph
    back onto ``g``. Mutates ``g`` in place (writes ``GraphNode.rank``);
    returns ``None``.

    Pre-conditions: ``g`` is a connected DAG with every edge carrying
    ``minlen`` and ``weight``.
    """
    simplified = simplify(g)
    longest_path(simplified)
    t = feasible_tree(simplified)
    _init_low_lim_values(t, None)
    _init_cut_values(t, simplified)
    while (e := _leave_edge(t)) is not None:
        f = _enter_edge(t, simplified, e)
        if f is None:
            break
        _exchange_edges(t, simplified, e, f)
    for v in g.nodes():
        node = g.node_mut(v)
        if node is None:
            continue
        simple_node = simplified.node(v)
        if simple_node is None:
            continue
        node.rank = simple_node.rank


def _init_cut_values(t: Graph, g: Graph) -> None:
    """Initialize cut values for all tree edges (mirrors grok ``init_cut_values``).

    Walks the tree in post-order, drops the root (the last node), and
    assigns each remaining node's tree-edge-to-parent its cut value.
    """
    node_ids = t.nodes()
    vs = postorder(t, node_ids)
    # grok: ``vs.pop()`` drops the post-order root. Vec::pop is a no-op on
    # an empty vec; guard to mirror that tolerance (a non-empty tree always
    # yields a non-empty post-order).
    if vs:
        vs.pop()
    for node_id in vs:
        _assign_cut_value(t, g, node_id)


def _assign_cut_value(t: Graph, g: Graph, child: str) -> None:
    """Write the cut value onto the tree edge between ``child`` and its parent.

    Mirrors grok ``assign_cut_value``. Computes the cut value via
    :func:`_calc_cut_value`, then stores it on the ``child -> parent``
    tree edge's ``cutvalue`` field.
    """
    cutvalue = _calc_cut_value(t, g, child)
    child_lab = t.node_mut(child)
    if child_lab is not None:
        # grok: ``child_lab.parent.clone().unwrap_or("".to_string())`` --
        # the clone releases the borrow on t before the edge_mut call.
        parent = child_lab.parent if child_lab.parent is not None else ""
        edge_label = t.edge_mut(child, parent, None)
        if edge_label is not None:
            edge_label.cutvalue = cutvalue


def _calc_cut_value(t: Graph, g: Graph, child: str) -> float:
    """Return the cut value for the edge between ``child`` and its parent.

    Mirrors grok ``calc_cut_value``. Accumulates the slack-weighted
    contribution of every edge incident to ``child`` (the tree edge to
    the parent, plus every other in/out edge, with tree-edge cut values
    folded in recursively). A missing edge label resolves to
    ``GraphEdge()`` whose manual ``Default`` seeds ``weight = 1.0``.
    """
    cut_value = 0.0
    child_lab = t.node_mut(child)
    if child_lab is not None:
        parent = child_lab.parent if child_lab.parent is not None else ""
        # True if the child is on the tail end of the directed tree edge.
        child_is_tail = True
        graph_edge = g.edge(child, parent, None)
        if graph_edge is None:
            child_is_tail = False
            graph_edge = g.edge(parent, child, None)
        # grok: ``graph_edge.cloned().unwrap_or(GraphEdge::default())`` --
        # a None edge becomes GraphEdge() whose manual Default weight is
        # 1.0 (NOT 0.0); a real label carrying weight=None contributes 0.0.
        edge_or_default = graph_edge if graph_edge is not None else GraphEdge()
        cut_value = edge_or_default.weight if edge_or_default.weight is not None else 0.0
        for e in g.node_edges(child, None) or []:
            is_out_edge = e.v == child
            # The tree walk is undirected: recover the far endpoint.
            other = e.w if is_out_edge else e.v
            if other != parent:
                points_to_head = is_out_edge == child_is_tail
                other_edge = g.edge_with_obj(e)
                other_or_default = (
                    other_edge if other_edge is not None else GraphEdge()
                )
                other_weight = (
                    other_or_default.weight
                    if other_or_default.weight is not None
                    else 0.0
                )
                cut_value += other_weight if points_to_head else -other_weight
                if _is_tree_edge(t, child, other):
                    out_edge = t.edge(child, other, None)
                    out_or_default = (
                        out_edge if out_edge is not None else GraphEdge()
                    )
                    out_cut_value = (
                        out_or_default.cutvalue
                        if out_or_default.cutvalue is not None
                        else 0.0
                    )
                    cut_value += -out_cut_value if points_to_head else out_cut_value
    return cut_value


def _init_low_lim_values(tree: Graph, root_: str | None) -> None:
    """DFS-number the tree, assigning ``[low, lim]`` intervals and parents.

    Mirrors grok ``init_low_lim_values``. Roots the DFS at ``root_`` if
    given, otherwise at ``tree.nodes()[0]`` (the "" sentinel for an empty
    tree). Each visited node is stamped with ``low`` (the DFS number it
    entered with), ``lim`` (the largest DFS number in its subtree), and
    ``parent``.
    """
    nodes = tree.nodes()
    # grok: ``tree.nodes().first().cloned().unwrap_or("".to_string())``.
    root = nodes[0] if nodes else ""
    if root_ is not None:
        root = root_
    visited: OrderedHashMap = OrderedHashMap()
    _dfs_assign_low_lim(tree, visited, 1, root, None)


def _dfs_assign_low_lim(
    tree: Graph,
    visited: OrderedHashMap,
    next_lim: int,
    v: str,
    parent: str | None,
) -> int:
    """Depth-first low/lim assignment (private, mirrors grok nested DFS).

    grok nests ``dfs_assign_low_lim`` as a free ``fn``; Python keeps it at
    module scope (paralleling R249 / R251 / R253 / R254 / R255's
    nested-DFS-to-module-scope decision). Marks ``v`` visited, recurses
    into every unvisited neighbor, then stamps ``v`` with ``low`` (the
    ``next_lim`` it entered with), ``lim`` (the running ``next_lim`` after
    the subtree), and ``parent``. Returns the post-increment ``next_lim``.
    """
    low = next_lim
    # grok: ``visited.entry(v.clone()).or_insert(true)`` -- insert-if-absent.
    # OrderedHashMap exposes a native entry().or_insert() API; we use the
    # R254 ``insert`` form for sub-package consistency.
    if v not in visited:
        visited.insert(v, True)
    for w in tree.neighbors(v) or []:
        if w not in visited:
            next_lim = _dfs_assign_low_lim(tree, visited, next_lim, w, v)
    label = tree.node_mut(v)
    if label is not None:
        label.low = low
        label.lim = next_lim
        next_lim += 1
        if parent is not None:
            label.parent = parent
        else:
            # TODO should be able to remove this when we incrementally
            # update low lim (grok comment preserved).
            label.parent = None
    return next_lim


def _leave_edge(tree: Graph) -> Edge | None:
    """Return the first tree edge with a negative cut value, or ``None``.

    Mirrors grok ``leave_edge``. A negative cut value marks an edge whose
    removal (and replacement) would shorten the graph. A stripped label
    resolves to ``GraphEdge()`` (``cutvalue`` None -> 0.0, never negative).
    """
    for edge_obj in tree.edges():
        edge = tree.edge_with_obj(edge_obj)
        edge_or_default = edge if edge is not None else GraphEdge()
        cutvalue = edge_or_default.cutvalue if edge_or_default.cutvalue is not None else 0.0
        if cutvalue < 0.0:
            return edge_obj
    return None


def _enter_edge(t: Graph, g: Graph, edge: Edge) -> Edge | None:
    """Return the smallest-slack non-tree edge crossing the leave-edge cut.

    Mirrors grok ``enter_edge``. Orients ``edge`` so ``v`` is the tail,
    picks the tail-side label (the one with the larger ``lim`` becomes
    the head), then among all graph edges with exactly one endpoint in
    the tail subtree returns the one with the smallest slack (first-wins
    on ties, matching grok ``min_by`` / dagre JS ``_.minBy``).
    """
    v = edge.v
    w = edge.w
    # Assume v is the tail, w is the head; flip if the graph lacks v -> w.
    if not g.has_edge(v, w, None):
        v = edge.w
        w = edge.v
    v_node = t.node(v)
    v_label = v_node if v_node is not None else GraphNode()
    w_node = t.node(w)
    w_label = w_node if w_node is not None else GraphNode()
    tail_label = v_label
    flip = False
    v_lim = v_label.lim if v_label.lim is not None else 0
    w_lim = w_label.lim if w_label.lim is not None else 0
    # If the root is in the tail of the edge, flip the head/tail candidate
    # logic below.
    if v_lim > w_lim:
        tail_label = w_label
        flip = True
    best: Edge | None = None
    best_slack: int | None = None
    for edge_obj in g.edges():
        ev_node = t.node(edge_obj.v)
        ev_label = ev_node if ev_node is not None else GraphNode()
        ew_node = t.node(edge_obj.w)
        ew_label = ew_node if ew_node is not None else GraphNode()
        if flip == _is_descendant(ev_label, tail_label) and flip != _is_descendant(
            ew_label, tail_label
        ):
            s = slack(g, edge_obj)
            # First-wins on ties (grok min_by replaces only on Greater).
            if best_slack is None or s < best_slack:
                best = edge_obj
                best_slack = s
    return best


def _exchange_edges(t: Graph, g: Graph, e: Edge, f: Edge) -> None:
    """Swap tree edge ``e`` out for ``f`` and re-propagate (mirrors grok).

    Removes ``e`` from the tree, adds ``f``, then re-runs low-lim / cut-
    value initialization and rank propagation so the next pivot loop
    iteration sees a consistent tree.
    """
    v = e.v
    w = e.w
    t.remove_edge(v, w, None)
    t.set_edge(f.v, f.w, GraphEdge(), None)
    _init_low_lim_values(t, None)
    _init_cut_values(t, g)
    _update_ranks(t, g)


def _update_ranks(t: Graph, g: Graph) -> None:
    """Propagate tree topology onto ``g``'s ranks (mirrors grok ``update_ranks``).

    Roots the walk at ``t.nodes()[0]``, groups tree nodes by parent, then
    stack-walks downward assigning each node ``parent_rank +/- minlen``
    (sign flipped when the graph edge points parent -> child rather than
    child -> parent). Writes ``GraphNode.rank`` on ``g`` in place.
    """
    nodes = t.nodes()
    if not nodes:
        return
    root = nodes[0]
    children: dict[str, list[str]] = {}
    for v in t.nodes():
        node = t.node(v)
        parent = node.parent if node is not None else None
        if parent is None:
            continue
        children.setdefault(parent, []).append(v)
    stack: list[str] = [root]
    while stack:
        parent = stack.pop()
        if parent not in children:
            continue
        # grok: ``children.get(&parent).cloned()`` -- the clone releases the
        # borrow so ``stack.push(v)`` is free to mutate below; Python reads
        # the list directly (the loop never mutates ``children``).
        for v in children[parent]:
            edge = g.edge(v, parent, None)
            flipped = False
            if edge is None:
                edge = g.edge(parent, v, None)
                flipped = True
            edge_or_default = edge if edge is not None else GraphEdge()
            minlen = edge_or_default.minlen if edge_or_default.minlen is not None else 0.0
            parent_node = g.node(parent)
            parent_rank = (
                parent_node.rank
                if parent_node is not None and parent_node.rank is not None
                else 0
            )
            v_node = g.node_mut(v)
            if v_node is not None:
                # grok: ``minlen as i32`` truncates toward zero; int() matches.
                delta = int(minlen) if flipped else -int(minlen)
                v_node.rank = parent_rank + delta
            stack.append(v)


def _is_tree_edge(tree: Graph, u: str, v: str) -> bool:
    """Return ``True`` if ``u -> v`` is in the tree (mirrors grok)."""
    return tree.has_edge(u, v, None)


def _is_descendant(v_label: GraphNode, root_label: GraphNode) -> bool:
    """Return ``True`` if ``v_label`` is a descendant of ``root_label``.

    Mirrors grok ``is_descendant``. Uses the ``[low, lim]`` DFS intervals:
    ``v`` is a descendant of ``root`` iff ``root.low <= v.lim <= root.lim``.
    """
    low = root_label.low if root_label.low is not None else 0
    v_lim = v_label.lim if v_label.lim is not None else 0
    root_lim = root_label.lim if root_label.lim is not None else 0
    return low <= v_lim <= root_lim
