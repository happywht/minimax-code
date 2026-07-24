"""Black-box tests for the migrated mermaid-to-svg timeline renderer (R287).

Exercises :mod:`minimax_code.mermaid.to_svg.timeline_diagram` -- the behavioral-
equivalent port of grok ``mermaid-to-svg/src/timeline_diagram.rs`` (direction
(1), brick 20). This is the ninth per-diagram leaf and the eighth self-
contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` / R282
``pie`` / R283 ``packet`` / R284 ``sankey`` / R285 ``gantt`` / R286 ``kanban``),
and the fourth per-diagram renderer to ship a ``<style>`` CSS block (after
R282 pie / R285 gantt / R286 kanban).

The timeline diagram lays a chronological board out as a horizontal axis of
period nodes each trailing a vertical stack of event cards, grouped under
optional colored section banners, with a left-to-right direction arrow
underneath. Coverage mirrors the five concerns in the module docstring:

* **Float / XML / text-height bridges** (``_fmt`` / ``_escape_xml`` /
  ``_estimate_text_height`` / ``_estimate_node_height`` /
  ``_estimate_event_height``): the Rust ``Display`` / byte-length semantics
  that keep the emitted CSS and geometry byte-identical to grok. ``_escape_xml``
  uses the ``&apos;`` named entity (matches pie / packet / gantt / kanban; radar
  / sankey use ``&#39;``).
* **Parser** (``_parse_timeline_diagram``): header detection, ``title`` /
  ``section`` / ``: event`` continuation / ``period : event`` / bare-period
  rules, blank / ``%%`` / ``#`` skipping, and the two ParseError paths
  (missing header, non-``timeline`` first token).
* **Renderer smoke + structure** (``render_timeline_diagram_to_svg``): the
  SVG root (``aria-roledescription="timeline"``, viewBox ``0 -25 W H+25``),
  the ``<style>`` CSS block, the ``<defs>`` arrowhead marker, and the
  horizontal direction arrow.
* **NOT theme-aware** (the core contract): grok hard-codes mermaid 11.12.2's
  default timeline palette and ignores the resolved theme, so the light and
  dark themes produce byte-identical SVGs. Unlike R284 sankey / R286 kanban
  (theme-aware), timeline matches R282 pie / R283 packet / R285 gantt.
* **CSS section palette quirk**: the 12 ``.section-{si}`` rules where
  ``si = i - 1`` (so ``i=0`` -> ``.section--1``), the three ``cScale`` palettes
  (fills / hue-180-inverted strokes / label text colors with white at indices
  0 and 3), and the float-format fidelity (``font-size:16px`` / ``14px`` /
  ``stroke-width:3`` via ``_fmt``, NOT ``16.0`` / ``14.0`` / ``3.0``).
* **Mutable dataclass surface** (``TimelineTask`` / ``TimelineDiagram``): the
  ``events`` list default_factory does not leak across instances, and the
  parser's ``tasks[-1].events.append`` continuation contract holds.
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme
from minimax_code.mermaid.to_svg.timeline_diagram import (
    _CSCALE_FILLS,
    _CSCALE_INV,
    MAX_SECTIONS,
    NODE_LINE_STROKE_WIDTH,
    TimelineDiagram,
    TimelineTask,
    _escape_xml,
    _estimate_event_height,
    _estimate_node_height,
    _estimate_text_height,
    _fmt,
    _parse_timeline_diagram,
    render_timeline_diagram_to_svg,
)

# === _fmt (Rust Display bridge, shared with R281 radar / R285 gantt / R286 kanban)


def test_fmt_int_renders_verbatim() -> None:
    """A plain int renders without a decimal point (Rust i32 Display)."""
    assert _fmt(60) == "60"
    assert _fmt(0) == "0"
    assert _fmt(-7) == "-7"


def test_fmt_integer_float_drops_trailing_zero() -> None:
    """An integer-valued float drops the trailing ``.0`` (Rust f64 Display)."""
    assert _fmt(200.0) == "200"
    assert _fmt(0.0) == "0"
    assert _fmt(-22.0) == "-22"


def test_fmt_non_integer_float_uses_repr() -> None:
    """A non-integer float renders via ``repr`` (shortest round-trip)."""
    assert _fmt(83.5294117647) == "83.5294117647"
    assert _fmt(-82.5) == "-82.5"


# === _escape_xml (grok escape_xml, &apos; variant) =========================


def test_escape_xml_apostrophe_uses_named_entity() -> None:
    """``'`` -> ``&apos;`` (XML named entity; matches pie / packet / gantt / kanban)."""
    assert _escape_xml("Tom's") == "Tom&apos;s"


