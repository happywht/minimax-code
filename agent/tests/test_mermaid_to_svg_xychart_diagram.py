"""Black-box + white-box tests for the migrated xychart-beta renderer (R293).

Exercises :mod:`minimax_code.mermaid.to_svg.xychart_diagram` -- the 15th
per-diagram leaf and 14th self-contained SVG emitter of the R269--R292 render
stack (direction (1) brick 24). Mirrors grok ``xychart_diagram.rs`` (867
lines): a cartesian line-chart engine that parses an ``xychart-beta`` source
(title / axes / categorical or numeric x-domain / ``line`` series), computes
a d3-style tick layout, and emits a fixed ``700x500`` canvas with two axes,
ticks, optional titles, and one polyline path per series.

Coverage groups:

* Dispatch smoke -- the ``xychart-beta`` token routes through the dedicated
  arm, not the unsupported surface.
* SVG structure -- the ``id="my-svg"`` header, the ``aria-roledescription``
  role, the fixed ``700x500`` viewBox, the ``main`` group + background rect,
  the closing ``mermaid-tmp-group`` sentinel.
* Series rendering -- one ``line-plot-{idx}`` group per series, the
  Tableau-10 ``SERIES_PALETTE`` stroke cycled by index, the ``stroke-width=2``
  polyline; a ``bar`` series is silently dropped at parse time.
* Parse rules -- categorical / numeric x-axis, the labeled-range title, the
  ``Numeric{0,0}`` default for a missing x-axis, the data-driven y-range, the
  ``%%`` / blank-line skipping.
* Parse errors -- missing header, every empty plot, a bad number, an unclosed
  bracket, an unclosed category list, the line-numbered message.
* Geometry -- ``_scale_linear`` (incl. the degenerate-domain short-circuit),
  ``_d3_ticks`` (forward / reversed / single / zero-count), ``_tick_step``'s
  nice-step thresholds, ``_format_tick`` (integer vs fractional), the
  categorical vs numeric ``_layout_x_axis`` (band centers, d3 ticks, outer
  padding), ``series_point_x`` (shared point count + band centers + single
  point at ``x0``), ``_points_to_path_d``.
* Band font shrink -- a wide category in a narrow band clamps to
  ``MIN_X_LABEL_FONT_SIZE``; a comfortable band keeps the full font.
* Palette -- the 10-color Tableau palette constant.
* Theme awareness -- the 2 channels (``text_color`` -> axis-line strokes +
  every text fill; ``background`` -> the SVG ``background-color`` style + the
  ``main`` background rect); dark differs from light; series colors are NOT
  themed (they come from ``SERIES_PALETTE``).
* Helpers -- ``_round`` (round half away from zero, not banker's), ``_fmt``
  (Rust f64 Display: integer floats drop ``.0``), ``_escape_xml`` (the
  ``&#39;`` numeric entity, diverging from journey/block/gitgraph's
  ``&apos;``).
* Public surface -- the single-symbol ``__all__`` and the dispatch no longer
  lists ``xychart-beta`` as unsupported (the set shrank 10 -> 9 in R293).
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.xychart_diagram import (
    AXIS_LABEL_FONT_SIZE,
    CHART_HEIGHT,
    CHART_WIDTH,
    MIN_X_LABEL_FONT_SIZE,
    SERIES_PALETTE,
    _approx_text_height,
    _approx_text_width,
    _auto_y_range,
    _CategoryGeom,
    _CategoryXAxis,
    _d3_ticks,
    _escape_xml,
    _fmt,
    _format_tick,
    _layout_x_axis,
    _NumericGeom,
    _NumericXAxis,
    _parse_category_list,
    _parse_xychart,
    _points_to_path_d,
    _round,
    _scale_linear,
    _strip_keyword,
    _tick_step,
    _unquote,
    _XAxisLayout,
    render_xychart_diagram_to_svg,
)

# A canonical category chart reused by several structural + theme tests: a
# two-series monthly view with a titled categorical x-axis and an explicit
# y-range, so the parser exercises the category list + labeled-range arms.
_CATEGORY_CHART = (
    'xychart-beta\n'
    'title "Monthly Sales"\n'
    'x-axis "Month" [Jan, Feb, Mar]\n'
    'y-axis "Sales" 0 --> 100\n'
    "line [10, 40, 80]\n"
    "line [20, 30, 50]"
)


# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_xychart_dispatches_to_renderer() -> None:
    """An ``xychart-beta`` block routes through the dedicated renderer."""
    svg = render_mermaid_to_svg("xychart-beta\nx-axis [a, b]\nline [1, 2]")
    assert isinstance(svg, str)
    # grok L95 emits the root with ``id="my-svg"`` as the *last* attribute
    # (after ``width="100%"``), so the substring check must not anchor on
    # ``<svg id=...`` -- mirroring test_svg_carries_my_svg_id_and_xychart_role.
    assert 'id="my-svg"' in svg
    assert 'aria-roledescription="xychart"' in svg


def test_render_xychart_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2, 3]", MermaidTheme.light()
    )
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


# === SVG structure ==========================================================


def test_svg_carries_my_svg_id_and_xychart_role() -> None:
    """The root carries ``id="my-svg"`` + the xychart aria role + viewBox."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert 'id="my-svg"' in svg
    assert 'aria-roledescription="xychart"' in svg
    assert 'role="graphics-document document"' in svg


