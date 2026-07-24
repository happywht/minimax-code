"""Black-box tests for the ``radar-beta`` renderer (R281) -- the behavioral-
equivalent port of grok's ``radar_diagram.rs``.

Direction (1) brick 12 (R281). The third per-diagram leaf and the second
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``
renderer; unlike R280's ``stateDiagram`` parser which rides the dagre
stack). The ``radar-beta`` diagram is mermaid's radar / spider chart: N
axes radiating from a center, one closed Catmull-Rom curve per series, a
fixed ``700 x 700`` canvas with a 5-ring graticule. This file exercises
the renderer's contract surface directly and through the dispatch,
mirroring grok's own coverage:

* **Dispatch smoke** (grok ``lib.rs`` ``test_simple_radar`` L948-L959):
  :func:`render_mermaid_to_svg` with the canonical radar fixture succeeds and
  emits a well-formed SVG containing the ``radarGraticule`` class and the
  series name. This is the single integration assertion grok makes for the
  radar arm.
* **Header recognition** (grok radar_diagram.rs L170-L179): the first
  substantial line's leading token must be ``radar-beta``; any other first
  token raises :class:`ParseError`, and an all-blank body raises pinning
  line 1.
* **Empty-axes ParseError** (grok L29-L34): a diagram with no ``axis`` line
  raises pinning line 1 ("radar diagram requires at least one axis").
* **Axis grammar** (grok L181-L189): ``axis A, B, C`` populates the axis
  list; a later ``axis`` line REPLACES it (last-one-wins).
* **Curve grammar** (grok L191-L195, L208-L239): ``curve Name { v1, v2 }``
  appends a :class:`RadarCurve`; a value-count mismatch silently skips the
  series at render time (grok L106-L108).
* **parse_curve errors** (grok L208-L234): missing ``{``, missing ``}``,
  and a non-numeric token each raise a distinct :class:`ParseError`.
* **Silent body-line skip** (grok parse_radar has NO ``else`` arm): a body
  line that is neither ``axis`` nor ``curve`` is silently skipped, NOT an
  error (the divergent contract vs. state_diagram's "Unrecognized" raise).
* **closed_round_curve** (grok L241-L273): empty input -> empty string;
  non-empty input -> ``M`` open + N ``C`` segments + ``Z`` close.
* **escape_xml** (grok L275-L281): the five XML-significant characters.
* **Module surface**: the single-symbol ``__all__``; the renderer stays out
  of the :mod:`.to_svg` barrel (dispatch-only reach, mirrors R279/R280).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import radar_diagram as radar_mod
from minimax_code.mermaid.to_svg.error import ParseError, UnsupportedDiagramType
from minimax_code.mermaid.to_svg.radar_diagram import (
    RadarCurve,
    RadarDiagram,
    _closed_round_curve,
    _escape_xml,
    _fmt,
    _parse_curve,
    parse_radar,
    render_radar_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# === dispatch smoke (grok lib.rs test_simple_radar L948-L959) ==============


def test_render_mermaid_to_svg_radar_dispatch_emits_svg() -> None:
    """The canonical radar fixture dispatches end-to-end to an SVG (grok L948-L959).

    Mirrors grok's sole integration assertion for the radar arm: the
    ``radar-beta`` header plus one ``axis`` line and one ``curve`` renders
    through ``parse_radar`` -> ``render_radar_diagram_to_svg`` and yields a
    well-formed SVG that carries the ``radarGraticule`` class and the series
    name verbatim. Same fixture, same three assertions as the Rust test.
    """
    source = "radar-beta\naxis A, B, C\ncurve Series1 { 1, 2, 3 }"
    svg = render_mermaid_to_svg(source)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "radarGraticule" in svg
    assert "Series1" in svg


def test_render_mermaid_to_svg_radar_is_not_unsupported() -> None:
    """``radar-beta`` no longer raises :class:`UnsupportedDiagramType` (R281).

    R277 shipped ``radar-beta`` in ``_UNSUPPORTED_DIAGRAM_TYPES``; R281 lifts
    it into a dedicated dispatch arm (mirrors grok lib.rs L94-L96). The token
    now renders instead of raising.
    """
    source = "radar-beta\naxis A, B\ncurve S { 1, 2 }"
    try:
        svg = render_mermaid_to_svg(source)
    except UnsupportedDiagramType:
        pytest.fail("radar-beta must not raise UnsupportedDiagramType after R281")
    assert "<svg" in svg


def test_render_radar_diagram_to_svg_returns_svg_directly() -> None:
    """The renderer entry returns a well-formed SVG string for a valid source."""
    svg = render_radar_diagram_to_svg(
        "radar-beta\naxis A, B\ncurve S { 1, 2 }", MermaidTheme.default()
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "radarGraticule" in svg
    assert "radarAxisLine" in svg
    assert "radarCurve-0" in svg


# === header recognition (grok radar_diagram.rs L170-L179) ==================


def test_parse_radar_header_skips_blank_and_comment_lines() -> None:
    """Leading blank / ``%%`` comment lines are skipped before the header."""
    diagram = parse_radar("\n%% a comment\n\n  radar-beta\naxis A, B")
    assert diagram.axes == ["A", "B"]


def test_parse_radar_missing_header_raises_line_one() -> None:
    """A body whose first token is not ``radar-beta`` raises (grok L171-L176)."""
    with pytest.raises(ParseError) as exc_info:
        parse_radar("flowchart TD\n  A --> B")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'radar-beta' declaration"


def test_parse_radar_wrong_first_token_carries_that_line_number() -> None:
    """The ParseError carries the offending header line's 1-based number."""
    with pytest.raises(ParseError) as exc_info:
        parse_radar("\n\npie\n  \"Dogs\" : 50")
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Expected 'radar-beta' declaration"