def test_escape_xml_ampersand_lt_gt_quot() -> None:
    """The other four XML-significant characters are entity-escaped."""
    assert _escape_xml("a & b") == "a &amp; b"
    assert _escape_xml("a < b") == "a &lt; b"
    assert _escape_xml("a > b") == "a &gt; b"
    assert _escape_xml('say "hi"') == "say &quot;hi&quot;"


def test_escape_xml_ampersand_runs_first() -> None:
    """``&`` is replaced first so a literal ``&lt;`` is not double-escaped."""
    assert _escape_xml("&lt;") == "&amp;lt;"


# === _estimate_text_height (byte-length bridge) ============================


def test_estimate_text_height_empty_is_one_line() -> None:
    """An empty string floors to one line (``max(ceil(0), 1)``) -> 19.2."""
    assert _estimate_text_height("") == 1.0 * 16.0 * 1.2


def test_estimate_text_height_ascii_short_is_one_line() -> None:
    """A short ASCII label fits one line (3 bytes * 9 = 27 < 150)."""
    assert _estimate_text_height("abc") == 1.0 * 16.0 * 1.2


def test_estimate_text_height_multibyte_uses_byte_count() -> None:
    """Multi-byte chars count by bytes, not codepoints (Rust ``String::len``).

    A CJK char is 3 bytes in UTF-8, so ``"中文"`` is 6 bytes -> 54px wide,
    still one line (54 < 150).
    """
    assert _estimate_text_height("中文") == 1.0 * 16.0 * 1.2


def test_estimate_text_height_long_ascii_wraps() -> None:
    """A 20-char ASCII label (180px) wraps to 2 lines (ceil(180/150) = 2)."""
    assert _estimate_text_height("a" * 20) == 2.0 * 16.0 * 1.2


# === _estimate_node_height / _estimate_event_height ========================


def test_estimate_node_height_short_text_with_padding() -> None:
    """``text_h + FONT_SIZE*1.1*0.5 + padding``, floored at max_height.

    For ``"x"`` (19.2 text height) + padding 20: 19.2 + 8.8 + 20 = 48.0.
    """
    assert _estimate_node_height("x", 20.0, 0.0) == pytest.approx(48.0)


def test_estimate_node_height_floor_applied() -> None:
    """A max_height larger than the computed height wins (floor)."""
    assert _estimate_node_height("x", 0.0, 100.0) == 100.0


def test_estimate_node_height_long_text_grows() -> None:
    """A 20-char label (2 lines = 38.4) + 8.8 + 20 = 67.2."""
    assert _estimate_node_height("a" * 20, 20.0, 0.0) == pytest.approx(67.2)


def test_estimate_event_height_floors_at_50() -> None:
    """A short event's computed height (48.0) floors to the 50px minimum."""
    assert _estimate_event_height("x") == pytest.approx(50.0)


def test_estimate_event_height_grows_past_floor() -> None:
    """A 20-char event (67.2) clears the 50px floor."""
    assert _estimate_event_height("a" * 20) == pytest.approx(67.2)


# === _parse_timeline_diagram ==============================================


def test_parse_simple_period_with_event() -> None:
    """A ``period : event`` line yields one task with that event."""
    diagram = _parse_timeline_diagram("timeline\n2024 : Launched")
    assert diagram.title is None
    assert diagram.sections == []
    assert len(diagram.tasks) == 1
    assert diagram.tasks[0].period == "2024"
    assert diagram.tasks[0].events == ["Launched"]


def test_parse_bare_period_no_events() -> None:
    """A bare line with no colon is a period with no events."""
    diagram = _parse_timeline_diagram("timeline\n2024")
    assert diagram.tasks[0].period == "2024"
    assert diagram.tasks[0].events == []


