"""Compound-forest parent-chain repair for long-edge dummies (R252, vendored).

Mirrors ``layout/parent_dummy_chains.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). After the R250
``normalize`` stage split every long edge into a chain of per-rank ``edge``
dummies and recorded each chain's head on ``GraphConfig.dummy_chains``, this
stage walks those heads and re-parents each dummy onto the compound-graph node
whose rank range spans it -- so a dummy that crosses a subgraph boundary is
placed inside the subgraph rather than left dangling at the top level.

``run_layout`` calls this at ``layout/mod.rs`` line 630, immediately after
``normalize::run`` (line 629, which produces ``dummy_chains``) and before
``add_border_segments`` (line 631, which pads compound rank spans). It is a
single permanent mutation (no ``undo`` counterpart -- unlike R250 ``normalize``
/ R251 ``acyclic``, the parent reassignment is not bracketed and undone; it
survives into the final layout).

Algorithm (mirrors grok):
  * :func:`_postorder` numbers every node with a ``(low, lim)`` post-order
    interval over the compound forest (a child's interval is nested inside its
    parent's), giving an O(1) ancestor test.
  * :func:`_find_path` walks ``v``'s and ``w``'s ancestor chains to the lowest
    common ancestor (LCA) -- the deepest compound node whose interval contains
    both endpoints -- and returns the full ``v -> lca -> w`` path plus the LCA.
  * The main loop walks each chain head-to-tail (``successors``); for every
    dummy it advances a ``path_idx`` cursor up the ``v -> lca`` ascending arm
    while the path node's ``max_rank`` is below the dummy's rank, then down the
    ``lca -> w`` descending arm while the next path node's ``min_rank`` is at or
    below the dummy's rank, and finally :meth:`set_parent` re-parents the dummy
    onto the path node under the cursor.

This is the sixth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic`` -> R252 ``parent_dummy_chains``) and the **first single-``pub fn``
leaf with no ``run`` / ``undo`` pair** (grok declares only ``pub fn
parent_dummy_chains``; there is no cleanup counterpart because the parent
reassignment is permanent). It consumes the R250 ``dummy_chains`` ledger
directly and reuses the R248 :meth:`Graph.children` / :meth:`Graph.parent` /
:meth:`Graph.set_parent` / :meth:`Graph.successors` compound primitives.

Borrow-checker clone artefacts
------------------------------
Unlike R250 ``normalize`` / R251 ``acyclic`` (each of which keeps two
*semantic* deep copies around an invalidating mutation), this stage performs
**no structural removal** -- it never calls ``remove_edge`` / ``remove_node``
and never mutates a node/edge label in place, only reads fields and calls
:meth:`set_parent`. Every grok ``clone()`` here is therefore a pure ownership
artefact and is stripped:

* ``dummy_chains.clone()`` (release the ``&GraphConfig`` borrow), ``v_.clone()``
  / ``edge_obj.v.clone()`` / ``edge_obj.w.clone()`` (iteration + arg passing),
  ``node.edge_obj.clone()`` (read the ``Option<Edge>`` value), ``path.get(i)
  .cloned()`` / ``lca.clone()`` / ``path_v.clone()`` (immutable ``Option<String>``
  indexing), ``g.parent(p).cloned()`` (ancestor-chain walk), ``successors
  (...).first().cloned()`` (chain step), ``post_order_nums.get(v).cloned()``
  (immutable ``(i32, i32)`` tuple), and the postorder ``lim.clone()`` /
  ``v.clone()`` (``i32`` / ``str`` accumulator threading) -- all collapse to
  direct Python references because ``str`` / ``int`` / ``tuple`` / ``Edge`` are
  immutable and the loop never mutates the shared value.

The grok ``let Some(x) = opt else { break; }`` guards become plain
``if x is None: break`` checks, and ``unwrap_or(default)`` becomes an explicit
``... if ... is not None else default`` (the R250 unwrap-or-strip pattern,
avoiding the falsy-zero trap on integer ranks).
"""

