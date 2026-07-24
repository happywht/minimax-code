"""Black-box tests for the ``gantt`` renderer (R285) -- the behavioral-equivalent
port of grok's ``gantt_diagram.rs``.

Direction (1) brick 18 (R285). The seventh per-diagram leaf and the seventh
self-contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` /
R282 ``pie`` / R283 ``packet`` / R284 ``sankey``; unlike R280 ``stateDiagram``
which rides the dagre stack). The ``gantt`` diagram is mermaid's project
schedule: tasks grouped into sections, laid out as horizontal bars on a
day-scaled timeline, with section background bands, a bottom date axis with
daily grid ticks, inside/outside task labels, a left-side section legend, and
an optional title. Pure calendar arithmetic + time-domain scaling, so this
leaf emits SVG directly from the parsed model -- no AST, no dagre.

This file exercises the renderer's contract surface directly and through the
dispatch, mirroring grok's own coverage shape:

* **Dispatch smoke**: :func:`render_mermaid_to_svg` with a canonical gantt
  fixture (title + dateFormat + section + ymd start + after dependency)
  succeeds and emits a well-formed ``<svg>...</svg>``; ``gantt`` no longer
  raises :class:`UnsupportedDiagramType`.
* **Header recognition** (grok gantt_diagram.rs header scan): the first
  substantial line's leading token must be ``gantt``; any other first token
  raises :class:`ParseError` carrying that line's number, and an all-blank /
  comment body falls through to the empty-tasks guard (gantt-specific: unlike
  pie, the header scan does not pin ``Expected 'gantt'`` on an all-blank body).
* **Title / dateFormat / section grammar**: ``title <text>`` sets the title;
  ``dateFormat ...`` is accepted but ignored (the port hard-codes YYYY-MM-DD);
  ``section <name>`` opens a section; a task before any section carries
  ``section=None``.
* **Task grammar**: ``<name> : <id>, <start>, <duration>`` populates the task
  list in source order, partitioning on ``:`` then comma-splitting the spec.
* **Duration parsing** (``_parse_duration_days``): ``<n>d`` / ``<n>D`` -> ``n``
  days; ``<n>w`` / ``<n>W`` -> ``n * 7`` days; empty / non-numeric / unknown
  unit raise ValueError (the parser wraps it into a line-stamped
  :class:`ParseError`).
* **``after`` dependency**: ``after <id>`` resolves to the referenced task's
  ``start_day + duration_days``; an unknown id raises :class:`ParseError`.
* **ymd parsing** (``_parse_ymd_to_day``): a ``YYYY-MM-DD`` string round-trips
  through :func:`_days_from_civil`; malformed dates raise ValueError (wrapped
  into a line-stamped :class:`ParseError` at the call site).
* **Howard Hinnant civil-from-days** (``_days_from_civil`` / ``_day_to_ymd``):
  the proleptic-Gregorian serial-day pair is an exact inverse over a spread of
  dates, including pre-epoch / negative-serial-day / negative-``z`` cases that
  exercise the Python ``//`` (floor) semantics versus grok's
  truncation-adjusted Rust idiom -- the function-not-line port intent.
* **SVG structure**: the six emission blocks -- embedded ``<style>`` (gantt is
  the second renderer with a CSS block, after pie), section background bands,
  bottom date axis with daily grid ticks, task bar rects, inside/outside task
  labels, section legend labels, and the optional title -- all land in the
  output for a representative fixture.
* **escape_xml** (grok ``escape_xml``): the five XML-significant characters;
  gantt maps ``'`` -> ``&apos;`` (named entity, like pie / packet; radar /
  sankey use ``&#39;`` -- the Rust functions disagree).
* **Float bridge** (:func:`_fmt`): integer-valued floats drop ``.0`` (Rust
  ``Display``; Python ``str`` keeps it).
* **Non-theme-aware**: the ``_theme`` arg is ignored -- dark and light produce
  byte-identical output (mirrors grok's hard-coded gantt palette; pie /
  packet behave the same, unlike the theme-aware sankey).
* **Module surface**: the single-symbol ``__all__``; the renderer stays out
  of the :mod:`.to_svg` barrel (dispatch-only reach, mirrors R279-R284); the
  two dataclasses are frozen.
* **Front-matter dispatch** (R285 contract lift): a gantt source carrying a
  ``---`` front-matter block still renders -- the dispatch passes the
  front-matter-stripped body (mirrors grok lib.rs L47 shadow), not the raw
  source.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import gantt_diagram as gantt_mod
from minimax_code.mermaid.to_svg.error import ParseError, UnsupportedDiagramType
from minimax_code.mermaid.to_svg.gantt_diagram import (
    GanttChart,
    GanttTask,
    _day_to_ymd,
    _day_to_ymd_str,
    _days_from_civil,
    _escape_xml,
    _fmt,
    _parse_duration_days,
    _parse_ymd_to_day,
    parse_gantt_diagram,
    render_gantt_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# Canonical fixture exercising title + dateFormat + section + ymd start +
# after dependency (covers the parse grammar end-to-end in one source).
_CANONICAL_GANTT = (
    "gantt\n"
    "title Project Gantt\n"
    "dateFormat YYYY-MM-DD\n"
    "section Section One\n"
    "Task A :a1, 2024-01-01, 5d\n"
    "Task B :b1, after a1, 3d"
)

# Minimal one-task fixture used by the SVG-structure assertions (deterministic
# geometry: 5-day bar on a 5-day span -> px_per_day = 634/5).
_SIMPLE_GANTT = "gantt\ntitle Demo\nsection A\nT1 :t1, 2024-01-01, 5d"

# A long-named task placed FIRST (left side of the timeline) so its label
# overflows a 1-day bar on a wide span and the renderer shifts it outside to
# the RIGHT (end_x + text_width + 1.5*LEFT_PADDING < _WIDTH - LEFT_PADDING).
_OUTSIDE_RIGHT_GANTT = (
    "gantt\nsection S\n"
    "AVeryLongTaskNameThatOverflowsTheBar :a, 2024-01-01, 1d\n"
    "B :b, 2025-01-01, 1d"
)


# === dispatch smoke =======================================================


def test_render_mermaid_to_svg_gantt_dispatch_emits_svg() -> None:
    """The canonical gantt fixture dispatches end-to-end to an SVG.

    Mirrors grok's integration shape for the gantt arm: the ``gantt`` header
    plus title / dateFormat / section / two tasks (one ymd start, one ``after``
    dependency) renders through ``parse_gantt_diagram`` ->
    ``render_gantt_diagram_to_svg`` and yields a well-formed SVG containing
    the title and both task labels.
    """
    svg = render_mermaid_to_svg(_CANONICAL_GANTT)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "Project Gantt" in svg
    assert "Task A" in svg
    assert "Task B" in svg


def test_render_mermaid_to_svg_gantt_is_not_unsupported() -> None:
    """``gantt`` no longer raises :class:`UnsupportedDiagramType` (R285).

    Pre-R285 ``gantt`` sat in ``_UNSUPPORTED_DIAGRAM_TYPES``; R285 lifts it
    into a dedicated dispatch arm (mirrors grok lib.rs L74-L76). The token now
    renders instead of raising.
    """
    try:
        svg = render_mermaid_to_svg("gantt\nT :t, 2024-01-01, 1d")
    except UnsupportedDiagramType:
        pytest.fail("gantt must not raise UnsupportedDiagramType after R285")
    assert "<svg" in svg


def test_render_gantt_diagram_to_svg_returns_svg_directly() -> None:
    """The renderer entry returns a well-formed SVG string for a valid source."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


