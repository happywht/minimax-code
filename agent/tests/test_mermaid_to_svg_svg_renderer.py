"""Black-box tests for the migrated mermaid-to-svg SVG emitter foundation
(R275a).

Exercises :mod:`minimax_code.mermaid.to_svg.svg_renderer` -- the R275a
foundation slice fused from grok ``mermaid-to-svg/src/svg_renderer.rs``
(direction (1), leaf 7, sub-leaf R275a). The full renderer spans three
sub-leaves (R275a/b/c); this test file covers the R275a foundation
surface plus the R275b node-shape + text-emitter surface:

* the 10 file-private ``const`` knobs (exact f64 values from grok),
* the private :class:`EdgeCurve` enum (two variants) +
  :meth:`EdgeCurve.from_mermaid_name` (ASCII-case-insensitive ``linear``
  match, mirroring grok ``eq_ignore_ascii_case``),
* the private :class:`SvgRenderOptions` dataclass (field defaults ARE grok's
  ``Default`` impl) + :meth:`SvgRenderOptions.from_render_config` (per-field
  ``unwrap_or(default)`` overlay of the R270 :class:`RenderConfig`),
* the :class:`SvgRenderer` actor dataclass (``output`` defaults to ``""``,
  matching grok's ``new``),
* the four self-contained emitters ``write_header`` / ``write_defs`` /
  ``write_footer`` / ``render_subgraph_background`` -- every one references
  only ``self`` fields / the ``subgraph`` argument / the resolved
  :class:`MermaidTheme`, so the foundation is independently testable.

The R275b slice (the file's lower half) covers the node-shape dispatcher
+ the 10 shape emitters + the two text emitters, fused from grok's
``render_subgraph_title`` / ``render_node`` / ``render_rectangle`` /
``render_start_state`` / ``render_end_state`` / ``render_fork_join`` /
``render_diamond`` / ``render_circle`` / ``render_hexagon`` /
``render_cylinder`` / ``render_subroutine`` / ``render_asymmetric`` /
``render_text`` / ``render_text_lines`` / ``escape_xml``: the exhaustive
12-variant ``render_node`` dispatch, each shape emitter's exact SVG bytes
(including the ``render_end_state`` inner-radius clamp, the literal
``rx="1"`` of ``render_fork_join``, the cylinder path + ellipse cap, the
subroutine rect + two bar lines, the asymmetric V-notch polygon), the
``render_text`` state-vs-default char-width fork, ``render_text_lines``
multi-line centering + escaping, ``render_subgraph_title`` wrap/center,
and the ``escape_xml`` five-character chain (``&`` first).

Two grok quirks are asserted verbatim (zero-semantic clone):

1. :meth:`SvgRenderer.render_subgraph_background` ends with a *literal*
   ``\\n`` (backslash + ``n`` -- two characters), reproducing grok's raw-string
   artifact; this is NOT a real newline.
2. :meth:`SvgRenderer.write_header` / :meth:`SvgRenderer.write_defs` end with
   a *real* trailing newline (the raw string's own final line break).

Internal-module contract (mirrors grok's private ``mod svg_renderer;``):
``svg_renderer`` is reachable by deep path but NOT re-exported through the
``to_svg`` barrel, and (until the R275c close) declares no ``__all__`` --
matching the :mod:`.layout` internal-module precedent.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import FlowchartConfig, MermaidTheme, RenderConfig
from minimax_code.mermaid.to_svg import svg_renderer as svg_renderer_mod
from minimax_code.mermaid.to_svg.ast import NodeShape
from minimax_code.mermaid.to_svg.layout import LayoutNode, LayoutSubgraph
from minimax_code.mermaid.to_svg.svg_renderer import (
    DEFAULT_FONT_FAMILY,
    EDGE_ARROWHEAD_OFFSET,
    EDGE_ARROWHEAD_OFFSET_THICK,
    EDGE_LABEL_BG_OPACITY,
    EDGE_LABEL_CHAR_WIDTH,
    EDGE_LABEL_PADDING_H,
    EDGE_LABEL_PADDING_V,
    STATE_CHAR_WIDTH,
    SUBGRAPH_TITLE_TOP_MARGIN,
    EdgeCurve,
    SvgRenderer,
    SvgRenderOptions,
)
from minimax_code.mermaid.to_svg.text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_LINE_HEIGHT,
    DEFAULT_WRAP_WIDTH,
)

# === test fixtures =========================================================


def _make_renderer(
    *,
    width: float = 800.0,
    height: float = 600.0,
    theme: MermaidTheme | None = None,
    is_state_diagram: bool = False,
    options: SvgRenderOptions | None = None,
) -> SvgRenderer:
    """Build an :class:`SvgRenderer` with light-theme + default options."""
    return SvgRenderer(
        width=width,
        height=height,
        theme=theme if theme is not None else MermaidTheme.light(),
        is_state_diagram=is_state_diagram,
        options=options if options is not None else SvgRenderOptions(),
    )


# === private constants (exact f64 values from grok) =========================


def test_private_constants_match_grok_values() -> None:
    """The 10 file-private ``const`` knobs mirror grok exactly."""
    assert EDGE_ARROWHEAD_OFFSET == 4.0
    assert EDGE_ARROWHEAD_OFFSET_THICK == 5.5  # 11 * (10-5) / 10
    assert EDGE_LABEL_CHAR_WIDTH == DEFAULT_CHAR_WIDTH  # tracks text-wrap default
    assert EDGE_LABEL_PADDING_H == 2.0
    assert EDGE_LABEL_PADDING_V == 2.0
    assert EDGE_LABEL_BG_OPACITY == 0.8
    assert SUBGRAPH_TITLE_TOP_MARGIN == 0.0
    assert STATE_CHAR_WIDTH == 6.7
    assert DEFAULT_FONT_FAMILY == "Trebuchet MS, verdana, arial, sans-serif"


# === EdgeCurve (private enum) ==============================================


def test_edge_curve_variant_values() -> None:
    """``EdgeCurve`` carries its mermaid curve name as the variant value."""
    assert EdgeCurve.Basis.value == "basis"
    assert EdgeCurve.Linear.value == "linear"


@pytest.mark.parametrize(
    "name",
    ["linear", "Linear", "LINEAR", "LiNeAr"],
)
def test_edge_curve_from_mermaid_name_matches_linear(name: str) -> None:
    """``"linear"`` (ASCII-case-insensitive) selects :attr:`Linear`.

    Mirrors grok ``eq_ignore_ascii_case``: ASCII letters fold before the
    compare, so any ASCII casing of ``"linear"`` matches. (mermaid curve
    names are ASCII-only; non-ASCII fold divergence is unreachable.)
    """
    assert EdgeCurve.from_mermaid_name(name) is EdgeCurve.Linear


@pytest.mark.parametrize(
    "name",
    [
        "basis",
        "Basis",
        "BASIS",
        "bAsIs",
        "",  # empty falls back to the default curve
        "cardinal",
        "step",
        "catmullrom",
        "linearBasis",  # substring does not match -- whole-string compare
        "linearly",  # prefix does not match
        " linear ",  # whitespace breaks the match -- eq_ignore_ascii_case does NOT trim
        " linear",  # leading whitespace alone also breaks it
        "linear ",  # trailing whitespace alone also breaks it
    ],
)
def test_edge_curve_from_mermaid_name_falls_back_to_basis(name: str) -> None:
    """Any non-``"linear"`` name falls back to :attr:`Basis` (mermaid default).

    Mirrors grok ``eq_ignore_ascii_case``: the compare is whole-string after
    ASCII-case folding -- it does NOT trim surrounding whitespace, so
    ``" linear "`` / ``" linear"`` / ``"linear "`` all miss and fall back to
    :attr:`Basis`.
    """
    assert EdgeCurve.from_mermaid_name(name) is EdgeCurve.Basis


# === SvgRenderOptions (private dataclass) ==================================


def test_svg_render_options_defaults_are_grok_default_impl() -> None:
    """Field defaults reproduce grok's ``SvgRenderOptions::default()``."""
    options = SvgRenderOptions()
    assert options.font_family == DEFAULT_FONT_FAMILY
    assert options.font_size == DEFAULT_FONT_SIZE  # 16.0
    assert options.wrapping_width == DEFAULT_WRAP_WIDTH  # 200.0
    assert options.edge_curve is EdgeCurve.Basis


