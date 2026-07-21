"""dagre type foundation (R246, vendored ``third_party/dagre_rust``).

Mirrors the crate-root type layer of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the four value objects
that every ``dagre_rust::layout::*`` module is built on -- :class:`GraphNode`
(the per-node geometry/style/layout-state record), :class:`GraphEdgePoint`
(an ``x, y`` waypoint), :class:`GraphEdge` (per-edge geometry + label state)
and :class:`GraphConfig` (the global layout configuration) -- plus the
:class:`BorderTypeName` two-variant enum.

This is the **first leaf of the ``dagre`` migration chain** and opens the
``dagre`` Python package as an independent top-level package (mirroring grok,
where ``dagre_rust`` is a standalone crate sibling to ``graphlib_rust`` and
``ordered_hashmap`` -- not nested under them). It sits directly on the now-
complete graphlib stack (R241 ordered_hashmap -> R242 graphlib vocabulary ->
R243 Graph nodes -> R244 Graph edges -> R245 algo traversal): every type here
parameterises ``Graph<GL, N, E>`` as ``Graph<GraphConfig, GraphNode, GraphEdge>``
and the layout algorithms that consume them migrate in later leaves
(``layout/coordinate_system``, ``layout/order``, ``layout/rank``,
``layout/position``, ...).

Cyclic-module-dependency break
-----------------------------
grok declares ``BorderTypeName`` in ``layout/add_border_segments.rs`` and
imports it *into* ``lib.rs`` (``use crate::layout::add_border_segments::
BorderTypeName``) because :struct:`GraphNode`'s ``border_type`` field is typed
``Option<BorderTypeName>``. That is a *module-level cycle* (``lib.rs`` ->
``layout::add_border_segments`` -> ``lib.rs`` via the ``GraphNode`` type) which
Rust resolves by whole-crate compilation; Python has no such resolution, so
:class:`BorderTypeName` is migrated *early*, into this same type-foundation
module, breaking the cycle at the source. When ``layout/add_border_segments``
(the algorithm body) migrates in a later leaf it will ``from minimax_code.dagre.lib
import BorderTypeName`` rather than re-define it. ``BorderTypeName`` stays out
of the package barrel ``__all__`` (grok does not re-export it at the crate
root) -- it is reachable as ``minimax_code.dagre.lib.BorderTypeName``.

Mutability
----------
All four structs carry ``#[derive(Debug, Clone)]`` (``Default`` too for
``GraphNode`` / ``GraphEdgePoint``; a manual ``Default`` impl for
``GraphEdge`` / ``GraphConfig``); none derive ``Copy``. The layout pipeline
mutates them in place (``node_mut`` writes ``x`` / ``y`` / ``rank`` / ``order``
throughout), so they are migrated as **mutable** :func:`dataclasses.dataclass`
(no ``frozen``), with ``slots=True`` for memory compactness and typo-proofing.
``Debug`` maps to the auto-generated ``__repr__``; ``Clone`` is implicit in
Python (no ``Copy``); equality is order-insensitive field-wise (grok derives
no ``PartialEq`` on these structs, so equality is a Python convenience, not a
ported contract).

Python-keyword field rename
---------------------------
grok's ``GraphNode.class: Option<String>`` cannot become a Python attribute
named ``class`` (reserved keyword), so it is renamed ``class_`` (trailing
underscore, PEP 8 convention) -- the only field-name divergence from grok;
every other field keeps its exact name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from minimax_code.data_structures.graphlib import Edge
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = [
    "BorderTypeName",
    "GraphConfig",
    "GraphEdge",
    "GraphEdgePoint",
    "GraphNode",
]


class BorderTypeName(Enum):
    """Which side of a compound-graph border a sentinel node represents.

    Mirrors grok ``layout::add_border_segments::BorderTypeName``
    (``#[derive(Debug, Clone, PartialEq)] pub enum BorderTypeName {
    BorderLeft, BorderRight }``). grok places this in the ``add_border_segments``
    layout module but it is migrated here alongside :class:`GraphNode`
    (whose ``border_type`` field is typed ``Option<BorderTypeName>``) to break
    the lib/layout module cycle -- see the module docstring.

    The string member values (``"BorderLeft"`` / ``"BorderRight"``) are a
    Python convenience for readable ``repr`` and future serialisation; grok's
    fieldless enum has no associated data and is matched purely by identity,
    which :class:`enum.Enum` membership (``is`` / ``==``) preserves exactly.
    """

    BorderLeft = "BorderLeft"
    BorderRight = "BorderRight"


@dataclass(slots=True)
class GraphEdgePoint:
    """A single ``x, y`` point on an edge path (mirrors grok ``GraphEdgePoint``).

    ``#[derive(Debug, Clone, Default)]`` -> mutable dataclass with both fields
    defaulting to ``0.0`` (grok's ``f32::default()``). ``x`` / ``y`` are
    ``f32`` upstream (CSS-pixel geometry); Python has no 32-bit float, so
    ``float`` carries the value -- downstream rendering rounds identically.
    """

    x: float = 0.0
    y: float = 0.0


@dataclass(slots=True)
class GraphEdge:
    """Per-edge geometry + label + ranking state (mirrors grok ``GraphEdge``).

    ``#[derive(Debug, Clone)]`` with a **manual** ``Default`` impl (lines
    112-131): the layout pipeline relies on every new edge starting with
    ``minlen=1.0`` / ``weight=1.0`` / ``labelpos="r"`` and the geometry fields
    zeroed, so the defaults below mirror grok's manual impl verbatim rather
    than the all-``None`` default a derived ``Default`` would give. ``points``
    is ``None`` until the position phase fills the polyline; ``cutvalue`` /
    ``label_rank`` are populated by the network-simplex ranker.
    """

    forward_name: str | None = None
    reversed: bool | None = None
    minlen: float | None = 1.0
    weight: float | None = 1.0
    width: float | None = 0.0
    height: float | None = 0.0
    label_rank: int | None = None
    labeloffset: float | None = 0.0
    labelpos: str | None = "r"
    nesting_edge: bool | None = None
    cutvalue: float | None = None
    points: list[GraphEdgePoint] | None = None
    x: float = 0.0
    y: float = 0.0


@dataclass(slots=True)
class GraphConfig:
    """Global layout configuration (mirrors grok ``GraphConfig``).

    ``#[derive(Debug, Clone)]`` with a **manual** ``Default`` impl (lines
    90-110): the well-known dagre defaults -- ``nodesep=50`` / ``edgesep=20``
    / ``ranksep=50`` (CSS px), ``rankdir="tb"`` (top-to-bottom) -- are baked
    into the manual impl, while ``marginx`` / ``marginy`` / ``ranker`` /
    ``align`` / ``node_rank_factor`` / ``dummy_chains`` start ``None`` (the
    layout passes treat ``None`` as "unset, derive a default"). ``width`` /
    ``height`` are the output graph bounds, zeroed until the position phase
    measures them.
    """

    width: float = 0.0
    height: float = 0.0
    nodesep: float | None = 50.0
    edgesep: float | None = 20.0
    ranksep: float | None = 50.0
    marginx: float | None = None
    marginy: float | None = None
    rankdir: str | None = "tb"
    acyclicer: str | None = None
    ranker: str | None = None
    align: str | None = None
    nesting_root: str | None = None
    root: str | None = None
    node_rank_factor: float | None = None
    dummy_chains: list[str] | None = None


@dataclass(slots=True)
class GraphNode:
    """The per-node record carried by every dagre layout graph.

    Mirrors grok ``lib.rs::GraphNode`` (``#[derive(Debug, Clone, Default)]``,
    lines 9-42): geometry (``x`` / ``y`` / ``width`` / ``height``), SVG style
    (``class_`` / ``shape`` / ``rx`` / ``ry`` / ``padding`` family), layout
    state (``rank`` / ``min_rank`` / ``max_rank`` / ``order`` / ``low`` /
    ``lim``), compound-graph links (``parent`` / ``border_top`` /
    ``border_bottom`` / ``border_left`` / ``border_right`` / ``border_type``),
    edge bookkeeping (``e`` / ``edge_obj`` / ``edge_label`` / ``label`` /
    ``self_edges``) and the ``dummy`` marker for rank-ordering sentinels.

    ``#[derive(Default)]`` gives every field its zero/empty/``None`` value, so
    the dataclass defaults below mirror that exactly. ``class_`` is the
    Python-keyword rename of grok's ``class`` field (see module docstring).
    ``self_edges`` defaults to a fresh ``list`` per instance (grok's
    ``Vec<(Edge, GraphEdge)>``); the two ``OrderedHashMap`` border fields are
    ``None`` until ``add_border_segments`` seeds them.
    """

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    class_: str | None = None
    label: GraphEdge | None = None
    padding: float | None = None
    padding_x: float | None = None
    padding_y: float | None = None
    rx: float | None = None
    ry: float | None = None
    shape: str | None = None
    dummy: str | None = None
    rank: int | None = None
    min_rank: int | None = None
    max_rank: int | None = None
    order: int | None = None
    border_top: str | None = None
    border_bottom: str | None = None
    border_left: OrderedHashMap[int, str] | None = None
    border_right: OrderedHashMap[int, str] | None = None
    border_left_: str | None = None
    border_right_: str | None = None
    low: int | None = None
    lim: int | None = None
    parent: str | None = None
    e: Edge | None = None
    edge_label: GraphEdge | None = None
    edge_obj: Edge | None = None
    labelpos: str | None = None
    border_type: BorderTypeName | None = None
    self_edges: list[tuple[Edge, GraphEdge]] = field(default_factory=list)
