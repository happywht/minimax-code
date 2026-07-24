"""Black-box tests for the migrated quadrant-chart renderer (R288).

Exercises :mod:`minimax_code.mermaid.to_svg.quadrant_diagram` -- the
functional clone of grok ``mermaid-to-svg/src/quadrant_diagram.rs``
(direction (1), brick 19). This is the 10th per-diagram leaf and the 9th
self-contained SVG emitter; it parses its own DSL and emits a fixed-size
500x500 SVG without touching the dagre flowchart stack.

Coverage matrix:

* :func:`parse_quadrant_chart` -- header scan (``quadrantChart`` declaration),
  the four line shapes (``title`` / ``x-axis`` / ``y-axis`` /
  ``quadrant-N`` / point), quote stripping, and the full parse-error matrix
  (bad header, missing point, bad axis, bad quadrant label, every malformed
  point shape).
* :func:`quadrant_theme_for` -- dark/light palette selection by
  ``theme.background`` hex prefix.
* :func:`render_quadrant_chart_to_svg` -- happy-path SVG shape, theme-aware
  palette flow, the quadrant-specific float-format split (``{:.1}`` coords
  vs ``Display`` scalars), axis label placement (middle vs start anchor),
  data-point clamping, XML escaping, and label quote stripping.
* Dispatch integration -- ``render_mermaid_to_svg("quadrantChart ...")``
  routes through the dedicated arm.
* Barrel invariant -- the per-diagram renderer does NOT enter the barrel
  ``__all__`` (stays 18); the symbol reaches callers only via dispatch.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import MermaidTheme, ParseError
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.quadrant_diagram import (
    FONT_FAMILY,
    parse_quadrant_chart,
    quadrant_theme_for,
    render_quadrant_chart_to_svg,
)

# Light palette signatures (quadrant_theme_for, light branch).
_LIGHT_Q1_FILL = "#ECECFF"
_LIGHT_Q4_FILL = "#FBFBFF"
_LIGHT_BORDER = "#C7C7F1"
_LIGHT_TEXT = "#333333"
# Dark palette signatures (quadrant_theme_for, dark branch).
_DARK_Q1_FILL = "#1f2020"
_DARK_Q4_FILL = "#2e2f2f"
_DARK_BORDER = "#e0dfdf"
_DARK_TEXT = "#ccc"
# MermaidTheme defaults.
_LIGHT_BG = "#ffffff"
_DARK_BG = "#1e1e1e"


# === parse_quadrant_chart: header scan =====================================


def test_parse_header_declaration_accepted() -> None:
    """``quadrantChart`` as the first real token parses."""
    chart = parse_quadrant_chart("quadrantChart\n  A: [0.5, 0.5]")
    assert len(chart.points) == 1


def test_parse_header_skips_blank_and_comment_lines() -> None:
    """Leading blanks / ``%%`` comments are skipped before the header."""
    chart = parse_quadrant_chart("\n%% comment\n   \nquadrantChart\n  A: [0.5, 0.5]")
    assert len(chart.points) == 1


def test_parse_header_extra_tokens_after_declaration_ok() -> None:
    """Tokens after ``quadrantChart`` on the header line are ignored."""
    chart = parse_quadrant_chart("quadrantChart auto e.g.\n  A: [0.5, 0.5]")
    assert len(chart.points) == 1


def test_parse_header_wrong_first_token_raises() -> None:
    """A first real token that is not ``quadrantChart`` is rejected at its line."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("pie title x\n  A: [0.5, 0.5]")
    assert exc_info.value.line == 1
    assert str(exc_info.value) == "Parse error at line 1: Expected 'quadrantChart' declaration"


def test_parse_header_wrong_token_after_blanks_reports_own_line() -> None:
    """The header error line is the offending line, not line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("\n\n%% c\nflowchart TD\n  A")
    assert exc_info.value.line == 4


# === parse_quadrant_chart: title / axis / quadrant / point lines ===========


def test_parse_title_sets_title() -> None:
    """``title <text>`` sets the chart title."""
    chart = parse_quadrant_chart('quadrantChart\n  title My Chart\n  A: [0.5, 0.5]')
    assert chart.title == "My Chart"


def test_parse_bare_title_keyword_is_invalid_point() -> None:
    """A bare ``title`` keyword (no trailing text) is NOT a title directive.

    grok matches the ``title `` prefix on the trimmed line
    (parse_quadrant_chart L434 ``strip_prefix("title ")``). A line that trims
    to just ``title`` carries no such prefix, so it falls through to the point
    branch where the missing colon makes it ``Invalid quadrant point``
    (L467-L472). The empty-text no-op at L436-L438 is therefore unreachable
    for a purely-whitespace title line -- there is no way to keep the
    ``title `` prefix while emptying the rest once the line is trimmed.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  title   \n  A: [0.5, 0.5]")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid quadrant point: title"