def test_svg_render_options_from_empty_config_equals_default() -> None:
    """An empty :class:`RenderConfig` overlays nothing -> the default."""
    assert SvgRenderOptions.from_render_config(RenderConfig()) == SvgRenderOptions()


def test_svg_render_options_from_config_overlays_each_knob() -> None:
    """Each present knob is taken from the config; absent knobs keep defaults."""
    config = RenderConfig(
        font_family="Arial, sans-serif",
        font_size="20px",
        flowchart=FlowchartConfig(curve="linear", wrapping_width=150),
    )
    options = SvgRenderOptions.from_render_config(config)
    assert options.font_family == "Arial, sans-serif"
    assert options.font_size == 20.0  # "20px" -> 20.0
    assert options.wrapping_width == 150.0  # int widened to float
    assert options.edge_curve is EdgeCurve.Linear


def test_svg_render_options_from_config_partial_overlay() -> None:
    """Only the set knobs overlay; the rest stay at the default."""
    config = RenderConfig(font_size="20px")  # font_family=None, flowchart empty
    options = SvgRenderOptions.from_render_config(config)
    assert options.font_family == DEFAULT_FONT_FAMILY  # default
    assert options.font_size == 20.0  # overlaid
    assert options.wrapping_width == DEFAULT_WRAP_WIDTH  # default
    assert options.edge_curve is EdgeCurve.Basis  # default (curve None)


