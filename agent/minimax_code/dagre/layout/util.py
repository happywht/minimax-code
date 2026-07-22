"""dagre layout utility primitives (R248, vendored ``third_party/dagre_rust``).

Mirrors ``layout/util.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the grab-bag of pure
helpers that every ``layout/*`` stage is built on -- unique-id minting, dummy /
border node injection, multi-edge aggregation (``simplify``), compound-graph
flattening (``as_non_compound_graph``), rank normalisation / empty-rank
compaction, the layer-matrix builder, the rectangle-ray intersection used by
edge-label placement, and a small ``partition`` collection helper.

This is the **third leaf of the ``dagre`` migration chain** (R246 type
foundation -> R247 ``coordinate_system`` -> R248 ``util``) and the **second
leaf of the ``layout`` submodule**. It is the foundational utility layer:
``run_layout`` (``layout/mod.rs``) imports six symbols directly from here
(``as_non_compound_graph`` / ``intersect_rect`` / ``normalize_ranks`` /
``remove_empty_ranks`` / ``transfer_node_edge_labels`` / ``Rect``), and every
later ``layout/*`` leaf (``order`` / ``rank`` / ``position`` /
``add_border_segments`` / ``normalize`` / ``acyclic`` / ``nesting_graph`` /
``parent_dummy_chains``) reaches for these helpers, so migrating ``util`` first
unblocks the rest of the chain.

Depends only on the R246 type layer (:class:`~minimax_code.dagre.GraphConfig` /
:class:`~minimax_code.dagre.GraphNode` /
:class:`~minimax_code.dagre.GraphEdge` /
:class:`~minimax_code.dagre.GraphEdgePoint`) + the now-complete graphlib
:class:`~minimax_code.data_structures.graphlib.Graph` surface (``nodes`` /
``edges`` / ``node`` / ``node_mut`` / ``edge`` / ``edge_with_obj`` /
``edge_mut_with_obj`` / ``set_node`` / ``set_edge`` / ``set_edge_with_obj`` /
``has_node`` / ``children`` / ``graph`` -- R241-R245). No other ``layout/*``
module is needed, so this leaf is self-contained and testable in isolation.

Visibility mirrors grok: ``util`` is a public submodule (``pub mod util`` in
``layout/mod.rs``) and every symbol below is ``pub fn`` / ``pub struct``, but
none are re-exported at the crate root (``lib.rs`` has no
``pub use layout::*``). Mirroring R245's ``algo`` + R247's ``coordinate_system``
decision, the 15 symbols stay out of the ``dagre`` barrel ``__all__`` and are
reachable only as ``minimax_code.dagre.layout.util.<symbol>``.

AtomicUsize -> itertools.count
------------------------------
grok declares ``static UNIQUE_STARTER: AtomicUsize = AtomicUsize::new(0)`` with
a vendoring-patch comment: upstream used a ``static mut`` mutated in an
``unsafe`` block, which is a data race when the engine renders on multiple
threads (the parallel ``cargo test`` suite). The patch swaps it for an
``AtomicUsize`` (``fetch_add(1, Ordering::Relaxed) + 1``) -- behaviour-
preserving (monotonic ids from 1) and ``unsafe``-free. Python collapses this
further to a module-level :func:`itertools.count(1)`: ``next()`` on a CPython
iterator is atomic under the GIL, so it is the exact thread-safe analogue of a
``Relaxed`` atomic fetch-add, with the same monotonic-from-1 sequence. The
private ``_UNIQUE_STARTER`` name (not ``UNIQUE_STARTER``) reflects that grok's
``static`` is itself module-private (no ``pub``), and every access is mediated
by :func:`unique_id`.

Graph-independence (clone -> deepcopy)
--------------------------------------
Four helpers build a fresh :class:`Graph` from an existing one (``simplify``,
``as_non_compound_graph``, ``transfer_node_edge_labels``) or inject a caller-
supplied node (``add_dummy_node``). grok deep-clones every node / edge label
that crosses the source/destination boundary (``g.node(&v).cloned()``,
``set_node(.., Some(node_data))`` with ``node_data = data.clone()``) so that
mutating the new graph -- most importantly ``rank`` writes during
``rank::run`` on the ``as_non_compound_graph`` projection -- never leaks back
into the source. Python boxes every value behind a reference, so the same
independence is achieved with :func:`copy.deepcopy`; sharing the label object
directly would let a later ``node.rank = ...`` on one graph corrupt the other,
silently breaking the layout pipeline. ``copy.deepcopy`` threads through
:class:`GraphNode` / :class:`GraphEdge` (dataclass ``slots=True``),
:class:`~minimax_code.data_structures.graphlib.Edge` (frozen dataclass), and
:class:`~minimax_code.data_structures.ordered_hashmap.OrderedHashMap`
(``__slots__`` single-dict backend) without custom hooks.

The two ``unwrap_or(GraphNode::default())`` fallbacks in
``as_non_compound_graph`` / ``transfer_node_edge_labels`` (grok's defensive
default when a leaf node somehow carries a ``None`` label) become
``copy.deepcopy(label) if label is not None else GraphNode()``: a ``None``
label yields a fresh default node rather than propagating ``None`` into the
destination graph, matching grok's ``Some(GraphNode::default())`` post-
condition.

Borrow-checker artefacts (clone -> identity)
--------------------------------------------
Where grok clones purely to appease the borrow checker -- the ``simplified``
edge-aggregation local (``simple_label_.cloned().unwrap()`` /
``edge_label_.cloned().unwrap()``) that is read, mutated, then re-stored in the
same loop iteration -- Python keeps the live label object and mutates it in
place. The ``Option::cloned`` / ``unwrap_or_else`` defaults are preserved
verbatim (``weight=0.0`` / ``minlen=1.0``), matching grok's post-conditions,
and the final ``set_edge`` is idempotent when the label is already the live
stored object. ``partition`` likewise appends live references rather than
grok's ``val.clone()`` (a Rust ownership artefact); partitioning classifies, it
does not mutate, so sharing is safe and Pythonic.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import count
from typing import Generic, TypeVar

from minimax_code.dagre import GraphConfig, GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.data_structures.graphlib import Graph, GraphOption

__all__ = [
    "PartitionResponse",
    "Rect",
    "add_border_node",
    "add_dummy_node",
    "as_non_compound_graph",
    "build_layer_matrix",
    "intersect_rect",
    "max_rank",
    "normalize_ranks",
    "partition",
    "remove_empty_ranks",
    "simplify",
    "simplify_ref",
    "transfer_node_edge_labels",
    "unique_id",
]

V = TypeVar("V")

# Monotonic unique-id source (mirrors grok ``static UNIQUE_STARTER: AtomicUsize``).
# See module docstring: ``itertools.count(1)`` + ``next()`` is the GIL-atomic
# analogue of ``fetch_add(1, Ordering::Relaxed) + 1`` (ids 1, 2, 3, ...).
_UNIQUE_STARTER: Iterator[int] = count(1)


def unique_id() -> int:
    """Return a fresh monotonic id starting at 1 (mirrors grok ``unique_id``).

    grok: ``UNIQUE_STARTER.fetch_add(1, Ordering::Relaxed) + 1``. CPython's
    ``next()`` on an iterator is atomic under the GIL, so this is the exact
    thread-safe analogue of the ``Relaxed`` atomic and yields the same sequence
    (1, 2, 3, ...).
    """
    return next(_UNIQUE_STARTER)


def add_dummy_node(
    graph: Graph[GraphConfig, GraphNode, GraphEdge],
    node_type: str,
    data: GraphNode,
    name: str,
) -> str:
    """Inject a dummy node and return its id (mirrors grok ``add_dummy_node``).

    Mints ``f"{name}{unique_id()}"`` and, on a collision with an existing node
    id, regenerates until free. The caller's ``data`` is deep-cloned before the
    ``dummy`` tag is stamped so the original object is left untouched (grok
    ``data.clone()``; see the graph-independence note in the module docstring).
    """
    node_id = f"{name}{unique_id()}"
    while graph.has_node(node_id):
        node_id = f"{name}{unique_id()}"

    node_data = copy.deepcopy(data)
    node_data.dummy = node_type
    graph.set_node(node_id, node_data)
    return node_id


def simplify(
    g: Graph[GraphConfig, GraphNode, GraphEdge],
) -> Graph[GraphConfig, GraphNode, GraphEdge]:
    """Return a simple-edge projection of ``g`` (mirrors grok ``simplify``).

    Builds a fresh directed, non-multigraph, non-compound graph copying every
    node label and collapsing parallel multi-edges into a single edge whose
    ``minlen`` is the max of the constituents and whose ``weight`` is the sum.
    Node and edge labels are deep-cloned so the projection is fully independent
    of ``g`` (grok ``.cloned()``; see graph-independence note).

    The per-edge aggregation local reuses the simplified graph's already-stored
    label live (grok ``simple_label_.cloned().unwrap()`` clone is a borrow-
    checker artefact; see that note) and re-``set_edge``s it after accumulation.
    """
    simplified: Graph[GraphConfig, GraphNode, GraphEdge] = Graph(
        GraphOption(directed=True),
    )
    simplified.set_graph(copy.deepcopy(g.graph()))

    for node_id in g.nodes():
        simplified.set_node(node_id, copy.deepcopy(g.node(node_id)))

    for edge_obj in g.edges():
        edge_label_ = g.edge_with_obj(edge_obj)

        # The simplified graph may already hold an aggregated label for (v, w)
        # if a parallel edge was processed earlier in this loop.
        simple_label_ = simplified.edge(edge_obj.v, edge_obj.w, None)
        if simple_label_ is None:
            simple_label = GraphEdge()
            simple_label.weight = 0.0
            simple_label.minlen = 1.0
        else:
            # grok ``.cloned().unwrap()`` is a borrow-checker artefact; the live
            # label is mutated in place and re-stored (idempotent set_edge).
            simple_label = simple_label_

        if edge_label_ is None:
            edge_label = GraphEdge()
            edge_label.weight = 0.0
            edge_label.minlen = 1.0
        else:
            edge_label = edge_label_

        minlen = edge_label.minlen if edge_label.minlen is not None else 1.0
        simple_minlen = simple_label.minlen if simple_label.minlen is not None else 1.0
        simple_label.minlen = max(simple_minlen, minlen)

        weight = edge_label.weight if edge_label.weight is not None else 0.0
        simple_weight = simple_label.weight if simple_label.weight is not None else 0.0
        simple_label.weight = simple_weight + weight

        simplified.set_edge(edge_obj.v, edge_obj.w, simple_label, None)

    return simplified


def simplify_ref(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Normalise ``weight`` / ``minlen`` defaults on every edge in place.

    Mirrors grok ``simplify_ref``: for each edge, a ``None`` ``weight`` becomes
    ``0.0`` and a ``None`` ``minlen`` becomes ``1.0``. Unlike :func:`simplify`
    this mutates ``g`` directly (no new graph, no clone).
    """
    for edge_obj in g.edges():
        edge_label = g.edge_mut_with_obj(edge_obj)
        assert edge_label is not None  # grok: edge_mut_with_obj(&e).unwrap()
        if edge_label.weight is None:
            edge_label.weight = 0.0
        if edge_label.minlen is None:
            edge_label.minlen = 1.0