def test_parse_x_axis_range_pair() -> None:
    """``x-axis Low --> High`` parses into the ``(low, high)`` pair."""
    chart = parse_quadrant_chart("quadrantChart\n  x-axis Low --> High\n  A: [0.5, 0.5]")
    assert chart.x_axis == ("Low", "High")


def test_parse_y_axis_range_pair() -> None:
    """``y-axis Low --> High`` parses into the ``(low, high)`` pair."""
    chart = parse_quadrant_chart("quadrantChart\n  y-axis Low --> High\n  A: [0.5, 0.5]")
    assert chart.y_axis == ("Low", "High")


def test_parse_x_axis_empty_high_end() -> None:
    """An x-axis whose ``-->`` right side is empty parses to ``(low, '')``.

    grok's :func:`_parse_axis` requires the ``-->`` arrow (a missing arrow is
    ``Invalid axis``); the high end is then whatever trims to empty when the
    arrow is the last token on the line.
    """
    chart = parse_quadrant_chart("quadrantChart\n  x-axis Reach -->\n  A: [0.5, 0.5]")
    assert chart.x_axis == ("Reach", "")


def test_parse_quadrant_labels_keyed_1_to_4() -> None:
    """``quadrant-N <label>`` stores under integer keys 1-4."""
    chart = parse_quadrant_chart(
        "quadrantChart\n"
        "  quadrant-1 We want\n"
        "  quadrant-2 maybe\n"
        "  quadrant-3 reorganize\n"
        "  quadrant-4 redo\n"
        "  A: [0.5, 0.5]"
    )
    assert chart.quadrants == {
        1: "We want",
        2: "maybe",
        3: "reorganize",
        4: "redo",
    }


def test_parse_point_basic() -> None:
    """``Label: [x, y]`` parses into a QuadrantPoint with float coords."""
    chart = parse_quadrant_chart("quadrantChart\n  Reach: [0.9, 0.8]")
    assert chart.points[0].label == "Reach"
    assert chart.points[0].x == 0.9
    assert chart.points[0].y == 0.8


def test_parse_point_strips_surrounding_quotes() -> None:
    """A double-quoted point label has its surrounding quotes stripped."""
    chart = parse_quadrant_chart('quadrantChart\n  "My Point": [0.5, 0.5]')
    assert chart.points[0].label == "My Point"


def test_parse_point_label_with_internal_colon_is_invalid() -> None:
    """A label containing a colon cannot be expressed in grok's grammar.

    grok splits label from coords at the FIRST colon (parse_quadrant_chart L467
    ``split_once(':')``), so ``ratio 2:1: [0.5, 0.5]`` splits into label
    ``ratio 2`` and coords ``1: [0.5, 0.5]``. The coords do not start with ``[``
    (L480-L486), so the whole line collapses to ``Invalid quadrant point``.
    Internal colons in labels are not supported -- quote-stripping (L475-L477)
    only peels a leading/trailing ``"``, it does not relocate the split point.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  ratio 2:1: [0.5, 0.5]")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == (
        "Parse error at line 2: Invalid quadrant point: ratio 2:1: [0.5, 0.5]"
    )


def test_parse_point_integer_coords() -> None:
    """Integer coords parse as floats (0 and 1 are the axis extremes)."""
    chart = parse_quadrant_chart("quadrantChart\n  Origin: [0, 0]\n  Far: [1, 1]")
    assert chart.points[0].x == 0.0
    assert chart.points[1].x == 1.0


def test_parse_comment_lines_skipped() -> None:
    """``%%`` comment lines anywhere in the body are skipped."""
    chart = parse_quadrant_chart(
        "quadrantChart\n  %% a comment\n  A: [0.5, 0.5]\n  %% trailing\n"
    )
    assert len(chart.points) == 1


def test_parse_blank_lines_skipped() -> None:
    """Blank lines anywhere in the body are skipped."""
    chart = parse_quadrant_chart("quadrantChart\n\n  A: [0.5, 0.5]\n\n")
    assert len(chart.points) == 1


# === parse_quadrant_chart: error matrix ====================================


def test_parse_no_point_raises() -> None:
    """A chart with no points fails the final guard at line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  x-axis Low --> High")
    assert exc_info.value.line == 1
    assert str(exc_info.value) == "Parse error at line 1: Quadrant chart requires at least one point"


