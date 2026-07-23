"""Mermaid-to-svg SVG emitter -- fusion of grok
``mermaid-to-svg/src/svg_renderer.rs``.

Direction (1) leaf 7 (sub-leaf R275a). The SVG renderer that turns a laid-out
flowchart (the R274 :class:`LayoutResult`) into the final SVG string. grok's
``svg_renderer.rs`` is a single ~990-line file; this Python port is sliced
across three sub-leaves to keep each migration unit reviewable:

* **R275a (this leaf)** -- the zero-dependency foundation: the 10 ``const``
  knobs, the private :class:`EdgeCurve` enum (+ ``from_mermaid_name``), the
  private :class:`SvgRenderOptions` dataclass (+ ``from_render_config``,
  consuming the R270 :class:`RenderConfig`), the :class:`SvgRenderer` actor
  dataclass, and the four self-contained emitters ``write_header`` /
  ``write_defs`` / ``write_footer`` / ``render_subgraph_background``. Every
  symbol here references only ``self`` fields / the ``subgraph`` argument /
  the resolved :class:`MermaidTheme` -- no R275b/c renderer method is called,
  so the foundation is independently testable.
* **R275b (next)** -- ``render_node`` shape dispatch + the 13 node shapes +
  the text emitters (``render_text`` / ``render_text_lines``) +
  ``render_subgraph_title`` (which calls ``render_text_lines``).
* **R275c (close)** -- edge geometry / path / label emitters + ``escape_xml``
  + the ``render`` orchestrator + the two public entry points
  (``render`` / ``render_with_config``) + ``__all__``.

Internal module
---------------

grok declares ``mod svg_renderer;`` (private -- ``lib.rs`` line 26, NOT
``pub mod``) and never ``pub use``-s it at the crate root; ``lib.rs`` calls
``svg_renderer::render`` / ``svg_renderer::render_with_config`` directly.
This module mirrors that: it is reachable by deep path but NOT re-exported
through the ``to_svg`` barrel (the barrel tracks grok's crate-root public
surface, which omits ``svg_renderer``). The ``__all__`` is established at
the R275c close (the two public entry points); until then this module has no
``__all__``, matching the :mod:`.layout` internal-module precedent (its
``__all__`` was written only once the module reached feature completeness).

Dependency mapping (Rust crate -> Python stdlib)
-------------------------------------------------

* ``f64`` -> ``float``; ``&str`` -> ``str``; ``bool`` -> ``bool``.
* ``struct SvgRenderOptions { ... }`` with ``impl Default`` + a ``from_*``
  constructor -> :class:`SvgRenderOptions` dataclass whose field defaults ARE
  the ``Default`` impl (``SvgRenderOptions()`` == grok
  ``SvgRenderOptions::default()``); ``from_render_config`` is a classmethod.
* ``struct SvgRenderer<'a> { ... output: String }`` -> :class:`SvgRenderer`
  dataclass with ``output: str = ""`` (grok's ``output: String::new()``);
  grok's ``SvgRenderer::new`` 5-arg constructor maps to the dataclass
  ``__init__`` (``output`` takes its default).
* ``format!("{:.0}", x)`` / ``format!("{:.1}", x)`` -> ``f"{x:.0f}"`` /
  ``f"{x:.1f}"`` (fixed-point, 0 / 1 decimals).

Two grok quirks are preserved verbatim (zero-semantic clone):

1. ``render_subgraph_background`` ends its ``<rect/>`` with a *literal*
   ``\\n`` (backslash + ``n``), not a newline -- grok wrote
   ``r#"...stroke-width=\"1\"/>\\n"#`` and a raw string literal does not
   process ``\\n``, so the emitted bytes are the two characters ``\\n``.
   This is reproduced with the Python literal ``"\\\\n"``.
2. ``write_header`` / ``write_defs`` end with a *real* trailing newline (the
   raw string's own final line break before the closing ``"#``); reproduced
   with ``"\\n"``.

YAGNI boundary: ``format!`` fixed-point rounding (``{:.0}`` on a ``.5``) uses
round-half-to-even in Python vs round-half-away-from-zero in Rust; flowchart
width / height / subgraph geometry are integer-valued pixel bounds in
practice, so the divergence is unreachable. Noted for completeness, not
compensated.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

from .ast import EdgeStyle, NodeShape
from .config import RenderConfig
from .layout import LayoutEdge, LayoutNode, LayoutResult, LayoutSubgraph
from .text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_LINE_HEIGHT,
    DEFAULT_WRAP_WIDTH,
    line_width_words,
    measure_wrapped_lines_with_font_size,
    scale_char_width,
    wrap_text_lines,
    wrapped_text_height_with_font_size,
)
from .theme import MermaidTheme

# === private constants (grok ``const``, not ``pub``) ========================
#
# The 10 file-private knobs grok declares between the imports and the first
# ``pub fn``. Edge / label / state-diagram sizing parameters that the R275b/c
# emitters consume; only ``DEFAULT_FONT_FAMILY`` feeds R275a's
# ``SvgRenderOptions::default``.

# Arrowhead marker tip offset (refX polish); used by R275c edge paths.
EDGE_ARROWHEAD_OFFSET: float = 4.0
# Thick-edge arrowhead offset: markerWidth 11 * (10 - 5) / 10 = 5.5.
EDGE_ARROWHEAD_OFFSET_THICK: float = 5.5
# Edge-label character width tracks the global text-wrap default.
EDGE_LABEL_CHAR_WIDTH: float = DEFAULT_CHAR_WIDTH
# Edge-label background rect horizontal / vertical padding (R275c).
EDGE_LABEL_PADDING_H: float = 2.0
EDGE_LABEL_PADDING_V: float = 2.0
# Edge-label background rect opacity (R275c).
EDGE_LABEL_BG_OPACITY: float = 0.8
# Subgraph title vertical offset above the cluster top edge (R275b).
SUBGRAPH_TITLE_TOP_MARGIN: float = 0.0
# State-diagram label character width (R275b state shapes).
STATE_CHAR_WIDTH: float = 6.7
# Default label font family (mermaid's verdana stack); feeds SvgRenderOptions.
DEFAULT_FONT_FAMILY: str = "Trebuchet MS, verdana, arial, sans-serif"

# f64::EPSILON -- Rust stdlib machine epsilon for binary64 (2.2204460492503131e-16).
# CPython's float IS IEEE 754 binary64 (identical to Rust f64), so
# sys.float_info.epsilon is the exact same value. Used by the corner-detection
# equality tests in fix_corners / corner_positions (R275c).
_F64_EPSILON: float = sys.float_info.epsilon


# === EdgeCurve (private enum) ===============================================
#
# grok ``#[derive(Debug, Clone, Copy, PartialEq, Eq)] enum EdgeCurve`` -- a
# private two-variant tag selecting the edge path interpolation (basis spline
# vs straight segments). ``from_mermaid_name`` mirrors grok's associated fn:
# the name "linear" (case-insensitive) selects Linear; anything else falls
# back to Basis (mermaid's default curve).


class EdgeCurve(Enum):
    """Edge path interpolation style (grok private ``enum EdgeCurve``).

    ``Basis`` -- a smooth basis spline (mermaid's default ``basis`` curve).
    ``Linear`` -- straight line segments between waypoints.
    """

    Basis = "basis"
    Linear = "linear"

    @classmethod
    def from_mermaid_name(cls, name: str) -> EdgeCurve:
        """Map a mermaid curve name to an :class:`EdgeCurve` variant.

        Mirrors grok ``EdgeCurve::from_mermaid_name``: ``"linear"`` (matched
        ASCII-case-insensitively, like ``eq_ignore_ascii_case``) selects
        :attr:`Linear`; any other name falls back to :attr:`Basis`.
        """
        if name.lower() == "linear":
            return cls.Linear
        return cls.Basis


# === SvgRenderOptions (private dataclass) ===================================
#
# grok ``struct SvgRenderOptions`` + ``impl Default`` + ``fn from_render_config``.
# The four resolved render knobs the emitter threads through every label /
# edge measurement. ``Default`` is reproduced by the dataclass field defaults
# (``SvgRenderOptions()`` == grok ``SvgRenderOptions::default()``);
# ``from_render_config`` overlays the R270 :class:`RenderConfig` values onto
# those defaults, falling back per-field when the config omits a knob.


@dataclass
class SvgRenderOptions:
    """Resolved SVG render knobs (grok private ``SvgRenderOptions``).

    The field defaults ARE grok's ``Default`` impl (so ``SvgRenderOptions()``
    reconstructs the default without a separate factory). ``from_render_config``
    overlays an R270 :class:`RenderConfig`, mirroring grok's per-field
    ``unwrap_or(default)`` fallback.
    """

    font_family: str = DEFAULT_FONT_FAMILY
    font_size: float = DEFAULT_FONT_SIZE
    wrapping_width: float = DEFAULT_WRAP_WIDTH
    edge_curve: EdgeCurve = EdgeCurve.Basis

    @classmethod
    def from_render_config(cls, config: RenderConfig) -> SvgRenderOptions:
        """Build options from an R270 :class:`RenderConfig` (grok
        ``SvgRenderOptions::from_render_config``).

        Each knob takes its config value when present, else the default.
        ``font_family`` reads :attr:`RenderConfig.font_family`;
        ``font_size`` reads :meth:`RenderConfig.font_size_px`;
        ``wrapping_width`` widens ``flowchart.wrapping_width`` (an int) to a
        float; ``edge_curve`` maps ``flowchart.curve`` via
        :meth:`EdgeCurve.from_mermaid_name`.
        """
        default = cls()
        font_family = (
            config.font_family if config.font_family is not None else default.font_family
        )
        font_size_px = config.font_size_px()
        font_size = font_size_px if font_size_px is not None else default.font_size
        wrapping_width = (
            float(config.flowchart.wrapping_width)
            if config.flowchart.wrapping_width is not None
            else default.wrapping_width
        )
        edge_curve = (
            EdgeCurve.from_mermaid_name(config.flowchart.curve)
            if config.flowchart.curve is not None
            else default.edge_curve
        )
        return cls(
            font_family=font_family,
            font_size=font_size,
            wrapping_width=wrapping_width,
            edge_curve=edge_curve,
        )


# === SvgRenderer actor (private dataclass) ==================================
#
# grok ``struct SvgRenderer<'a> { width, height, theme: &'a MermaidTheme,
# is_state_diagram, options, output: String }`` + ``fn new``. The mutable
# accumulator that walks the layout and appends SVG fragments to ``output``.
# grok's 5-arg ``new`` (output always starts empty) maps to the dataclass
# ``__init__`` with ``output`` taking its ``""`` default.


@dataclass
class SvgRenderer:
    """The SVG accumulator actor (grok private ``SvgRenderer``).

    ``output`` is the running SVG string; every ``write_*`` /
    ``render_*`` method appends to it. Construct with the 5 layout/theme
    arguments (``output`` defaults to empty, matching grok's ``new``).
    """

    width: float
    height: float
    theme: MermaidTheme
    is_state_diagram: bool
    options: SvgRenderOptions
    output: str = ""

    def write_header(self) -> None:
        """Emit the XML declaration, ``<svg>`` root, and background rect.

        Mirrors grok ``write_header``: sizing via the viewBox (mermaid
        ``setupGraphViewviewbox``), background via the SVG ``style`` attribute
        (mermaid-cli ``svg.style.backgroundColor``), plus an explicit
        background ``<rect>`` for rasterizers. Width/height render at 0
        decimals (``{:.0}`` -> ``:.0f``).
        """
        self.output += (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg width="{self.width:.0f}" height="{self.height:.0f}" '
            f'viewBox="0 0 {self.width:.0f} {self.height:.0f}" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'style="background-color: {self.theme.background};">\n'
            f'<rect x="0" y="0" width="{self.width:.0f}" height="{self.height:.0f}" '
            f'fill="{self.theme.background}" stroke="none"/>\n'
        )

    def write_defs(self) -> None:
        """Emit the ``<defs>`` block with the two arrowhead markers.

        Mirrors grok ``write_defs``: the default ``arrowhead`` marker
        (markerWidth 8) and the thick ``arrowhead-thick`` marker (markerWidth
        11), both filled/stroked with :attr:`theme.edge_color`. grok reuses
        the single positional arg for the thick marker's ``fill``/``stroke``
        (``{0}``); the named ``{ec}`` slot reproduces that reuse.
        """
        ec = self.theme.edge_color
        self.output += (
            "<defs>\n"
            '  <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="5" refY="5" '
            'orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 10 10">\n'
            f'    <path d="M 0 0 L 10 5 L 0 10 z" fill="{ec}" stroke="{ec}" stroke-width="1"/>\n'
            "  </marker>\n"
            '  <marker id="arrowhead-thick" markerWidth="11" markerHeight="11" refX="5" refY="5" '
            'orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 10 10">\n'
            f'    <path d="M 0 0 L 10 5 L 0 10 z" fill="{ec}" stroke="{ec}" stroke-width="1"/>\n'
            "  </marker>\n"
            "</defs>\n"
        )

    def write_footer(self) -> None:
        """Emit the closing ``</svg>`` tag (grok ``write_footer``)."""
        self.output += "</svg>\n"

    def render_subgraph_background(self, subgraph: LayoutSubgraph) -> None:
        """Emit a subgraph cluster's background ``<rect>`` (grok
        ``render_subgraph_background``).

        Coordinates / size render at 1 decimal (``{:.1}`` -> ``:.1f``); fill
        / stroke come from :attr:`theme.subgraph_fill` /
        :attr:`theme.subgraph_stroke`.

        Quirk preserved verbatim: grok's raw-string template ends with a
        *literal* ``\\n`` (backslash + ``n``), not a newline -- so the emitted
        rect is followed by the two characters ``\\n``, reproduced here with
        the ``"\\\\n"`` literal.
        """
        self.output += (
            f'<rect x="{subgraph.x:.1f}" y="{subgraph.y:.1f}" '
            f'width="{subgraph.width:.1f}" height="{subgraph.height:.1f}" '
            f'fill="{self.theme.subgraph_fill}" stroke="{self.theme.subgraph_stroke}" '
            'stroke-width="1"/>\\n'
        )

    def render_subgraph_title(self, subgraph: LayoutSubgraph) -> None:
        """Emit a subgraph cluster's title text (grok ``render_subgraph_title``).

        Wraps the title to the renderer's ``wrapping_width`` and centers it
        horizontally over the cluster; its vertical center is offset down by
        :data:`SUBGRAPH_TITLE_TOP_MARGIN` plus half the wrapped text height. A
        ``None`` title (or one that wraps to zero lines) emits nothing.
        """
        if subgraph.title is None:
            return
        char_width = scale_char_width(DEFAULT_CHAR_WIDTH, self.options.font_size)
        lines = wrap_text_lines(subgraph.title, self.options.wrapping_width, char_width)
        if not lines:
            return
        _, text_height = measure_wrapped_lines_with_font_size(
            lines, char_width, self.options.font_size
        )
        title_x = subgraph.x + subgraph.width / 2.0
        title_y = subgraph.y + SUBGRAPH_TITLE_TOP_MARGIN + text_height / 2.0
        self.render_text_lines(
            title_x,
            title_y,
            lines,
            self.options.font_size,
            DEFAULT_LINE_HEIGHT,
            self.theme.text_color,
        )

    def render_node(self, node: LayoutNode) -> None:
        """Dispatch a node to its shape-specific emitter (grok ``render_node``).

        Mirrors grok's exhaustive ``match node.shape`` over the 12
        :class:`NodeShape` variants: Rectangle / RoundedRectangle / Stadium all
        route to :meth:`render_rectangle` (differing only in ``rx``); each other
        variant routes to its own emitter.
        """
        shape = node.shape
        if shape is NodeShape.Rectangle:
            self.render_rectangle(node, 0.0)
        elif shape is NodeShape.RoundedRectangle:
            self.render_rectangle(node, 5.0)
        elif shape is NodeShape.Stadium:
            self.render_rectangle(node, node.height / 2.0)
        elif shape is NodeShape.Diamond:
            self.render_diamond(node)
        elif shape is NodeShape.Circle:
            self.render_circle(node)
        elif shape is NodeShape.StartState:
            self.render_start_state(node)
        elif shape is NodeShape.EndState:
            self.render_end_state(node)
        elif shape is NodeShape.ForkJoin:
            self.render_fork_join(node)
        elif shape is NodeShape.Hexagon:
            self.render_hexagon(node)
        elif shape is NodeShape.Cylinder:
            self.render_cylinder(node)
        elif shape is NodeShape.Subroutine:
            self.render_subroutine(node)
        elif shape is NodeShape.Asymmetric:
            self.render_asymmetric(node)
        else:  # pragma: no cover - exhaustive over NodeShape
            raise ValueError(f"unhandled NodeShape: {shape!r}")

    def render_rectangle(self, node: LayoutNode, rx: float) -> None:
        """Emit a rectangle node (grok ``render_rectangle``).

        The box is centered on ``(node.x, node.y)`` (top-left at
        ``x - w/2, y - h/2``); ``rx`` controls corner rounding (0 for
        Rectangle, 5 for RoundedRectangle, ``h/2`` for Stadium). Fill / stroke
        fall back to :attr:`theme.node_fill` / :attr:`node_stroke` when the
        node carries no explicit color.
        """
        x = node.x - node.width / 2.0
        y = node.y - node.height / 2.0
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        self.output += (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="{rx:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1"/>\n'
        )
        self.render_text(node.x, node.y, node.label)

    def render_start_state(self, node: LayoutNode) -> None:
        """Emit a state-diagram start state: a filled black circle (grok
        ``render_start_state``).

        Radius is half the smaller box dimension; fill / stroke both take
        :attr:`theme.edge_color` with a thicker 1.5 stroke.
        """
        r = min(node.width, node.height) / 2.0
        self.output += (
            f'<circle cx="{node.x:.1f}" cy="{node.y:.1f}" r="{r:.1f}" '
            f'fill="{self.theme.edge_color}" stroke="{self.theme.edge_color}" '
            f'stroke-width="1.5"/>\n'
        )

    def render_end_state(self, node: LayoutNode) -> None:
        """Emit a state-diagram end state: a double circle (grok
        ``render_end_state``).

        Outer circle filled with :attr:`theme.node_stroke` and stroked with
        :attr:`theme.background`; inner circle filled with
        :attr:`theme.background` and unstroked. The inner radius clamps to
        ``max(outer-4, outer*0.55)`` then ``min(_, outer-2)`` (grok's
        ``(outer-4).max(outer*0.55).min(outer-2)``).
        """
        outer_r = min(node.width, node.height) / 2.0
        inner_r = min(max(outer_r - 4.0, outer_r * 0.55), outer_r - 2.0)
        self.output += (
            f'<circle cx="{node.x:.1f}" cy="{node.y:.1f}" r="{outer_r:.1f}" '
            f'fill="{self.theme.node_stroke}" stroke="{self.theme.background}" '
            f'stroke-width="1"/>\n'
        )
        self.output += (
            f'<circle cx="{node.x:.1f}" cy="{node.y:.1f}" r="{inner_r:.1f}" '
            f'fill="{self.theme.background}" stroke="none"/>\n'
        )

    def render_fork_join(self, node: LayoutNode) -> None:
        """Emit a fork/join bar: a rect with literal ``rx="1"`` (grok
        ``render_fork_join``). Fill / stroke both take :attr:`theme.edge_color`.
        """
        x = node.x - node.width / 2.0
        y = node.y - node.height / 2.0
        self.output += (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="1" fill="{self.theme.edge_color}" '
            f'stroke="{self.theme.edge_color}" stroke-width="1"/>\n'
        )

    def render_diamond(self, node: LayoutNode) -> None:
        """Emit a diamond decision node: a 4-point polygon (grok
        ``render_diamond``). Points at top / right / bottom / left of the box.
        """
        hw = node.width / 2.0
        hh = node.height / 2.0
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        points = (
            f"{node.x:.1f},{node.y - hh:.1f} "
            f"{node.x + hw:.1f},{node.y:.1f} "
            f"{node.x:.1f},{node.y + hh:.1f} "
            f"{node.x - hw:.1f},{node.y:.1f}"
        )
        self.output += (
            f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>\n'
        )
        self.render_text(node.x, node.y, node.label)

    def render_circle(self, node: LayoutNode) -> None:
        """Emit a circle node (grok ``render_circle``). Radius is half the
        smaller box dimension; fill / stroke fall back to the theme defaults."""
        r = min(node.width, node.height) / 2.0
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        self.output += (
            f'<circle cx="{node.x:.1f}" cy="{node.y:.1f}" r="{r:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>\n'
        )
        self.render_text(node.x, node.y, node.label)

    def render_hexagon(self, node: LayoutNode) -> None:
        """Emit a hexagon node: a 6-point polygon (grok ``render_hexagon``).

        The top / bottom horizontal edges are inset by ``h/3`` from the
        left/right extremes (grok ``inset = height / 3``).
        """
        hw = node.width / 2.0
        hh = node.height / 2.0
        inset = node.height / 3.0
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        points = (
            f"{node.x - hw + inset:.1f},{node.y - hh:.1f} "
            f"{node.x + hw - inset:.1f},{node.y - hh:.1f} "
            f"{node.x + hw:.1f},{node.y:.1f} "
            f"{node.x + hw - inset:.1f},{node.y + hh:.1f} "
            f"{node.x - hw + inset:.1f},{node.y + hh:.1f} "
            f"{node.x - hw:.1f},{node.y:.1f}"
        )
        self.output += (
            f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>\n'
        )
        self.render_text(node.x, node.y, node.label)

    def render_cylinder(self, node: LayoutNode) -> None:
        """Emit a cylinder (DB) node: a body path + top ellipse cap (grok
        ``render_cylinder``).

        The body is a closed path of two vertical sides joined by two
        half-ellipses (the bottom curve and the back of the top cap); a full
        ``<ellipse>`` is layered on top for the visible cap. The ellipse
        ``ry`` is ``min(hw/4, hh/2)``. Text centers on the body's vertical
        midpoint (below the cap).
        """
        hw = node.width / 2.0
        hh = node.height / 2.0
        ellipse_ry = min(hw / 4.0, hh / 2.0)
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        x = node.x - hw
        y = node.y - hh
        body_top = y + ellipse_ry
        body_bottom = node.y + hh - ellipse_ry
        self.output += (
            f'<path d="M {x:.1f} {body_top:.1f} L {x:.1f} {body_bottom:.1f} '
            f"A {hw:.1f} {ellipse_ry:.1f} 0 0 0 {node.x + hw:.1f} {body_bottom:.1f} "
            f"L {node.x + hw:.1f} {body_top:.1f} "
            f'A {hw:.1f} {ellipse_ry:.1f} 0 0 0 {x:.1f} {body_top:.1f} Z" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>\n'
        )
        self.output += (
            f'<ellipse cx="{node.x:.1f}" cy="{body_top:.1f}" rx="{hw:.1f}" '
            f'ry="{ellipse_ry:.1f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>\n'
        )
        body_center_y = (body_top + body_bottom) / 2.0
        self.render_text(node.x, body_center_y, node.label)

    def render_subroutine(self, node: LayoutNode) -> None:
        """Emit a subroutine node: a rect + two vertical ``<line>`` bars (grok
        ``render_subroutine``). The bars sit ``bar_inset`` (8px) inside the
        left/right edges.
        """
        x = node.x - node.width / 2.0
        y = node.y - node.height / 2.0
        bar_inset = 8.0
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        self.output += (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>\n'
        )
        self.output += (
            f'<line x1="{x + bar_inset:.1f}" y1="{y:.1f}" '
            f'x2="{x + bar_inset:.1f}" y2="{y + node.height:.1f}" '
            f'stroke="{stroke}" stroke-width="1"/>\n'
        )
        self.output += (
            f'<line x1="{x + node.width - bar_inset:.1f}" y1="{y:.1f}" '
            f'x2="{x + node.width - bar_inset:.1f}" y2="{y + node.height:.1f}" '
            f'stroke="{stroke}" stroke-width="1"/>\n'
        )
        self.render_text(node.x, node.y, node.label)

    def render_asymmetric(self, node: LayoutNode) -> None:
        """Emit an asymmetric ``>text]`` flag node: a 5-point polygon with a
        V-notch on the left (grok ``render_asymmetric``). Text is nudged right
        by ``point_offset/4`` (``point_offset == hh``) to balance the notch.
        """
        hw = node.width / 2.0
        hh = node.height / 2.0
        point_offset = hh
        fill = node.fill_color if node.fill_color is not None else self.theme.node_fill
        stroke = (
            node.stroke_color if node.stroke_color is not None else self.theme.node_stroke
        )
        points = (
            f"{node.x - hw + point_offset:.1f},{node.y - hh:.1f} "
            f"{node.x + hw:.1f},{node.y - hh:.1f} "
            f"{node.x + hw:.1f},{node.y + hh:.1f} "
            f"{node.x - hw + point_offset:.1f},{node.y + hh:.1f} "
            f"{node.x - hw:.1f},{node.y:.1f}"
        )
        self.output += (
            f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>\n'
        )
        self.render_text(node.x + point_offset / 4.0, node.y, node.label)

    def render_text(self, x: float, y: float, text: str) -> None:
        """Emit a node label, choosing the state-diagram char width when the
        renderer is in state mode (grok ``render_text``).

        Wraps ``text`` to ``wrapping_width`` at the resolved character width
        (:data:`STATE_CHAR_WIDTH` for state diagrams, :data:`DEFAULT_CHAR_WIDTH`
        otherwise, both scaled by ``font_size``); an empty wrap emits nothing.
        """
        char_width = (
            scale_char_width(STATE_CHAR_WIDTH, self.options.font_size)
            if self.is_state_diagram
            else scale_char_width(DEFAULT_CHAR_WIDTH, self.options.font_size)
        )
        lines = wrap_text_lines(text, self.options.wrapping_width, char_width)
        if not lines:
            return
        self.render_text_lines(
            x,
            y,
            lines,
            self.options.font_size,
            DEFAULT_LINE_HEIGHT,
            self.theme.text_color,
        )

    def render_text_lines(
        self,
        x: float,
        y: float,
        lines: list[list[str]],
        font_size: float,
        line_height: float,
        color: str,
    ) -> None:
        """Emit wrapped text as a centered ``<text>`` + per-line ``<tspan>``s
        (grok ``render_text_lines``).

        With ``dominant-baseline="central"`` the ``y`` anchors the glyph's
        vertical center, so ``n`` lines distribute evenly around it: the first
        line sits at ``y - (n-1)/2 * line_height_px``. Each sub-line's words
        join with a single space (grok ``line.join(" ")``); the font family and
        each tspan body are XML-escaped.
        """
        line_height_px = font_size * line_height
        start_y = y - (len(lines) - 1.0) * line_height_px / 2.0
        font_family = self.escape_xml(self.options.font_family)
        self.output += (
            f'<text text-anchor="middle" dominant-baseline="central" '
            f'font-family="{font_family}" font-size="{font_size:.0f}" '
            f'fill="{color}">\n'
        )
        for i, line in enumerate(lines):
            line_y = start_y + (i * line_height_px)
            line_text = " ".join(line)
            self.output += (
                f'<tspan x="{x:.1f}" y="{line_y:.1f}">'
                f"{self.escape_xml(line_text)}</tspan>\n"
            )
        self.output += "</text>\n"

    @staticmethod
    def escape_xml(s: str) -> str:
        """Escape the five XML-significant characters (grok ``escape_xml``).

        ``&`` is escaped first so the entities introduced by later replacements
        are not themselves re-escaped. Order matches grok exactly:
        ``&`` -> ``&amp;``, ``<`` -> ``&lt;``, ``>`` -> ``&gt;``,
        ``"`` -> ``&quot;``, ``'`` -> ``&#39;``.
        """
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    # === R275c edge geometry / path / label emitters + orchestrator =========
    #
    # The 12 methods below fuse the remainder of grok ``svg_renderer.rs`` --
    # every edge polyline path / marker / label emitter, the 8-phase render
    # orchestrator, and the two public entry points. Together with the R275a
    # foundation + R275b node shapes, this closes svg_renderer.py.

    def render(self, layout: LayoutResult) -> str:
        """Emit the full SVG document (grok ``SvgRenderer::render``).

        Orchestrates the 8-phase emission order exactly as grok: header +
        defs, then subgraph backgrounds, then edges (paths + markers), then
        nodes (sorted by id for deterministic output), then subgraph titles,
        then edge labels (collision-resolved), then footer. Returns the
        accumulated output and resets ``self.output`` to empty -- mirroring
        ``std::mem::take(&mut self.output)`` (drain into the return value).
        """
        self.write_header()
        self.write_defs()

        for subgraph in layout.subgraphs:
            self.render_subgraph_background(subgraph)

        for edge in layout.edges:
            self.render_edge_line(edge)

        # nodes sorted by id for deterministic output (grok ``nodes.sort_by``).
        nodes = sorted(layout.nodes.values(), key=lambda node: node.id)
        for node in nodes:
            self.render_node(node)

        for subgraph in layout.subgraphs:
            self.render_subgraph_title(subgraph)

        self.render_edge_labels(layout.edges)

        self.write_footer()
        # std::mem::take(&mut self.output): drain the buffer, reset to "".
        result = self.output
        self.output = ""
        return result

    def render_edge_line(self, edge: LayoutEdge) -> None:
        """Emit a single edge as an SVG ``<path>`` (grok ``render_edge_line``).

        Edges with fewer than 2 control points are skipped. ``stroke-width``,
        ``stroke-dasharray`` and ``marker-end`` derive from the
        :class:`EdgeStyle` variant -- dotted styles dash, thick styles widen
        to 3.5 and select the ``-thick`` arrowhead marker, arrowed styles
        shorten their endpoint by the arrowhead offset so the tip lands on the
        target node border.
        """
        if len(edge.points) < 2:
            return

        has_arrow = edge.style in (
            EdgeStyle.Arrow,
            EdgeStyle.DottedArrow,
            EdgeStyle.ThickArrow,
        )
        is_dotted = edge.style in (EdgeStyle.DottedArrow, EdgeStyle.DottedLine)
        is_thick = edge.style in (EdgeStyle.ThickArrow, EdgeStyle.ThickLine)

        if has_arrow and is_thick:
            marker = ' marker-end="url(#arrowhead-thick)"'
        elif has_arrow and not is_thick:
            marker = ' marker-end="url(#arrowhead)"'
        else:
            marker = ""

        stroke_width = 3.5 if is_thick else 1.0
        # dotted -> round-capped short dashes for a dot look (mermaid default).
        dash_array = ' stroke-dasharray="3 3"' if is_dotted else ""

        # clone the control points so arrow-tip shortening does not mutate the
        # caller's layout (grok ``edge.points.clone()``).
        points = list(edge.points)
        if has_arrow:
            offset = EDGE_ARROWHEAD_OFFSET_THICK if is_thick else EDGE_ARROWHEAD_OFFSET
            self.shorten_end_for_marker(points, offset)

        d = self.edge_path_d(points)

        self.output += (
            f'<path d="{d}" fill="none" stroke="{self.theme.edge_color}" '
            f'stroke-width="{stroke_width:.1f}" stroke-linecap="round" '
            f'stroke-linejoin="round"{dash_array}{marker}/>\n'
        )

    @staticmethod
    def shorten_end_for_marker(
        points: list[tuple[float, float]], offset: float
    ) -> None:
        """Pull the final control point back along its segment (grok static).

        The arrowhead marker tip extends past the path endpoint, so arrowed
        edges shorten their last segment by ``offset`` px to land the tip on
        the target node border. Mutates ``points`` in place (mirrors grok's
        ``&mut [(f64, f64)]``); no-op when there are fewer than 2 points or the
        segment is already shorter than the offset.
        """
        if len(points) < 2 or offset <= 0.0:
            return

        last_idx = len(points) - 1
        prev = points[last_idx - 1]
        last = points[last_idx]

        dx = last[0] - prev[0]
        dy = last[1] - prev[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length <= offset:
            return

        ux = dx / length
        uy = dy / length
        points[last_idx] = (last[0] - ux * offset, last[1] - uy * offset)

    def edge_path_d(self, points: list[tuple[float, float]]) -> str:
        """Build the SVG ``d`` attribute for an edge (grok ``edge_path_d``).

        Basis curves first round right-angle corners (:meth:`fix_corners`)
        then map to a B-spline; linear curves map straight to ``M``/``L``
        polyline segments.
        """
        if self.options.edge_curve is EdgeCurve.Basis:
            smoothed = self.fix_corners(points)
            return self.basis_spline_path_d(smoothed)
        return self.linear_path_d(points)

    @staticmethod
    def linear_path_d(points: list[tuple[float, float]]) -> str:
        """Build a polyline ``d`` (``M`` + ``L`` segments) from control points.

        Empty input -> ``""`` (grok ``let-else`` early return on
        ``points.first().copied()``). Mirrors grok's ``M{x:.1},{y:.1}`` then
        one ``L{x:.1},{y:.1}`` per subsequent point.
        """
        if not points:
            return ""
        first_x, first_y = points[0]
        d = f"M{first_x:.1f},{first_y:.1f}"
        for x, y in points[1:]:
            d += f"L{x:.1f},{y:.1f}"
        return d

    @staticmethod
    def basis_spline_path_d(points: list[tuple[float, float]]) -> str:
        """Build a B-spline ``d`` from control points (grok static).

        Three-state machine over the control points: the 1st emits ``M``, the
        2nd only advances state, the 3rd emits the lead-in ``L`` + a basis
        cubic segment, and every subsequent point emits another basis cubic.
        After the loop, state 3 / 2 each close with a final ``L`` to the last
        point. The ``x0/y0/x1/y1`` lag registers start as NaN (mirrors grok
        ``f64::NAN``); they are only read once state >= 2 has populated them
        from prior iterations, so the NaN initial values are never observed.
        """
        if not points:
            return ""

        d = ""
        x0 = float("nan")
        y0 = float("nan")
        x1 = float("nan")
        y1 = float("nan")
        point_state = 0

        for x, y in points:
            if point_state == 0:
                point_state = 1
                d += f"M{x:.1f},{y:.1f}"
            elif point_state == 1:
                point_state = 2
            elif point_state == 2:
                point_state = 3
                d += f"L{(5.0 * x0 + x1) / 6.0:.1f},{(5.0 * y0 + y1) / 6.0:.1f}"
                d += SvgRenderer.basis_point(x0, y0, x1, y1, x, y)
            else:
                d += SvgRenderer.basis_point(x0, y0, x1, y1, x, y)

            x0 = x1
            x1 = x
            y0 = y1
            y1 = y

        if point_state == 3:
            d += SvgRenderer.basis_point(x0, y0, x1, y1, x1, y1)
            d += f"L{x1:.1f},{y1:.1f}"
        elif point_state == 2:
            d += f"L{x1:.1f},{y1:.1f}"

        return d

    @staticmethod
    def basis_point(
        x0: float, y0: float, x1: float, y1: float, x: float, y: float
    ) -> str:
        """Emit one cubic Bezier basis segment ``C...`` (grok static).

        Six control coordinates derived from the lag pair ``(x0, y0)`` /
        ``(x1, y1)`` and the current point ``(x, y)``; format matches grok's
        ``C{:.1},{:.1} {:.1},{:.1} {:.1},{:.1}`` (three control-point pairs,
        space-separated).
        """
        return (
            f"C{(2.0 * x0 + x1) / 3.0:.1f},{(2.0 * y0 + y1) / 3.0:.1f} "
            f"{(x0 + 2.0 * x1) / 3.0:.1f},{(y0 + 2.0 * y1) / 3.0:.1f} "
            f"{(x0 + 4.0 * x1 + x) / 6.0:.1f},{(y0 + 4.0 * y1 + y) / 6.0:.1f}"
        )

    @staticmethod
    def fix_corners(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Round right-angle polyline corners (grok static ``fix_corners``).

        For each detected corner (see :meth:`corner_positions`), replace the
        single corner point with three points -- ``new_prev`` /
        ``new_corner`` / ``new_next`` -- where ``new_prev`` / ``new_next`` sit
        5 px in from the adjacent legs (see :meth:`find_adjacent_point`). When
        the corner spans a large-enough box (>= 10 px on both axes),
        ``new_corner`` is offset by ``a = sqrt(2) * 2`` along the bisector to
        round the turn. Other points pass through unchanged.
        """
        corner_indices = set(SvgRenderer.corner_positions(points))
        new_points: list[tuple[float, float]] = []
        for idx, point in enumerate(points):
            if idx in corner_indices:
                prev_point = points[idx - 1]
                next_point = points[idx + 1]
                corner_point = point
                new_prev = SvgRenderer.find_adjacent_point(prev_point, corner_point, 5.0)
                new_next = SvgRenderer.find_adjacent_point(next_point, corner_point, 5.0)
                x_diff = new_next[0] - new_prev[0]
                y_diff = new_next[1] - new_prev[1]
                new_corner = corner_point
                a = (2.0 ** 0.5) * 2.0
                if (
                    abs(next_point[0] - prev_point[0]) > 10.0
                    and abs(next_point[1] - prev_point[1]) >= 10.0
                ):
                    if abs(corner_point[0] - new_prev[0]) < _F64_EPSILON:
                        new_corner = (
                            (new_prev[0] - 5.0 + a) if x_diff < 0.0 else (new_prev[0] + 5.0 - a),
                            (new_prev[1] - a) if y_diff < 0.0 else (new_prev[1] + a),
                        )
                    else:
                        new_corner = (
                            (new_prev[0] - a) if x_diff < 0.0 else (new_prev[0] + a),
                            (new_prev[1] - 5.0 + a)
                            if y_diff < 0.0
                            else (new_prev[1] + 5.0 - a),
                        )
                new_points.append(new_prev)
                new_points.append(new_corner)
                new_points.append(new_next)
            else:
                new_points.append(point)
        return new_points

    @staticmethod
    def corner_positions(points: list[tuple[float, float]]) -> list[int]:
        """Detect right-angle corner indices (grok static ``corner_positions``).

        A corner is a midpoint ``curr`` whose prev/next legs turn 90 degrees --
        either a vertical leg (``prev.x == curr.x``) into a horizontal one
        (``curr.y == next.y``), or the mirror. Each leg must also span > 5 px
        so tiny jitter is not mistaken for a real corner. Equality is tested
        against ``f64::EPSILON`` (mapped to :data:`_F64_EPSILON`).
        """
        positions: list[int] = []
        if len(points) < 3:
            return positions
        for i in range(1, len(points) - 1):
            prev = points[i - 1]
            curr = points[i]
            next_point = points[i + 1]
            if (
                (
                    abs(prev[0] - curr[0]) < _F64_EPSILON
                    and abs(curr[1] - next_point[1]) < _F64_EPSILON
                    and abs(curr[0] - next_point[0]) > 5.0
                    and abs(curr[1] - prev[1]) > 5.0
                )
                or (
                    abs(prev[1] - curr[1]) < _F64_EPSILON
                    and abs(curr[0] - next_point[0]) < _F64_EPSILON
                    and abs(curr[0] - prev[0]) > 5.0
                    and abs(curr[1] - next_point[1]) > 5.0
                )
            ):
                positions.append(i)
        return positions

    @staticmethod
    def find_adjacent_point(
        a: tuple[float, float], b: tuple[float, float], distance: float
    ) -> tuple[float, float]:
        """Step back from ``b`` toward ``a`` by ``distance`` (grok static).

        Returns the point on segment ``a -> b`` that is ``distance`` px away
        from ``b`` (i.e. ``b`` shrunk toward ``a`` by ``distance``). When
        ``a == b`` (zero length), returns ``a`` unchanged.
        """
        x_diff = b[0] - a[0]
        y_diff = b[1] - a[1]
        length = (x_diff * x_diff + y_diff * y_diff) ** 0.5
        if length == 0.0:
            return a
        ratio = distance / length
        return (b[0] - ratio * x_diff, b[1] - ratio * y_diff)

    def render_edge_labels(self, edges: list[LayoutEdge]) -> None:
        """Emit edge label backgrounds + text with collision resolution.

        Collects a label box per edge (skipping edges with no/blank label, or
        no label_pos and < 2 control points), sizes each box from the wrapped
        label text, then runs up to 10 passes of pairwise separation (8 px min
        gap) to push overlapping boxes apart. Finally emits an
        ``rgba(232,232,232,0.8)`` ``<rect>`` per label and the wrapped text
        via :meth:`render_text_lines`.
        """
        @dataclass
        class _LabelInfo:
            x: float
            y: float
            width: float
            height: float
            lines: list[list[str]]

        labels: list[_LabelInfo] = []
        if self.is_state_diagram:
            char_width = scale_char_width(STATE_CHAR_WIDTH, self.options.font_size)
        else:
            char_width = scale_char_width(EDGE_LABEL_CHAR_WIDTH, self.options.font_size)

        for edge in edges:
            label = edge.label
            if label is None:
                continue
            if label.strip() == "" or (edge.label_pos is None and len(edge.points) < 2):
                continue

            if edge.label_pos is not None:
                pos_x, pos_y = edge.label_pos
                if pos_x > 0.0 and pos_y > 0.0:
                    label_x = pos_x
                    label_y = pos_y
                else:
                    label_points = SvgRenderer.fix_corners(edge.points)
                    label_x, label_y = SvgRenderer.label_position(label_points)
            else:
                label_points = SvgRenderer.fix_corners(edge.points)
                label_x, label_y = SvgRenderer.label_position(label_points)

            lines = wrap_text_lines(label, self.options.wrapping_width, char_width)
            if not lines:
                continue

            # grok ``lines.iter().map(...).fold(0.0, f64::max)`` -- widest sub-line.
            max_line_width = max(
                (line_width_words(line, char_width) for line in lines),
                default=0.0,
            )
            total_height = wrapped_text_height_with_font_size(
                len(lines), self.options.font_size
            )
            rect_width = max_line_width + EDGE_LABEL_PADDING_H * 2.0
            rect_height = total_height + EDGE_LABEL_PADDING_V * 2.0

            labels.append(
                _LabelInfo(
                    x=label_x,
                    y=label_y,
                    width=rect_width,
                    height=rect_height,
                    lines=lines,
                )
            )

        # fn-local consts (grok ``const MIN_SEPARATION: f64 = 8.0`` etc.).
        min_separation = 8.0
        max_iterations = 10

        for _ in range(max_iterations):
            any_collision = False

            for i in range(len(labels)):
                for j in range(i + 1, len(labels)):
                    a_left = labels[i].x - labels[i].width / 2.0 - min_separation
                    a_right = labels[i].x + labels[i].width / 2.0 + min_separation
                    a_top = labels[i].y - labels[i].height / 2.0 - min_separation
                    a_bottom = labels[i].y + labels[i].height / 2.0 + min_separation

                    b_left = labels[j].x - labels[j].width / 2.0 - min_separation
                    b_right = labels[j].x + labels[j].width / 2.0 + min_separation
                    b_top = labels[j].y - labels[j].height / 2.0 - min_separation
                    b_bottom = labels[j].y + labels[j].height / 2.0 + min_separation

                    overlap_x = a_right > b_left and b_right > a_left
                    overlap_y = a_bottom > b_top and b_bottom > a_top

                    if overlap_x and overlap_y:
                        any_collision = True
                        dx = labels[j].x - labels[i].x
                        dy = labels[j].y - labels[i].y

                        overlap_amount_x = min(a_right - b_left, b_right - a_left)
                        overlap_amount_y = min(a_bottom - b_top, b_bottom - a_top)

                        if overlap_amount_x < overlap_amount_y:
                            shift = overlap_amount_x / 2.0
                            if dx >= 0.0:
                                labels[i].x -= shift
                                labels[j].x += shift
                            else:
                                labels[i].x += shift
                                labels[j].x -= shift
                        else:
                            shift = overlap_amount_y / 2.0
                            if dy >= 0.0:
                                labels[i].y -= shift
                                labels[j].y += shift
                            else:
                                labels[i].y += shift
                                labels[j].y -= shift

            if not any_collision:
                break

        for info in labels:
            rect_x = info.x - info.width / 2.0
            rect_y = info.y - info.height / 2.0
            self.output += (
                f'<rect x="{rect_x:.1f}" y="{rect_y:.1f}" width="{info.width:.1f}" '
                f'height="{info.height:.1f}" fill="rgba(232,232,232,{EDGE_LABEL_BG_OPACITY})" rx="2"/>\n'
            )
            self.render_text_lines(
                info.x,
                info.y,
                info.lines,
                self.options.font_size,
                DEFAULT_LINE_HEIGHT,
                self.theme.text_color,
            )

    @staticmethod
    def label_position(points: list[tuple[float, float]]) -> tuple[float, float]:
        """Place a label at the polyline's half-length point (grok static).

        Walks the segments accumulating length; returns the interpolated point
        where cumulative length first reaches ``total / 2``. With < 2 points
        the first point (or ``(0, 0)`` when empty) is returned; a
        near-zero-length polyline short-circuits to the first point. Falls
        back to the endpoint midpoint if the half-length target is never
        reached.
        """
        if len(points) < 2:
            return points[0] if points else (0.0, 0.0)

        segment_lengths: list[float] = []
        total_length = 0.0
        for i in range(len(points) - 1):
            dx = points[i + 1][0] - points[i][0]
            dy = points[i + 1][1] - points[i][1]
            length = (dx * dx + dy * dy) ** 0.5
            segment_lengths.append(length)
            total_length += length

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


