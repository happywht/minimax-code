"""Black-box tests for the migrated mermaid-to-svg SVG emitter foundation
(R275a).

Exercises :mod:`minimax_code.mermaid.to_svg.svg_renderer` -- the R275a
foundation slice fused from grok ``mermaid-to-svg/src/svg_renderer.rs``
(direction (1), leaf 7, sub-leaf R275a). The full renderer spans three
sub-leaves (R275a/b/c); this test file covers only the R275a surface:

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
from minimax_code.mermaid.to_svg.layout import LayoutSubgraph
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