def test_svg_render_options_from_config_curve_basis_explicit() -> None:
    """An explicit ``curve="basis"`` config selects :attr:`Basis` (not default)."""
    config = RenderConfig(flowchart=FlowchartConfig(curve="basis"))
    options = SvgRenderOptions.from_render_config(config)
    assert options.edge_curve is EdgeCurve.Basis


def test_svg_render_options_from_config_curve_unknown_falls_back_to_basis() -> None:
    """An unknown curve name falls back to :attr:`Basis` (EdgeCurve rule)."""
    config = RenderConfig(flowchart=FlowchartConfig(curve="cardinal"))
    options = SvgRenderOptions.from_render_config(config)
    assert options.edge_curve is EdgeCurve.Basis


def test_svg_render_options_from_config_font_size_plain_number() -> None:
    """A bare numeric ``font_size`` (no ``px`` suffix) also parses."""
    config = RenderConfig(font_size=" 18 ")
    options = SvgRenderOptions.from_render_config(config)
    assert options.font_size == 18.0


def test_svg_render_options_from_config_font_size_invalid_keeps_default() -> None:
    """A non-positive / non-numeric ``font_size`` falls back to the default."""
    for raw in ["0", "-3", "abc", "NaN"]:
        config = RenderConfig(font_size=raw)
        options = SvgRenderOptions.from_render_config(config)
        assert options.font_size == DEFAULT_FONT_SIZE


# === SvgRenderer actor (private dataclass) =================================


def test_svg_renderer_output_defaults_to_empty() -> None:
    """``output`` defaults to ``""`` (grok ``SvgRenderer::new`` empties it)."""
    renderer = _make_renderer()
    assert renderer.output == ""


def test_svg_renderer_carries_layout_and_theme_fields() -> None:
    """The actor stores width/height/theme/is_state_diagram/options verbatim."""
    theme = MermaidTheme.light()
    options = SvgRenderOptions()
    renderer = SvgRenderer(
        width=1024.0,
        height=768.0,
        theme=theme,
        is_state_diagram=True,
        options=options,
    )
    assert renderer.width == 1024.0
    assert renderer.height == 768.0
    assert renderer.theme is theme
    assert renderer.is_state_diagram is True
    assert renderer.options is options
    assert renderer.output == ""


# === write_header (exact SVG) ==============================================


def test_write_header_exact_output() -> None:
    """``write_header`` emits the XML decl + ``<svg>`` root + background rect.

    Width/height render at 0 decimals (``{:.0}`` -> ``:.0f``); the background
    color threads through both the SVG ``style`` and the explicit ``<rect>``.
    Ends with a real trailing newline.
    """
    renderer = _make_renderer(width=800.0, height=600.0)
    renderer.write_header()
    assert renderer.output == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg width="800" height="600" viewBox="0 0 800 600" '
        'xmlns="http://www.w3.org/2000/svg" '
        'style="background-color: #ffffff;">\n'
        '<rect x="0" y="0" width="800" height="600" fill="#ffffff" stroke="none"/>\n'
    )


def test_write_header_threads_theme_background() -> None:
    """A non-default theme background flows into both the style and the rect."""
    dark = MermaidTheme(
        background="#101010",
        node_fill="#1a1a1a",
        node_stroke="#555555",
        text_color="#eeeeee",
        edge_color="#eeeeee",
        subgraph_fill="#222233",
        subgraph_stroke="#444466",
    )
    renderer = _make_renderer(width=100.0, height=50.0, theme=dark)
    renderer.write_header()
    assert 'style="background-color: #101010;">' in renderer.output
    assert 'fill="#101010" stroke="none"/>' in renderer.output


def test_write_header_renders_integer_pixels() -> None:
    """Float width/height collapse to integer pixel strings via ``:.0f``."""
    renderer = _make_renderer(width=1024.0, height=768.0)
    renderer.write_header()
    assert 'width="1024" height="768"' in renderer.output


# === write_defs (exact SVG) ================================================


