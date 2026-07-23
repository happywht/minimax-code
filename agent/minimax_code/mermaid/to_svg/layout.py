r"""Mermaid-to-svg dagre-backed layout -- fusion of grok
``mermaid-to-svg/src/layout.rs``.

Direction (1) leaf 6 (the layout engine). The dagre-backed flowchart / state
layout that positions parsed nodes + routes edges. This leaf is the orchestrator
that consumes the R271 ``ast`` (:class:`~minimax_code.mermaid.to_svg.ast.FlowchartGraph`),
the R270 ``config`` (:class:`~minimax_code.mermaid.to_svg.config.RenderConfig`),
the R273 ``text_wrap`` (node-box sizing), and the R246--R268 ``dagre`` port
(the rank/position solver).

YAGNI dead-code stripping (the engineering win of this migration)
-----------------------------------------------------------------

grok's ``layout.rs`` ships ~3375 lines, but a static reachability pass
(``#[allow(dead_code)]`` markers + zero-call ``grep``) shows ~1500 lines are
dead and are NOT ported:

* ``compute()`` (grok L1466) -- the backup layout orchestrator, explicitly
  marked ``#[allow(dead_code)]``. Its entire private call chain
  (``assign_ranks`` / ``group_by_rank`` / ``compute_positions`` /
  ``compute_positions_vertical`` / ``compute_positions_horizontal`` /
  ``solve_rank_offsets`` / ``normalize_positions`` / ``separate_subgraphs`` /
  ``compute_max_node_dimensions``) is unreachable: the four public entries all
  route through ``compute_with_dagre(bool)``. ``grep '\.compute\('`` returns
  zero call sites.
* ``compute_layout_no_subgraph_centering`` /
  ``compute_layout_no_subgraph_centering_with_config`` -- two
  ``#[allow(dead_code)]`` public entries that nobody imports.
* ``apply_flip`` (L1092), ``rotate_layout`` (L800),
  ``normalize_positions_and_edges`` (L2086) -- zero call sites (dead beyond the
  backup chain).
* ``shift_external_nodes`` (L2513) -- called only from inside ``compute()``.

The live surface (~1800 lines) ports across five sub-leaves:

* **R274a (this leaf)** -- the type layer: 18 layout constants, the 4 public
  ``pub struct`` (``LayoutNode`` / ``LayoutEdge`` / ``LayoutSubgraph`` /
  ``LayoutResult``), the 4 private internal structs (``SubgraphInfo`` /
  ``NodeInfo`` / ``EdgeInfo`` / ``ClusterAnalysis``), the
  ``FlowchartLayoutOptions`` value type (``Default`` +
  ``from_render_config``), 4 stdlib type aliases, and the
  ``graph_contains_state_shapes`` module helper. Zero method dependencies.
* **R274b** -- ``LayoutEngine`` constructor + node/edge collection + measurement.
* **R274c** -- ``compute_with_dagre`` orchestrator + dagre bridge + subgraph
  ops + state-diagram specials (this leaf re-introduces the ``DagreGraph`` type
  alias + the ``dagre`` dependency).
* **R274d** -- edge geometry + bounds.
* **R274e** -- ``compute_with_dagre`` body + the 2 live public entries
  (``compute_layout`` / ``compute_layout_with_config``) + ``__all__`` closure.

Type mapping (grok struct/enum -> Python)
-----------------------------------------

* ``#[derive(Debug, Clone)] pub struct`` -> ``@dataclass``.
* ``#[derive(Debug, Clone, Copy)] struct`` -> ``@dataclass(frozen=True)`` -- the
  ``Copy`` semantics surface as immutability; ``Default`` maps to field defaults
  so ``FlowchartLayoutOptions()`` mirrors ``FlowchartLayoutOptions::default()``.
* ``Option<String>`` -> ``str | None`` (UP007); ``String`` -> ``str``.
* ``Vec<(f64, f64)>`` -> ``list[tuple[float, float]]``;
  ``HashMap<String, LayoutNode>`` -> ``dict[str, LayoutNode]``;
  ``HashMap<String, HashSet<String>>`` -> ``dict[str, set[str]]``;
  ``HashMap<String, bool>`` -> ``dict[str, bool]``.
* ``Option<u32>`` (R270 flowchart knobs) -> ``int | None``; ``from_render_config``
  widens back to ``float`` via ``f64::from`` -> ``float()``.
* ``Option<(f64, f64)>`` -> ``tuple[float, float] | None``.
* grok ``struct Edge { from: String }`` -> ``from_: str`` (``from`` is a Python
  keyword; the trailing underscore mirrors
  :class:`~minimax_code.mermaid.to_svg.ast.Edge`).
* ``usize`` / ``i32`` -> ``int``.
* ``type X = HashMap<...>`` -> ``X: TypeAlias = dict[...]``.

Internal module
---------------

grok declares ``mod layout;`` (private) -- consumed by ``lib.rs``
(``layout::compute_layout`` for stateDiagram,
``layout::compute_layout_with_config`` for flowchart), never ``pub use``-d at
the crate root. This module mirrors that: it has its own ``__all__`` (the 4
``pub struct`` that survive the dead-code strip; the 2 ``pub fn`` entries join
in R274e) but is NOT re-exported through the ``to_svg`` barrel (the barrel
tracks grok's crate-root public surface, which omits ``layout``). Future
``svg_renderer.py`` consumes it via ``from .layout import ...``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

# R274c: dagre bridge -- the 5th type alias (``DagreGraph``) + the
# ``build_dagre_graph`` / ``extract_layout_from_dagre`` / snap-rank helpers pull
# in the now-complete dagre layout stack (R246-R268) + the graphlib ``Graph``
# core (R241-R245). Mirrors grok's ``use dagre::...`` + ``use graphlib::...``.
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.mod import layout as dagre_layout
from minimax_code.data_structures import Graph, GraphOption

from .ast import (
    Edge,
    EdgeStyle,
    FlowchartGraph,
    GraphDirection,
    Node,
    NodeShape,
    Statement,
    StyleStatement,
    Subgraph,
)
from .config import RenderConfig
from .text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_WRAP_WIDTH,
    measure_wrapped_lines_with_font_size,
    scale_char_width,
    wrap_text_lines,
)

# === private layout constants (grok ``const``, 18 f64) ======================
#
# grok declares these as module-private ``const`` (no ``pub``); they feed the
# LayoutEngine spacing/sizing math. Kept module-private here (not in __all__);
# tests reach them by direct import.

FLOWCHART_PADDING: float = 15.0
EDGE_LABEL_PADDING: float = 2.0
RANK_SEP: float = 50.0
NODE_SEP: float = 50.0
MARGIN: float = 8.0
SUBGRAPH_PADDING: float = 8.0
SUBGRAPH_TITLE_HEIGHT: float = 24.0
SUBGRAPH_GAP: float = 25.0
MIN_NODE_WIDTH: float = 0.0
MIN_NODE_HEIGHT: float = 0.0
# grok tags this ``#[allow(dead_code)]`` -- kept for parity (a future renderer
# leaf may read it).
EDGE_LABEL_GAP: float = 24.0
STATE_CHAR_WIDTH: float = 6.7
STATE_NODE_WIDTH_PADDING: float = 6.0
STATE_NODE_HEIGHT_PADDING: float = 16.0
STATE_NODE_MIN_HEIGHT: float = 40.0
STATE_DIAMOND_PADDING: float = 18.0
STATE_FORK_WIDTH: float = 70.0
STATE_FORK_HEIGHT: float = 7.0


# === public layout data types (grok ``pub struct``, 4) ======================


@dataclass
class LayoutNode:
    """A positioned node box (grok ``pub struct LayoutNode``).

    The SVG renderer fills this box with the node label + shape. ``x`` / ``y``
    are the box center (dagre convention); ``width`` / ``height`` the box size.
    """

    id: str
    x: float
    y: float
    width: float
    height: float
    shape: NodeShape
    label: str
    fill_color: str | None
    stroke_color: str | None


@dataclass
class LayoutEdge:
    """A routed edge (grok ``pub struct LayoutEdge``, ``#[allow(dead_code)]``).

    ``points`` is the polyline through the edge (entry -> ... -> exit);
    ``label_pos`` is the optional mid-label anchor. ``from`` -> ``from_``
    (keyword collision, mirrors :class:`~minimax_code.mermaid.to_svg.ast.Edge`).
    The ``#[allow(dead_code)]`` is a grok placeholder -- once the SVG renderer
    leaf lands, every field is read.
    """

    from_: str
    to: str
    label: str | None
    style: EdgeStyle
    points: list[tuple[float, float]]
    label_pos: tuple[float, float] | None


@dataclass
class LayoutSubgraph:
    """A positioned cluster frame (grok ``pub struct LayoutSubgraph``,
    ``#[allow(dead_code)]``).

    The SVG renderer strokes this as the ``subgraph`` rectangle + title bar.
    ``title`` is ``None`` when the source ``subgraph`` had no title.
    """

    id: str
    title: str | None
    x: float
    y: float
    width: float
    height: float


@dataclass
class LayoutResult:
    """The full layout output (grok ``pub struct LayoutResult``).

    ``nodes`` is keyed by node id; ``edges`` + ``subgraphs`` are ordered lists.
    ``width`` / ``height`` are the overall diagram bounds (the SVG viewport).
    """

    nodes: dict[str, LayoutNode]
    edges: list[LayoutEdge]
    subgraphs: list[LayoutSubgraph]
    width: float
    height: float


# === module-level helper ====================================================


def graph_contains_state_shapes(statements: list[Statement]) -> bool:
    """Whether ``statements`` (recursively) contain a state-diagram shape.

    Mirrors grok ``graph_contains_state_shapes``: a node carrying
    :attr:`NodeShape.StartState` / :attr:`NodeShape.EndState` /
    :attr:`NodeShape.ForkJoin` flags the diagram as a state diagram (which the
    layout engine routes through its state-diagram specials). Subgraph bodies
    are scanned recursively; edges + style directives are ignored.
    """
    for statement in statements:
        if isinstance(statement, Node):
            if statement.shape in (
                NodeShape.StartState,
                NodeShape.EndState,
                NodeShape.ForkJoin,
            ):
                return True
        elif isinstance(statement, Subgraph):
            if graph_contains_state_shapes(statement.statements):
                return True
    return False


# === private internal data types (grok private ``struct``, 4) ===============


@dataclass
class SubgraphInfo:
    """Subgraph bookkeeping (grok private ``struct SubgraphInfo``).

    ``parent_subgraph_id`` is ``None`` for a top-level subgraph, else the
    enclosing subgraph id (nested clusters).
    """

    id: str
    title: str | None
    parent_subgraph_id: str | None


@dataclass
class NodeInfo:
    """Per-node measurement + rank (grok private ``struct NodeInfo``,
    ``#[allow(dead_code)]``).

    ``rank`` is the dagre layer (vertical position in TB/BT, horizontal in
    LR/RL); ``order`` is the in-rank index (collection order).
    """

    id: str
    label: str
    shape: NodeShape
    width: float
    height: float
    rank: int
    order: int


@dataclass
class EdgeInfo:
    """Per-edge bookkeeping (grok private ``struct EdgeInfo``)."""

    from_: str
    to: str
    label: str | None
    style: EdgeStyle


@dataclass
class ClusterAnalysis:
    """Subgraph clustering analysis (grok private ``struct ClusterAnalysis``,
    ``#[allow(dead_code)]``).

    ``subgraph_nodes`` maps each subgraph id to its member node set;
    ``external_edges`` flags whether a subgraph has any edge crossing its
    boundary.
    """

    subgraph_nodes: dict[str, set[str]]
    external_edges: dict[str, bool]


# === type aliases (grok ``type``, 5 stdlib) =================================
#
# The 5th grok alias ``type DagreGraph = Graph<GraphConfig, GraphNode,
# GraphEdge>`` lands here (R274c) alongside the dagre-bridge methods that
# consume it: it is the layout-stage view of the dagre ``Graph`` (R241-R245
# graphlib core + R246 dagre type foundation). The 4 leaf-only aliases (edge
# index -> endpoint pair, node id -> center, edge index -> polyline points,
# edge index -> label center) feed the bridge's extract step.

EdgeMap: TypeAlias = dict[int, tuple[str, str]]
PositionMap: TypeAlias = dict[str, tuple[float, float]]
EdgePointMap: TypeAlias = dict[int, list[tuple[float, float]]]
EdgeLabelPosMap: TypeAlias = dict[int, tuple[float, float]]
DagreGraph: TypeAlias = Graph[GraphConfig, GraphNode, GraphEdge]


# === flowchart layout options ===============================================


def _or_default(value: int | None, default: float) -> float:
    """Map grok ``Option<u32>.map(f64::from).unwrap_or(default)`` -> float.

    Widens the R270 ``int | None`` flowchart knob to ``float`` (grok
    ``f64::from``), falling back to ``default`` when the knob is unset.
    """
    return float(value) if value is not None else default


@dataclass(frozen=True)
class FlowchartLayoutOptions:
    """Spacing/sizing knobs (grok ``struct FlowchartLayoutOptions``, Copy).

    ``Copy`` surfaces as ``frozen=True`` (immutability + value semantics);
    ``Default`` surfaces as field defaults so ``FlowchartLayoutOptions()``
    mirrors ``FlowchartLayoutOptions::default()``. ``from_render_config`` widens
    the R270 ``int | None`` flowchart knobs back to ``float`` (grok
    ``f64::from``) and falls back to the defaults for unset knobs.
    """

    node_spacing: float = NODE_SEP
    rank_spacing: float = RANK_SEP
    padding: float = FLOWCHART_PADDING
    wrapping_width: float = DEFAULT_WRAP_WIDTH
    font_size: float = DEFAULT_FONT_SIZE

    @classmethod
    def from_render_config(cls, config: RenderConfig) -> FlowchartLayoutOptions:
        """Build options from a :class:`RenderConfig` (grok ``from_render_config``).

        Each flowchart knob is widened ``int -> float`` (grok ``f64::from``) and
        falls back to the default when ``None``; ``font_size`` delegates to
        :meth:`RenderConfig.font_size_px` (called once -- mirrors grok).
        """
        default = cls()
        font_size = config.font_size_px()
        return cls(
            node_spacing=_or_default(config.flowchart.node_spacing, default.node_spacing),
            rank_spacing=_or_default(config.flowchart.rank_spacing, default.rank_spacing),
            padding=_or_default(config.flowchart.padding, default.padding),
            wrapping_width=_or_default(config.flowchart.wrapping_width, default.wrapping_width),
            font_size=font_size if font_size is not None else default.font_size,
        )


# === LayoutEngine (grok ``struct LayoutEngine``, impl block) ================
#
# grok's ``LayoutEngine`` owns the collected nodes / edges / subgraphs + the
# adjacency / reverse-adjacency maps + the node-measurement primitive. This
# leaf (R274b) ports the constructor (``new`` + ``new_with_options`` fused into
# ``__init__``), the recursive ``collect_nodes_and_edges`` pass, the
# ``add_node`` / ``ensure_node_exists`` / ``is_subgraph_id`` helpers, and the
# ``measure_node`` sizing primitive. The dagre bridge + subgraph-endpoint ops +
# ``compute_with_dagre`` orchestrator land in R274c--e.


class LayoutEngine:
    """The dagre-backed layout engine (grok ``struct LayoutEngine``).

    Owns the parsed graph plus the collected node / edge / subgraph bookkeeping
    and the adjacency / reverse-adjacency maps. Construction runs the recursive
    :meth:`collect_nodes_and_edges` pass once (mirrors grok ``new_with_options``
    calling it at the end of the constructor); every node is measured through
    :meth:`measure_node` on first sight.

    grok's lifetime ``'a`` borrow (``graph: &'a FlowchartGraph``) surfaces as a
    bare Python reference -- the engine does not clone the graph, it borrows the
    caller's :class:`~minimax_code.mermaid.to_svg.ast.FlowchartGraph` for the
    collection pass and later rank/position solving (R274c--e).

    R274b surface (8 of the engine's symbols): ``__init__`` (``new`` +
    ``new_with_options``), ``collect_nodes_and_edges``, ``add_node``,
    ``ensure_node_exists``, ``is_subgraph_id``, ``measure_node``. Deferred to
    R274c: ``compute_with_dagre`` + the dagre bridge + subgraph-endpoint ops.
    """

    def __init__(
        self,
        graph: FlowchartGraph,
        options: FlowchartLayoutOptions | None = None,
    ) -> None:
        """Build the engine + run the collection pass (grok ``new`` / ``new_with_options``).

        ``options=None`` mirrors ``new`` delegating to ``new_with_options`` with
        ``FlowchartLayoutOptions::default()``. The 11 grok struct fields map to
        instance attributes; ``is_state_diagram`` is decided once by
        :func:`graph_contains_state_shapes`; then
        :meth:`collect_nodes_and_edges` walks the graph.
        """
        self.graph = graph
        self.options = options if options is not None else FlowchartLayoutOptions()
        self.is_state_diagram = graph_contains_state_shapes(graph.statements)
        self.nodes: dict[str, NodeInfo] = {}
        self.edges: list[EdgeInfo] = []
        self.subgraphs: list[SubgraphInfo] = []
        self.adjacency: dict[str, list[str]] = {}
        self.reverse_adjacency: dict[str, list[str]] = {}
        self.next_node_order: int = 0
        self.node_to_subgraph: dict[str, str] = {}
        self.node_styles: dict[str, list[tuple[str, str]]] = {}
        self.collect_nodes_and_edges(graph.statements, None)

    def collect_nodes_and_edges(
        self,
        statements: list[Statement],
        current_subgraph_id: str | None = None,
    ) -> None:
        """Walk ``statements`` recursively, collecting nodes / edges / subgraphs.

        Mirrors grok ``collect_nodes_and_edges``: a ``match stmt`` over the four
        ``Statement`` variants. Python dispatches with ``isinstance`` against the
        R271 AST subclasses (:class:`Node` / :class:`Edge` / :class:`Subgraph` /
        :class:`StyleStatement`).

        * :class:`Node` -- skipped when its id is itself a subgraph id; otherwise
          :meth:`add_node` measures it, and the enclosing subgraph (if any)
          registers the node in ``node_to_subgraph`` (first-write-wins, guarded
          by ``not in``).
        * :class:`Edge` -- :meth:`ensure_node_exists` materializes both endpoints
          as Rectangle fallbacks; the enclosing subgraph registers any endpoint
          that is not itself a subgraph id; adjacency / reverse-adjacency are
          appended; the :class:`EdgeInfo` is pushed.
        * :class:`Subgraph` -- a :class:`SubgraphInfo` is pushed (``title`` falls
          back to the subgraph id when ``None``, mirroring grok's
          ``or_else(|| Some(id))``), then the body is recursed with the subgraph
          id as the new ``current_subgraph_id``.
        * :class:`StyleStatement` -- ``node_styles[node_id] = properties``
          (last-write-wins, mirrors grok ``HashMap::insert``).
        """
        for stmt in statements:
            if isinstance(stmt, Node):
                if self.is_subgraph_id(stmt.id):
                    continue
                self.add_node(stmt)
                if current_subgraph_id is not None and stmt.id not in self.node_to_subgraph:
                    self.node_to_subgraph[stmt.id] = current_subgraph_id
            elif isinstance(stmt, Edge):
                self.ensure_node_exists(stmt.from_)
                self.ensure_node_exists(stmt.to)

                if current_subgraph_id is not None:
                    if not self.is_subgraph_id(stmt.from_) and stmt.from_ not in self.node_to_subgraph:
                        self.node_to_subgraph[stmt.from_] = current_subgraph_id
                    if not self.is_subgraph_id(stmt.to) and stmt.to not in self.node_to_subgraph:
                        self.node_to_subgraph[stmt.to] = current_subgraph_id

                self.adjacency.setdefault(stmt.from_, []).append(stmt.to)
                self.reverse_adjacency.setdefault(stmt.to, []).append(stmt.from_)

                self.edges.append(
                    EdgeInfo(
                        from_=stmt.from_,
                        to=stmt.to,
                        label=stmt.label,
                        style=stmt.style,
                    )
                )
            elif isinstance(stmt, Subgraph):
                title = stmt.title if stmt.title is not None else stmt.id
                self.subgraphs.append(
                    SubgraphInfo(
                        id=stmt.id,
                        title=title,
                        parent_subgraph_id=current_subgraph_id,
                    )
                )
                self.collect_nodes_and_edges(stmt.statements, stmt.id)
            elif isinstance(stmt, StyleStatement):
                self.node_styles[stmt.node_id] = list(stmt.properties)

    def add_node(self, node: Node) -> None:
        """Measure + insert ``node`` (grok ``add_node``).

        Subgraph ids are skipped. A node already collected and re-seen with a
        ``None`` label is a no-op (grok's ``contains_key && label.is_none``
        guard) -- the earlier sighting with a real label wins. Otherwise the
        label defaults to the node id (grok ``unwrap_or(&node.id)``); the box is
        measured; ``order`` reuses the existing entry's order or allocates the
        next ``next_node_order`` slot.
        """
        if self.is_subgraph_id(node.id):
            return
        if node.id in self.nodes and node.label is None:
            return

        label = node.label if node.label is not None else node.id
        width, height = self.measure_node(label, node.shape)

        existing = self.nodes.get(node.id)
        if existing is not None:
            order = existing.order
        else:
            order = self.next_node_order
            self.next_node_order += 1

        self.nodes[node.id] = NodeInfo(
            id=node.id,
            label=label,
            shape=node.shape,
            width=width,
            height=height,
            rank=0,
            order=order,
        )

    def ensure_node_exists(self, node_id: str) -> None:
        """Materialize an implicit endpoint as a Rectangle fallback (grok ``ensure_node_exists``).

        An edge referencing a node never declared as a :class:`Node` gets a
        synthetic entry: label = id, shape = Rectangle, measured at the default
        font. Subgraph ids are skipped (they are not real nodes).
        """
        if self.is_subgraph_id(node_id):
            return
        if node_id not in self.nodes:
            width, height = self.measure_node(node_id, NodeShape.Rectangle)
            order = self.next_node_order
            self.next_node_order += 1
            self.nodes[node_id] = NodeInfo(
                id=node_id,
                label=node_id,
                shape=NodeShape.Rectangle,
                width=width,
                height=height,
                rank=0,
                order=order,
            )

    def is_subgraph_id(self, node_id: str) -> bool:
        """Whether ``node_id`` matches a collected subgraph id (grok ``is_subgraph_id``)."""
        return any(subgraph.id == node_id for subgraph in self.subgraphs)

    def measure_node(self, label: str, shape: NodeShape) -> tuple[float, float]:
        """Return the ``(width, height)`` box for ``label`` in ``shape`` (grok ``measure_node``).

        Mirrors mermaid.js shape sizing (grok cites the
        ``rendering-elements/shapes/*.ts`` sources). State diagrams route through
        a tighter char width (:data:`STATE_CHAR_WIDTH`) + their own size table;
        non-state-matching shapes fall through to the flowchart table (grok
        ``_ => {}`` in the state-diagram match). Both tables mirror grok's f64
        arithmetic exactly -- no rounding.
        """
        char_width = (
            scale_char_width(STATE_CHAR_WIDTH, self.options.font_size)
            if self.is_state_diagram
            else scale_char_width(DEFAULT_CHAR_WIDTH, self.options.font_size)
        )
        lines = wrap_text_lines(label, self.options.wrapping_width, char_width)
        text_width, text_height = measure_wrapped_lines_with_font_size(
            lines, char_width, self.options.font_size
        )
        padding = self.options.padding

        if self.is_state_diagram:
            if shape is NodeShape.RoundedRectangle:
                width = max(text_width + STATE_NODE_WIDTH_PADDING, 32.0)
                height = max(text_height + STATE_NODE_HEIGHT_PADDING, STATE_NODE_MIN_HEIGHT)
                return (width, height)
            if shape is NodeShape.Diamond:
                size = max(
                    text_width + STATE_DIAMOND_PADDING,
                    text_height + STATE_DIAMOND_PADDING,
                    STATE_NODE_MIN_HEIGHT,
                )
                return (size, size)
            if shape is NodeShape.StartState:
                return (14.0, 14.0)
            if shape is NodeShape.EndState:
                return (14.0, 14.0)
            if shape is NodeShape.ForkJoin:
                return (STATE_FORK_WIDTH, STATE_FORK_HEIGHT)
            # grok `_ => {}` -- fall through to the flowchart sizing table.

        if shape is NodeShape.Rectangle:
            return (text_width + padding * 4.0, text_height + padding * 2.0)
        if shape is NodeShape.RoundedRectangle:
            return (text_width + padding * 2.0, text_height + padding * 2.0)
        if shape is NodeShape.Subroutine:
            w = text_width + padding
            h = text_height + padding
            return (w + 16.0, h)
        if shape is NodeShape.Asymmetric:
            w = text_width + padding
            h = text_height + padding
            return (w + h / 4.0, h)
        if shape is NodeShape.Hexagon:
            h = text_height + padding
            w = text_width + padding * 2.5
            return (w * 7.0 / 6.0, h)
        if shape is NodeShape.Diamond:
            w = text_width + padding
            h = text_height + padding
            s = w + h
            return (s, s)
        if shape is NodeShape.Circle:
            diameter = text_width + padding
            return (diameter, diameter)
        if shape is NodeShape.StartState:
            return (14.0, 14.0)
        if shape is NodeShape.EndState:
            return (20.0, 20.0)
        if shape is NodeShape.ForkJoin:
            return (70.0, 10.0)
        if shape is NodeShape.Stadium:
            h = text_height + padding
            w = text_width + h / 4.0 + padding
            return (w, h)
        if shape is NodeShape.Cylinder:
            w = text_width + padding
            rx = w / 2.0
            ry = rx / (2.5 + w / 50.0)
            h = text_height + ry + padding
            return (w, h + 2.0 * ry)

        # grok's match is exhaustive over the 12 NodeShape variants; reaching
        # here is a programming error (a new variant added without a size rule).
        raise ValueError(f"unhandled NodeShape: {shape!r}")

    # === R274c: dagre bridge helpers (grok L465-512 + L811-1447 + L1663-1734) =
    #
    # The 14 helpers below wire the collected layout model to the dagre layout
    # stack (R246-R268) via the graphlib ``Graph`` core (R241-R245). They split
    # into 5 cohorts mirroring grok's ``layout.rs``:
    #
    # 1. subgraph ordering (``subgraph_ids_in_mermaid_order``) -- a post-order
    #    DFS over the subgraph forest so outer subgraphs seed before inner.
    # 2. edge-endpoint collapse (``nodes_in_subgraph_by_order`` /
    #    ``subgraph_entry_node_id`` / ``subgraph_exit_node_id`` /
    #    ``dagre_edge_endpoint`` / ``layout_endpoint_node``) -- a subgraph-bound
    #    edge anchors onto its boundary node for dagre, then re-expands to a
    #    centred box for rendering.
    # 3. edge-label sizing (``edge_label_dimensions``).
    # 4. rank / alignment passes (``longest_path_ranks_without_back_edges`` /
    #    ``snap_state_ranks`` / ``align_state_terminal_singletons``).
    # 5. back-edge discovery (``dfs_detect_back_edges`` / ``detect_back_edges``)
    #    + the bridge pair (``build_dagre_graph`` / ``extract_layout_from_dagre``).

    def subgraph_ids_in_mermaid_order(self) -> list[str]:
        """Return subgraph ids in mermaid declaration order (grok L465-512).

        Walks the subgraph forest depth-first (post-order) rooted at every
        parentless subgraph, recording each id on the way back up, then reverses
        the post-order so the outermost-first declaration order wins. Mirrors
        grok's ``children_by_parent`` map + ``roots`` list + nested ``dfs``
        helper (``visited`` guards against forest cycles).
        """
        if not self.subgraphs:
            return []

        children_by_parent: dict[str, list[str]] = {}
        roots: list[str] = []
        for sg in self.subgraphs:
            parent_key = sg.parent_subgraph_id if sg.parent_subgraph_id is not None else ""
            children_by_parent.setdefault(parent_key, []).append(sg.id)
            if sg.parent_subgraph_id is None:
                roots.append(sg.id)

        def dfs(node_id: str, visited: set[str], out: list[str]) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            for child in children_by_parent.get(node_id, []):
                dfs(child, visited, out)
            out.append(node_id)

        post_order: list[str] = []
        visited: set[str] = set()
        for root in roots:
            dfs(root, visited, post_order)
        post_order.reverse()
        return post_order

    def nodes_in_subgraph_by_order(self, subgraph_id: str) -> list[str]:
        """Return the node ids directly inside ``subgraph_id``, sorted by order.

        Mirrors grok L1301-1315: filter ``node_to_subgraph`` for the direct
        children of this subgraph, then sort by ``NodeInfo.order`` with a
        sentinel ``inf`` for any unsighted id (grok ``usize::MAX``) -- ``inf``
        sorts after every real ``order`` so unsighted nodes land last and stay
        stable.
        """
        node_ids = [
            node_id
            for node_id, sg_id in self.node_to_subgraph.items()
            if sg_id == subgraph_id
        ]
        node_ids.sort(
            key=lambda node_id: self.nodes[node_id].order
            if node_id in self.nodes
            else float("inf")
        )
        return node_ids

    def subgraph_entry_node_id(self, subgraph_id: str) -> str | None:
        """Return the entry node id of a subgraph (grok L1264-1280).

        The entry is the first node (in declaration order) with no incoming
        edge from a sibling inside the subgraph; if every node has such a
        back-edge (a cycle), fall back to the first node. Mirrors grok's
        ``find(...).or_else(first)`` chain.
        """
        node_ids = self.nodes_in_subgraph_by_order(subgraph_id)
        if not node_ids:
            return None
        node_id_set = set(node_ids)
        for node_id in node_ids:
            if not any(
                edge.from_ in node_id_set and edge.to == node_id for edge in self.edges
            ):
                return node_id
        return node_ids[0]

    def subgraph_exit_node_id(self, subgraph_id: str) -> str | None:
        """Return the exit node id of a subgraph (grok L1282-1299).

        The exit is the last node (in declaration order) with no outgoing edge
        to a sibling inside the subgraph; if every node has such a forward-edge
        (a cycle), fall back to the last node. Mirrors grok's
        ``rev().find(...).or_else(last)`` chain.
        """
        node_ids = self.nodes_in_subgraph_by_order(subgraph_id)
        if not node_ids:
            return None
        node_id_set = set(node_ids)
        for node_id in reversed(node_ids):
            if not any(
                edge.from_ == node_id and edge.to in node_id_set for edge in self.edges
            ):
                return node_id
        return node_ids[-1]

    def dagre_edge_endpoint(self, id: str, is_source: bool) -> str | None:
        """Map a (possibly subgraph) edge endpoint to a concrete dagre node id.

        Mirrors grok L1252-1262: a plain node id maps to itself; a subgraph id
        resolves to its exit node when the edge starts there (``is_source``)
        and its entry node when the edge ends there. This collapses
        subgraph-bound edges onto the boundary nodes dagre can actually position.
        """
        if not self.is_subgraph_id(id):
            return id
        if is_source:
            return self.subgraph_exit_node_id(id)
        return self.subgraph_entry_node_id(id)

    def layout_endpoint_node(
        self,
        layout_nodes: dict[str, LayoutNode],
        layout_subgraphs: list[LayoutSubgraph],
        id: str,
    ) -> LayoutNode | None:
        """Resolve an edge endpoint id to a positioned layout node (grok L1317-1344).

        A node id present in ``layout_nodes`` returns its clone; a subgraph id
        synthesises a centred rectangle layout node (centre = subgraph centre,
        label = title or id) so edges collapsed onto a subgraph boundary still
        anchor to a renderable box. grok's ``.clone()`` is Rust ownership; the
        frozen layout-node value is returned by reference in Python.
        """
        node = layout_nodes.get(id)
        if node is not None:
            return node
        for subgraph in layout_subgraphs:
            if subgraph.id == id:
                return LayoutNode(
                    id=subgraph.id,
                    x=subgraph.x + subgraph.width / 2.0,
                    y=subgraph.y + subgraph.height / 2.0,
                    width=subgraph.width,
                    height=subgraph.height,
                    shape=NodeShape.Rectangle,
                    label=subgraph.title if subgraph.title is not None else subgraph.id,
                    fill_color=None,
                    stroke_color=None,
                )
        return None

    def edge_label_dimensions(self, label: str) -> tuple[float, float] | None:
        """Return the ``(width, height)`` box for an edge label (grok L1429-1447).

        Wraps the label at the flowchart wrapping width (state diagrams use a
        wider per-char estimate), measures the wrapped block, and pads it on
        all sides by ``EDGE_LABEL_PADDING``. An empty / whitespace-only label
        yields ``None`` (grok returns ``None`` so the caller skips the label box).
        """
        if not label.strip():
            return None
        char_width = scale_char_width(
            STATE_CHAR_WIDTH if self.is_state_diagram else DEFAULT_CHAR_WIDTH,
            self.options.font_size,
        )
        lines = wrap_text_lines(label, self.options.wrapping_width, char_width)
        if not lines:
            return None
        text_width, text_height = measure_wrapped_lines_with_font_size(
            lines, char_width, self.options.font_size
        )
        return (
            text_width + EDGE_LABEL_PADDING * 2.0,
            text_height + EDGE_LABEL_PADDING * 2.0,
        )

    def longest_path_ranks_without_back_edges(
        self, back_edges: set[tuple[str, str]]
    ) -> dict[str, int]:
        """Rank nodes by longest path through the back-edge-stripped DAG.

        Mirrors grok L1017-1089: a Kahn-style topological sweep (in-degree
        counter, ``ready`` queue sorted by ``NodeInfo.order``) yields a
        declaration-stable topo order, then a forward pass assigns each node
        ``max(rank, parent+1)`` so rank = the longest acyclic path from a
        source. Back edges are ignored in both passes. Empty on an all-cyclic
        graph (no source reaches the queue).
        """
        indegree: dict[str, int] = {node_id: 0 for node_id in self.nodes}
        for edge in self.edges:
            if (edge.from_, edge.to) in back_edges:
                continue
            if edge.to in indegree:
                indegree[edge.to] += 1

        def order_key(node_id: str) -> float:
            info = self.nodes.get(node_id)
            return float(info.order) if info is not None else float("inf")

        ready = sorted(
            (node_id for node_id, count in indegree.items() if count == 0),
            key=order_key,
        )
        topo: list[str] = []
        while ready:
            node_id = ready.pop(0)
            topo.append(node_id)
            for edge in self.edges:
                if edge.from_ != node_id:
                    continue
                if (edge.from_, edge.to) in back_edges:
                    continue
                if edge.to in indegree:
                    indegree[edge.to] = max(indegree[edge.to] - 1, 0)
                    if indegree[edge.to] == 0:
                        ready.append(edge.to)
            ready.sort(key=order_key)

        ranks: dict[str, int] = {node_id: 0 for node_id in self.nodes}
        for node_id in topo:
            base_rank = ranks[node_id]
            for edge in self.edges:
                if edge.from_ != node_id:
                    continue
                if (edge.from_, edge.to) in back_edges:
                    continue
                if edge.to in ranks:
                    ranks[edge.to] = max(ranks[edge.to], base_rank + 1)
        return ranks

    def snap_state_ranks(
        self,
        positions: PositionMap,
        back_edges: set[tuple[str, str]],
    ) -> None:
        """Snap state-diagram node y to the nearest declared rank grid (grok L933-957).

        Dagre's longest-path ranker for state diagrams can leave terminals off
        the rank grid; this re-projects each node onto the y of its declared
        rank (``longest_path_ranks_without_back_edges``). The grid is the sorted,
        deduped set of distinct y-values (within 0.5px). No-op when the grid is
        too short for the max rank, or the graph is all-cyclic (empty ranks).
        The tuple is reassigned whole because Python tuples are immutable
        (grok mutates ``*y`` in place).
        """
        ranks = self.longest_path_ranks_without_back_edges(back_edges)
        if not ranks:
            return
        level_positions = sorted(y for _, y in positions.values())
        deduped: list[float] = []
        for y in level_positions:
            if not deduped or abs(y - deduped[-1]) >= 0.5:
                deduped.append(y)
        if not deduped:
            return
        max_rank = max(ranks.values()) if ranks else 0
        if len(deduped) <= max_rank:
            return
        for node_id, rank in ranks.items():
            if node_id in positions:
                x, _ = positions[node_id]
                positions[node_id] = (x, deduped[rank])

    def align_state_terminal_singletons(
        self,
        positions: PositionMap,
        back_edges: set[tuple[str, str]],
    ) -> None:
        """Align lone terminal singletons to their predecessors' x (grok L959-1015).

        For each rank holding exactly one node that is a sink (no forward
        out-edge) with >= 2 predecessors at strictly lower ranks, snap the
        node's x to the largest predecessor x. This keeps state-diagram
        terminal columns aligned under their fan-in instead of drifting to the
        dagre-computed median. No-op on an all-cyclic graph (empty ranks).
        """
        ranks = self.longest_path_ranks_without_back_edges(back_edges)
        if not ranks:
            return
        nodes_by_rank: dict[int, list[str]] = {}
        for node_id, rank in ranks.items():
            nodes_by_rank.setdefault(rank, []).append(node_id)
        for node_id, rank in ranks.items():
            if rank <= 0:
                continue
            rank_nodes = nodes_by_rank.get(rank)
            if rank_nodes is None:
                continue
            if len(rank_nodes) != 1:
                continue
            has_forward_outgoing = any(
                edge.from_ == node_id and (edge.from_, edge.to) not in back_edges
                for edge in self.edges
            )
            if has_forward_outgoing:
                continue
            predecessor_xs: list[float] = [
                positions[edge.from_][0]
                for edge in self.edges
                if edge.to == node_id
                and (edge.from_, edge.to) not in back_edges
                and edge.from_ in ranks
                and ranks[edge.from_] < rank
                and edge.from_ in positions
            ]
            if len(predecessor_xs) < 2:
                continue
            predecessor_xs.sort()
            if node_id in positions:
                _, y = positions[node_id]
                positions[node_id] = (predecessor_xs[-1], y)

    def dfs_detect_back_edges(
        self,
        node: str,
        visited: set[str],
        in_stack: set[str],
        back_edges: set[tuple[str, str]],
    ) -> None:
        """Recursive DFS marking edges to in-stack nodes as back edges.

        Mirrors grok L1709-1734: a standard iterative-stack back-edge
        discovery -- a neighbor still on the recursion stack is reached via a
        back edge (``(node, neighbor)``), an unvisited neighbor recurses.
        ``visited`` makes each node enter the DFS once; ``in_stack`` tracks the
        active path. ``discard`` matches grok's ``HashSet::remove`` (no-op if
        absent, never panics).
        """
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        for neighbor in self.adjacency.get(node, []):
            if neighbor in in_stack:
                back_edges.add((node, neighbor))
            elif neighbor not in visited:
                self.dfs_detect_back_edges(neighbor, visited, in_stack, back_edges)
        in_stack.discard(node)

    def detect_back_edges(self) -> set[tuple[str, str]]:
        """Discover all back edges in the collected graph (grok L1663-1706).

        Seeds the DFS from every source (a node with no in-edge;
        ``reverse_adjacency`` has no entry for it); if there are none but edges
        exist, seeds from the first edge's source; if there are no sources and
        no edges but nodes exist, seeds from the lexicographically smallest
        node id. A second sweep visits any node the seed DFS missed
        (disconnected components), in sorted order. Each call to
        :meth:`dfs_detect_back_edges` records ``(from, to)`` back edges.
        """
        back_edges: set[tuple[str, str]] = set()
        visited: set[str] = set()
        in_stack: set[str] = set()

        start_nodes = [
            node_id for node_id in self.nodes if node_id not in self.reverse_adjacency
        ]
        if not start_nodes and self.edges:
            start_nodes.append(self.edges[0].from_)
        elif not start_nodes and self.nodes:
            start_nodes.append(min(self.nodes))

        for start in start_nodes:
            self.dfs_detect_back_edges(start, visited, in_stack, back_edges)

        remaining = sorted(node_id for node_id in self.nodes if node_id not in visited)
        for node_id in remaining:
            if node_id not in visited:
                self.dfs_detect_back_edges(node_id, visited, in_stack, back_edges)
        return back_edges

    def build_dagre_graph(
        self,
        rank_dir: str,
        node_sep: float,
        rank_sep: float,
        back_edges: set[tuple[str, str]],
    ) -> tuple[DagreGraph, EdgeMap]:
        """Build the dagre :class:`Graph` for layout + the edge-index map (grok L811-903).

        Mirrors grok's bridge step: a compound multigraph (``directed`` /
        ``multigraph`` / ``compound``) with the flowchart's rankdir / nodesep /
        ranksep (+ a fixed 20px edgesep, ``MARGIN`` px margins, and the
        ``longest-path`` ranker for state diagrams). Subgraphs seed zero-size
        nodes with ``SUBGRAPH_PADDING``; collected nodes (sorted by
        ``NodeInfo.order`` for declaration-stable seeding) carry their measured
        ``width`` / ``height``; ``set_parent`` wires the compound nesting.

        Edges collapse subgraph-bound endpoints onto boundary nodes
        (:meth:`dagre_edge_endpoint`); back edges and self-loops are dropped
        from dagre -- the dropped indices are also missing from ``edge_map``,
        so the extract step re-attaches them via the surviving indices.
        Labelled edges carry a measured label box (``labelpos="c"``).
        """
        g: DagreGraph = Graph(
            GraphOption(directed=True, multigraph=True, compound=True),
            node_default_factory=GraphNode,
            edge_default_factory=GraphEdge,
        )
        g.set_graph(
            GraphConfig(
                rankdir=rank_dir,
                nodesep=node_sep,
                ranksep=rank_sep,
                edgesep=20.0,
                marginx=MARGIN,
                marginy=MARGIN,
                ranker="longest-path" if self.is_state_diagram else None,
            )
        )

        for sg_id in self.subgraph_ids_in_mermaid_order():
            g.set_node(
                sg_id,
                GraphNode(width=0.0, height=0.0, padding=SUBGRAPH_PADDING),
            )

        sorted_nodes = sorted(self.nodes.items(), key=lambda item: item[1].order)
        for node_id, info in sorted_nodes:
            g.set_node(node_id, GraphNode(width=info.width, height=info.height))

        for node_id, sg_id in self.node_to_subgraph.items():
            g.set_parent(node_id, sg_id)
        for sg in self.subgraphs:
            if sg.parent_subgraph_id is not None:
                g.set_parent(sg.id, sg.parent_subgraph_id)

        edge_map: EdgeMap = {}
        for idx, edge in enumerate(self.edges):
            if (edge.from_, edge.to) in back_edges:
                continue
            dagre_from = self.dagre_edge_endpoint(edge.from_, True)
            if dagre_from is None:
                dagre_from = edge.from_
            dagre_to = self.dagre_edge_endpoint(edge.to, False)
            if dagre_to is None:
                dagre_to = edge.to
            if dagre_from == dagre_to:
                continue
            edge_label = GraphEdge(labelpos="c")
            if edge.label is not None:
                dimensions = self.edge_label_dimensions(edge.label)
                if dimensions is not None:
                    edge_label.width, edge_label.height = dimensions
            g.set_edge(dagre_from, dagre_to, edge_label, None)
            edge_map[idx] = (dagre_from, dagre_to)

        return g, edge_map

    def extract_layout_from_dagre(
        self,
        g: DagreGraph,
        edge_map: EdgeMap,
    ) -> tuple[PositionMap, EdgePointMap, EdgeLabelPosMap]:
        """Extract positions + edge geometry from a laid-out dagre graph (grok L905-931).

        Reads every node's dagre-computed ``(x, y)`` into ``positions``; for
        each edge in the index map, copies its control-point polyline into
        ``edge_points`` and, when the edge carries a measured label box
        (width/height > 0), records the label centre ``(edge.x, edge.y)``. The
        triple drives the post-dagre geometry pass that snaps state ranks,
        aligns terminal singletons, and materialises the render layout.
        """
        positions: PositionMap = {}
        for node_id in g.nodes():
            node = g.node(node_id)
            if node is not None:
                positions[node_id] = (node.x, node.y)

        edge_points: EdgePointMap = {}
        edge_label_positions: EdgeLabelPosMap = {}
        for idx, (from_, to) in edge_map.items():
            edge = g.edge(from_, to, None)
            if edge is None:
                continue
            if edge.points is not None:
                edge_points[idx] = [(point.x, point.y) for point in edge.points]
            if (edge.width or 0.0) > 0.0 or (edge.height or 0.0) > 0.0:
                edge_label_positions[idx] = (edge.x, edge.y)

        return positions, edge_points, edge_label_positions

    def compute_spacing(self) -> tuple[float, float]:
        """Return ``(node_spacing, rank_spacing)`` from the layout options.

        Mirrors grok ``compute_spacing`` -- the single source the post-dagre
        pass reads when it needs the engine's spacing knobs as a pair.
        """
        return (self.options.node_spacing, self.options.rank_spacing)

    def center_nodes_in_subgraphs(self, positions: PositionMap, is_vertical: bool) -> None:
        """Translate each subgraph's nodes so its centre matches the group centre.

        Mirrors grok ``center_nodes_in_subgraphs``. For every connected group of
        subgraphs (cross-cluster edges link them) the overall group centre is
        computed from all member nodes; then each subgraph in the group is
        translated -- preserving intra-subgraph offsets -- so its own node
        centre matches the group centre. ``is_vertical`` selects the axis (``x``
        for TB/BT, ``y`` for LR/RL). Mutates ``positions`` in place.
        """
        connected_groups = self.find_connected_subgraph_groups()
        for group in connected_groups:
            # Compute the overall group centre from all member nodes.
            all_nodes_in_group: list[str] = []
            for sg_id in group:
                for node_id, owner_id in self.node_to_subgraph.items():
                    if owner_id == sg_id:
                        all_nodes_in_group.append(node_id)
            if not all_nodes_in_group:
                continue
            all_coords: list[float] = []
            for node_id in all_nodes_in_group:
                pos = positions.get(node_id)
                if pos is not None:
                    all_coords.append(pos[0] if is_vertical else pos[1])
            if not all_coords:
                continue
            group_avg = sum(all_coords) / len(all_coords)

            # Shift each subgraph's nodes so the subgraph centre matches the
            # group centre, preserving relative offsets within each subgraph.
            for sg_id in group:
                sg_nodes: list[str] = []
                for node_id, owner_id in self.node_to_subgraph.items():
                    if owner_id == sg_id:
                        sg_nodes.append(node_id)
                if not sg_nodes:
                    continue
                sg_coords: list[float] = []
                for node_id in sg_nodes:
                    pos = positions.get(node_id)
                    if pos is not None:
                        sg_coords.append(pos[0] if is_vertical else pos[1])
                if not sg_coords:
                    continue
                sg_avg = sum(sg_coords) / len(sg_coords)
                shift = group_avg - sg_avg
                for node_id in sg_nodes:
                    pos = positions.get(node_id)
                    if pos is not None:
                        x, y = pos
                        if is_vertical:
                            positions[node_id] = (x + shift, y)
                        else:
                            positions[node_id] = (x, y + shift)

    def find_connected_subgraph_groups(self) -> list[set[str]]:
        """Group subgraphs linked by cross-cluster edges into connected sets.

        Mirrors grok ``find_connected_subgraph_groups``. Builds an undirected
        adjacency over subgraphs from edges whose endpoints live in different
        subgraphs, then a stack DFS collects each connected component; only
        components of size > 1 are returned (lone subgraphs need no centring).
        """
        if not self.subgraphs:
            return []
        subgraph_connections: dict[str, set[str]] = {sg.id: set() for sg in self.subgraphs}
        for edge in self.edges:
            from_sg = self.node_to_subgraph.get(edge.from_)
            to_sg = self.node_to_subgraph.get(edge.to)
            if from_sg is not None and to_sg is not None and from_sg != to_sg:
                subgraph_connections.setdefault(from_sg, set()).add(to_sg)
                subgraph_connections.setdefault(to_sg, set()).add(from_sg)
        visited: set[str] = set()
        groups: list[set[str]] = []
        for sg in self.subgraphs:
            if sg.id in visited:
                continue
            group: set[str] = set()
            stack: list[str] = [sg.id]
            while stack:
                current = stack.pop()
                if current not in group:
                    group.add(current)
                    visited.add(current)
                    for neighbor in subgraph_connections.get(current, set()):
                        if neighbor not in group:
                            stack.append(neighbor)
            if len(group) > 1:
                groups.append(group)
        return groups

    def analyze_clusters(self) -> ClusterAnalysis:
        """Map each subgraph to its member nodes + whether it has external edges.

        Mirrors grok ``analyze_clusters``. ``subgraph_nodes`` collects the member
        node set per subgraph id (reverse of ``node_to_subgraph``);
        ``external_edges`` flags ``True`` when any edge has exactly one endpoint
        inside the subgraph (the XOR of endpoint membership).
        """
        subgraph_nodes: dict[str, set[str]] = {}
        for sg in self.subgraphs:
            nodes: set[str] = set()
            for node_id, owner_id in self.node_to_subgraph.items():
                if owner_id == sg.id:
                    nodes.add(node_id)
            subgraph_nodes[sg.id] = nodes
        external_edges: dict[str, bool] = {}
        for sg in self.subgraphs:
            nodes_in_sg = subgraph_nodes[sg.id]
            has_external = any(
                (edge.from_ in nodes_in_sg) != (edge.to in nodes_in_sg)
                for edge in self.edges
            )
            external_edges[sg.id] = has_external
        return ClusterAnalysis(
            subgraph_nodes=subgraph_nodes,
            external_edges=external_edges,
        )

    def compute_subgraph_bounds(
        self,
        layout_nodes: dict[str, LayoutNode],
        padding: float,
    ) -> list[LayoutSubgraph]:
        """Compute padded bounding rectangles for every subgraph, bottom-up.

        Mirrors grok ``compute_subgraph_bounds``. Subgraphs are processed
        leaf-first (children before parents) so a parent rect can expand to
        encompass its children's padded rects. For each subgraph the min/max
        bounds come from its directly-owned nodes (centre +/- half-size) plus
        any already-computed child rects; the title bar reserves
        :meth:`subgraph_title_height` above the content (0 when the subgraph has
        no title -- ``title is None``). Subgraphs with no content are skipped.
        """
        ordered_ids = self.subgraph_ids_bottom_up()
        rect_map: dict[str, tuple[float, float, float, float]] = {}

        for sg_id in ordered_ids:
            sg = next(s for s in self.subgraphs if s.id == sg_id)
            direct_nodes: list[str] = [
                node_id
                for node_id, owner_id in self.node_to_subgraph.items()
                if owner_id == sg_id
            ]

            min_x = math.inf
            min_y = math.inf
            max_x = -math.inf
            max_y = -math.inf

            for node_id in direct_nodes:
                node = layout_nodes.get(node_id)
                if node is not None:
                    min_x = min(min_x, node.x - node.width / 2.0)
                    max_x = max(max_x, node.x + node.width / 2.0)
                    min_y = min(min_y, node.y - node.height / 2.0)
                    max_y = max(max_y, node.y + node.height / 2.0)

            for child_sg in self.subgraphs:
                if child_sg.parent_subgraph_id == sg_id:
                    child_rect = rect_map.get(child_sg.id)
                    if child_rect is not None:
                        cx, cy, cx2, cy2 = child_rect
                        min_x = min(min_x, cx)
                        min_y = min(min_y, cy)
                        max_x = max(max_x, cx2)
                        max_y = max(max_y, cy2)

            if math.isinf(min_x):
                continue

            title_padding = (
                self.subgraph_title_height(sg.title) if sg.title is not None else 0.0
            )
            rx = min_x - padding
            ry = min_y - padding - title_padding
            rw = (max_x - min_x) + padding * 2.0
            rh = (max_y - min_y) + padding * 2.0 + title_padding
            rect_map[sg_id] = (rx, ry, rx + rw, ry + rh)

        result: list[LayoutSubgraph] = []
        for sg in self.subgraphs:
            rect = rect_map.get(sg.id)
            if rect is not None:
                rx, ry, rx2, ry2 = rect
                result.append(
                    LayoutSubgraph(
                        id=sg.id,
                        title=sg.title,
                        x=rx,
                        y=ry,
                        width=rx2 - rx,
                        height=ry2 - ry,
                    )
                )
        return result

    def subgraph_title_height(self, title: str) -> float:
        """Height of the wrapped subgraph title, floored at the default.

        Mirrors grok ``subgraph_title_height``. The title is wrapped at the
        engine's ``wrapping_width`` using the font-size-scaled char width; the
        wrapped height comes from :func:`measure_wrapped_lines_with_font_size`
        and is clamped to at least :data:`SUBGRAPH_TITLE_HEIGHT`.
        """
        char_width = scale_char_width(DEFAULT_CHAR_WIDTH, self.options.font_size)
        lines = wrap_text_lines(title, self.options.wrapping_width, char_width)
        _, text_height = measure_wrapped_lines_with_font_size(
            lines, char_width, self.options.font_size
        )
        return max(text_height, SUBGRAPH_TITLE_HEIGHT)

    def subgraph_ids_bottom_up(self) -> list[str]:
        """Return subgraph ids in leaf-first (post-order) traversal order.

        Mirrors grok ``subgraph_ids_bottom_up``. Builds a parent -> children map
        (a missing parent key groups top-level subgraphs), then a post-order DFS
        from each root appends a node after all its descendants -- so leaves
        appear before their enclosing subgraphs.
        """
        children_by_parent: dict[str, list[str]] = {}
        roots: list[str] = []
        for sg in self.subgraphs:
            parent_key = sg.parent_subgraph_id if sg.parent_subgraph_id is not None else ""
            children_by_parent.setdefault(parent_key, []).append(sg.id)
            if sg.parent_subgraph_id is None:
                roots.append(sg.id)
        result: list[str] = []

        def dfs_post(node_id: str) -> None:
            for child in children_by_parent.get(node_id, []):
                dfs_post(child)
            result.append(node_id)

        for root in roots:
            dfs_post(root)
        return result

    def compute_bounds(self, positions: PositionMap) -> tuple[float, float]:
        """Compute the overall diagram bounds ``(width, height)``.

        Mirrors grok ``compute_bounds``. Empty positions -> ``(200.0, 200.0)``
        (the fallback viewport). Otherwise the half-extent of every positioned
        node is maxed into ``(max_x, max_y)`` and padded with :data:`MARGIN`.
        """
        if not positions:
            return (200.0, 200.0)
        max_x = 0.0
        max_y = 0.0
        for node_id, (x, y) in positions.items():
            node = self.nodes.get(node_id)
            if node is None:
                continue
            max_x = max(max_x, x + node.width / 2.0)
            max_y = max(max_y, y + node.height / 2.0)
        return (max_x + MARGIN, max_y + MARGIN)

    def get_node_colors(self, node_id: str) -> tuple[str | None, str | None]:
        """Look up a node's ``(fill, stroke)`` colours from its style directives.

        Mirrors grok ``get_node_colors``. Reads the node's style property list
        (last-write-wins from ``StyleStatement`` collection) and returns the
        first ``fill`` / ``stroke`` value, or ``(None, None)`` when the node has
        no style directive (or no matching keys).
        """
        props = self.node_styles.get(node_id)
        if props is None:
            return (None, None)
        fill = next((value for key, value in props if key == "fill"), None)
        stroke = next((value for key, value in props if key == "stroke"), None)
        return (fill, stroke)

    # === R274e: edge geometry (grok layout.rs L2602-L3246) ===================
    #
    # 17 edge-routing primitives (13 instance + 4 associated/static) consumed
    # by ``compute_with_dagre`` (R274f). All are internal helpers; none widens
    # the leaf's public surface (``__all__`` stays at 4 data classes).
    #
    # Mapping notes (grok -> Python):
    # * ``&self`` -> regular method; bare ``fn name(args)`` -> ``@staticmethod``.
    # * ``&HashMap<String, LayoutNode>`` -> ``dict[str, LayoutNode]``;
    #   ``&[&LayoutNode]`` -> ``list[LayoutNode]`` (collections.abc not imported;
    #   callers always pass concrete list/dict -- YAGNI).
    # * ``&mut Vec<(f64,f64)>`` / ``&mut [(f64,f64)]`` -> ``list[tuple[float,
    #   float]]`` mutated in place (returns ``None``).
    # * ``Vec::dedup`` -> module-level ``_dedup_consecutive`` (consecutive
    #   de-duplication; Python lists have no stdlib equivalent).
    # * ``(0.0..=1.0).contains(&t)`` -> ``0.0 <= t <= 1.0``.

    def edge_label_bounds(
        self, edge: LayoutEdge
    ) -> tuple[float, float, float, float] | None:
        """Return ``(x, y, width, height)`` of an edge label box, or ``None``.

        Mirrors grok ``edge_label_bounds``. Falls back to the geometric midpoint
        of the edge points when ``label_pos`` is unset.
        """
        label = edge.label
        if label is None:
            return None
        dims = self.edge_label_dimensions(label)
        if dims is None:
            return None
        width, height = dims
        if edge.label_pos is not None:
            x, y = edge.label_pos
        else:
            x, y = LayoutEngine.edge_label_midpoint(edge.points)
        return (x, y, width, height)

    @staticmethod
    def edge_label_midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
        """Geometric midpoint of an edge poly-line, weighted by segment length.

        Mirrors grok ``edge_label_midpoint`` (associated function). Fewer than
        two points collapses to the first point, or ``(0.0, 0.0)`` when empty.
        A near-zero total length returns the first point; the unreachable tail
        fallback returns the average of the first and last points.
        """
        if len(points) < 2:
            return points[0] if points else (0.0, 0.0)
        segment_lengths: list[float] = []
        total_length = 0.0
        for i in range(len(points) - 1):
            dx = points[i + 1][0] - points[i][0]
            dy = points[i + 1][1] - points[i][1]
            seg_len = math.sqrt(dx * dx + dy * dy)
            segment_lengths.append(seg_len)
            total_length += seg_len
        if total_length < 0.001:
            return points[0]
        target_distance = total_length * 0.5
        accumulated = 0.0
        for i, seg_len in enumerate(segment_lengths):
            if accumulated + seg_len >= target_distance:
                remaining = target_distance - accumulated
                t = remaining / seg_len if seg_len > 0.001 else 0.0
                x = points[i][0] + t * (points[i + 1][0] - points[i][0])
                y = points[i][1] + t * (points[i + 1][1] - points[i][1])
                return (x, y)
            accumulated += seg_len
        last = len(points) - 1
        return (
            (points[0][0] + points[last][0]) / 2.0,
            (points[0][1] + points[last][1]) / 2.0,
        )

    def is_back_edge(self, from_node: LayoutNode, to_node: LayoutNode) -> bool:
        """Whether ``to_node`` lies upstream of ``from_node`` (a back edge).

        Mirrors grok ``is_back_edge``. Direction-aware: vertical layouts flag
        ``to`` above/below ``from`` against the flow; horizontal layouts flag
        ``to`` left/right of ``from`` against the flow. The 10px hysteresis
        avoids classifying a near-aligned edge as backward.
        """
        dx = to_node.x - from_node.x
        dy = to_node.y - from_node.y
        is_vertical = self.graph.direction in (
            GraphDirection.TopToBottom,
            GraphDirection.BottomToTop,
        )
        if is_vertical:
            if self.graph.direction == GraphDirection.TopToBottom:
                return dy < -10.0
            if self.graph.direction == GraphDirection.BottomToTop:
                return dy > 10.0
            return False
        if self.graph.direction == GraphDirection.LeftToRight:
            return dx < -10.0
        if self.graph.direction == GraphDirection.RightToLeft:
            return dx > 10.0
        return False

    def compute_edge_points_with_obstacles(
        self,
        from_node: LayoutNode,
        to_node: LayoutNode,
        all_nodes: dict[str, LayoutNode],
    ) -> list[tuple[float, float]]:
        """Route an edge between two nodes, detouring around obstacles.

        Mirrors grok ``compute_edge_points_with_obstacles``. A back edge takes
        the simple U-shaped detour; otherwise the orthogonal router runs and
        every node except the endpoints is treated as a potential obstacle.
        """
        dx = to_node.x - from_node.x
        dy = to_node.y - from_node.y
        is_vertical = self.graph.direction in (
            GraphDirection.TopToBottom,
            GraphDirection.BottomToTop,
        )
        if is_vertical:
            if self.graph.direction == GraphDirection.TopToBottom:
                back_edge = dy < -10.0
            elif self.graph.direction == GraphDirection.BottomToTop:
                back_edge = dy > 10.0
            else:
                back_edge = False
        else:
            if self.graph.direction == GraphDirection.LeftToRight:
                back_edge = dx < -10.0
            elif self.graph.direction == GraphDirection.RightToLeft:
                back_edge = dx > 10.0
            else:
                back_edge = False
        if back_edge:
            return self.compute_back_edge_points_simple(from_node, to_node, is_vertical)
        obstacles = [
            node
            for node in all_nodes.values()
            if node.id != from_node.id and node.id != to_node.id
        ]
        if is_vertical:
            return self.compute_vertical_edge_with_obstacles(from_node, to_node, obstacles)
        return self.compute_horizontal_edge_with_obstacles(from_node, to_node, obstacles)

    def compute_horizontal_edge_with_obstacles(
        self,
        from_node: LayoutNode,
        to_node: LayoutNode,
        obstacles: list[LayoutNode],
    ) -> list[tuple[float, float]]:
        """Orthogonal router for left/right-flowing edges (with detours).

        Mirrors grok ``compute_horizontal_edge_with_obstacles``. Endpoints leave
        the left/right node border; an obstacle whose margin-expanded AABB
        overlaps the band is routed around (above or below, nearer to ``from``).
        """
        travel_right = to_node.x > from_node.x
        from_x = from_node.x + (from_node.width / 2.0) * (1.0 if travel_right else -1.0)
        to_x = to_node.x + (to_node.width / 2.0) * (-1.0 if travel_right else 1.0)
        min_x = min(from_x, to_x)
        max_x = max(from_x, to_x)
        min_y = min(from_node.y, to_node.y)
        max_y = max(from_node.y, to_node.y)
        blocking = None
        for obs in obstacles:
            obs_left = obs.x - obs.width / 2.0 - 10.0
            obs_right = obs.x + obs.width / 2.0 + 10.0
            obs_top = obs.y - obs.height / 2.0 - 10.0
            obs_bottom = obs.y + obs.height / 2.0 + 10.0
            if obs_left < max_x and obs_right > min_x and obs_top < max_y and obs_bottom > min_y:
                blocking = obs
                break
        if blocking is not None:
            obs_top = blocking.y - blocking.height / 2.0
            obs_bottom = blocking.y + blocking.height / 2.0
            route_above = abs(from_node.y - obs_top) < abs(from_node.y - obs_bottom)
            route_y = obs_top - 30.0 if route_above else obs_bottom + 30.0
            start = self.connection_point_towards(from_node, from_x, route_y)
            corner1 = (from_x, route_y)
            corner2 = (to_x, route_y)
            end = self.connection_point_towards(to_node, to_x, route_y)
            return [start, corner1, corner2, end]
        mid_x = (from_x + to_x) / 2.0
        mid_y = to_node.y
        start = self.connection_point_towards(from_node, mid_x, mid_y)
        end = self.connection_point_towards(to_node, mid_x, mid_y)
        return _dedup_consecutive([start, (mid_x, mid_y), end])

    def compute_vertical_edge_with_obstacles(
        self,
        from_node: LayoutNode,
        to_node: LayoutNode,
        obstacles: list[LayoutNode],
    ) -> list[tuple[float, float]]:
        """Orthogonal router for top/bottom-flowing edges (with detours).

        Mirrors grok ``compute_vertical_edge_with_obstacles`` -- the vertical
        symmetric twin of :meth:`compute_horizontal_edge_with_obstacles`.
        """
        travel_down = to_node.y > from_node.y
        from_y = from_node.y + (from_node.height / 2.0) * (1.0 if travel_down else -1.0)
        to_y = to_node.y + (to_node.height / 2.0) * (-1.0 if travel_down else 1.0)
        min_x = min(from_node.x, to_node.x)
        max_x = max(from_node.x, to_node.x)
        min_y = min(from_y, to_y)
        max_y = max(from_y, to_y)
        blocking = None
        for obs in obstacles:
            obs_left = obs.x - obs.width / 2.0 - 10.0
            obs_right = obs.x + obs.width / 2.0 + 10.0
            obs_top = obs.y - obs.height / 2.0 - 10.0
            obs_bottom = obs.y + obs.height / 2.0 + 10.0
            if obs_left < max_x and obs_right > min_x and obs_top < max_y and obs_bottom > min_y:
                blocking = obs
                break
        if blocking is not None:
            obs_left = blocking.x - blocking.width / 2.0
            obs_right = blocking.x + blocking.width / 2.0
            route_left = abs(from_node.x - obs_left) < abs(from_node.x - obs_right)
            route_x = obs_left - 30.0 if route_left else obs_right + 30.0
            start = self.connection_point_towards(from_node, route_x, from_y)
            corner1 = (route_x, from_y)
            corner2 = (route_x, to_y)
            end = self.connection_point_towards(to_node, route_x, to_y)
            return [start, corner1, corner2, end]
        mid_y = (from_y + to_y) / 2.0
        mid_x = to_node.x
        start = self.connection_point_towards(from_node, mid_x, mid_y)
        end = self.connection_point_towards(to_node, mid_x, mid_y)
        return _dedup_consecutive([start, (mid_x, mid_y), end])

    def compute_back_edge_points_simple(
        self, from_node: LayoutNode, to_node: LayoutNode, is_vertical: bool
    ) -> list[tuple[float, float]]:
        """U-shaped detour for back edges (simple variant).

        Mirrors grok ``compute_back_edge_points_simple``. The detour sits
        ``60px`` past the outer edge of the wider/taller endpoint, on the side
        the flow came from.
        """
        offset = 60.0
        if is_vertical:
            side_x = (
                max(from_node.x, to_node.x)
                + max(from_node.width, to_node.width) / 2.0
                + offset
            )
            start = (from_node.x + from_node.width / 2.0, from_node.y)
            end = (to_node.x + to_node.width / 2.0, to_node.y)
            return LayoutEngine.build_smooth_u_path(start, end, side_x, True)
        below_y = (
            max(from_node.y, to_node.y)
            + max(from_node.height, to_node.height) / 2.0
            + offset
        )
        start = (from_node.x, from_node.y + from_node.height / 2.0)
        end = (to_node.x, to_node.y + to_node.height / 2.0)
        return LayoutEngine.build_smooth_u_path(start, end, below_y, False)

    def compute_back_edge_points(
        self,
        from_node: LayoutNode,
        to_node: LayoutNode,
        is_vertical: bool,
        all_nodes: dict[str, LayoutNode],
    ) -> list[tuple[float, float]]:
        """U-shaped detour for back edges (obstacle-aware variant).

        Mirrors grok ``compute_back_edge_points``. Vertical layouts route left
        or right of the endpoints depending on which side ``from`` sits; the
        horizontal branch scans every node overlapping the column and picks the
        nearer of the above/below bands.
        """
        margin = 30.0
        if is_vertical:
            max_right = max(
                from_node.x + from_node.width / 2.0,
                to_node.x + to_node.width / 2.0,
            )
            min_left = min(
                from_node.x - from_node.width / 2.0,
                to_node.x - to_node.width / 2.0,
            )
            center_x = (from_node.x + to_node.x) / 2.0
            side_x = max_right + margin if from_node.x >= to_node.x else min_left - margin
            if side_x > center_x:
                start = (from_node.x + from_node.width / 2.0, from_node.y)
                end = (to_node.x + to_node.width / 2.0, to_node.y)
            else:
                start = (from_node.x - from_node.width / 2.0, from_node.y)
                end = (to_node.x - to_node.width / 2.0, to_node.y)
            return LayoutEngine.build_smooth_u_path(start, end, side_x, True)
        min_x = min(from_node.x, to_node.x)
        max_x = max(from_node.x, to_node.x)
        max_bottom = max(
            from_node.y + from_node.height / 2.0,
            to_node.y + to_node.height / 2.0,
        )
        min_top = min(
            from_node.y - from_node.height / 2.0,
            to_node.y - to_node.height / 2.0,
        )
        for node in all_nodes.values():
            node_left = node.x - node.width / 2.0
            node_right = node.x + node.width / 2.0
            if node_right >= min_x - margin and node_left <= max_x + margin:
                max_bottom = max(max_bottom, node.y + node.height / 2.0)
                min_top = min(min_top, node.y - node.height / 2.0)
        below_y = max_bottom + margin
        above_y = min_top - margin
        center_y = (from_node.y + to_node.y) / 2.0
        if abs(below_y - center_y) <= abs(above_y - center_y):
            route_y = below_y
        else:
            route_y = above_y
        if route_y > center_y:
            start = (from_node.x, from_node.y + from_node.height / 2.0)
            end = (to_node.x, to_node.y + to_node.height / 2.0)
        else:
            start = (from_node.x, from_node.y - from_node.height / 2.0)
            end = (to_node.x, to_node.y - to_node.height / 2.0)
        return LayoutEngine.build_smooth_u_path(start, end, route_y, False)

    def straighten_if_aligned(
        self,
        dagre_points: list[tuple[float, float]],
        from_node: LayoutNode,
        to_node: LayoutNode,
        is_vertical: bool,
        all_nodes: dict[str, LayoutNode],
    ) -> list[tuple[float, float]]:
        """Collapse a near-aligned dagre route to a straight 2-point segment.

        Mirrors grok ``straighten_if_aligned``. Only applied when the endpoints
        align within ``15px`` on the cross axis and the straight line clears
        every other node; otherwise the dagre route is returned unchanged.
        """
        tolerance = 15.0
        are_aligned = (
            abs(from_node.x - to_node.x) < tolerance
            if is_vertical
            else abs(from_node.y - to_node.y) < tolerance
        )
        if are_aligned and len(dagre_points) >= 2:
            start = dagre_points[0] if dagre_points else (from_node.x, from_node.y)
            end = dagre_points[-1] if dagre_points else (to_node.x, to_node.y)
            if is_vertical:
                avg_x = (from_node.x + to_node.x) / 2.0
                candidate = [(avg_x, start[1]), (avg_x, end[1])]
            else:
                avg_y = (from_node.y + to_node.y) / 2.0
                candidate = [(start[0], avg_y), (end[0], avg_y)]
            if self.edge_crosses_any_node(candidate, from_node, to_node, all_nodes):
                return list(dagre_points)
            return candidate
        return list(dagre_points)

    def edge_crosses_any_node(
        self,
        points: list[tuple[float, float]],
        from_node: LayoutNode,
        to_node: LayoutNode,
        all_nodes: dict[str, LayoutNode],
    ) -> bool:
        """Whether the straight line between the first/last point crosses a node.

        Mirrors grok ``edge_crosses_any_node``. The path is approximated by the
        chord between its endpoints; every node except ``from``/``to`` is tested
        with a ``5px`` margin expansion.
        """
        if len(points) < 2:
            return False
        x1, y1 = points[0]
        x2, y2 = points[len(points) - 1]
        margin = 5.0
        for node in all_nodes.values():
            if node.id == from_node.id or node.id == to_node.id:
                continue
            left = node.x - node.width / 2.0 - margin
            right = node.x + node.width / 2.0 + margin
            top = node.y - node.height / 2.0 - margin
            bottom = node.y + node.height / 2.0 + margin
            if LayoutEngine.line_intersect_rect(
                (x1, y1), (x2, y2), (left, top), (right, bottom)
            ):
                return True
        return False

    @staticmethod
    def line_intersect_rect(
        p1: tuple[float, float],
        p2: tuple[float, float],
        rect_min: tuple[float, float],
        rect_max: tuple[float, float],
    ) -> bool:
        """Test whether segment ``p1``-``p2`` intersects an axis-aligned rect.

        Mirrors grok ``line_intersect_rect`` (associated function). Same-side
        rejection first, then inside-endpoint shortcut, then a 4-edge segment
        intersection sweep via the cross-product parameterisation.
        """
        x1, y1 = p1
        x2, y2 = p2
        left, top = rect_min
        right, bottom = rect_max
        # Same-side rejection: both endpoints outside the same edge.
        if (x1 < left and x2 < left) or (x1 > right and x2 > right) or (
            y1 < top and y2 < top
        ) or (y1 > bottom and y2 > bottom):
            return False
        # Either endpoint inside the rect -> intersection.
        if left <= x1 <= right and top <= y1 <= bottom:
            return True
        if left <= x2 <= right and top <= y2 <= bottom:
            return True
        dx = x2 - x1
        dy = y2 - y1
        edges = (
            (left, top, left, bottom),      # left edge
            (right, top, right, bottom),    # right edge
            (left, top, right, top),        # top edge
            (left, bottom, right, bottom),  # bottom edge
        )
        for ex1, ey1, ex2, ey2 in edges:
            edx = ex2 - ex1
            edy = ey2 - ey1
            denom = dx * edy - dy * edx
            if abs(denom) < 1e-10:
                continue  # parallel
            t = ((ex1 - x1) * edy - (ey1 - y1) * edx) / denom
            u = ((ex1 - x1) * dy - (ey1 - y1) * dx) / denom
            if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
                return True
        return False

    @staticmethod
    def build_smooth_u_path(
        start: tuple[float, float],
        end: tuple[float, float],
        route_coord: float,
        is_vertical: bool,
    ) -> list[tuple[float, float]]:
        """Build a 9-point smooth U-shaped detour poly-line.

        Mirrors grok ``build_smooth_u_path`` (associated function). Each leg
        carries a ``0.3``-fraction cubic-approximation curve (3 control points)
        onto the routing rail, which the rail segment spans at the mid-point.
        """
        if is_vertical:
            side_x = route_coord
            total_height = abs(start[1] - end[1])
            curve_fraction = 0.3
            curve_height = total_height * curve_fraction
            mid_y = (start[1] + end[1]) / 2.0
            top_curve_end_y = start[1] - curve_height
            bottom_curve_start_y = end[1] + curve_height
            return [
                start,
                (start[0], start[1] - curve_height * 0.33),
                (side_x, top_curve_end_y + curve_height * 0.33),
                (side_x, top_curve_end_y),
                (side_x, mid_y),
                (side_x, bottom_curve_start_y),
                (side_x, bottom_curve_start_y - curve_height * 0.33),
                (end[0], end[1] + curve_height * 0.33),
                end,
            ]
        below_y = route_coord
        total_width = abs(start[0] - end[0])
        curve_fraction = 0.3
        curve_width = total_width * curve_fraction
        mid_x = (start[0] + end[0]) / 2.0
        left_curve_end_x = start[0] - curve_width
        right_curve_start_x = end[0] + curve_width
        return [
            start,
            (start[0] - curve_width * 0.33, start[1]),
            (left_curve_end_x + curve_width * 0.33, below_y),
            (left_curve_end_x, below_y),
            (mid_x, below_y),
            (right_curve_start_x, below_y),
            (right_curve_start_x - curve_width * 0.33, below_y),
            (end[0] + curve_width * 0.33, end[1]),
            end,
        ]

    @staticmethod
    def trim_cluster_interior_points(
        points: list[tuple[float, float]],
        from_node: LayoutNode,
        to_node: LayoutNode,
        from_is_cluster: bool,
        to_is_cluster: bool,
    ) -> None:
        """Drop route points inside a cluster endpoint's rect (in place).

        Mirrors grok ``trim_cluster_interior_points`` (associated function,
        ``&mut Vec``). The to-side truncates after the last outside point plus
        one interior transition; the from-side drops leading interior points
        keeping one transition before the first outside point. The poly-line is
        never reduced below two points.
        """

        def inside(node: LayoutNode, point: tuple[float, float]) -> bool:
            hw = node.width / 2.0
            hh = node.height / 2.0
            px, py = point
            return (
                px > node.x - hw and px < node.x + hw and py > node.y - hh and py < node.y + hh
            )

        if to_is_cluster and len(points) > 2:
            last_out = None
            for idx in range(len(points) - 1, -1, -1):
                if not inside(to_node, points[idx]):
                    last_out = idx
                    break
            if last_out is not None:
                keep = min(last_out + 2, len(points))
                del points[keep:]

        if from_is_cluster and len(points) > 2:
            first_out = None
            for idx in range(len(points)):
                if not inside(from_node, points[idx]):
                    first_out = idx
                    break
            if first_out is not None:
                drop = max(first_out - 1, 0)
                if drop > 0:
                    del points[0:drop]

    def clip_edge_to_boundaries(
        self,
        points: list[tuple[float, float]],
        from_node: LayoutNode,
        to_node: LayoutNode,
    ) -> None:
        """Snap an edge's endpoints onto the from/to node borders (in place).

        Mirrors grok ``clip_edge_to_boundaries`` (``&mut [..]``). The start is
        re-anchored towards the second point; the end is handled by
        :meth:`clip_edge_end_only`.
        """
        if len(points) < 2:
            return
        second_point = points[1]
        new_start = self.connection_point_on_node(from_node, second_point[0], second_point[1])
        points[0] = new_start
        self.clip_edge_end_only(points, to_node)

    def clip_edge_end_only(
        self, points: list[tuple[float, float]], to_node: LayoutNode
    ) -> None:
        """Snap only the final edge point onto the ``to_node`` border (in place).

        Mirrors grok ``clip_edge_end_only`` (``&mut [..]``). The end is
        re-anchored towards the second-to-last point.
        """
        if len(points) < 2:
            return
        length = len(points)
        second_last = points[length - 2]
        new_end = self.connection_point_on_node(to_node, second_last[0], second_last[1])
        points[length - 1] = new_end

    def connection_point_on_node(
        self, node: LayoutNode, from_x: float, from_y: float
    ) -> tuple[float, float]:
        """Border point of ``node`` closest to the external point (``from``).

        Mirrors grok ``connection_point_on_node``. Shape-aware: circles project
        along the radius, diamonds project onto the rhombus edge, the default
        box shape scales the half-extents to the nearer axis. Direction is
        inbound (towards ``node`` from ``from``).
        """
        dx = node.x - from_x
        dy = node.y - from_y
        if node.shape in (NodeShape.Circle, NodeShape.StartState, NodeShape.EndState):
            r = min(node.width, node.height) / 2.0
            length = math.sqrt(dx * dx + dy * dy)
            if length == 0.0:
                return (node.x, node.y - r)
            return (node.x - r * dx / length, node.y - r * dy / length)
        if node.shape == NodeShape.Diamond:
            hw = node.width / 2.0
            hh = node.height / 2.0
            denom = abs(dx) / hw + abs(dy) / hh
            if denom == 0.0:
                return (node.x, node.y - hh)
            t = 1.0 / denom
            return (node.x - dx * t, node.y - dy * t)
        hw = node.width / 2.0
        hh = node.height / 2.0
        denom_x = abs(dx) / hw if hw > 0.0 else 0.0
        denom_y = abs(dy) / hh if hh > 0.0 else 0.0
        denom = max(denom_x, denom_y)
        if denom == 0.0:
            return (node.x, node.y - hh)
        t = 1.0 / denom
        return (node.x - dx * t, node.y - dy * t)

    def connection_point_towards(
        self, node: LayoutNode, target_x: float, target_y: float
    ) -> tuple[float, float]:
        """Border point of ``node`` closest to the external point (``target``).

        Mirrors grok ``connection_point_towards`` -- the outbound twin of
        :meth:`connection_point_on_node` (direction is away from ``node``
        towards ``target``).
        """
        dx = target_x - node.x
        dy = target_y - node.y
        if node.shape in (NodeShape.Circle, NodeShape.StartState, NodeShape.EndState):
            r = min(node.width, node.height) / 2.0
            length = math.sqrt(dx * dx + dy * dy)
            if length == 0.0:
                return (node.x + r, node.y)
            return (node.x + r * dx / length, node.y + r * dy / length)
        if node.shape == NodeShape.Diamond:
            hw = node.width / 2.0
            hh = node.height / 2.0
            denom = abs(dx) / hw + abs(dy) / hh
            if denom == 0.0:
                return (node.x + hw, node.y)
            t = 1.0 / denom
            return (node.x + dx * t, node.y + dy * t)
        hw = node.width / 2.0
        hh = node.height / 2.0
        denom_x = abs(dx) / hw if hw > 0.0 else 0.0
        denom_y = abs(dy) / hh if hh > 0.0 else 0.0
        denom = max(denom_x, denom_y)
        if denom == 0.0:
            return (node.x + hw, node.y)
        t = 1.0 / denom
        return (node.x + dx * t, node.y + dy * t)

    def compute_with_dagre(self, center_subgraph_nodes: bool) -> LayoutResult:
        """Solve the flowchart layout end-to-end via the dagre bridge.

        The orchestrator wiring the R274c--e helpers into grok's pipeline:
        spacing -> direction -> back-edge detection -> dagre graph build + solve
        -> layout extraction -> state-diagram specials (snap + align) -> subgraph
        centering -> bounds -> node/edge/subgraph assembly -> global margin shift
        -> viewport sizing.

        Three Python-specific adaptations (each with a one-line rationale):

        * ``analyze_clusters`` omitted -- grok's ``build_dagre_graph`` takes
          ``_cluster_analysis`` (leading underscore = Rust unused parameter);
          cluster routing reads ``self.node_to_subgraph`` / ``self.subgraphs``
          instead, so the analysis is dead input even in grok. R274c already
          dropped the parameter (4-arg signature).
        * Tuple immutability -- grok mutates ``point.0 += x_shift`` in place on
          ``&mut (f64, f64)``; Python tuples are immutable, so edge-point /
          label-pos shifts rebuild the container (``edge.points = [...]``,
          ``edge.label_pos = (...)``).
        * ``min`` / ``max`` chains -- grok's ``a.min(b).min(c)`` folds into the
          running ``min(min_x, v)`` / ``max(max_x, v)`` loop form.
        """
        node_sep, rank_sep = self.compute_spacing()
        if self.graph.direction is GraphDirection.TopToBottom:
            rank_dir = "tb"
        elif self.graph.direction is GraphDirection.BottomToTop:
            rank_dir = "bt"
        elif self.graph.direction is GraphDirection.LeftToRight:
            rank_dir = "lr"
        else:
            rank_dir = "rl"

        back_edges = self.detect_back_edges()
        dagre_graph, edge_map = self.build_dagre_graph(
            rank_dir, node_sep, rank_sep, back_edges
        )
        dagre_layout(dagre_graph)
        positions, edge_points, edge_label_positions = self.extract_layout_from_dagre(
            dagre_graph, edge_map
        )
        if self.is_state_diagram:
            self.snap_state_ranks(positions, back_edges)
            self.align_state_terminal_singletons(positions, back_edges)
        is_vertical = self.graph.direction in (
            GraphDirection.TopToBottom,
            GraphDirection.BottomToTop,
        )
        if center_subgraph_nodes:
            self.center_nodes_in_subgraphs(positions, is_vertical)
        width, height = self.compute_bounds(positions)
        layout_nodes: dict[str, LayoutNode] = {}
        for node_id, (x, y) in positions.items():
            info = self.nodes.get(node_id)
            if info is None:
                continue
            fill_color, stroke_color = self.get_node_colors(node_id)
            layout_nodes[node_id] = LayoutNode(
                id=node_id,
                x=x,
                y=y,
                width=info.width,
                height=info.height,
                shape=info.shape,
                label=info.label,
                fill_color=fill_color,
                stroke_color=stroke_color,
            )
        layout_subgraphs = self.compute_subgraph_bounds(layout_nodes, SUBGRAPH_PADDING)
        layout_edges: list[LayoutEdge] = []
        for idx, edge in enumerate(self.edges):
            from_node = self.layout_endpoint_node(layout_nodes, layout_subgraphs, edge.from_)
            if from_node is None:
                continue
            to_node = self.layout_endpoint_node(layout_nodes, layout_subgraphs, edge.to)
            if to_node is None:
                continue
            is_back_edge = self.is_back_edge(from_node, to_node)
            dagre_points = edge_points.get(idx)
            if dagre_points is None:
                dagre_points = self.compute_edge_points_with_obstacles(
                    from_node, to_node, layout_nodes
                )
            if is_back_edge:
                points = self.compute_back_edge_points(
                    from_node, to_node, is_vertical, layout_nodes
                )
            else:
                points = self.straighten_if_aligned(
                    dagre_points, from_node, to_node, is_vertical, layout_nodes
                )
            # Cluster-target edges: dagre routes to an interior member node, so
            # the polyline tail dives inside the cluster rect and the clip then
            # curls it back to the boundary. Drop the interior points first so
            # the edge approaches the cluster boundary monotonically from outside.
            if not is_back_edge:
                from_is_cluster = self.is_subgraph_id(edge.from_)
                to_is_cluster = self.is_subgraph_id(edge.to)
                if from_is_cluster or to_is_cluster:
                    LayoutEngine.trim_cluster_interior_points(
                        points,
                        from_node,
                        to_node,
                        from_is_cluster,
                        to_is_cluster,
                    )
            self.clip_edge_to_boundaries(points, from_node, to_node)

            label_pos: tuple[float, float] | None = None
            if edge.label is not None and edge.label.strip():
                if is_back_edge:
                    label_pos = LayoutEngine.edge_label_midpoint(points)
                else:
                    midpoint = LayoutEngine.edge_label_midpoint(points)
                    candidate = edge_label_positions.get(idx)
                    if candidate is None:
                        label_pos = midpoint
                    else:
                        # Keep the dagre-suggested label only when it sits within
                        # the edge polyline's bounding box (+/-8px slack); else
                        # fall back to the midpoint. Empty points -> inf box ->
                        # candidate rejected (matches grok's INFINITY init).
                        box_min_x = math.inf
                        box_max_x = -math.inf
                        box_min_y = math.inf
                        box_max_y = -math.inf
                        for px, py in points:
                            box_min_x = min(box_min_x, px)
                            box_max_x = max(box_max_x, px)
                            box_min_y = min(box_min_y, py)
                            box_max_y = max(box_max_y, py)
                        cx, cy = candidate
                        if (
                            box_min_x - 8.0 <= cx <= box_max_x + 8.0
                            and box_min_y - 8.0 <= cy <= box_max_y + 8.0
                        ):
                            label_pos = candidate
                        else:
                            label_pos = midpoint

            layout_edges.append(
                LayoutEdge(
                    from_=edge.from_,
                    to=edge.to,
                    label=edge.label,
                    style=edge.style,
                    points=points,
                    label_pos=label_pos,
                )
            )

        min_x = math.inf
        min_y = math.inf
        for sg in layout_subgraphs:
            min_x = min(min_x, sg.x)
            min_y = min(min_y, sg.y)
        for node in layout_nodes.values():
            min_x = min(min_x, node.x - node.width / 2.0)
            min_y = min(min_y, node.y - node.height / 2.0)
        for edge in layout_edges:
            label_bounds = self.edge_label_bounds(edge)
            if label_bounds is not None:
                label_x, label_y, label_w, label_h = label_bounds
                min_x = min(min_x, label_x - label_w / 2.0)
                min_y = min(min_y, label_y - label_h / 2.0)
        x_shift = MARGIN - min_x if min_x < MARGIN else 0.0
        y_shift = MARGIN - min_y if min_y < MARGIN else 0.0

        # Global margin shift: grok shadow-rebuilds the three containers via
        # into_iter().map().collect(); Python mutates the mutable dataclasses
        # in place (LayoutNode/LayoutSubgraph) and rebuilds the tuple lists
        # (LayoutEdge.points / label_pos) since tuples are immutable.
        for node in layout_nodes.values():
            node.x += x_shift
            node.y += y_shift
        for edge in layout_edges:
            edge.points = [(p[0] + x_shift, p[1] + y_shift) for p in edge.points]
            if edge.label_pos is not None:
                edge.label_pos = (
                    edge.label_pos[0] + x_shift,
                    edge.label_pos[1] + y_shift,
                )
        for sg in layout_subgraphs:
            sg.x += x_shift
            sg.y += y_shift

        final_width = width + x_shift
        final_height = height + y_shift
        for sg in layout_subgraphs:
            final_width = max(final_width, sg.x + sg.width + MARGIN)
            final_height = max(final_height, sg.y + sg.height + MARGIN)
        for edge in layout_edges:
            for px, py in edge.points:
                final_width = max(final_width, px + MARGIN)
                final_height = max(final_height, py + MARGIN)
            label_bounds = self.edge_label_bounds(edge)
            if label_bounds is not None:
                label_x, label_y, label_w, label_h = label_bounds
                final_width = max(final_width, label_x + label_w / 2.0 + MARGIN)
                final_height = max(final_height, label_y + label_h / 2.0 + MARGIN)

        return LayoutResult(
            nodes=layout_nodes,
            edges=layout_edges,
            subgraphs=layout_subgraphs,
            width=final_width,
            height=final_height,
        )


def _dedup_consecutive(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop consecutive duplicate points (mirrors Rust ``Vec::dedup``).

    Python lists have no stdlib consecutive-de-duplication; this private module
    helper bridges grok's ``points.dedup()`` calls in the obstacle routers.
    """
    result: list[tuple[float, float]] = []
    for point in points:
        if not result or result[-1] != point:
            result.append(point)
    return result


def compute_layout(graph: FlowchartGraph) -> LayoutResult:
    """Solve ``graph`` layout with default options (mirrors grok ``compute_layout``).

    The crate-root public entry consumed by ``svg_renderer`` -- builds the engine
    with default :class:`FlowchartLayoutOptions` and runs the full dagre pipeline
    with subgraph node centering enabled.
    """
    engine = LayoutEngine(graph)
    return engine.compute_with_dagre(True)


def compute_layout_with_config(
    graph: FlowchartGraph, config: RenderConfig
) -> LayoutResult:
    """Solve ``graph`` layout driven by a :class:`RenderConfig` (grok twin).

    The crate-root public entry consumed when front-matter / render options flow
    in from the caller -- derives :class:`FlowchartLayoutOptions` from ``config``
    via :meth:`FlowchartLayoutOptions.from_render_config`, then runs the full
    dagre pipeline with subgraph node centering enabled.
    """
    engine = LayoutEngine(graph, FlowchartLayoutOptions.from_render_config(config))
    return engine.compute_with_dagre(True)


__all__ = [
    "LayoutEdge",
    "LayoutNode",
    "LayoutResult",
    "LayoutSubgraph",
    "compute_layout",
    "compute_layout_with_config",
]