def test_parse_empty_body_after_header_raises() -> None:
    """A bare ``quadrantChart`` header with nothing after fails the guard."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart")
    assert "at least one point" in str(exc_info.value)


def test_parse_bad_axis_missing_arrow_raises() -> None:
    """An axis line without ``-->`` is rejected with the verbatim axis text."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  x-axis just low\n  A: [0.5, 0.5]")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid axis: just low"


def test_parse_bad_quadrant_label_no_space_raises() -> None:
    """``quadrant-1`` with no label-space is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  quadrant-1\n  A: [0.5, 0.5]")
    assert "Invalid quadrant label" in str(exc_info.value)


def test_parse_bad_quadrant_label_non_integer_raises() -> None:
    """``quadrant-X`` with a non-integer key is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  quadrant-x label\n  A: [0.5, 0.5]")
    assert "Invalid quadrant label" in str(exc_info.value)


def test_parse_bad_point_no_colon_raises() -> None:
    """A point line without a colon is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  just text here")
    assert "Invalid quadrant point" in str(exc_info.value)


def test_parse_bad_point_missing_brackets_raises() -> None:
    """A point without the ``[...]`` bracket pair is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: 0.5, 0.5")
    assert "Invalid quadrant point" in str(exc_info.value)


def test_parse_bad_point_missing_close_bracket_raises() -> None:
    """A point with only the open bracket is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: [0.5, 0.5")
    assert "Invalid quadrant point" in str(exc_info.value)


def test_parse_bad_point_wrong_arity_raises() -> None:
    """A point with other than exactly two coords is rejected."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: [0.5, 0.5, 0.5]")
    assert "Invalid quadrant point" in str(exc_info.value)


def test_parse_bad_point_single_coord_raises() -> None:
    """A point with a single coord is rejected (arity != 2)."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: [0.5]")
    assert "Invalid quadrant point" in str(exc_info.value)


def test_parse_bad_point_non_numeric_coord_raises() -> None:
    """A non-numeric coord is rejected with the full offending line."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: [abc, 0.5]")
    assert exc_info.value.line == 2
    assert "Invalid quadrant point" in str(exc_info.value)
    assert "A: [abc, 0.5]" in str(exc_info.value)


def test_parse_point_rejects_underscore_numeric() -> None:
    """Rust f64 grammar rejects ``1_000`` underscores (stricter than float)."""
    with pytest.raises(ParseError) as exc_info:
        parse_quadrant_chart("quadrantChart\n  A: [1_000, 0.5]")
    assert "Invalid quadrant point" in str(exc_info.value)


# === quadrant_theme_for: dark / light palette selection ====================


def test_theme_light_selects_light_palette() -> None:
    """The default light theme (background ``#ffffff``) picks the light palette."""
    qt = quadrant_theme_for(MermaidTheme.light())
    assert qt.quadrant1_fill == _LIGHT_Q1_FILL
    assert qt.quadrant4_fill == _LIGHT_Q4_FILL
    assert qt.border_stroke == _LIGHT_BORDER
    assert qt.title_fill == _LIGHT_TEXT
    assert qt.point_text_fill == _LIGHT_TEXT


def test_theme_dark_selects_dark_palette() -> None:
    """The dark theme (background ``#1e1e1e``, prefix ``#1``) picks the dark palette."""
    qt = quadrant_theme_for(MermaidTheme.dark())
    assert qt.quadrant1_fill == _DARK_Q1_FILL
    assert qt.quadrant4_fill == _DARK_Q4_FILL
    assert qt.border_stroke == _DARK_BORDER
    assert qt.title_fill == _DARK_TEXT