def test_write_defs_exact_output() -> None:
    """``write_defs`` emits the two arrowhead markers, both themed by edge_color.

    The default marker has markerWidth 8; the thick marker has markerWidth 11.
    Both ``<path>`` fills/strokes take ``theme.edge_color`` (light theme =
    ``#333333``). Ends with a real trailing newline.
    """
    renderer = _make_renderer()  # light theme: edge_color = "#333333"
    renderer.write_defs()
    assert renderer.output == (
        "<defs>\n"
        '  <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="5" refY="5" '
        'orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 10 10">\n'
        '    <path d="M 0 0 L 10 5 L 0 10 z" fill="#333333" stroke="#333333" '
        'stroke-width="1"/>\n'
        "  </marker>\n"
        '  <marker id="arrowhead-thick" markerWidth="11" markerHeight="11" '
        'refX="5" refY="5" orient="auto" markerUnits="userSpaceOnUse" '
        'viewBox="0 0 10 10">\n'
        '    <path d="M 0 0 L 10 5 L 0 10 z" fill="#333333" stroke="#333333" '
        'stroke-width="1"/>\n'
        "  </marker>\n"
        "</defs>\n"
    )


def test_write_defs_threads_theme_edge_color() -> None:
    """A non-default ``edge_color`` flows into both markers' fill/stroke."""
    themed = MermaidTheme(
        background="#ffffff",
        node_fill="#ECECFF",
        node_stroke="#9370DB",
        text_color="#333333",
        edge_color="#007acc",  # non-default edge color
        subgraph_fill="#ffffde",
        subgraph_stroke="#aaaa33",
    )
    renderer = _make_renderer(theme=themed)
    renderer.write_defs()
    assert renderer.output.count('fill="#007acc" stroke="#007acc"') == 2  # both paths


def test_write_defs_has_two_markers() -> None:
    """The defs block declares exactly the default + thick arrowhead markers."""
    renderer = _make_renderer()
    renderer.write_defs()
    assert renderer.output.count('<marker id="arrowhead"') == 1
    assert renderer.output.count('<marker id="arrowhead-thick"') == 1


# === write_footer (exact SVG) ==============================================


def test_write_footer_exact_output() -> None:
    """``write_footer`` appends the closing ``</svg>`` tag + a newline."""
    renderer = _make_renderer()
    renderer.write_footer()
    assert renderer.output == "</svg>\n"


# === write_* accumulation order ============================================


def test_write_methods_accumulate_in_call_order() -> None:
    """Each ``write_*`` appends to ``output`` in the order called."""
    renderer = _make_renderer()
    renderer.write_header()
    renderer.write_defs()
    renderer.write_footer()
    # the full output starts with the header, contains the defs, ends with </svg>
    assert renderer.output.startswith('<?xml version="1.0"')
    assert "<defs>" in renderer.output
    assert renderer.output.endswith("</svg>\n")


# === render_subgraph_background (exact SVG + literal-\n quirk) =============


def test_render_subgraph_background_exact_output() -> None:
    """``render_subgraph_background`` emits the cluster ``<rect>`` at 1 decimal.

    Coordinates / size render at 1 decimal (``{:.1}`` -> ``:.1f``); fill/stroke
    come from ``theme.subgraph_fill`` / ``subgraph_stroke`` (light theme =
    ``#ffffde`` / ``#aaaa33``).
    """
    renderer = _make_renderer()  # light: subgraph_fill=#ffffde, subgraph_stroke=#aaaa33
    subgraph = LayoutSubgraph(
        id="cluster_0",
        title="My Cluster",
        x=10.0,
        y=20.0,
        width=100.0,
        height=50.0,
    )
    renderer.render_subgraph_background(subgraph)
    assert renderer.output == (
        '<rect x="10.0" y="20.0" width="100.0" height="50.0" '
        'fill="#ffffde" stroke="#aaaa33" stroke-width="1"/>\\n'
    )


def test_render_subgraph_background_literal_newline_quirk() -> None:
    """Quirk: the rect ends with a *literal* ``\\n`` (backslash + n), NOT a newline.

    grok wrote ``r#".../>\\n"#`` and a raw string does not process ``\\n``, so
    the emitted bytes are the two characters backslash + n. This is reproduced
    verbatim -- the output does NOT end with a real newline here.
    """
    renderer = _make_renderer()
    subgraph = LayoutSubgraph(
        id="cluster_0",
        title=None,
        x=0.0,
        y=0.0,
        width=1.0,
        height=1.0,
    )
    renderer.render_subgraph_background(subgraph)
    # the last two characters are backslash + 'n', not a real newline
    assert renderer.output.endswith('/>\\n')
    assert not renderer.output.endswith('/>\n')  # NOT a real newline