def test_svg_carries_fixed_700x500_viewbox() -> None:
    """The canvas is the fixed ``700x500`` (CHART_WIDTH x CHART_HEIGHT)."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert f'viewBox="0 0 {_fmt(CHART_WIDTH)} {_fmt(CHART_HEIGHT)}"' in svg
    assert f'max-width: {_fmt(CHART_WIDTH)}px' in svg


def test_svg_opens_main_group_with_background_rect() -> None:
    """The ``main`` group opens with a ``background`` rect painted from the theme."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert '<g/><g class="main">' in svg
    assert 'class="background"' in svg


def test_svg_closes_with_mermaid_tmp_group() -> None:
    """The SVG closes with the ``mermaid-tmp-group`` sentinel (grok L224)."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert svg.endswith('<g class="mermaid-tmp-group"/></svg>')


# === series rendering =======================================================


def test_svg_emits_line_plot_group_per_series() -> None:
    """One ``line-plot-{idx}`` group per series, indexed from 0."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2, 3]\nline [4, 5, 6]", MermaidTheme.light()
    )
    assert '<g class="plot">' in svg
    assert '<g class="line-plot-0">' in svg
    assert '<g class="line-plot-1">' in svg


def test_svg_uses_tableau_palette_for_series_strokes() -> None:
    """Series colors cycle the fixed Tableau-10 palette by index."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2]\nline [3, 4]", MermaidTheme.light()
    )
    assert f'stroke="{SERIES_PALETTE[0]}"' in svg
    assert f'stroke="{SERIES_PALETTE[1]}"' in svg


def test_series_path_uses_stroke_width_two() -> None:
    """The polyline path carries the grok ``stroke-width=2``."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2]", MermaidTheme.light()
    )
    assert '<path stroke-width="2" stroke="' in svg


def test_bar_series_is_silently_ignored() -> None:
    """grok ships only the ``line`` path; a ``bar`` series yields no plot."""
    chart = _parse_xychart("xychart-beta\nbar [1, 2, 3]\nline [4, 5, 6]")
    assert len(chart.series) == 1
    assert chart.series[0] == [4.0, 5.0, 6.0]


