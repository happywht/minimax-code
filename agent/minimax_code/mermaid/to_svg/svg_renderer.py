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

from dataclasses import dataclass
from enum import Enum

from .ast import NodeShape
from .config import RenderConfig
from .layout import LayoutNode, LayoutSubgraph
from .text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_LINE_HEIGHT,
    DEFAULT_WRAP_WIDTH,
    measure_wrapped_lines_with_font_size,
    scale_char_width,
    wrap_text_lines,
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