def test_render_subgraph_background_rounds_to_one_decimal() -> None:
    """Fractional geometry renders at exactly one decimal place."""
    renderer = _make_renderer()
    subgraph = LayoutSubgraph(
        id="cluster_1",
        title=None,
        x=12.5,
        y=34.567,
        width=100.0,
        height=50.0,
    )
    renderer.render_subgraph_background(subgraph)
    assert 'x="12.5"' in renderer.output  # 12.5 -> "12.5"
    assert 'y="34.6"' in renderer.output  # 34.567 -> "34.6" (1 decimal)


def test_render_subgraph_background_threads_theme_subgraph_colors() -> None:
    """A non-default subgraph palette flows into the rect fill/stroke."""
    themed = MermaidTheme(
        background="#ffffff",
        node_fill="#ECECFF",
        node_stroke="#9370DB",
        text_color="#333333",
        edge_color="#333333",
        subgraph_fill="#f0f0ff",
        subgraph_stroke="#9999cc",
    )
    renderer = _make_renderer(theme=themed)
    subgraph = LayoutSubgraph(
        id="cluster_0", title=None, x=5.0, y=6.0, width=10.0, height=8.0
    )
    renderer.render_subgraph_background(subgraph)
    assert 'fill="#f0f0ff" stroke="#9999cc"' in renderer.output


# === internal-module contract (mirrors grok private ``mod svg_renderer;``) ==


def test_svg_renderer_module_has_no_all_until_r275c_close() -> None:
    """R275a does NOT establish ``__all__`` yet (mirrors the layout precedent).

    The :mod:`.layout` internal module wrote its ``__all__`` only once it
    reached feature completeness; svg_renderer follows the same discipline --
    ``__all__`` is established at the R275c close (the two public entry points
    ``render`` / ``render_with_config``).
    """
    assert not hasattr(svg_renderer_mod, "__all__")


def test_svg_renderer_symbols_not_in_to_svg_barrel() -> None:
    """svg_renderer is internal: its symbols are NOT re-exported via barrel.

    The ``to_svg`` barrel tracks grok's crate-root ``pub use`` surface, which
    omits ``svg_renderer`` (the crate calls ``svg_renderer::render`` /
    ``svg_renderer::render_with_config`` internally from ``lib.rs`` -- never
    ``pub use``-d at the root).
    """
    internal_symbols = [
        "EdgeCurve",
        "SvgRenderOptions",
        "SvgRenderer",
        "DEFAULT_FONT_FAMILY",
        "EDGE_ARROWHEAD_OFFSET",
    ]
    for symbol in internal_symbols:
        assert symbol not in to_svg.__all__


def test_svg_renderer_importable_via_deep_path() -> None:
    """svg_renderer is importable as ``minimax_code.mermaid.to_svg.svg_renderer``.

    The deep path is how the future ``lib``/host leaf will consume the renderer
    (``svg_renderer::render``) -- no barrel re-export.
    """
    import importlib

    deep = importlib.import_module("minimax_code.mermaid.to_svg.svg_renderer")
    assert deep.EdgeCurve is EdgeCurve
    assert deep.SvgRenderOptions is SvgRenderOptions
    assert deep.SvgRenderer is SvgRenderer


def test_mermaid_root_barrel_unchanged_by_r275a() -> None:
    """R275a adds an internal module; the R38 root surface stays at 17."""
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__


# === R275b: node-shape dispatch + emitters + text ============================
#
# The R275b slice fuses grok's ``render_subgraph_title`` / ``render_node`` /
# 10 shape emitters / ``render_text`` / ``render_text_lines`` / ``escape_xml``.
# Every assertion below is a byte-exact comparison against grok's format
# strings (coordinates at ``:.1f``, theme colors via ``unwrap_or`` fallback).

# A six-word label that wraps to 2 lines at DEFAULT_CHAR_WIDTH (8.0) but
# fits on 1 line at STATE_CHAR_WIDTH (6.7) -- separates the two render_text
# char-width forks.
_SIX_WORDS = "word word word word word word"


def _make_node(
    shape: NodeShape,
    *,
    x: float = 100.0,
    y: float = 50.0,
    width: float = 80.0,
    height: float = 40.0,
    label: str = "",
    fill_color: str | None = None,
    stroke_color: str | None = None,
) -> LayoutNode:
    """Build a :class:`LayoutNode` with the given shape + geometry."""
    return LayoutNode(
        id="n1",
        x=x,
        y=y,
        width=width,
        height=height,
        shape=shape,
        label=label,
        fill_color=fill_color,
        stroke_color=stroke_color,
    )