def test_bar_series_does_not_render_in_svg() -> None:
    """A dropped ``bar`` series never reaches a ``line-plot-{idx}`` group."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nbar [1, 2, 3]\nline [4, 5, 6]", MermaidTheme.light()
    )
    assert '<g class="line-plot-0">' in svg
    assert '<g class="line-plot-1">' not in svg


# === chart + axis titles ====================================================


def test_chart_title_emitted_when_present() -> None:
    svg = render_xychart_diagram_to_svg(_CATEGORY_CHART, MermaidTheme.light())
    assert '<g class="chart-title">' in svg
    assert "Monthly Sales" in svg


def test_chart_title_absent_when_not_set() -> None:
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2]", MermaidTheme.light()
    )
    assert '<g class="chart-title">' not in svg


def test_chart_title_escapes_apostrophe_to_numeric_entity() -> None:
    """The xychart ``escape_xml`` variant emits ``&#39;`` (not ``&apos;``)."""
    svg = render_xychart_diagram_to_svg(
        'xychart-beta\ntitle "It\'s"\nline [1, 2]', MermaidTheme.light()
    )
    assert "It&#39;s" in svg
    assert "It's" not in svg


def test_x_axis_title_emitted() -> None:
    svg = render_xychart_diagram_to_svg(_CATEGORY_CHART, MermaidTheme.light())
    assert '<g class="x-axis-title">' in svg


def test_y_axis_title_uses_rotate_minus_90() -> None:
    """The y-axis title is rotated ``-90`` deg (grok L216)."""
    svg = render_xychart_diagram_to_svg(_CATEGORY_CHART, MermaidTheme.light())
    assert '<g class="y-axis-title">' in svg
    assert "rotate(-90)" in svg


# === axis classes (the ``axisl-line`` typo fidelity) ========================