def test_theme_dark_prefix_zero_also_dark() -> None:
    """A background starting ``#0`` (near-black) also selects the dark palette."""
    qt = quadrant_theme_for(MermaidTheme(background="#0a0a0a"))
    assert qt.quadrant1_fill == _DARK_Q1_FILL
    assert qt.quadrant_text_fill == _DARK_TEXT


def test_theme_light_for_non_dark_prefix() -> None:
    """Any non-``#1``/``#0`` prefix falls through to the light palette."""
    qt = quadrant_theme_for(MermaidTheme(background="#abcdef"))
    assert qt.quadrant1_fill == _LIGHT_Q1_FILL


# === render_quadrant_chart_to_svg: happy path ==============================


def _full_chart_source() -> str:
    """A chart exercising every feature (title, both axes, 4 quadrants, point)."""
    return (
        "quadrantChart\n"
        "  title Reach and engagement\n"
        "  x-axis Low Reach --> High Reach\n"
        "  y-axis Low Engagement --> High Engagement\n"
        "  quadrant-1 We want\n"
        "  quadrant-2 maybe\n"
        "  quadrant-3 reorganize\n"
        "  quadrant-4 redo\n"
        '  Campaign A: [0.9, 0.8]'
    )


def test_render_full_chart_returns_svg_string() -> None:
    """A full chart renders to a non-empty ``<svg>...</svg>`` string."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert isinstance(svg, str)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_render_canvas_viewbox_is_500_by_500() -> None:
    """The canvas viewBox is the fixed 500x500 (scalar via Display -> bare ints)."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert 'viewBox="0 0 500 500"' in svg


def test_render_background_uses_theme_background() -> None:
    """``theme.background`` flows into the full-canvas background rect fill."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert f'fill="{_LIGHT_BG}"' in svg


def test_render_background_dark_when_dark_theme() -> None:
    """The dark theme's background hex appears in the background rect."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.dark())
    assert f'fill="{_DARK_BG}"' in svg


def test_render_emits_all_four_quadrant_fills() -> None:
    """All four quadrant fills appear (light palette)."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert _LIGHT_Q1_FILL in svg
    assert "#F1F1FF" in svg  # quadrant2
    assert "#F6F6FF" in svg  # quadrant3
    assert _LIGHT_Q4_FILL in svg


def test_render_emits_all_four_quadrant_labels() -> None:
    """All four quadrant labels render as escaped text."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert ">We want<" in svg
    assert ">maybe<" in svg
    assert ">reorganize<" in svg
    assert ">redo<" in svg


def test_render_emits_title_text() -> None:
    """The title renders as escaped text."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert ">Reach and engagement<" in svg


def test_render_emits_axis_labels() -> None:
    """Both x-axis and y-axis range labels render (low and high ends)."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert ">Low Reach<" in svg
    assert ">High Reach<" in svg
    assert ">Low Engagement<" in svg
    assert ">High Engagement<" in svg


def test_render_emits_data_point_circle_and_label() -> None:
    """A data point emits a filled circle plus its escaped label."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert "<circle" in svg
    assert 'r="5"' in svg  # POINT_RADIUS via Display
    assert ">Campaign A<" in svg


def test_render_emits_internal_and_external_borders() -> None:
    """The border stroke appears on the frame and the two internal dividers."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert _LIGHT_BORDER in svg


# === render: theme-aware palette flow ======================================