# === render_node: exhaustive 12-variant dispatch ===========================


@pytest.mark.parametrize(
    ("shape", "marker"),
    [
        (NodeShape.Rectangle, 'rx="0.0"'),
        (NodeShape.RoundedRectangle, 'rx="5.0"'),
        (NodeShape.Stadium, 'rx="20.0"'),  # height=40 -> h/2=20
        (NodeShape.Diamond, "<polygon"),
        (NodeShape.Circle, "<circle"),
        (NodeShape.StartState, 'stroke-width="1.5"'),
        (NodeShape.EndState, 'stroke="none"'),  # inner circle
        (NodeShape.ForkJoin, 'rx="1"'),
        (NodeShape.Hexagon, "<polygon"),
        (NodeShape.Cylinder, "<path"),
        (NodeShape.Subroutine, "<line"),
        (NodeShape.Asymmetric, "<polygon"),
    ],
)
def test_render_node_dispatches_each_shape_to_its_emitter(
    shape: NodeShape, marker: str
) -> None:
    """Each of the 12 NodeShape variants routes to its shape-specific emitter.

    Mirrors grok's exhaustive ``match node.shape``: Rectangle /
    RoundedRectangle / Stadium all hit ``render_rectangle`` (differing only
    in ``rx``); every other variant hits its own emitter.
    """
    renderer = _make_renderer()
    renderer.render_node(_make_node(shape, label=""))
    assert marker in renderer.output


# === render_rectangle (rx knobs + label + color fallback) ===================


def test_render_rectangle_exact_output_with_label() -> None:
    """Rectangle (rx=0.0) emits a themed rect + a centered single-line label."""
    renderer = _make_renderer()  # light: node_fill=#ECECFF, node_stroke=#9370DB
    renderer.render_node(_make_node(NodeShape.Rectangle, label="Hi"))
    assert renderer.output == (
        '<rect x="60.0" y="30.0" width="80.0" height="40.0" rx="0.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
        '<text text-anchor="middle" dominant-baseline="central" '
        'font-family="Trebuchet MS, verdana, arial, sans-serif" '
        'font-size="16" fill="#333333">\n'
        '<tspan x="100.0" y="50.0">Hi</tspan>\n'
        "</text>\n"
    )


def test_render_rectangle_empty_label_emits_no_text() -> None:
    """An empty label wraps to zero lines -> no <text> appended."""
    renderer = _make_renderer()
    renderer.render_node(_make_node(NodeShape.Rectangle, label=""))
    assert "<text" not in renderer.output
    assert renderer.output.endswith('stroke-width="1"/>\n')


def test_render_rectangle_explicit_colors_override_theme() -> None:
    """Explicit fill_color / stroke_color override the theme defaults."""
    renderer = _make_renderer()
    node = _make_node(
        NodeShape.Rectangle, label="", fill_color="#ff0000", stroke_color="#00ff00"
    )
    renderer.render_node(node)
    assert 'fill="#ff0000" stroke="#00ff00"' in renderer.output


def test_render_rectangle_partial_color_fill_only() -> None:
    """Only fill_color set -> fill overridden, stroke falls back to theme."""
    renderer = _make_renderer()
    node = _make_node(NodeShape.Rectangle, label="", fill_color="#ff0000")
    renderer.render_node(node)
    assert 'fill="#ff0000"' in renderer.output
    assert 'stroke="#9370DB"' in renderer.output  # theme node_stroke


def test_render_rounded_rectangle_rx_five_exact() -> None:
    """RoundedRectangle routes to render_rectangle with rx=5.0."""
    renderer = _make_renderer()
    renderer.render_node(_make_node(NodeShape.RoundedRectangle, label=""))
    assert renderer.output == (
        '<rect x="60.0" y="30.0" width="80.0" height="40.0" rx="5.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


def test_render_stadium_rx_half_height_exact() -> None:
    """Stadium routes to render_rectangle with rx=height/2 (full pill)."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Stadium, width=80.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<rect x="60.0" y="30.0" width="80.0" height="40.0" rx="20.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


# === render_start_state / render_end_state =================================


def test_render_start_state_exact_output() -> None:
    """Start state: filled edge-color circle, thicker 1.5 stroke (grok)."""
    renderer = _make_renderer()  # light: edge_color=#333333
    renderer.render_node(
        _make_node(NodeShape.StartState, width=40.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<circle cx="100.0" cy="50.0" r="20.0" fill="#333333" stroke="#333333" '
        'stroke-width="1.5"/>\n'
    )


def test_render_end_state_exact_output() -> None:
    """End state: outer circle (node_stroke fill, background stroke) + inner
    circle (background fill, no stroke). inner_r=min(max(16,11),18)=16."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.EndState, width=40.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<circle cx="100.0" cy="50.0" r="20.0" fill="#9370DB" stroke="#ffffff" '
        'stroke-width="1"/>\n'
        '<circle cx="100.0" cy="50.0" r="16.0" fill="#ffffff" stroke="none"/>\n'
    )