def test_parse_event_continuation_appends_to_previous_task() -> None:
    """A ``: event`` line appends to the previous task's events."""
    diagram = _parse_timeline_diagram("timeline\n2024 : First\n: Second\n: Third")
    assert diagram.tasks[0].events == ["First", "Second", "Third"]


def test_parse_title_directive() -> None:
    """A ``title <text>`` line sets the diagram title."""
    diagram = _parse_timeline_diagram("timeline\ntitle Project History\n2024")
    assert diagram.title == "Project History"


def test_parse_section_directive_dedup() -> None:
    """``section <name>`` records a banner (de-duplicated) and tags tasks."""
    diagram = _parse_timeline_diagram(
        "timeline\nsection A\n2024 : x\nsection B\n2025 : y\nsection A\n2026 : z"
    )
    assert diagram.sections == ["A", "B"]
    assert diagram.tasks[0].section == "A"
    assert diagram.tasks[1].section == "B"
    assert diagram.tasks[2].section == "A"


def test_parse_skips_blank_and_comment_lines() -> None:
    """Blank lines, ``%%`` comments, and ``#`` lines are skipped."""
    diagram = _parse_timeline_diagram(
        "%% header\ntimeline\n\n# a note\n%% body comment\n2024 : x"
    )
    assert len(diagram.tasks) == 1
    assert diagram.tasks[0].period == "2024"


def test_parse_period_with_colon_in_event_preserves_rest() -> None:
    """The first colon splits period from event; later colons stay in event."""
    diagram = _parse_timeline_diagram("timeline\n2024 : http://example.com")
    assert diagram.tasks[0].period == "2024"
    assert diagram.tasks[0].events == ["http://example.com"]


def test_parse_missing_header_raises() -> None:
    """A body whose first token is not ``timeline`` raises ParseError."""
    with pytest.raises(ParseError, match="Expected 'timeline' declaration"):
        _parse_timeline_diagram("flowchart TD\n  A --> B")


def test_parse_empty_body_returns_empty_model() -> None:
    """An empty body yields an empty model (no tasks); the renderer raises later."""
    diagram = _parse_timeline_diagram("")
    assert diagram.title is None
    assert diagram.sections == []
    assert diagram.tasks == []


def test_parse_header_only_no_tasks() -> None:
    """A bare ``timeline`` header with no entries parses to zero tasks."""
    diagram = _parse_timeline_diagram("timeline")
    assert diagram.tasks == []


# === render_timeline_diagram_to_svg: smoke + SVG structure =================


def test_render_emits_timeline_role_and_closing_tag() -> None:
    """The SVG root carries the timeline role; the document is well-closed."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert svg.startswith("<svg ")
    assert svg.endswith("</svg>")
    assert 'aria-roledescription="timeline"' in svg


def test_render_period_label_appears_in_svg() -> None:
    """The period label is emitted as node text inside the SVG."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert "2024" in svg
    assert "Launch" in svg


def test_render_viewbox_uses_negative_y_origin() -> None:
    """The viewBox starts at ``0 -25`` (grok L228) for the title row above."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert 'viewBox="0 -25 ' in svg


def test_render_emits_style_block_and_defs_marker() -> None:
    """A ``<style>`` CSS block and the ``<defs>`` arrowhead marker are present."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert "<style>" in svg and "</style>" in svg
    assert '<marker id="arrowhead"' in svg


def test_render_emits_direction_arrow_with_arrowhead_marker() -> None:
    """The horizontal direction arrow references the arrowhead marker."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert 'marker-end="url(#arrowhead)"' in svg


def test_render_dashed_connector_for_events() -> None:
    """A task with events emits a dashed vertical connector."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert 'stroke-dasharray="5,5"' in svg


# === render_timeline_diagram_to_svg: NOT theme-aware (core contract) =======


