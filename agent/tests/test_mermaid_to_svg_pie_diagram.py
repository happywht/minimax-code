"""Black-box tests for the ``pie`` renderer (R282) -- the behavioral-equivalent
port of grok's ``pie_diagram.rs``.

Direction (1) brick 15 (R282). The fourth per-diagram leaf and the third
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``
and R281's ``radar``; unlike R280's ``stateDiagram`` parser which rides the
dagre stack). The ``pie`` diagram is mermaid's pie / donut chart: N labeled
slices swept clockwise from 12 o'clock, each a colored wedge with an inline
``pct%`` label, plus a right-side legend. This file exercises the renderer's
contract surface directly and through the dispatch, mirroring grok's own
coverage:

* **Dispatch smoke** (grok ``lib.rs`` ``test_simple_pie_diagram`` L649-L662):
  :func:`render_mermaid_to_svg` with the canonical pie fixture succeeds and
  emits a well-formed ``<svg>...</svg>``. This is the integration assertion
  grok makes for the pie arm.
* **Header recognition** (grok pie_diagram.rs L221-L243): the first
  substantial line's leading token must be ``pie``; any other first token
  raises :class:`ParseError`, and an all-blank body raises pinning line 1.
  The optional ``showData`` header token toggles the ``[value]`` legend suffix.
* **Title grammar** (grok L247-L252): a ``title <text>`` line sets the chart
  title; absent title leaves it ``None``.
* **Slice grammar** (grok L254-L273): ``"<label>" : <value>`` /
  ``<label> : <value>`` populates the slice list in insertion order; a
  surrounding pair of double-quotes is stripped from the label; values parse
  as floats.
* **Slice errors** (grok L257-L269, L275-L277): a slice line with no ``:``
  separator raises ``Invalid pie slice``; a non-numeric value raises
  ``Invalid pie value``; a diagram with zero slices raises pinning line 1.
* **Total positivity** (grok L51-L53): a non-positive slice total raises
  ``Pie diagram total must be > 0``.
* **>=1% filter** (grok L57): slices contributing <1% of the total are
  dropped from the wedges (no ``pieCircle`` path) but still listed in the
  legend (the legend iterates the unfiltered original-order list).
* **Percentage rounding** (grok L114): ``pct`` uses Rust
  ``f64::round() as i64`` (round-half-away-from-zero); the port mirrors it
  with ``int(math.floor(x + 0.5))`` -- verified at the ``12.5 -> 13``
  boundary where Python's banker's ``round`` would give ``12``.
* **escape_xml** (grok L303-L309): the five XML-significant characters. grok's
  pie maps ``'`` -> ``&apos;`` (named entity; the radar renderer uses
  ``&#39;`` -- both faithfully mirror their respective grok source).
* **Float bridge** (:func:`_fmt`): integer-valued floats drop ``.0`` (Rust
  ``Display``; Python ``str`` keeps it).
* **Module surface**: the single-symbol ``__all__``; the renderer stays out
  of the :mod:`.to_svg` barrel (dispatch-only reach, mirrors R279/R280/R281);
  the two dataclasses are frozen.
* **Front-matter dispatch** (R282 contract lift): a pie source carrying a
  ``---`` front-matter block still renders -- the dispatch passes the
  front-matter-stripped body (mirrors grok lib.rs L47 shadow), not the raw
  source.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import pie_diagram as pie_mod
from minimax_code.mermaid.to_svg.error import ParseError, UnsupportedDiagramType
from minimax_code.mermaid.to_svg.pie_diagram import (
    MERMAID_PIE_COLORS,
    PieChart,
    PieSlice,
    _escape_xml,
    _fmt,
    _polar,
    parse_pie_diagram,
    render_pie_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# === dispatch smoke (grok lib.rs test_simple_pie_diagram L649-L662) ========


def test_render_mermaid_to_svg_pie_dispatch_emits_svg() -> None:
    """The canonical pie fixture dispatches end-to-end to an SVG (grok L649-L662).

    Mirrors grok's integration assertion for the pie arm: the ``pie`` header
    plus a title and three quoted slices renders through ``parse_pie_diagram``
    -> ``render_pie_diagram_to_svg`` and yields a well-formed SVG. Same fixture,
    same two assertions (``<svg`` / ``</svg>``) as the Rust test.
    """
    source = (
        'pie\ntitle Pets adopted by volunteers\n"Dogs" : 386\n"Cats" : 85\n"Rats" : 15'
    )
    svg = render_mermaid_to_svg(source)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_mermaid_to_svg_pie_is_not_unsupported() -> None:
    """``pie`` no longer raises :class:`UnsupportedDiagramType` (R282).

    Pre-R282 ``pie`` sat in ``_UNSUPPORTED_DIAGRAM_TYPES``; R282 lifts it into
    a dedicated dispatch arm (mirrors grok lib.rs L70-L72). The token now
    renders instead of raising.
    """
    source = 'pie\n"Dogs" : 50\n"Cats" : 50'
    try:
        svg = render_mermaid_to_svg(source)
    except UnsupportedDiagramType:
        pytest.fail("pie must not raise UnsupportedDiagramType after R282")
    assert "<svg" in svg


def test_render_pie_diagram_to_svg_returns_svg_directly() -> None:
    """The renderer entry returns a well-formed SVG string for a valid source."""
    svg = render_pie_diagram_to_svg(
        'pie\ntitle T\n"A" : 50\n"B" : 50', MermaidTheme.default()
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "pieOuterCircle" in svg
    assert "pieTitleText" in svg
    assert "legend" in svg


# === header recognition (grok pie_diagram.rs L221-L243) ====================


def test_parse_pie_header_skips_blank_and_comment_lines() -> None:
    """Leading blank / ``%%`` comment lines are skipped before the header."""
    chart = parse_pie_diagram('\n%% a comment\n\n  pie\n"A" : 1')
    assert chart.slices == [PieSlice(label="A", value=1.0)]


def test_parse_pie_missing_header_raises_line_one() -> None:
    """A body whose first token is not ``pie`` raises (grok L226-L237)."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram("flowchart TD\n  A --> B")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'pie' declaration"