def test_bottom_axis_uses_axis_line_class() -> None:
    """The bottom axis uses the correctly-spelled ``axis-line`` class."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert '<g class="bottom-axis">' in svg
    assert '<g class="axis-line">' in svg


def test_left_axis_uses_axisl_line_typo_class() -> None:
    """grok L169 ships ``axisl-line`` (not ``axis-line``) -- preserved verbatim."""
    svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1]", MermaidTheme.light()
    )
    assert '<g class="left-axis">' in svg
    assert '<g class="axisl-line">' in svg


# === parse rules ============================================================


def test_parse_category_x_axis() -> None:
    """An ``x-axis [a, b, c]`` body yields a categorical domain."""
    chart = _parse_xychart("xychart-beta\nx-axis [a, b, c]\nline [1, 2, 3]")
    assert isinstance(chart.x_axis, _CategoryXAxis)
    assert chart.x_axis.categories == ["a", "b", "c"]


def test_parse_numeric_x_axis() -> None:
    """An ``x-axis min --> max`` body yields a numeric domain."""
    chart = _parse_xychart("xychart-beta\nx-axis 0 --> 10\nline [1, 2, 3]")
    assert isinstance(chart.x_axis, _NumericXAxis)
    assert chart.x_axis.min == 0.0
    assert chart.x_axis.max == 10.0


def test_parse_numeric_x_axis_with_title() -> None:
    """A labeled range keeps its left-side title."""
    chart = _parse_xychart('xychart-beta\nx-axis "Count" 0 --> 10\nline [1, 2]')
    assert chart.x_title == "Count"
    assert isinstance(chart.x_axis, _NumericXAxis)


def test_missing_x_axis_defaults_to_numeric_zero_zero() -> None:
    """No ``x-axis`` line -> a degenerate ``Numeric{0, 0}`` (grok default)."""
    chart = _parse_xychart("xychart-beta\nline [1, 2, 3]")
    assert isinstance(chart.x_axis, _NumericXAxis)
    assert chart.x_axis.min == 0.0
    assert chart.x_axis.max == 0.0


def test_missing_y_range_auto_ranges_from_data() -> None:
    """No explicit y-range -> auto-range spans the data min/max."""
    chart = _parse_xychart("xychart-beta\nline [3, 1, 2]")
    assert chart.y_min == 1.0
    assert chart.y_max == 3.0


def test_parse_skips_comments_and_blank_lines() -> None:
    """``%%`` comments and blank lines are ignored before and within the body."""
    chart = _parse_xychart("%% lead\n\nxychart-beta\n  %% inline\nline [1, 2]")
    assert chart.series == [[1.0, 2.0]]


def test_parse_unquote_strips_surrounding_quotes() -> None:
    """A quoted title drops one pair of matching surrounding quotes."""
    chart = _parse_xychart('xychart-beta\ntitle "Demo"\nline [1, 2]')
    assert chart.title == "Demo"


def test_parse_category_list_splits_and_unquotes() -> None:
    assert _parse_category_list('a, "b", c') == ["a", "b", "c"]
    assert _parse_category_list("a,b,c") == ["a", "b", "c"]
    assert _parse_category_list("") == []


def test_unquote_strips_matching_surrounding_quotes() -> None:
    assert _unquote('"hello"') == "hello"
    assert _unquote("'world'") == "world"
    assert _unquote("plain") == "plain"
    # Mismatched quotes are left verbatim (only a matching pair is stripped).
    assert _unquote("\"mismatch'") == "\"mismatch'"


def test_strip_keyword_matches_standalone_only() -> None:
    """``line`` matches only when followed by end / whitespace / ``[``."""
    assert _strip_keyword("line", "line") == ""
    assert _strip_keyword("line [1, 2]", "line") == " [1, 2]"
    assert _strip_keyword("line[1]", "line") == "[1]"
    # ``linear`` does not match (the trailing char is ``a``).
    assert _strip_keyword("linear", "line") is None
    # A different keyword entirely.
    assert _strip_keyword("bar", "line") is None


# === parse errors ===========================================================


def test_parse_missing_header_raises() -> None:
    """A first content line that is not ``xychart-beta`` raises."""
    with pytest.raises(ParseError):
        _parse_xychart("line [1, 2, 3]")


def test_parse_empty_input_raises() -> None:
    with pytest.raises(ParseError):
        _parse_xychart("")


def test_parse_only_comments_raises() -> None:
    with pytest.raises(ParseError):
        _parse_xychart("%% a\n\n%% b")


def test_parse_all_empty_series_raises() -> None:
    """Every series empty -> ``xychart requires at least one plot``."""
    with pytest.raises(ParseError):
        _parse_xychart("xychart-beta\nline []")


def test_parse_bad_number_raises() -> None:
    with pytest.raises(ParseError):
        _parse_xychart("xychart-beta\nline [1, abc]")


def test_parse_unclosed_bracket_raises() -> None:
    with pytest.raises(ParseError):
        _parse_xychart("xychart-beta\nline [1, 2")


def test_parse_x_axis_unclosed_category_raises() -> None:
    with pytest.raises(ParseError):
        _parse_xychart("xychart-beta\nx-axis [a, b\nline [1]")


def test_parse_error_str_carries_line_number() -> None:
    """ParseError stringifies with the 1-based line number (error.py contract)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_xychart("xychart-beta\nline [1, abc]")
    assert "line 2" in str(exc_info.value)


# === geometry: scale_linear =================================================


def test_scale_linear_maps_midpoint() -> None:
    assert _scale_linear(5.0, 0.0, 10.0, 0.0, 100.0) == 50.0


def test_scale_linear_clamps_to_range_endpoints() -> None:
    assert _scale_linear(0.0, 0.0, 10.0, 100.0, 200.0) == 100.0
    assert _scale_linear(10.0, 0.0, 10.0, 100.0, 200.0) == 200.0


def test_scale_linear_degenerate_domain_returns_range_min() -> None:
    """A zero-width domain short-circuits to ``range_min``."""
    assert _scale_linear(99.0, 5.0, 5.0, 7.0, 13.0) == 7.0


# === geometry: d3_ticks =====================================================


def test_d3_ticks_equal_start_stop_returns_single() -> None:
    assert _d3_ticks(0.0, 0.0, 10) == [0.0]


def test_d3_ticks_zero_count_returns_empty() -> None:
    assert _d3_ticks(0.0, 10.0, 0) == []


def test_d3_ticks_nice_decade_range() -> None:
    """``[0, 100]`` over 10 ticks -> the 11-value decade ladder 0..100."""
    ticks = _d3_ticks(0.0, 100.0, 10)
    assert ticks == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]