# === header recognition ===================================================


def test_parse_gantt_header_skips_blank_and_comment_lines() -> None:
    """Leading blank / ``%%`` comment lines are skipped before the header."""
    chart = parse_gantt_diagram("\n%% a comment\n\n  gantt\nT :t, 2024-01-01, 1d")
    assert len(chart.tasks) == 1
    assert chart.tasks[0].name == "T"


def test_parse_gantt_missing_header_raises_line_one() -> None:
    """A body whose first token is not ``gantt`` raises pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("flowchart TD\nA --> B")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'gantt' declaration"


def test_parse_gantt_wrong_first_token_carries_that_line_number() -> None:
    """The ParseError carries the offending header line's 1-based number."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("\n\npie\n\"A\" : 1")
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Expected 'gantt' declaration"


def test_parse_gantt_all_blank_body_raises_no_tasks_line_one() -> None:
    """An all-blank / comment body falls through to the empty-tasks guard.

    gantt-specific: the header scan skips every blank / ``%%`` line without
    raising (no token to mismatch on), then the trailing empty-tasks guard
    fires pinning line 1. pie's header scan raises ``Expected 'pie'`` on the
    same input -- the two parsers disagree, and the port mirrors grok's gantt
    behavior exactly.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("\n%% only\n   \n")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Gantt diagram requires at least one task"


# === title / dateFormat / section grammar =================================


def test_parse_gantt_title_line_sets_title() -> None:
    """A ``title <text>`` line sets the chart title (grok strip_prefix)."""
    chart = parse_gantt_diagram("gantt\ntitle My Schedule\nT :t, 2024-01-01, 1d")
    assert chart.title == "My Schedule"


def test_parse_gantt_no_title_is_none() -> None:
    """With no ``title`` line the chart title stays ``None``."""
    chart = parse_gantt_diagram("gantt\nT :t, 2024-01-01, 1d")
    assert chart.title is None


def test_parse_gantt_dateformat_directive_accepted_and_ignored() -> None:
    """``dateFormat ...`` is accepted but ignored (the port hard-codes YYYY-MM-DD).

    grok's parser reads ``dateFormat`` to choose a date parser; the port only
    supports ``YYYY-MM-DD`` and so drops the directive. The directive must not
    be mistaken for a task row.
    """
    chart = parse_gantt_diagram(
        "gantt\ndateFormat YYYY-MM-DD\nT :t, 2024-01-01, 1d"
    )
    assert len(chart.tasks) == 1
    assert chart.tasks[0].name == "T"


def test_parse_gantt_section_groups_subsequent_tasks() -> None:
    """A ``section <name>`` directive stamps every following task's section."""
    chart = parse_gantt_diagram(
        "gantt\nsection Alpha\nA :a, 2024-01-01, 1d\n"
        "section Beta\nB :b, 2024-01-02, 1d"
    )
    assert chart.tasks[0].section == "Alpha"
    assert chart.tasks[1].section == "Beta"