def test_render_light_palette_text_fills() -> None:
    """Light theme palette text fills (#333333) appear in the SVG."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert _LIGHT_TEXT in svg


def test_render_dark_palette_text_fills() -> None:
    """Dark theme palette text fills (#ccc) appear in the SVG."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.dark())
    assert _DARK_TEXT in svg


# === render: the quadrant-specific float-format split =====================


def test_render_title_coords_use_one_decimal() -> None:
    """The title x/y use ``{:.1}`` -> ``250.0`` / ``10.0`` (NOT Display ``250``/``10``).

    This is the quadrant-specific divergence from the other eight renderers:
    dynamic coordinates keep exactly one decimal place. If ``_fmt`` and
    ``_fmt1`` were conflated, the title would shift off-centre.
    """
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert 'x="250.0"' in svg  # CHART_WIDTH / 2.0 via _fmt1
    assert 'y="10.0"' in svg  # TITLE_PADDING via _fmt1


def test_render_scalar_knobs_drop_decimal() -> None:
    """Scalar knobs use Display -> bare ints (``500``, ``5``, ``20``)."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert 'viewBox="0 0 500 500"' in svg  # CHART_WIDTH/HEIGHT via _fmt
    assert 'r="5"' in svg  # POINT_RADIUS via _fmt
    assert 'font-size="20"' in svg  # TITLE_FONT_SIZE via _fmt


def test_render_coord_and_scalar_formats_coexist() -> None:
    """``{:.1}`` coords and ``Display`` scalars coexist in one element.

    The title text carries ``x="250.0"`` (coord, ``.1f``) AND
    ``font-size="20"`` (scalar, Display) -- proves the two formatters are
    applied to the right attributes, never swapped.
    """
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    title_start = svg.find("Reach and engagement")
    assert title_start != -1
    # Walk back to the opening <text ...> tag for this title.
    text_open = svg.rfind("<text", 0, title_start)
    title_tag = svg[text_open : title_start]
    assert 'x="250.0"' in title_tag
    assert 'font-size="20"' in title_tag


def test_render_point_coords_use_one_decimal() -> None:
    """Data point cx/cy use ``{:.1}`` (e.g. ``247.5``), never Display."""
    # Quadrant geometry with no axes: quadrant_left=5, quadrant_width=490.
    # Point at x=0.5 -> px = 5 + 0.5*490 = 250.0 -> "250.0" (not "250").
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  Mid: [0.5, 0.5]", MermaidTheme.light()
    )
    assert 'cx="250.0"' in svg
    assert 'cy="250.0"' in svg


# === render: axis label placement ==========================================


def test_render_x_axis_labels_anchor_middle_when_high_present() -> None:
    """With a high x-axis end, both labels use text-anchor=middle."""
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  x-axis Low --> High\n  A: [0.5, 0.5]",
        MermaidTheme.light(),
    )
    low_idx = svg.find(">Low<")
    assert low_idx != -1
    text_open = svg.rfind("<text", 0, low_idx)
    assert 'text-anchor="middle"' in svg[text_open:low_idx]


def test_render_x_axis_low_label_anchor_start_when_no_high() -> None:
    """With an empty x-axis high end, the low label left-aligns (anchor=start).

    ``draw_x_labels_in_middle = high != ""`` -> the low label pins to the
    quadrant's left edge with ``text-anchor="start"`` instead of centring.
    """
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  x-axis Reach -->\n  A: [0.5, 0.5]",
        MermaidTheme.light(),
    )
    low_idx = svg.find(">Reach<")
    text_open = svg.rfind("<text", 0, low_idx)
    assert 'text-anchor="start"' in svg[text_open:low_idx]


def test_render_y_axis_labels_rotated_minus_90() -> None:
    """Y-axis labels carry the ``rotate(-90)`` transform."""
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  y-axis Low --> High\n  A: [0.5, 0.5]",
        MermaidTheme.light(),
    )
    assert "rotate(-90)" in svg


# === render: data-point clamping ==========================================


def test_render_point_above_one_clamped_to_edge() -> None:
    """A point with x > 1 clamps to the right edge (same cx as x == 1)."""
    over = render_quadrant_chart_to_svg(
        "quadrantChart\n  Over: [1.5, 0.5]", MermaidTheme.light()
    )
    exact = render_quadrant_chart_to_svg(
        "quadrantChart\n  Exact: [1.0, 0.5]", MermaidTheme.light()
    )
    # Extract the first circle's cx from each render.
    over_cx = _first_circle_cx(over)
    exact_cx = _first_circle_cx(exact)
    assert over_cx == exact_cx


def test_render_point_below_zero_clamped_to_edge() -> None:
    """A point with y < 0 clamps to the bottom edge (same cy as y == 0)."""
    under = render_quadrant_chart_to_svg(
        "quadrantChart\n  Under: [0.5, -0.5]", MermaidTheme.light()
    )
    exact = render_quadrant_chart_to_svg(
        "quadrantChart\n  Exact: [0.5, 0.0]", MermaidTheme.light()
    )
    assert _first_circle_cy(under) == _first_circle_cy(exact)


def _first_circle_cx(svg: str) -> str:
    """Extract the first ``<circle>``'s ``cx`` attribute value."""
    start = svg.find("<circle")
    cx_start = svg.find('cx="', start) + len('cx="')
    cx_end = svg.find('"', cx_start)
    return svg[cx_start:cx_end]


def _first_circle_cy(svg: str) -> str:
    """Extract the first ``<circle>``'s ``cy`` attribute value."""
    start = svg.find("<circle")
    cy_start = svg.find('cy="', start) + len('cy="')
    cy_end = svg.find('"', cy_start)
    return svg[cy_start:cy_end]


# === render: XML escaping & quote stripping ===============================


def test_render_escapes_ampersand_in_label() -> None:
    """A ``&`` in a label escapes to ``&amp;``."""
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  A & B: [0.5, 0.5]", MermaidTheme.light()
    )
    assert "A &amp; B" in svg
    assert "A & B<" not in svg