def test_d3_ticks_reverses_when_stop_below_start() -> None:
    """A downhill range yields the ticks in descending order."""
    ticks = _d3_ticks(10.0, 0.0, 5)
    assert ticks == [10.0, 8.0, 6.0, 4.0, 2.0, 0.0]


def test_d3_ticks_non_finite_returns_empty() -> None:
    assert _d3_ticks(float("nan"), 10.0, 10) == []


# === geometry: tick_step ====================================================


def test_tick_step_nice_decade() -> None:
    """``[0, 100] / 10`` -> step0=10 -> step1=10 -> error=1 -> 1x bump -> 10."""
    assert _tick_step(0.0, 100.0, 10.0) == 10.0


def test_tick_step_doubles_when_error_above_sqrt_two() -> None:
    """``[0, 10] / 5`` -> step0=2 -> step1=1 -> error=2 >= sqrt(2) -> 2x -> 2."""
    assert _tick_step(0.0, 10.0, 5.0) == 2.0


def test_tick_step_negates_when_range_runs_downhill() -> None:
    """A downhill range negates the step (drives the d3_ticks reversal)."""
    assert _tick_step(10.0, 0.0, 5.0) == -2.0


# === geometry: format_tick ==================================================


def test_format_tick_integer_drops_decimal() -> None:
    assert _format_tick(5.0) == "5"
    assert _format_tick(0.0) == "0"
    assert _format_tick(-3.0) == "-3"


def test_format_tick_fractional_trims_trailing_zeros() -> None:
    assert _format_tick(2.5) == "2.5"
    assert _format_tick(1.25) == "1.25"


# === geometry: layout_x_axis ================================================


def test_layout_x_axis_category_tick_positions_at_band_centers() -> None:
    """Categorical ticks sit at band centers; band_w = plot_w / n."""
    layout = _layout_x_axis(_CategoryXAxis(["a", "b", "c"]), 100.0, 300.0, 3)
    assert layout.tick_positions == [150.0, 250.0, 350.0]
    assert layout.tick_labels == ["a", "b", "c"]
    assert layout.point_count == 3
    assert isinstance(layout.geom, _CategoryGeom)
    assert layout.geom.plot_left == 100.0
    assert layout.geom.band_w == 100.0


def test_layout_x_axis_numeric_uses_d3_ticks_and_outer_padding() -> None:
    """Numeric layout mirrors the d3 tick count and insets the geom by padding."""
    layout = _layout_x_axis(_NumericXAxis(0.0, 100.0), 0.0, 500.0, 5)
    assert isinstance(layout.geom, _NumericGeom)
    assert layout.point_count == 5
    # d3_ticks(0, 100, 10) -> 11 ticks; the layout mirrors that count.
    assert len(layout.tick_positions) == len(_d3_ticks(0.0, 100.0, 10))
    # x0/x1 are inset from the plot edges by the outer padding.
    assert layout.geom.x0 > 0.0
    assert layout.geom.x1 < 500.0
    assert layout.geom.x0 < layout.geom.x1


# === geometry: series_point_x (shared point count) ==========================


def test_series_point_x_numeric_uses_shared_point_count() -> None:
    """All series share the longest series' length as the x-domain."""
    layout = _XAxisLayout(
        tick_positions=[],
        tick_labels=[],
        label_font=14.0,
        point_count=5,
        geom=_NumericGeom(x0=0.0, x1=100.0),
    )
    assert layout.series_point_x(0) == 0.0
    assert layout.series_point_x(4) == 100.0
    assert layout.series_point_x(2) == 50.0