def test_parse_gantt_task_before_any_section_has_none_section() -> None:
    """A task declared before the first ``section`` carries ``section=None``."""
    chart = parse_gantt_diagram("gantt\nA :a, 2024-01-01, 1d")
    assert chart.tasks[0].section is None


# === task grammar =========================================================


def test_parse_gantt_task_with_ymd_start_and_duration() -> None:
    """``<name> : <id>, <YYYY-MM-DD>, <duration>`` populates one task bar."""
    chart = parse_gantt_diagram("gantt\nTask :t, 2024-01-01, 5d")
    task = chart.tasks[0]
    assert task.name == "Task"
    assert task.section is None
    assert task.start_day == _days_from_civil(2024, 1, 1)
    assert task.duration_days == 5


def test_parse_gantt_multiple_tasks_preserve_source_order() -> None:
    """Tasks land in the list in source (insertion) order."""
    chart = parse_gantt_diagram(
        "gantt\nA :a, 2024-01-01, 1d\nB :b, 2024-01-02, 1d\nC :c, 2024-01-03, 1d"
    )
    assert [t.name for t in chart.tasks] == ["A", "B", "C"]


# === duration parsing (_parse_duration_days) ==============================


def test_parse_duration_days_day_unit() -> None:
    """``<n>d`` -> ``n`` days."""
    assert _parse_duration_days("5d") == 5


def test_parse_duration_days_day_unit_uppercase() -> None:
    """``<n>D`` is accepted (case-insensitive unit)."""
    assert _parse_duration_days("5D") == 5


def test_parse_duration_days_week_unit() -> None:
    """``<n>w`` -> ``n * 7`` days."""
    assert _parse_duration_days("2w") == 14


def test_parse_duration_days_week_unit_uppercase() -> None:
    """``<n>W`` is accepted (case-insensitive unit)."""
    assert _parse_duration_days("2W") == 14


def test_parse_duration_days_strips_surrounding_whitespace() -> None:
    """The spec is stripped before parsing (mermaid allows indent padding)."""
    assert _parse_duration_days("  3d  ") == 3


def test_parse_duration_days_empty_raises() -> None:
    """An empty / whitespace-only spec raises ValueError (caller wraps it)."""
    with pytest.raises(ValueError, match="Empty duration"):
        _parse_duration_days("")
    with pytest.raises(ValueError, match="Empty duration"):
        _parse_duration_days("   ")