def test_parse_pie_wrong_first_token_carries_that_line_number() -> None:
    """The ParseError carries the offending header line's 1-based number."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram("\n\nradar-beta\naxis A, B")
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Expected 'pie' declaration"


def test_parse_pie_all_blank_raises_line_one() -> None:
    """An all-blank / comment body raises :class:`ParseError` pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram("\n%% only\n   \n")
    assert exc_info.value.line == 1


# === showData + title (grok L241-L252) ====================================


def test_parse_pie_showdata_token_sets_flag() -> None:
    """The ``showData`` header token toggles the legend ``[value]`` suffix."""
    chart = parse_pie_diagram('pie showData\n"A" : 50')
    assert chart.show_data is True


def test_parse_pie_without_showdata_flag_false() -> None:
    """A bare ``pie`` header leaves ``show_data`` False."""
    chart = parse_pie_diagram('pie\n"A" : 50')
    assert chart.show_data is False


def test_parse_pie_title_line_sets_title() -> None:
    """A ``title <text>`` line sets the chart title (grok strip_prefix)."""
    chart = parse_pie_diagram('pie\ntitle My Chart\n"A" : 50')
    assert chart.title == "My Chart"


def test_parse_pie_no_title_is_none() -> None:
    """With no ``title`` line the chart title stays ``None``."""
    chart = parse_pie_diagram('pie\n"A" : 50')
    assert chart.title is None


# === slice grammar (grok L254-L273) =======================================


def test_parse_pie_quoted_label_strips_quotes() -> None:
    """A surrounding pair of double-quotes is stripped from the label."""
    chart = parse_pie_diagram('pie\n"Dogs" : 386')
    assert chart.slices == [PieSlice(label="Dogs", value=386.0)]


def test_parse_pie_unquoted_label_accepted() -> None:
    """An unquoted label is accepted verbatim (grok strips only when both ends)."""
    chart = parse_pie_diagram("pie\nDogs : 386")
    assert chart.slices == [PieSlice(label="Dogs", value=386.0)]


def test_parse_pie_multiple_slices_preserve_order() -> None:
    """Slices land in the list in insertion (source) order."""
    chart = parse_pie_diagram('pie\n"A" : 1\n"B" : 2\n"C" : 3')
    assert [s.label for s in chart.slices] == ["A", "B", "C"]
    assert [s.value for s in chart.slices] == [1.0, 2.0, 3.0]