def test_series_point_x_numeric_single_point_sits_at_x0() -> None:
    """A single-point series sits at ``x0`` (no span to distribute over)."""
    layout = _XAxisLayout(
        tick_positions=[],
        tick_labels=[],
        label_font=14.0,
        point_count=1,
        geom=_NumericGeom(x0=42.0, x1=99.0),
    )
    assert layout.series_point_x(0) == 42.0


def test_series_point_x_category_at_band_centers() -> None:
    """Categorical series points sit at the band centers ``plot_left + (i+0.5)*band_w``."""
    layout = _XAxisLayout(
        tick_positions=[],
        tick_labels=[],
        label_font=14.0,
        point_count=3,
        geom=_CategoryGeom(plot_left=100.0, band_w=50.0),
    )
    assert layout.series_point_x(0) == 125.0
    assert layout.series_point_x(1) == 175.0
    assert layout.series_point_x(2) == 225.0


# === geometry: points_to_path_d =============================================


def test_points_to_path_d_empty_returns_empty() -> None:
    assert _points_to_path_d([]) == ""


def test_points_to_path_d_single_point_is_move_only() -> None:
    assert _points_to_path_d([(1.0, 2.0)]) == "M1,2"


def test_points_to_path_d_multi_point_is_move_then_lines() -> None:
    assert _points_to_path_d([(1.0, 2.0), (3.0, 4.0)]) == "M1,2L3,4"


def test_points_to_path_d_drops_trailing_zero_for_integers() -> None:
    """Coordinates flow through ``_fmt`` so ``5.0`` renders as ``5``."""
    assert _points_to_path_d([(5.0, 0.0)]) == "M5,0"


# === band font shrink =======================================================


def test_layout_x_axis_shrinks_label_font_to_floor_for_wide_labels() -> None:
    """A wide category in a narrow band clamps to ``MIN_X_LABEL_FONT_SIZE``."""
    layout = _layout_x_axis(_CategoryXAxis(["abcdefghij"]), 0.0, 20.0, 1)
    assert layout.label_font == MIN_X_LABEL_FONT_SIZE


def test_layout_x_axis_keeps_full_font_when_bands_fit() -> None:
    """A comfortable band keeps the full ``AXIS_LABEL_FONT_SIZE``."""
    layout = _layout_x_axis(_CategoryXAxis(["ab", "cd"]), 0.0, 500.0, 2)
    assert layout.label_font == AXIS_LABEL_FONT_SIZE


# === palette ================================================================


def test_series_palette_has_ten_colors() -> None:
    """The Tableau-10 palette is a fixed 10-tuple, cycled by series index."""
    assert len(SERIES_PALETTE) == 10
    assert SERIES_PALETTE[0] == "#4e79a7"
    assert SERIES_PALETTE[9] == "#bab0ac"


# === theme awareness (2 channels) ===========================================


def test_theme_text_color_flows_into_axis_strokes_and_text() -> None:
    """``text_color`` drives every axis-line stroke + every text fill."""
    light = MermaidTheme.light()
    svg = render_xychart_diagram_to_svg("xychart-beta\nline [1, 2]", light)
    assert f'stroke="{light.text_color}"' in svg
    assert f'fill="{light.text_color}"' in svg


def test_theme_background_flows_into_svg_style_and_main_rect() -> None:
    """``background`` paints the SVG ``background-color`` style + the main rect."""
    dark = MermaidTheme.dark()
    svg = render_xychart_diagram_to_svg("xychart-beta\nline [1, 2]", dark)
    assert f"background-color: {dark.background};" in svg
    assert f'<rect fill="{dark.background}" class="background"' in svg