def test_parse_duration_days_non_numeric_raises() -> None:
    """A non-numeric magnitude raises ``Invalid duration``."""
    with pytest.raises(ValueError, match="Invalid duration"):
        _parse_duration_days("abc")


def test_parse_duration_days_unknown_unit_raises() -> None:
    """A magnitude with an unrecognized unit raises ``Unsupported duration unit``."""
    with pytest.raises(ValueError, match="Unsupported duration unit"):
        _parse_duration_days("5x")


def test_parse_gantt_bad_duration_wrapped_into_parse_error() -> None:
    """A bad duration spec surfaces as a line-stamped :class:`ParseError`."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\nT :t, 2024-01-01, 5x")
    assert exc_info.value.line == 2
    assert "Unsupported duration unit" in exc_info.value.message


# === after dependency =====================================================


def test_parse_gantt_after_dependency_resolves_start_day() -> None:
    """``after <id>`` resolves to ``ref.start_day + ref.duration_days``."""
    chart = parse_gantt_diagram(
        "gantt\nsection S\nA :a, 2024-01-01, 5d\nB :b, after a, 3d"
    )
    task_a = chart.tasks[0]
    task_b = chart.tasks[1]
    assert task_a.start_day == _days_from_civil(2024, 1, 1)
    assert task_a.duration_days == 5
    # B starts where A ends (start + duration).
    assert task_b.start_day == task_a.start_day + task_a.duration_days
    assert task_b.duration_days == 3


def test_parse_gantt_after_unknown_id_raises() -> None:
    """An ``after <id>`` naming an unseen id raises pinning that task's line."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\nsection S\nB :b, after missing, 3d")
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Unknown gantt dependency id: missing"


# === ymd parsing (_parse_ymd_to_day) ======================================


def test_parse_ymd_to_day_valid_date_round_trips() -> None:
    """A ``YYYY-MM-DD`` string parses to the same serial day as ``_days_from_civil``."""
    assert _parse_ymd_to_day("2024-01-01") == _days_from_civil(2024, 1, 1)


def test_parse_ymd_to_day_strips_surrounding_whitespace() -> None:
    """The date string is stripped before splitting."""
    assert _parse_ymd_to_day("  2024-01-01  ") == _days_from_civil(2024, 1, 1)


def test_parse_ymd_to_day_wrong_segment_count_raises() -> None:
    """A date without exactly three ``-``-separated parts raises ``Invalid date``."""
    with pytest.raises(ValueError, match="Invalid date"):
        _parse_ymd_to_day("2024-01")


def test_parse_ymd_to_day_non_numeric_year_raises() -> None:
    """A non-numeric year raises ``Invalid year``."""
    with pytest.raises(ValueError, match="Invalid year"):
        _parse_ymd_to_day("abcd-01-01")


def test_parse_ymd_to_day_non_numeric_month_raises() -> None:
    """A non-numeric month raises ``Invalid month``."""
    with pytest.raises(ValueError, match="Invalid month"):
        _parse_ymd_to_day("2024-xx-01")


def test_parse_ymd_to_day_non_numeric_day_raises() -> None:
    """A non-numeric day raises ``Invalid day``."""
    with pytest.raises(ValueError, match="Invalid day"):
        _parse_ymd_to_day("2024-01-xx")