def as_non_compound_graph(
    g: Graph[GraphConfig, GraphNode, GraphEdge],
) -> Graph[GraphConfig, GraphNode, GraphEdge]:
    """Project ``g`` onto a non-compound graph of its leaf nodes (mirrors grok).

    Builds a fresh directed, multigraph, non-compound graph that copies only
    the nodes with no children (compound-graph leaves) plus every edge, each
    label deep-cloned for independence (grok ``.cloned()``). This is the
    projection ``rank::run`` operates on so its ``rank`` writes never touch the
    original compound graph; :func:`transfer_node_edge_labels` copies the ranks
    back afterwards.
    """
    simplified: Graph[GraphConfig, GraphNode, GraphEdge] = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
    )
    simplified.set_graph(copy.deepcopy(g.graph()))

    for v in g.nodes():
        if len(g.children(v)) == 0:
            label = g.node(v)
            simplified.set_node(
                v,
                copy.deepcopy(label) if label is not None else GraphNode(),
            )

    for e in g.edges():
        simplified.set_edge_with_obj(e, copy.deepcopy(g.edge_with_obj(e)))

    return simplified


def transfer_node_edge_labels(
    source: Graph[GraphConfig, GraphNode, GraphEdge],
    destination: Graph[GraphConfig, GraphNode, GraphEdge],
) -> None:
    """Copy leaf-node labels + every edge label from ``source`` to ``destination``.

    Mirrors grok ``transfer_node_edge_labels``: only nodes with no children are
    copied (compound leaves), and all edges are copied. Each label is deep-cloned
    so the two graphs remain independent (grok ``.cloned()``). Used by
    ``run_layout`` to ferry ``rank`` results off the :func:`as_non_compound_graph`
    projection back onto the original graph.
    """
    for v in source.nodes():
        if len(source.children(v)) == 0:
            label = source.node(v)
            destination.set_node(
                v,
                copy.deepcopy(label) if label is not None else GraphNode(),
            )

    for e in source.edges():
        destination.set_edge_with_obj(e, copy.deepcopy(source.edge_with_obj(e)))