def test_dark_theme_svg_differs_from_light() -> None:
    light_svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2]", MermaidTheme.light()
    )
    dark_svg = render_xychart_diagram_to_svg(
        "xychart-beta\nline [1, 2]", MermaidTheme.dark()
    )
    assert light_svg != dark_svg


def test_theme_does_not_tint_series_palette() -> None:
    """Series colors come from ``SERIES_PALETTE``, never the theme text_color."""
    svg = render_xychart_diagram_to_svg("xychart-beta\nline [1, 2]", MermaidTheme.dark())
    assert f'stroke="{SERIES_PALETTE[0]}"' in svg


# === helpers: _round / _fmt / _escape_xml ===================================


def test_round_half_away_from_zero() -> None:
    """``_round`` rounds half away from zero (Rust f64::round), not banker's."""
    # The banker's-rounding divergences: 0.5 -> 1 (not 0), 2.5 -> 3 (not 2).
    assert _round(0.5) == 1
    assert _round(2.5) == 3
    assert _round(-0.5) == -1
    assert _round(-2.5) == -3
    # Non-.5 values round to the nearest integer as expected.
    assert _round(2.4) == 2
    assert _round(2.6) == 3


def test_fmt_drops_trailing_zero_for_integers() -> None:
    """``_fmt`` mirrors Rust f64 Display: integer floats drop ``.0``."""
    assert _fmt(0.0) == "0"
    assert _fmt(5.0) == "5"
    assert _fmt(-19.0) == "-19"
    assert _fmt(350.0) == "350"


def test_fmt_keeps_fraction_for_non_integers() -> None:
    assert _fmt(5.5) == "5.5"
    assert _fmt(0.625) == "0.625"


def test_escape_xml_uses_numeric_apos_entity() -> None:
    """``'`` -> ``&#39;`` (the numeric-entity variant; journey/block/gitgraph use ``&apos;``)."""
    assert _escape_xml("it's") == "it&#39;s"
    assert _escape_xml('"q"') == "&quot;q&quot;"
    assert _escape_xml("a&b<c>d") == "a&amp;b&lt;c&gt;d"


# === helpers: text measurement ==============================================


def test_approx_text_width_uses_display_units_times_font() -> None:
    """width = display_units x font_size x 0.525 (``ab`` = 2 units, font 14)."""
    assert _approx_text_width("ab", 14.0) == pytest.approx(2 * 14 * 0.525)


def test_approx_text_height_rounds_font_times_115() -> None:
    """height = round(font_size * 1.15), half away from zero."""
    assert _approx_text_height(20.0) == 23  # round(23.0)
    assert _approx_text_height(14.0) == 16  # round(16.1) away from zero


# === helpers: auto_y_range ==================================================


def test_auto_y_range_spans_all_values() -> None:
    assert _auto_y_range([[1.0, 5.0, 3.0]]) == (1.0, 5.0)
    assert _auto_y_range([[2.0, 8.0], [1.0, 9.0]]) == (1.0, 9.0)


def test_auto_y_range_pads_flat_data() -> None:
    """A flat (single-value) range is padded by one on each side."""
    assert _auto_y_range([[4.0, 4.0]]) == (3.0, 5.0)


def test_auto_y_range_empty_returns_zero_zero() -> None:
    assert _auto_y_range([]) == (0.0, 0.0)


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    from minimax_code.mermaid.to_svg import xychart_diagram

    assert xychart_diagram.__all__ == ["render_xychart_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_xychart_unsupported() -> None:
    """R291 removed ``gitGraph`` (12 -> 11); R292 removed ``mindmap`` (11 -> 10);
    R293 removed ``xychart-beta`` (10 -> 9); R294 removed ``requirementDiagram``
    (9 -> 8); R295 removed ``erDiagram`` (8 -> 7) -- the set keeps shrinking as
    renderers ship."""
    assert "xychart-beta" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 7
