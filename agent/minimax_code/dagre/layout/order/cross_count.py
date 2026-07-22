"""Weighted crossing count for a layered ordering (R259, vendored ``third_party/dagre_rust``).

Mirrors ``layout/order/cross_count.rs`` of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the bilinear
cross-counting measure that scores a candidate ``layering`` matrix.
``order::mod``'s sweep loop calls :func:`cross_count` after every up/down pass
(``layout/order/mod.rs`` keeps the best-crossing-count ``layering`` across
iterations), so this is the **first direct downstream consumer** of the
R258 :func:`init_order` seed ordering -- ``init_order`` mints the initial
matrix and :func:`cross_count` measures how many edge crossings it has, so
the barycenter sweep has a baseline to beat.

The algorithm is Barth, Mutzel & Yannakakis, "Bilayer Cross Counting" (JGAA
2004): for two adjacent layers it collects every north -> south edge as a
``(south_position, weight)`` pair (the per-north runs sorted by south
position), then accumulates them through a complete binary tree to count,
for each edge, the total weight of already-inserted edges whose south
position is *strictly greater* -- i.e. the inversions, which are exactly the
edge crossings. The tree turns the naive O(k^2) pairwise comparison into
O(k log k), the crossing-minimization stage's core performance optimization.

This is the second leaf of the ``order`` sub-package (R258 ``init_order`` ->
R259 ``cross_count``) and the **eighth zero-semantic-clone leaf** after R252
/ R253 / R254 / R255 / R256 / R257 / R258 (the eleventh reuse of the R250 /
R251 borrow-checker classification framework). Every grok ``clone()`` here is
a pure ``String`` / ``usize`` borrow-or-Copy artefact, and all are stripped:

* ``south_pos.insert(v.clone(), i)`` -- ``&String`` borrow for the hash key;
  Python passes the immutable ``str`` by reference.
* ``south_pos.get(&e.w)`` / ``*pos`` -- ``&String`` borrow / ``&usize``
  deref; Python reads the int / looks up the str directly.

The stage performs **no structural removal that invalidates a still-held
reference** (it reads ``out_edges`` / ``edge_with_obj`` and the layering
matrix only), so no ``copy.deepcopy`` survives.

Pre-conditions (mirrors grok)
-----------------------------
* The input graph is simple (not a multigraph), directed, with simple edges.
* Edges carry an assigned ``weight`` (``GraphEdge.weight``; default 1.0).

Post-conditions: the graph and the ``layering`` matrix are left unchanged.
"""

from __future__ import annotations

import operator

from minimax_code.data_structures.graphlib import Graph

__all__ = ["cross_count", "two_layer_cross_count"]


def cross_count(g: Graph, layering: list[list[str]]) -> float:
    """Sum the weighted bilayer crossing counts across a layering (mirrors grok).

    Accumulates :func:`two_layer_cross_count` over every adjacent layer pair
    ``(layering[i - 1], layering[i])`` for ``i`` in ``1..len(layering)``. A
    single layer (or an empty layering) yields ``0.0`` (no adjacent pair).
    Does NOT mutate ``g`` or ``layering``.

    Pre-conditions: see module docstring (simple directed graph, weighted
    edges); the ``layering`` matrix is the output of R258 :func:`init_order`
    (or a sweep refinement of it).
    """
    cc = 0.0
    for i in range(1, len(layering)):
        cc += two_layer_cross_count(g, layering[i - 1], layering[i])
    return cc


def two_layer_cross_count(
    g: Graph,
    north_layer: list[str],
    south_layer: list[str],
) -> float:
    """Weighted crossing count between two adjacent layers (Barth bilinear).

    Collects every ``north -> south`` edge as a ``(south_position, weight)``
    pair (each north node's run sorted by south position, then concatenated
    in north order), then accumulates them through a complete binary tree:
    for each edge the tree yields the total weight of already-inserted edges
    whose south position is strictly greater (the inversions, = the
    crossings). O(k log k) where k is the north -> south edge count.

    Edges whose south endpoint is absent from ``south_layer`` are skipped (they
    do not cross this layer boundary). Edges whose label is missing or whose
    ``weight`` is ``None`` count as weight ``0.0`` (grok ``.unwrap_or(0.0)``).
    Does NOT mutate ``g``.
    """
    # grok: ``south_pos.insert(v.clone(), i)`` -- map each south node to its
    # position index. The ``.clone()`` is a ``&String`` borrow artefact;
    # Python passes the immutable ``str`` by reference.
    south_pos: dict[str, int] = {}
    for i, v in enumerate(south_layer):
        south_pos[v] = i

    # grok: the north -> south edge list as ``(south_pos, weight)`` pairs.
    # ``out_edges(v, None).unwrap_or_default()`` -> Python ``or []`` (None when
    # ``v`` is absent -- the R258 successors trap applied to out-edges).
    # ``filter_map`` drops edges whose south endpoint is not in ``south_pos``
    # (not a cross-layer edge); ``edge_with_obj(e).and_then(|e| e.weight)
    # .unwrap_or(0.0)`` is the R244 None-guard chain (edge missing OR weight
    # missing -> 0.0). Each north's run is sorted by south position before the
    # extend, so ``south_entries`` stays in north order with per-north runs
    # left-to-right -- the order the Barth tree then walks.
    south_entries: list[tuple[int, float]] = []
    for v in north_layer:
        out_edges = g.out_edges(v, None) or []
        out_pair: list[tuple[int, float]] = []
        for e in out_edges:
            pos = south_pos.get(e.w)
            if pos is None:
                continue
            edge = g.edge_with_obj(e)
            weight = edge.weight if (edge is not None and edge.weight is not None) else 0.0
            out_pair.append((pos, weight))
        out_pair.sort(key=operator.itemgetter(0))
        south_entries.extend(out_pair)

    # grok: the smallest power of two >= ``len(south_layer)``; the complete
    # binary tree spans ``[0, 2 * first_index - 1)`` with leaves at
    # ``[first_index - 1, 2 * first_index - 1)``. The ``<<=`` bit-shift doubles
    # ``first_index`` until it covers the south span.
    first_index = 1
    while first_index < len(south_layer):
        first_index <<= 1
    tree_size = 2 * first_index - 1
    first_index -= 1

    tree: list[float] = [0.0] * tree_size

    cc = 0.0
    for pos, weight in south_entries:
        index = pos + first_index
        tree[index] += weight

        weight_sum = 0.0
        # grok: climb from the leaf to the root. On every left-child visit
        # (odd ``index``) add the right sibling's accumulated weight -- those
        # are the edges already inserted whose south position is strictly
        # greater (the inversions = crossings).
        while index > 0:
            if index % 2 != 0:
                weight_sum += tree[index + 1]
            index = (index - 1) >> 1
            tree[index] += weight
        cc += weight * weight_sum

    return cc