@dataclass(slots=True)
class Rect:
    """An axis-aligned rectangle (mirrors grok ``layout::util::Rect``).

    Plain ``pub struct`` with four ``f32`` fields and no derived traits; used
    solely by :func:`intersect_rect` to compute where an edge ray exits a node
    box. Migrated as a mutable slotted dataclass (grok's fields are ``pub`` and
    the struct is passed by immutable reference); no defaults (grok has no
    ``Default`` impl, so all four fields are required at construction).
    """

    x: float
    y: float
    width: float
    height: float


def intersect_rect(rect: Rect, point: GraphEdgePoint) -> GraphEdgePoint:
    """Return where the ray from ``rect``'s centre to ``point`` exits the rect.

    Mirrors grok ``intersect_rect`` (rectangle-ray intersection,
    math.stackexchange #108113). For a query at the rectangle's centre the exit
    is the midpoint of the right edge (``x + width/2, y``); otherwise the branch
    compares ``|dy| * w`` against ``|dx| * h`` to pick the top/bottom vs
    left/right edge, then scales the off-axis coordinate along the ray.
    """
    x = rect.x
    y = rect.y
    dx = point.x - x
    dy = point.y - y
    w = rect.width / 2.0
    h = rect.height / 2.0

    if dx == 0.0 and dy == 0.0:
        sx = w
        sy = 0.0
    elif abs(dy) * w > abs(dx) * h:
        # Intersection is on the top or bottom edge.
        if dy < 0.0:
            sx = -h * dx / dy
            sy = -h
        else:
            sx = h * dx / dy
            sy = h
    else:
        # Intersection is on the left or right edge.
        if dx < 0.0:
            sx = -w
            sy = -w * dy / dx
        else:
            sx = w
            sy = w * dy / dx

    return GraphEdgePoint(x=x + sx, y=y + sy)


