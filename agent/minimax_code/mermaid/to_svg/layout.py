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

from .ast import EdgeStyle, Node, NodeShape, Statement, Subgraph
from .config import RenderConfig
from .text_wrap import DEFAULT_FONT_SIZE, DEFAULT_WRAP_WIDTH

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


__all__ = [
    "LayoutEdge",
    "LayoutNode",
    "LayoutResult",
    "LayoutSubgraph",
]