def test_parse_gantt_bad_date_wrapped_into_parse_error() -> None:
    """A bad start date surfaces as a line-stamped :class:`ParseError`.

    ``notadate`` has no ``-`` separators -> one segment -> the wrong-count
    branch (``Invalid date``); a hyphenated non-date like ``not-a-date``
    would instead hit the year-parser branch (``Invalid year``), which is
    already covered by :func:`test_parse_ymd_to_day_non_numeric_year_raises`.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\nT :t, notadate, 1d")
    assert exc_info.value.line == 2
    assert "Invalid date" in exc_info.value.message


# === Howard Hinnant civil-from-days =======================================


def test_days_from_civil_unix_epoch_is_zero() -> None:
    """1970-01-01 is serial day 0 (the Howard Hinnant / Unix epoch anchor)."""
    assert _days_from_civil(1970, 1, 1) == 0


def test_days_from_civil_next_day_is_one() -> None:
    """1970-01-02 is serial day 1."""
    assert _days_from_civil(1970, 1, 2) == 1


@pytest.mark.parametrize(
    "y,m,d",
    [
        (1970, 1, 1),  # Unix epoch (serial day 0).
        (2000, 2, 29),  # leap day (366-day year edge).
        (2024, 1, 1),  # modern start-of-year.
        (2024, 12, 31),  # modern end-of-year.
        (1900, 3, 1),  # pre-epoch -> negative serial day.
        (0, 1, 1),  # proleptic Gregorian year 0 -> negative serial day AND
        # negative ``z`` in ``_day_to_ymd`` -- exercises the Python ``//``
        # floor vs grok's truncation-adjusted idiom (function-not-line intent).
    ],
)
def test_days_from_civil_and_day_to_ymd_are_exact_inverses(
    y: int, m: int, d: int
) -> None:
    """The serial-day pair round-trips for every fixture date.

    grok writes ``if y >= 0 { y } else { y - 399 } / 400`` to *simulate* floor
    division (pre-adjust the dividend non-negative, then truncate toward
    zero); the port uses Python's native ``//`` (floor) -- same math, idiomatic
    host language. The (0, 1, 1) case is the discriminating fixture: it forces
    a negative ``z = day + 719468`` inside :func:`_day_to_ymd`, where Rust
    truncation and Python floor would diverge without the grok pre-adjustment
    (or, equivalently, without Python's native floor).
    """
    day = _days_from_civil(y, m, d)
    assert _day_to_ymd(day) == (y, m, d)


def test_day_to_ymd_str_formats_iso_date() -> None:
    """Serial day -> ``YYYY-MM-DD`` zero-padded axis label (grok ``day_to_ymd_str``)."""
    assert _day_to_ymd_str(0) == "1970-01-01"
    assert _day_to_ymd_str(_days_from_civil(2024, 1, 1)) == "2024-01-01"


# === SVG structure (six emission blocks) ==================================


def test_render_gantt_emits_svg_root_with_viewbox() -> None:
    """The SVG root carries the fixed-width viewBox (grok ``let w = 784.0``)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    # Width 784 routes through _fmt -> "784" (Rust Display drops the .0).
    assert 'viewBox="0 0 784 ' in svg


def test_render_gantt_embeds_style_block() -> None:
    """A CSS ``<style>`` block defines the ganttStyles classes (grok L78-L96)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert "<style>" in svg
    assert ".task0" in svg  # task-bar fill/stroke class.
    assert ".section0" in svg  # section-band class.
    assert ".grid" in svg  # date-axis grid class.


def test_render_gantt_emits_section_background_band() -> None:
    """One section -> one section background rect (grok L99-L115)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="section section0"' in svg


def test_render_gantt_emits_date_axis_with_daily_ticks() -> None:
    """The bottom date axis carries one tick per day (grok L117-L139).

    A 5-day task yields 6 ticks (d=0..5), labelled ``2024-01-01`` ..
    ``2024-01-06``.
    """
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="grid"' in svg
    assert 'class="tick"' in svg
    assert "2024-01-01" in svg
    assert "2024-01-06" in svg


def test_render_gantt_emits_task_bar_rect() -> None:
    """One task -> one task bar rect (grok L141-L158)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="task task0"' in svg


def test_render_gantt_places_task_label_inside_bar() -> None:
    """A short label fits inside its bar -> ``taskText taskText0`` (grok L160-L185)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="taskText taskText0"' in svg
    assert ">T1</text>" in svg


def test_render_gantt_shifts_overflowing_label_outside_right() -> None:
    """A label wider than its bar shifts outside (grok L160-L185 overflow branch).

    The first task's 1-day bar on a 367-day span is far narrower than its long
    label, so the renderer shifts the text outside the bar. Placing the long
    task first (left side) leaves horizontal room on the right, selecting the
    ``taskTextOutsideRight`` branch.
    """
    svg = render_gantt_diagram_to_svg(_OUTSIDE_RIGHT_GANTT, MermaidTheme.default())
    assert 'class="taskTextOutsideRight"' in svg


def test_render_gantt_emits_section_legend_label() -> None:
    """A named section emits a left-side legend label (grok L187-L213)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="sectionTitle sectionTitle0"' in svg
    assert ">A</text>" in svg


def test_render_gantt_emits_title_text() -> None:
    """A ``title`` directive emits a centered title text (grok L215-L222)."""
    svg = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.default())
    assert 'class="titleText"' in svg
    assert ">Demo</text>" in svg


# === escape_xml + float bridge ============================================


def test_escape_xml_uses_named_apos_entity() -> None:
    """grok's gantt maps ``'`` -> ``&apos;`` (named entity, like pie / packet).

    radar / sankey use ``&#39;`` (numeric) -- both faithfully mirror their
    respective grok source; the Rust functions disagree.
    """
    assert _escape_xml("it's") == "it&apos;s"


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """The five XML-significant characters all become entities."""
    assert _escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&apos;f"


def test_escape_xml_ampersand_not_double_escaped() -> None:
    """``&`` is escaped first so it does not double-escape introduced entities."""
    assert _escape_xml("<") == "&lt;"


def test_fmt_integer_valued_float_drops_trailing_dot_zero() -> None:
    """An integer-valued float formats as a bare integer (Rust Display bridge)."""
    assert _fmt(784.0) == "784"
    assert _fmt(0.0) == "0"


def test_fmt_non_integer_uses_repr() -> None:
    """A non-integer float uses ``repr`` (shortest round-trippable decimal)."""
    assert _fmt(0.3) == "0.3"


# === non-theme-aware ======================================================


def test_render_gantt_is_not_theme_aware() -> None:
    """The ``_theme`` arg is ignored -- dark and light produce identical output.

    Mirrors grok: gantt hard-codes mermaid 11.12.2's default palette and
    ignores the resolved theme (same as pie / packet; sankey IS theme-aware).
    """
    light = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.light())
    dark = render_gantt_diagram_to_svg(_SIMPLE_GANTT, MermaidTheme.dark())
    assert light == dark


# === task-row errors ======================================================


def test_parse_gantt_task_without_colon_raises() -> None:
    """A task row with no ``:`` separator raises ``Invalid gantt task line``."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\nNoColonHere")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid gantt task line: NoColonHere"