def build_layer_matrix(
    g: Graph[GraphConfig, GraphNode, GraphEdge],
) -> list[list[str]]:
    """Return nodes grouped into per-rank layers, ordered by ``node.order``.

    Mirrors grok ``build_layer_matrix``: builds ``max_rank(g) + 1`` layers, drops
    each node into the layer indexed by its ``rank`` keyed by its ``order``
    (defaulting to ``0``), then emits each layer sorted by ``order`` key. Nodes
    without a ``rank`` are skipped. Used by the ``order`` phase to read the
    current layering as a matrix it can reorder.
    """
    layering: list[dict[int, str]] = [dict() for _ in range(max_rank(g) + 1)]

    for v in g.nodes():
        node = g.node(v)
        assert node is not None  # grok: g.node(v).unwrap()
        rank = node.rank
        if rank is None:
            continue
        layer = layering[rank]
        order = node.order if node.order is not None else 0
        layer[order] = v

    return [[layer[key] for key in sorted(layer)] for layer in layering]


def normalize_ranks(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Shift every node's ``rank`` so the minimum becomes 0 (mirrors grok).

    Collects each node's ``rank`` (``None`` counted as ``0`` for the minimum),
    subtracts the minimum from every node that carries a ``rank``. Mirrors grok
    ``normalize_ranks`` including the ``unwrap_or(&GraphNode::default())`` node
    lookup fallback (an absent node contributes ``0`` to the minimum).
    """
    node_ids = g.nodes()
    node_ranks: list[int] = []
    for v in node_ids:
        node = g.node(v)
        rank = node.rank if (node is not None and node.rank is not None) else 0
        node_ranks.append(rank)

    min_rank = min(node_ranks, default=0)

    for node_id in node_ids:
        node = g.node_mut(node_id)
        if node is None or node.rank is None:
            continue
        node.rank = node.rank - min_rank


def remove_empty_ranks(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> None:
    """Compact empty ranks out of the layering (mirrors grok ``remove_empty_ranks``).

    Bucket every node by ``rank - min(ranks)``; then, if the graph's
    ``node_rank_factor`` is positive, walk the buckets and decrement a running
    ``delta`` for each empty bucket that does not fall on a ``node_rank_factor``
    multiple, applying the accumulated ``delta`` to every node in each
    subsequent non-empty bucket. This closes the gaps left by ranks that no
    node occupies without disturbing the factor-aligned skeleton ranks.
    """
    node_ids = g.nodes()
    if not node_ids:
        return

    node_ranks: list[int] = []
    for v in node_ids:
        node = g.node(v)
        rank = node.rank if (node is not None and node.rank is not None) else 0
        node_ranks.append(rank)

    offset = min(node_ranks)
    max_rank_value = max(node_ranks) - offset
    layers: list[list[str]] = [[] for _ in range(max(max_rank_value + 1, 0))]

    for v in node_ids:
        node = g.node(v)
        rank = (node.rank if (node is not None and node.rank is not None) else 0) - offset
        if rank >= 0:
            layers[rank].append(v)

    config = g.graph()
    raw_factor = config.node_rank_factor if config is not None else None
    node_rank_factor = int(raw_factor if raw_factor is not None else 0.0)
    if node_rank_factor <= 0:
        return

    delta = 0
    for i, vs in enumerate(layers):
        if not vs and i % node_rank_factor != 0:
            delta -= 1
        elif delta != 0:
            for v in vs:
                node = g.node_mut(v)
                assert node is not None  # grok: graph.node_mut(v).unwrap()
                node.rank = (node.rank if node.rank is not None else 0) + delta


def add_border_node(
    graph: Graph[GraphConfig, GraphNode, GraphEdge],
    prefix: str,
    rank: int | None = None,
    order: int | None = None,
) -> str:
    """Inject a ``border`` dummy node and return its id (mirrors grok).

    Builds a default :class:`GraphNode`, stamps ``rank`` / ``order`` only when
    supplied (grok's ``Option<&usize>`` -> Python ``int | None`` default args),
    then delegates to :func:`add_dummy_node` with ``node_type="border"``. The
    returned id is ``f"{prefix}{unique_id()}"``.
    """
    node = GraphNode()
    if rank is not None:
        node.rank = int(rank)
    if order is not None:
        node.order = int(order)
    return add_dummy_node(graph, "border", node, prefix)


def max_rank(g: Graph[GraphConfig, GraphNode, GraphEdge]) -> int:
    """Return the maximum ``rank`` across all nodes, or ``0`` if none is set.

    Mirrors grok ``max_rank``: ``filter_map`` over nodes keeping only those with
    a ``Some(rank)``, then ``max().unwrap_or(0)``.
    """
    ranks: list[int] = []
    for v in g.nodes():
        node = g.node(v)
        if node is not None and node.rank is not None:
            ranks.append(node.rank)
    return max(ranks, default=0)


@dataclass(slots=True)
class PartitionResponse(Generic[V]):
    """Result of :func:`partition` -- values split into ``lhs`` / ``rhs`` lists.

    Mirrors grok ``#[derive(Debug, Clone)] pub struct PartitionResponse<V>``.
    Generic over the element type ``V``; slotted for compactness. Carries fields
    so the R236 ``frozen + slots + fieldless`` trap does not apply.
    """

    lhs: list[V]
    rhs: list[V]


def partition(collection: list[V], fn_: Callable[[V], bool]) -> PartitionResponse[V]:
    """Split ``collection`` by predicate ``fn_`` into a :class:`PartitionResponse`.

    Mirrors grok ``partition``: ``true`` -> ``lhs``, ``false`` -> ``rhs``.
    grok ``val.clone()`` is a Rust ownership artefact (the closure receives
    ``&V`` and ``Vec::push`` needs an owned ``V``); Python appends live
    references, matching the classification-only intent.
    """
    result: PartitionResponse[V] = PartitionResponse(lhs=[], rhs=[])
    for val in collection:
        if fn_(val):
            result.lhs.append(val)
        else:
            result.rhs.append(val)
    return result