def test_parse_radar_all_blank_raises_line_one() -> None:
    """An all-blank / comment body raises :class:`ParseError` pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_radar("\n%% only\n   \n")
    assert exc_info.value.line == 1


# === empty-axes ParseError (grok L29-L34) ==================================


def test_render_radar_empty_axes_raises_line_one() -> None:
    """A diagram with no ``axis`` line raises pinning line 1 (grok L29-L34)."""
    with pytest.raises(ParseError) as exc_info:
        render_radar_diagram_to_svg("radar-beta\ncurve S { 1, 2 }", MermaidTheme.default())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "radar diagram requires at least one axis"


# === axis grammar (grok L181-L189) ========================================


def test_parse_radar_axis_line_populates_axes() -> None:
    """``axis A, B, C`` populates the axis list in order."""
    diagram = parse_radar("radar-beta\naxis A, B, C")
    assert diagram.axes == ["A", "B", "C"]
    assert diagram.curves == []


def test_parse_radar_axis_line_replaces_previous() -> None:
    """A later ``axis`` line REPLACES the axis list (grok last-one-wins)."""
    diagram = parse_radar("radar-beta\naxis A, B\naxis X, Y, Z")
    assert diagram.axes == ["X", "Y", "Z"]


# === curve grammar (grok L191-L195) =======================================


def test_parse_radar_curve_line_appends_curve() -> None:
    """``curve Name { v1, v2 }`` appends a :class:`RadarCurve`."""
    diagram = parse_radar("radar-beta\naxis A, B\ncurve S1 { 1, 2 }\ncurve S2 { 3, 4 }")
    assert len(diagram.curves) == 2
    assert diagram.curves[0] == RadarCurve(name="S1", values=[1.0, 2.0])
    assert diagram.curves[1] == RadarCurve(name="S2", values=[3.0, 4.0])


def test_render_radar_curve_value_count_mismatch_is_skipped() -> None:
    """A series whose value count != axis count is skipped at render (grok L106-L108).

    The mismatched curve emits no ``<path>`` and no legend entry; the matched
    curve still renders.
    """
    svg = render_radar_diagram_to_svg(
        "radar-beta\naxis A, B, C\ncurve Bad { 1, 2 }\ncurve Good { 1, 2, 3 }",
        MermaidTheme.default(),
    )
    assert "Good" in svg
    assert "Bad" not in svg


# === parse_curve errors (grok L208-L234) ==================================


def test_parse_curve_missing_open_brace_raises() -> None:
    """A curve body with no ``{`` raises ``Invalid curve`` (grok L209-L214)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_curve("Series1 1, 2, 3", line=3)
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Invalid curve: Series1 1, 2, 3"


def test_parse_curve_missing_close_brace_raises() -> None:
    """A curve body with no closing ``}`` raises ``Invalid curve`` (grok L217-L223)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_curve("Series1 { 1, 2, 3", line=5)
    assert exc_info.value.line == 5
    assert exc_info.value.message == "Invalid curve: Series1 { 1, 2, 3"


def test_parse_curve_non_numeric_value_raises() -> None:
    """A non-numeric value token raises ``Invalid curve value`` (grok L231-L234)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_curve("Series1 { 1, oops, 3 }", line=7)
    assert exc_info.value.line == 7
    assert exc_info.value.message == "Invalid curve value: oops"


def test_parse_curve_returns_name_and_floats() -> None:
    """A well-formed curve body yields ``(name, [floats])`` with empties skipped."""
    name, values = _parse_curve("Series1 { 1, 2, , 3 }", line=1)
    assert name == "Series1"
    assert values == [1.0, 2.0, 3.0]


# === silent body-line skip (grok parse_radar has NO else arm) =============


def test_parse_radar_silently_skips_unrecognized_body_line() -> None:
    """A body line matching neither ``axis`` nor ``curve`` is skipped, not an error.

    grok's ``parse_radar`` body loop has no ``else`` arm (radar_diagram.rs
    L181-L195); an unrecognized line is silently dropped. This is the
    contract diverging from ``state_diagram``, which raises "Unrecognized".
    """
    diagram = parse_radar("radar-beta\naxis A, B\nsome random line\n  curve S { 1, 2 }")
    # The unrecognized "some random line" did not abort the parse; the curve
    # after it still landed.
    assert diagram.axes == ["A", "B"]
    assert len(diagram.curves) == 1


def test_parse_radar_skips_blank_and_comment_body_lines() -> None:
    """Blank / ``%%`` lines interspersed in the body are skipped."""
    diagram = parse_radar("radar-beta\n\n  %% a note\n  axis A, B\n  \n  curve S { 1, 2 }")
    assert diagram.axes == ["A", "B"]
    assert len(diagram.curves) == 1


# === closed_round_curve (grok L241-L273) ==================================


def test_closed_round_curve_empty_returns_empty_string() -> None:
    """Empty input yields the empty string (grok L242-L244)."""
    assert _closed_round_curve([], 0.17) == ""


def test_closed_round_curve_single_point_is_move_and_close() -> None:
    """A single point opens with ``M`` and closes with ``Z`` (one segment)."""
    d = _closed_round_curve([(10.0, 20.0)], 0.17)
    assert d.startswith("M10,20")
    assert d.endswith(" Z")
    assert " C" in d


def test_closed_round_curve_multi_point_emits_n_segments() -> None:
    """Three points emit three ``C`` segments (one per vertex, closed)."""
    d = _closed_round_curve([(0.0, 0.0), (100.0, 0.0), (50.0, 100.0)], 0.17)
    assert d.startswith("M0,0")
    assert d.endswith(" Z")
    assert d.count(" C") == 3


# === escape_xml (grok L275-L281) ==========================================


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """The five XML-significant characters become entities (grok L276-L280)."""
    assert _escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&#39;f"


def test_escape_xml_ampersand_not_double_escaped() -> None:
    """``&`` is escaped first so it does not double-escape introduced entities."""
    assert _escape_xml("<") == "&lt;"


# === float formatting bridge (_fmt) =======================================


def test_fmt_integer_valued_float_drops_trailing_dot_zero() -> None:
    """An integer-valued float formats as a bare integer (Rust Display bridge)."""
    assert _fmt(350.0) == "350"
    assert _fmt(0.0) == "0"
    assert _fmt(-262.5) != "-262"  # non-integer keeps its fractional part


def test_fmt_non_integer_uses_repr() -> None:
    """A non-integer float uses ``repr`` (shortest round-trippable decimal)."""
    assert _fmt(0.3) == "0.3"
    assert _fmt(262.5) == "262.5"


# === module surface =======================================================


def test_radar_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``)."""
    assert radar_mod.__all__ == ["render_radar_diagram_to_svg"]


def test_radar_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer stays out of the barrel -- dispatch-only reach (R281).

    grok's ``lib.rs`` never re-exports ``radar_diagram``'s symbols at the
    crate root; the renderer is invoked only via the radar dispatch arm. The
    barrel ``__all__`` does not grow for R281.
    """
    assert "render_radar_diagram_to_svg" not in to_svg.__all__
    assert not hasattr(to_svg, "render_radar_diagram_to_svg")


def test_radar_diagram_dataclasses_are_frozen() -> None:
    """The two dataclasses mirror grok's immutable structs (frozen=True)."""
    curve = RadarCurve(name="S", values=[1.0, 2.0])
    diagram = RadarDiagram(axes=["A", "B"], curves=[curve])
    assert diagram.axes == ["A", "B"]
    assert diagram.curves == [curve]
    with pytest.raises(FrozenInstanceError):
        curve.name = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        diagram.axes = []  # type: ignore[misc]