def test_parse_gantt_task_spec_too_few_parts_raises() -> None:
    """A post-``:`` spec with fewer than 3 comma-parts raises ``Invalid gantt task spec``."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\nTask :a, 2024-01-01")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid gantt task spec: a, 2024-01-01"


def test_parse_gantt_no_tasks_raises_line_one() -> None:
    """A gantt with a header but zero task rows raises pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_gantt_diagram("gantt\ntitle Only a title")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Gantt diagram requires at least one task"


# === module surface =======================================================


def test_gantt_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``)."""
    assert gantt_mod.__all__ == ["render_gantt_diagram_to_svg"]


def test_gantt_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer stays out of the barrel -- dispatch-only reach (R285).

    grok's ``lib.rs`` never re-exports ``gantt_diagram``'s symbols at the crate
    root; the renderer is invoked only via the gantt dispatch arm. The barrel
    ``__all__`` does not grow for R285.
    """
    assert "render_gantt_diagram_to_svg" not in to_svg.__all__
    assert not hasattr(to_svg, "render_gantt_diagram_to_svg")


def test_gantt_diagram_dataclasses_are_frozen() -> None:
    """The two dataclasses mirror grok's immutable structs (frozen=True)."""
    task = GanttTask(section="A", name="T", start_day=0, duration_days=1)
    chart = GanttChart(title=None, tasks=[task])
    assert chart.tasks == [task]
    with pytest.raises(FrozenInstanceError):
        task.name = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        chart.title = "x"  # type: ignore[misc]


# === front-matter dispatch (R285 contract lift) ===========================


def test_render_mermaid_to_svg_gantt_strips_frontmatter_before_dispatch() -> None:
    """R285: dispatch passes the front-matter-stripped body to the gantt arm.

    Mirrors grok lib.rs L47 (``let mermaid_source = parsed_source.body``) --
    the per-diagram arm receives the body, not the raw source. Without the
    strip, the ``---`` fence would reach ``parse_gantt_diagram`` and break the
    header scan; the rendered SVG proves dispatch saw the clean body.
    """
    source = (
        "---\ntitle: Demo\n---\n"
        "gantt\ntitle T\nsection A\nT1 :t1, 2024-01-01, 5d"
    )
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert ">T1</text>" in svg