# === R275c public entry points (grok crate-level ``pub fn``) ================
#
# grok declares ``pub fn render`` / ``pub fn render_with_config`` at the
# crate root and calls them from ``lib.rs`` (this ``svg_renderer`` module is a
# private ``mod``, never ``pub use``-d). The two functions are mirrored here
# as module-level entry points; ``__all__`` closes the module's public surface
# at exactly these two names (matching the internal-module precedent set by
# ``layout.py`` whose ``__all__`` was written only at feature completeness).


def render(layout: LayoutResult, theme: MermaidTheme) -> str:
    """Render a laid-out flowchart to an SVG string (grok ``render``).

    Convenience entry -- delegates to :func:`render_with_config` with the
    default :class:`RenderConfig`.
    """
    return render_with_config(layout, theme, RenderConfig())


def render_with_config(
    layout: LayoutResult, theme: MermaidTheme, config: RenderConfig
) -> str:
    """Render a laid-out flowchart to an SVG string with a config (grok entry).

    Detects state-diagram mode (any node shaped ``StartState`` / ``EndState``
    / ``ForkJoin``), constructs the :class:`SvgRenderer` actor with options
    derived from ``config``, and runs the 8-phase emission.
    """
    is_state_diagram = any(
        node.shape
        in (NodeShape.StartState, NodeShape.EndState, NodeShape.ForkJoin)
        for node in layout.nodes.values()
    )
    svg = SvgRenderer(
        width=layout.width,
        height=layout.height,
        theme=theme,
        is_state_diagram=is_state_diagram,
        options=SvgRenderOptions.from_render_config(config),
    )
    return svg.render(layout)


__all__ = ["render", "render_with_config"]