from __future__ import annotations

from minimax_code.data_structures.graphlib import GRAPH_NODE, Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["parent_dummy_chains"]


def parent_dummy_chains(g: Graph) -> None:
    """Re-parent each long-edge dummy onto its spanning compound node.

    Mirrors grok ``parent_dummy_chains(g)``: walks every head recorded on
    ``GraphConfig.dummy_chains`` (the R250 ``normalize`` ledger), and for each
    dummy in the head-to-tail chain advances a path cursor from the edge's
    source (``v``) up to the lowest common ancestor and back down to the sink
    (``w``), re-parenting the dummy onto whichever compound path node spans its
    rank. Mutates ``g`` in place (no return value, mirroring grok).
    """
    post_order_nums = _postorder(g)
    graph_label = g.graph()
    assert graph_label is not None  # grok ``g.graph()`` (always present at this stage)
    # grok: ``g.graph().dummy_chains.clone().unwrap_or(vec![])`` -- the clone
    # releases the &GraphConfig borrow; Python shares the live list (the loop
    # below only reads it). ``or []`` covers the pre-normalize ``None`` case.
    dummy_chains = graph_label.dummy_chains or []

    for v in dummy_chains:
        node = g.node(v)
        assert node is not None  # grok ``g.node(&v).unwrap()``
        assert node.edge_obj is not None  # grok ``node.edge_obj.clone().unwrap()``
        # grok: ``.clone()`` is a borrow artefact; the Edge value is only read
        # (``.v`` / ``.w``), never mutated, so the live reference is reused.
        edge_obj = node.edge_obj
        path, lca = _find_path(g, post_order_nums, edge_obj.v, edge_obj.w)

        path_idx = 0
        path_v = _path_get(path, path_idx, lca)
        ascending = True

        while v != edge_obj.w:
            node = g.node(v)
            assert node is not None  # grok ``g.node(&v).unwrap()`` (re-read per step)
            node_rank = node.rank if node.rank is not None else 0

            if ascending:
                # Walk up the v -> lca arm while the path node's max_rank sits
                # below this dummy's rank (the dummy outgrows the subgraph).
                while True:
                    path_v = _path_get(path, path_idx, lca)
                    if path_v == lca:
                        ascending = False
                        break
                    if path_v is None:  # grok ``let Some(id) = path_v.as_ref() else break``
                        break
                    path_node = g.node(path_v)
                    assert path_node is not None  # grok ``g.node(id).unwrap()``
                    max_rank = path_node.max_rank if path_node.max_rank is not None else 0
                    if max_rank < node_rank:
                        path_idx += 1
                        continue
                    break

            if not ascending:
                # Walk down the lca -> w arm while the next path node's min_rank
                # still fits this dummy's rank (the dummy stays in-subgraph).
                while path_idx < len(path) - 1:  # grok ``path.len().saturating_sub(1)``
                    next_slot = path[path_idx + 1]  # grok ``path.get(i+1).cloned()``
                    if next_slot is None:  # grok ``let Some(id) = next.as_ref() else break``
                        break
                    next_node = g.node(next_slot)
                    assert next_node is not None  # grok ``g.node(id).unwrap()``
                    min_rank = next_node.min_rank if next_node.min_rank is not None else 0
                    if min_rank <= node_rank:
                        path_idx += 1
                    else:
                        break
                path_v = _path_get(path, path_idx, lca)

            # grok: ``set_parent(&v, path_v.clone())`` -- the clone is a borrow
            # artefact (Option<String>); set_parent takes ``str | None`` directly.
            g.set_parent(v, path_v)

            successors = g.successors(v) or []  # grok ``unwrap_or_default()``
            if not successors:  # grok ``let Some(next) = ...first().cloned() else break``
                break
            v = successors[0]