def test_render_end_state_inner_radius_clamps_when_small() -> None:
    """A tiny end state exercises the inner-radius clamp.

    outer_r=3 -> inner_r=min(max(3-4, 3*0.55), 3-2)=min(max(-1, 1.65), 1)=1.0.
    The ``outer*0.55`` floor wins the lower bound; ``outer-2`` clamps the top.
    """
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.EndState, width=6.0, height=6.0, label="")
    )
    assert renderer.output == (
        '<circle cx="100.0" cy="50.0" r="3.0" fill="#9370DB" stroke="#ffffff" '
        'stroke-width="1"/>\n'
        '<circle cx="100.0" cy="50.0" r="1.0" fill="#ffffff" stroke="none"/>\n'
    )


# === render_fork_join ======================================================


def test_render_fork_join_literal_rx_one_exact() -> None:
    """Fork/join bar: rect with the *literal* ``rx="1"`` (not ``rx="1.0"``)."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.ForkJoin, width=60.0, height=10.0, label="")
    )
    assert renderer.output == (
        '<rect x="70.0" y="45.0" width="60.0" height="10.0" rx="1" '
        'fill="#333333" stroke="#333333" stroke-width="1"/>\n'
    )


# === render_diamond / render_circle / render_hexagon =======================


def test_render_diamond_four_points_exact() -> None:
    """Diamond: 4-point polygon (top / right / bottom / left of the box)."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Diamond, width=80.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<polygon points="100.0,30.0 140.0,50.0 100.0,70.0 60.0,50.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


def test_render_circle_exact_output() -> None:
    """Circle: radius is half the smaller box dimension."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Circle, width=40.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<circle cx="100.0" cy="50.0" r="20.0" fill="#ECECFF" stroke="#9370DB" '
        'stroke-width="1"/>\n'
    )


def test_render_hexagon_six_points_exact() -> None:
    """Hexagon: 6-point polygon, top/bottom edges inset by height/3 (=20)."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Hexagon, width=90.0, height=60.0, label="")
    )
    assert renderer.output == (
        '<polygon points="75.0,20.0 125.0,20.0 145.0,50.0 125.0,80.0 '
        '75.0,80.0 55.0,50.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


# === render_cylinder =======================================================


def test_render_cylinder_path_and_ellipse_exact() -> None:
    """Cylinder: body path (two sides + two half-ellipse arcs) + top ellipse cap.

    ellipse_ry=min(hw/4, hh/2)=min(20, 30)=10; the body spans body_top=30 to
    body_bottom=70; the visible cap is a full ellipse at cy=body_top=30.
    """
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Cylinder, width=80.0, height=60.0, label="")
    )
    assert renderer.output == (
        '<path d="M 60.0 30.0 L 60.0 70.0 A 40.0 10.0 0 0 0 140.0 70.0 '
        'L 140.0 30.0 A 40.0 10.0 0 0 0 60.0 30.0 Z" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
        '<ellipse cx="100.0" cy="30.0" rx="40.0" ry="10.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


# === render_subroutine / render_asymmetric =================================


def test_render_subroutine_rect_and_bars_exact() -> None:
    """Subroutine: rect + two vertical <line> bars inset 8px from each edge."""
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Subroutine, width=80.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<rect x="60.0" y="30.0" width="80.0" height="40.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
        '<line x1="68.0" y1="30.0" x2="68.0" y2="70.0" '
        'stroke="#9370DB" stroke-width="1"/>\n'
        '<line x1="132.0" y1="30.0" x2="132.0" y2="70.0" '
        'stroke="#9370DB" stroke-width="1"/>\n'
    )


def test_render_asymmetric_v_notch_exact() -> None:
    """Asymmetric ``>text]`` flag: 5-point polygon with a left V-notch.

    point_offset=hh=20; the notch tip sits at the vertical center (60.0, 50.0).
    """
    renderer = _make_renderer()
    renderer.render_node(
        _make_node(NodeShape.Asymmetric, width=80.0, height=40.0, label="")
    )
    assert renderer.output == (
        '<polygon points="80.0,30.0 140.0,30.0 140.0,70.0 80.0,70.0 60.0,50.0" '
        'fill="#ECECFF" stroke="#9370DB" stroke-width="1"/>\n'
    )


