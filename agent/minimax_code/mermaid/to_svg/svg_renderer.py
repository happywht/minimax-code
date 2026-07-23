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

from .config import RenderConfig
from .layout import LayoutSubgraph
from .text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_WRAP_WIDTH,
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