def test_render_light_and_dark_themes_produce_identical_svg() -> None:
    """Timeline is NOT theme-aware: light and dark produce byte-identical SVGs.

    grok hard-codes mermaid 11.12.2's default timeline palette and ignores the
    resolved theme (same stance as R282 pie / R283 packet / R285 gantt; unlike
    R284 sankey / R286 kanban which consult ``theme``). The strongest assertion
    is byte-equality of the two outputs.
    """
    source = "timeline\ntitle Demo\nsection A\n2024 : x\n: y\nsection B\n2025 : z"
    svg_light = render_timeline_diagram_to_svg(source, MermaidTheme.light())
    svg_dark = render_timeline_diagram_to_svg(source, MermaidTheme.dark())
    assert svg_light == svg_dark


def test_render_ignores_dark_node_fill_palette() -> None:
    """The dark theme's node-fill hex never appears in the timeline SVG."""
    svg = render_timeline_diagram_to_svg(
        "timeline\n2024 : Launch", MermaidTheme.dark()
    )
    # MermaidTheme.dark().node_fill is ``#2d2d2d``; timeline never consults it.
    assert "#2d2d2d" not in svg


# === render_timeline_diagram_to_svg: CSS section palette quirk =============


def test_render_emits_twelve_section_css_rules() -> None:
    """The CSS block emits exactly 12 ``.section-{si}`` fill rules (i = 0..11)."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    # Each of the 12 iterations emits one rect/path/circle fill rule.
    for i in range(MAX_SECTIONS):
        si = i - 1
        assert f".section-{si} rect,.section-{si} path,.section-{si} circle" in svg


def test_render_section_minus_1_uses_first_palette_entries() -> None:
    """``i=0`` -> ``.section--1`` with fill[0], label[0]=white, inv[0] stroke."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert f".section--1 rect,.section--1 path,.section--1 circle{{fill:{_CSCALE_FILLS[0]};" in svg
    assert ".section--1 text{fill:#ffffff;}" in svg  # _CSCALE_LABEL[0] == "#ffffff"
    assert f".section--1 line{{stroke:{_CSCALE_INV[0]};" in svg


def test_render_section_2_label_is_white() -> None:
    """``i=3`` -> ``.section-2`` text fill is white (_CSCALE_LABEL[3] == "#ffffff")."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert ".section-2 text{fill:#ffffff;}" in svg


def test_render_section_0_and_1_labels_are_black() -> None:
    """``i=1`` / ``i=2`` -> ``.section-0`` / ``.section-1`` text fill is black."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert ".section-0 text{fill:#000000;}" in svg  # _CSCALE_LABEL[1]
    assert ".section-1 text{fill:#000000;}" in svg  # _CSCALE_LABEL[2]


def test_render_section_line_uses_integer_stroke_width() -> None:
    """The section-line stroke-width renders as ``3`` (via ``_fmt``), NOT ``3.0``."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert f"stroke-width:{_fmt(NODE_LINE_STROKE_WIDTH)};" in svg
    assert "stroke-width:3.0;" not in svg


def test_render_svg_root_font_size_is_integer() -> None:
    """The SVG root CSS ``font-size`` renders as ``16px`` (via ``_fmt``), NOT ``16.0px``."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert "font-size:16px;" in svg
    assert "font-size:16.0px;" not in svg


def test_render_node_text_font_size_is_integer() -> None:
    """The node text style ``font-size`` renders as ``14px``, NOT ``14.0px``."""
    svg = render_timeline_diagram_to_svg("timeline\n2024 : Launch", MermaidTheme.light())
    assert "font-size:14px;" in svg
    assert "font-size:14.0px;" not in svg


# === render_timeline_diagram_to_svg: title / section / events =============


def test_render_title_appears_as_bold_text() -> None:
    """A parsed title emits a bold ``<text>`` element near the top."""
    svg = render_timeline_diagram_to_svg(
        "timeline\ntitle Project History\n2024", MermaidTheme.light()
    )
    assert "Project History" in svg
    assert 'font-weight="bold"' in svg


def test_render_no_title_omits_bold_text() -> None:
    """Without a title, no bold ``<text>`` title element is emitted."""
    svg = render_timeline_diagram_to_svg("timeline\n2024", MermaidTheme.light())
    assert 'font-weight="bold"' not in svg


def test_render_section_banner_emits_node() -> None:
    """A section grouping emits a section-banner node (timeline-node class)."""
    svg = render_timeline_diagram_to_svg(
        "timeline\nsection Phase 1\n2024 : x", MermaidTheme.light()
    )
    assert "Phase 1" in svg
    # The section banner and the task node both carry the timeline-node class.
    assert svg.count('class="timeline-node section-') >= 2


def test_render_apostrophe_in_event_escaped_to_pos_entity() -> None:
    """An apostrophe in an event label is escaped to ``&apos;``."""
    svg = render_timeline_diagram_to_svg(
        "timeline\n2024 : Tom's Launch", MermaidTheme.light()
    )
    assert "Tom&apos;s Launch" in svg
    assert "Tom's Launch" not in svg


# === render_timeline_diagram_to_svg: ParseError paths =====================


def test_render_missing_header_raises_parse_error() -> None:
    """A source whose first token is not ``timeline`` raises ParseError."""
    with pytest.raises(ParseError, match="Expected 'timeline' declaration"):
        render_timeline_diagram_to_svg("flowchart TD\n  A", MermaidTheme.light())


def test_render_header_only_raises_no_entries() -> None:
    """A bare ``timeline`` header with no tasks raises the no-entries ParseError."""
    with pytest.raises(ParseError, match="Timeline requires at least one entry"):
        render_timeline_diagram_to_svg("timeline", MermaidTheme.light())


def test_render_empty_source_raises_no_entries() -> None:
    """An empty source parses to zero tasks, then raises the no-entries error."""
    with pytest.raises(ParseError, match="Timeline requires at least one entry"):
        render_timeline_diagram_to_svg("", MermaidTheme.light())


# === mutable dataclass surface ===========================================


def test_timeline_task_events_default_does_not_leak_across_instances() -> None:
    """Two default-constructed tasks do not share their ``events`` lists."""
    a = TimelineTask(period="x")
    b = TimelineTask(period="y")
    a.events.append("e1")
    assert b.events == []


def test_timeline_task_events_append_mutates_in_place() -> None:
    """The parser's ``tasks[-1].events.append`` continuation contract holds."""
    task = TimelineTask(period="x")
    task.events.append("first")
    task.events.append("second")
    assert task.events == ["first", "second"]


def test_timeline_task_carries_optional_section() -> None:
    """A task's ``section`` defaults to ``None``."""
    assert TimelineTask(period="x").section is None
    assert TimelineTask(period="x", section="A").section == "A"


def test_timeline_diagram_holds_full_parsed_model() -> None:
    """The parsed model carries title, sections, and tasks together."""
    diagram = TimelineDiagram(
        title="T",
        sections=["A"],
        tasks=[TimelineTask(period="2024", events=["e"], section="A")],
    )
    assert diagram.title == "T"
    assert diagram.sections == ["A"]
    assert diagram.tasks[0].period == "2024"


# === dispatch (render_mermaid_to_svg) ====================================


def test_dispatch_timeline_no_longer_raises_unsupported() -> None:
    """The ``timeline`` token routes to the dedicated renderer (R287 arm)."""
    svg = render_mermaid_to_svg("timeline\n2024 : Launch")
    assert 'aria-roledescription="timeline"' in svg
    assert "2024" in svg


def test_dispatch_timeline_strips_frontmatter_before_render() -> None:
    """A ``---...---`` front-matter block is stripped before the timeline arm runs."""
    source = "---\ntitle: Doc\n---\ntimeline\n2024 : Launch"
    svg = render_mermaid_to_svg(source)
    assert "Launch" in svg
    assert 'aria-roledescription="timeline"' in svg


def test_dispatch_timeline_ignores_frontmatter_theme() -> None:
    """A front-matter ``config.theme`` preset is ignored (NOT theme-aware).

    Unlike R284 sankey / R286 kanban, the timeline renderer hard-codes its
    palette, so a dark front-matter preset must NOT flow into the SVG.
    """
    source = "---\nconfig:\n  theme: dark\n---\ntimeline\n2024 : Launch"
    svg = render_mermaid_to_svg(source)
    assert "#2d2d2d" not in svg  # dark node_fill never reaches timeline
    assert 'aria-roledescription="timeline"' in svg