def test_parse_pie_float_value() -> None:
    """A fractional value parses as a float."""
    chart = parse_pie_diagram('pie\n"A" : 12.5')
    assert chart.slices[0].value == 12.5


# === slice errors (grok L257-L269, L275-L277) =============================


def test_parse_pie_slice_without_colon_raises() -> None:
    """A slice line with no ``:`` separator raises ``Invalid pie slice``."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram("pie\nNoColonHere")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid pie slice: NoColonHere"


def test_parse_pie_non_numeric_value_raises() -> None:
    """A non-numeric value raises ``Invalid pie value`` (grok L265-L269)."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram('pie\n"A" : notanumber')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid pie value: notanumber"


def test_parse_pie_no_slices_raises_line_one() -> None:
    """A diagram with no slice lines raises pinning line 1 (grok L275-L277)."""
    with pytest.raises(ParseError) as exc_info:
        parse_pie_diagram("pie\ntitle Only a title")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Pie diagram requires at least one slice"


# === total positivity (grok L51-L53) ======================================


def test_render_pie_total_zero_raises() -> None:
    """A non-positive slice total raises ``Pie diagram total must be > 0``."""
    with pytest.raises(ParseError) as exc_info:
        render_pie_diagram_to_svg('pie\n"A" : 0\n"B" : 0', MermaidTheme.default())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Pie diagram total must be > 0"


# === >=1% filter + percentage rounding (grok L57, L114) ===================


def test_render_pie_filters_below_one_percent_slices() -> None:
    """A slice <1% of total is dropped from wedges but kept in the legend.

    grok L57 filters slices whose share is below 1% from the d3-pie sweep
    (no ``pieCircle`` path emitted); the legend (grok L156-L199) still
    iterates the unfiltered original-order list, so the tiny slice's label
    still appears.
    """
    # total=101, Tiny=1/101 ~= 0.99% < 1% -> filtered; Big ~= 99% -> one wedge.
    svg = render_pie_diagram_to_svg(
        'pie\n"Big" : 100\n"Tiny" : 1', MermaidTheme.default()
    )
    assert svg.count('class="pieCircle"') == 1
    assert "Big" in svg
    assert "Tiny" in svg  # legend still lists the filtered slice


def test_render_pie_all_slices_above_threshold_emit_wedges() -> None:
    """Every slice well above 1% emits its own ``pieCircle`` wedge."""
    svg = render_pie_diagram_to_svg(
        'pie\n"A" : 33\n"B" : 33\n"C" : 34', MermaidTheme.default()
    )
    assert svg.count('class="pieCircle"') == 3


def test_render_pie_percentage_rounds_half_away_from_zero() -> None:
    """``pct`` uses Rust round-half-away-from-zero, not Python banker's.

    total=8, A=1 -> 12.5% exactly. Rust ``(12.5).round() as i64`` -> 13; the
    port's ``int(math.floor(12.5 + 0.5))`` -> 13. Python's built-in ``round``
    would give 12 (banker's, nearest even). Asserting ``13%`` pins the bridge.
    """
    svg = render_pie_diagram_to_svg('pie\n"A" : 1\n"B" : 7', MermaidTheme.default())
    assert ">13%</text>" in svg


def test_render_pie_percentage_labels_present() -> None:
    """Each surviving slice's rounded percentage appears as an inline label."""
    # A=10/60=16.67->17, B=20/60=33.33->33, C=30/60=50.
    svg = render_pie_diagram_to_svg(
        'pie\n"A" : 10\n"B" : 20\n"C" : 30', MermaidTheme.default()
    )
    assert ">50%</text>" in svg
    assert ">33%</text>" in svg
    assert ">17%</text>" in svg


# === showData legend suffix (grok L70) ====================================


def test_render_pie_show_data_appends_value_to_legend() -> None:
    """``showData`` toggles a ``[value]`` suffix on legend labels.

    grok renders the legend label as ``format!("{} [{}]", label, value)``;
    the value routes through :func:`_fmt` so ``50.0`` -> ``"50"`` (Rust
    ``Display`` drops the trailing ``.0``).
    """
    svg = render_pie_diagram_to_svg(
        'pie showData\n"A" : 50', MermaidTheme.default()
    )
    assert "A [50]" in svg