def test_render_escapes_angle_brackets_in_label() -> None:
    """``<`` and ``>`` in a label escape to ``&lt;`` / ``&gt;``."""
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  A<B>C: [0.5, 0.5]", MermaidTheme.light()
    )
    assert "A&lt;B&gt;C" in svg


def test_render_escapes_apostrophe_with_named_entity() -> None:
    """A ``'`` escapes to ``&apos;`` (named entity, NOT numeric ``&#39;``)."""
    svg = render_quadrant_chart_to_svg(
        "quadrantChart\n  it's: [0.5, 0.5]", MermaidTheme.light()
    )
    assert "it&apos;s" in svg
    assert "&#39;" not in svg


def test_render_strips_surrounding_quotes_then_escapes_inner() -> None:
    """A quoted label is unquoted, then any inner XML chars are escaped."""
    svg = render_quadrant_chart_to_svg(
        'quadrantChart\n  "A & B": [0.5, 0.5]', MermaidTheme.light()
    )
    assert ">A &amp; B<" in svg


def test_render_uses_mermaid_font_family() -> None:
    """Every text element carries the shared mermaid font stack."""
    svg = render_quadrant_chart_to_svg(_full_chart_source(), MermaidTheme.light())
    assert f'font-family="{FONT_FAMILY}"' in svg


# === dispatch integration ==================================================


def test_render_dispatch_routes_quadrant_chart() -> None:
    """``render_mermaid_to_svg`` routes ``quadrantChart`` to the dedicated arm."""
    from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

    svg = render_mermaid_to_svg(_full_chart_source())
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert ">Campaign A<" in svg


def test_render_dispatch_quadrant_no_longer_unsupported() -> None:
    """``quadrantChart`` is NOT in the unsupported set (arm shipped in R288)."""
    from minimax_code.mermaid.to_svg.render import _UNSUPPORTED_DIAGRAM_TYPES

    assert "quadrantChart" not in _UNSUPPORTED_DIAGRAM_TYPES


def test_render_dispatch_strips_frontmatter_before_quadrant_parse() -> None:
    """A front-matter block is stripped before the quadrant parser runs."""
    from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

    source = "---\ntitle: Doc\n---\n" + _full_chart_source()
    svg = render_mermaid_to_svg(source)
    assert ">Campaign A<" in svg


def test_render_dispatch_dark_theme_via_frontmatter() -> None:
    """A front-matter ``theme: dark`` selects the dark palette for the quadrant."""
    from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

    source = "---\nconfig:\n  theme: dark\n---\n" + _full_chart_source()
    svg = render_mermaid_to_svg(source)
    assert _DARK_Q1_FILL in svg
    assert f'fill="{_DARK_BG}"' in svg


# === barrel invariant ======================================================


def test_quadrant_renderer_not_in_barrel_all() -> None:
    """The per-diagram renderer does NOT enter the barrel ``__all__``."""
    assert "render_quadrant_chart_to_svg" not in to_svg.__all__
    # Barrel size unchanged (per-diagram renderers never enter the barrel).
    assert len(to_svg.__all__) == 18


def test_quadrant_symbol_importable_from_leaf() -> None:
    """The renderer is importable from its own leaf module."""
    assert callable(render_quadrant_chart_to_svg)
    assert callable(parse_quadrant_chart)
    assert callable(quadrant_theme_for)


def test_render_module_all_unchanged() -> None:
    """The render leaf still exports only the 3 dispatch symbols."""
    assert render_mod.__all__ == [
        "is_mermaid_diagram",
        "render_mermaid_to_svg",
        "strip_mermaid_frontmatter",
    ]