def _find_path(
    g: Graph,
    post_order_nums: OrderedHashMap,
    v: str,
    w: str,
) -> tuple[list[str | None], str | None]:
    """Find a path from ``v`` to ``w`` through their lowest common ancestor.

    Mirrors grok ``find_path`` (private). Walks each endpoint's ancestor chain
    up to the deepest compound node whose post-order interval ``[low, lim]``
    contains both endpoints (the LCA), then stitches the ``v -> lca`` and
    reversed ``lca -> w`` ancestor lists into a single path. Returns
    ``(path, lca)`` where ``path`` is the full ``v -> lca -> w`` node list
    (each slot possibly ``None`` for a detached endpoint) and ``lca`` is the
    common ancestor (``None`` if the endpoints share no compound ancestor).
    """
    v_path: list[str | None] = []
    w_path: list[str | None] = []

    v_pn = post_order_nums.get(v) or (0, 0)  # grok ``.cloned().unwrap_or((0, 0))``
    w_pn = post_order_nums.get(w) or (0, 0)
    low = min(v_pn[0], w_pn[0])
    lim = max(v_pn[1], w_pn[1])

    # Ascend v's ancestor chain until a node whose interval spans both endpoints.
    parent: str | None = v
    while True:
        parent = g.parent(parent)  # grok ``parent.and_then(|p| g.parent(p).cloned())``
        v_path.append(parent)
        if parent is None:  # grok ``let Some(id) = parent.as_ref() else break``
            break
        pn = post_order_nums.get(parent)
        if pn is None:  # grok ``let Some(pn) = post_order_nums.get(id) else break``
            break
        if pn[0] <= low and lim <= pn[1]:
            break
    lca = parent

    # Ascend w's ancestor chain until it meets the LCA (or runs out).
    parent = w
    while True:
        parent = g.parent(parent)
        if parent == lca:
            break
        w_path.append(parent)
        if parent is None:
            break

    w_path.reverse()
    v_path.extend(w_path)
    return v_path, lca


def _path_get(path: list[str | None], idx: int, fallback: str | None) -> str | None:
    """Return ``path[idx]`` or ``fallback`` when ``idx`` is out of range.

    Mirrors grok ``path.get(idx).cloned().unwrap_or(lca.clone())``: a missing
    slot (``idx`` past the end) resolves to the LCA fallback; an in-range slot
    yields its (possibly-``None``) entry. The ``.cloned()`` / ``lca.clone()``
    are borrow-checker artefacts (immutable ``Option<String>``), stripped here.
    """
    return path[idx] if idx < len(path) else fallback


def _postorder(g: Graph) -> OrderedHashMap:
    """Number every node with a post-order ``[low, lim]`` interval.

    Mirrors grok ``postorder`` (private). Walks the compound forest roots
    (``children(GRAPH_NODE)``) depth-first; each node's ``low`` is the counter
    on entry and ``lim`` the counter just before exit, so a parent's interval
    strictly contains every descendant's -- the O(1) ancestor test
    :func:`_find_path` relies on.
    """
    result: OrderedHashMap = OrderedHashMap()
    lim = 0
    for v in g.children(GRAPH_NODE):
        lim = _postorder_dfs(v, g, lim, result)
    return result


def _postorder_dfs(
    v: str, g: Graph, lim: int, result: OrderedHashMap
) -> int:
    """Depth-first post-order numbering (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``postorder`` as a Rust nested ``fn`` that takes
    ``&mut lim``; Python hoists it to module scope (paralleling the R249 /
    R251 nested-``dfs`` decision) and threads the counter as a return value --
    each recursion hands back the incremented ``lim`` so siblings see the
    accumulated offset, exactly matching grok's shared ``&mut`` reference.
    Returns the updated counter.
    """
    low = lim
    for child in g.children(v):
        lim = _postorder_dfs(child, g, lim, result)
    result.insert(v, (low, lim))
    return lim + 1