def test_render_pie_title_is_xml_escaped() -> None:
    """The title text is XML-escaped (grok L154 -> escape_xml)."""
    svg = render_pie_diagram_to_svg(
        'pie\ntitle A & B <C>\n"X" : 1', MermaidTheme.default()
    )
    assert "A &amp; B &lt;C&gt;" in svg


# === escape_xml (grok L303-L309) ==========================================


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """The five XML-significant characters become entities (grok pie variant).

    grok's ``pie_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named entity),
    unlike the radar renderer's ``&#39;`` (numeric). Both faithfully mirror
    their respective grok source -- the two Rust functions disagree.
    """
    assert _escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&apos;f"


def test_escape_xml_ampersand_not_double_escaped() -> None:
    """``&`` is escaped first so it does not double-escape introduced entities."""
    assert _escape_xml("<") == "&lt;"


# === float formatting bridge (_fmt) =======================================


def test_fmt_integer_valued_float_drops_trailing_dot_zero() -> None:
    """An integer-valued float formats as a bare integer (Rust Display bridge)."""
    assert _fmt(450.0) == "450"
    assert _fmt(0.0) == "0"


def test_fmt_non_integer_uses_repr() -> None:
    """A non-integer float uses ``repr`` (shortest round-trippable decimal)."""
    assert _fmt(0.3) == "0.3"
    assert _fmt(185.5) == "185.5"


# === polar (grok L299-L301) ===============================================


def test_polar_returns_cartesian_from_center_radius_angle() -> None:
    """Polar-to-cartesian conversion (grok ``polar``)."""
    # angle=0 -> (cx+r, cy).
    assert _polar(0.0, 0.0, 10.0, 0.0) == (10.0, 0.0)
    # angle=pi/2 -> (cx, cy+r).
    x, y = _polar(0.0, 0.0, 10.0, math.pi / 2)
    assert math.isclose(x, 0.0, abs_tol=1e-9)
    assert math.isclose(y, 10.0, abs_tol=1e-9)


# === color palette (grok L9-L22) ==========================================


def test_mermaid_pie_colors_has_twelve_entries() -> None:
    """grok L9-L22 defines exactly 12 palette colors (d3 pieCYCLE)."""
    assert len(MERMAID_PIE_COLORS) == 12


def test_mermaid_pie_colors_first_two_are_hex_literals() -> None:
    """pie1 / pie2 are the primary / secondary hex literals (verbatim)."""
    assert MERMAID_PIE_COLORS[0] == "#ECECFF"
    assert MERMAID_PIE_COLORS[1] == "#ffffde"


# === module surface =======================================================


def test_pie_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``)."""
    assert pie_mod.__all__ == ["render_pie_diagram_to_svg"]


def test_pie_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer stays out of the barrel -- dispatch-only reach (R282).

    grok's ``lib.rs`` never re-exports ``pie_diagram``'s symbols at the crate
    root; the renderer is invoked only via the pie dispatch arm. The barrel
    ``__all__`` does not grow for R282.
    """
    assert "render_pie_diagram_to_svg" not in to_svg.__all__
    assert not hasattr(to_svg, "render_pie_diagram_to_svg")


def test_pie_diagram_dataclasses_are_frozen() -> None:
    """The two dataclasses mirror grok's immutable structs (frozen=True)."""
    sl = PieSlice(label="A", value=1.0)
    chart = PieChart(title=None, show_data=False, slices=[sl])
    assert chart.slices == [sl]
    with pytest.raises(FrozenInstanceError):
        sl.label = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        chart.title = "x"  # type: ignore[misc]


# === front-matter dispatch (R282 contract lift) ===========================


def test_render_mermaid_to_svg_pie_strips_frontmatter_before_dispatch() -> None:
    """R282: dispatch passes the front-matter-stripped body to the pie arm.

    Mirrors grok lib.rs L47 (``let mermaid_source = parsed_source.body``) --
    the per-diagram arm receives the body, not the raw source. Without the
    strip, the ``---`` fence would reach ``parse_pie_diagram`` and break the
    header scan; the rendered SVG proves dispatch saw the clean body. The
    same lift was applied to the info / radar arms for dispatch consistency.
    """
    source = '---\ntitle: Demo\n---\npie\n"A" : 50\n"B" : 50'
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert "pieOuterCircle" in svg