# === render_text (state-vs-default char-width fork) ========================


def test_render_text_default_width_wraps_six_words_to_two_lines() -> None:
    """Non-state mode uses DEFAULT_CHAR_WIDTH (8.0): 6 words exceed the cap
    once the 6th is added -> 2 wrapped lines -> 2 tspans."""
    renderer = _make_renderer()  # is_state_diagram=False
    renderer.render_text(0.0, 0.0, _SIX_WORDS)
    assert renderer.output.count("<tspan") == 2


def test_render_text_state_width_fits_six_words_on_one_line() -> None:
    """State mode uses STATE_CHAR_WIDTH (6.7): the same 6 words now fit on
    one line -> 1 tspan."""
    renderer = _make_renderer(is_state_diagram=True)
    renderer.render_text(0.0, 0.0, _SIX_WORDS)
    assert renderer.output.count("<tspan") == 1


def test_render_text_empty_emits_nothing() -> None:
    """Empty text wraps to zero lines -> nothing appended."""
    renderer = _make_renderer()
    renderer.render_text(0.0, 0.0, "")
    assert renderer.output == ""


# === render_text_lines (centering + escaping) ==============================


def test_render_text_lines_centers_and_escapes_exact() -> None:
    """Two lines center around y=50 at font_size=20 (line_height_px=22).

    start_y=50-(2-1)*22/2=39; line 1 at y=39, line 2 at y=61. Each sub-line's
    words join with a space and the body is XML-escaped (``&`` -> ``&amp;``,
    ``<`` -> ``&lt;``, ``>`` -> ``&gt;``, ``"`` -> ``&quot;``).
    """
    renderer = _make_renderer()
    renderer.render_text_lines(
        100.0, 50.0, [["a<b>", "c&d"], ['e"f']], 20.0, DEFAULT_LINE_HEIGHT, "#333333"
    )
    assert renderer.output == (
        '<text text-anchor="middle" dominant-baseline="central" '
        'font-family="Trebuchet MS, verdana, arial, sans-serif" '
        'font-size="20" fill="#333333">\n'
        '<tspan x="100.0" y="39.0">a&lt;b&gt; c&amp;d</tspan>\n'
        '<tspan x="100.0" y="61.0">e&quot;f</tspan>\n'
        "</text>\n"
    )


# === render_subgraph_title =================================================


def test_render_subgraph_title_exact_output() -> None:
    """A cluster title wraps + centers over the cluster.

    title_x=10+100/2=60; the single wrapped line's text_height=24 (1 line at
    the default font), so title_y=20+0+24/2=32. The tspan then anchors at 32.
    """
    renderer = _make_renderer()
    subgraph = LayoutSubgraph(
        id="cluster_0", title="My Cluster", x=10.0, y=20.0, width=100.0, height=50.0
    )
    renderer.render_subgraph_title(subgraph)
    assert renderer.output == (
        '<text text-anchor="middle" dominant-baseline="central" '
        'font-family="Trebuchet MS, verdana, arial, sans-serif" '
        'font-size="16" fill="#333333">\n'
        '<tspan x="60.0" y="32.0">My Cluster</tspan>\n'
        "</text>\n"
    )


def test_render_subgraph_title_none_emits_nothing() -> None:
    """A None title short-circuits -> nothing appended."""
    renderer = _make_renderer()
    subgraph = LayoutSubgraph(
        id="cluster_0", title=None, x=10.0, y=20.0, width=100.0, height=50.0
    )
    renderer.render_subgraph_title(subgraph)
    assert renderer.output == ""


# === escape_xml (five-character chain, &-first) ============================


def test_escape_xml_escapes_five_chars_ampersand_first() -> None:
    """All five XML-significant chars escape; & first so later entities safe."""
    assert (
        SvgRenderer.escape_xml('a&b<c>d"e\'f')
        == 'a&amp;b&lt;c&gt;d&quot;e&#39;f'
    )


def test_escape_xml_ampersand_first_avoids_double_escaping() -> None:
    """Escaping & first means the &lt; from < is not re-escaped to &amp;lt;."""
    assert SvgRenderer.escape_xml("&<") == "&amp;&lt;"


def test_escape_xml_passes_through_plain_text() -> None:
    """Text without special chars is returned unchanged."""
    assert SvgRenderer.escape_xml("plain text 123") == "plain text 123"


def test_escape_xml_empty_string_unchanged() -> None:
    """Empty in, empty out."""
    assert SvgRenderer.escape_xml("") == ""
