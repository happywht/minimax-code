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

from dataclasses import dataclass
from typing import TypeAlias

from .ast import (
    Edge,
    EdgeStyle,
    FlowchartGraph,
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


# === type aliases (grok ``type``, 4 stdlib) =================================
#
# The 5th grok alias ``type DagreGraph = Graph<GraphConfig, GraphNode,
# GraphEdge>`` is deferred to the dagre-bridge leaf (R274c) -- it is unused
# before that leaf and pulls in the ``dagre`` package types.

EdgeMap: TypeAlias = dict[int, tuple[str, str]]
PositionMap: TypeAlias = dict[str, tuple[float, float]]
EdgePointMap: TypeAlias = dict[int, list[tuple[float, float]]]
EdgeLabelPosMap: TypeAlias = dict[int, tuple[float, float]]


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


__all__ = [
    "LayoutEdge",
    "LayoutNode",
    "LayoutResult",
    "LayoutSubgraph",
]
