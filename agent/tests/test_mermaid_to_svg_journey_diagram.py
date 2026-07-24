"""Black-box tests for the migrated journey renderer (R290, direction (1) brick 21).

Exercises :mod:`minimax_code.mermaid.to_svg.journey_diagram` -- the functional
clone of grok ``mermaid-to-svg/src/journey_diagram.rs``. This is the 12th
per-diagram leaf and the 11th self-contained SVG emitter (same lineage as
R279 info / R281 radar / R282 pie / R283 packet / R284 sankey / R285 gantt /
R286 kanban / R287 timeline / R288 quadrant / R289 block). ``journey`` is
mermaid's user-journey map: a titled timeline of tasks grouped into sections,
where each task carries a 1-5 satisfaction score (rendered as a happy/neutral/
sad face icon) and the actors responsible for it.

The renderer is **theme-unaware** (like pie / packet / gantt / timeline): the
``_theme`` parameter is accepted for dispatch symmetry but never read -- grok
hard-codes mermaid 11.12.2's default journey palette. Tests pin this boundary
by asserting a dark theme does not change the emitted SVG.

Coverage areas (mirrors the R279-R289 per-diagram test-file pattern):

* **dispatch smoke** -- ``journey`` end-to-end renders ``<svg>...</svg>``,
  no longer raises ``UnsupportedDiagramType``, the role attribute is
  ``journey``.
* **parse rules** -- header must be ``journey`` (else ``ParseError``),
  ``title`` / ``section`` / ``name: score [: actors]`` rows, score int cast,
  comma-separated actors, blank / ``%%`` skips, empty title/section ignored.
* **boundary errors** -- missing ``journey`` declaration / invalid task line
  (``< 2`` colon parts) / invalid score / empty body / section-only (no task)
  -> two distinct ``ParseError`` messages (parse-phase vs render-phase).
* **geometry** -- task.x stride, task_y (section_v_height 110), arrow_y 200,
  face_cy by score, width/height/viewBox/svg-height formulas, title adds 70,
  arrow_x2.
* **face icon three states** -- smile (``score > 3``) / sad (``score < 3``) /
  neutral line (``score == 3``), arc path strings via the D3 number bridge.
* **theme-unaware boundary** -- dark == light output.
* **helpers** -- ``_fmt_num`` (D3 3-decimal), ``_fmt0`` (whole number),
  ``_escape_xml`` (``&apos;`` variant), ``_position``, arc builders.
* **module / barrel surface** -- ``__all__`` is the single public symbol; the
  symbol is NOT re-exported by the to_svg barrel (dispatch-only reach).
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import MermaidTheme, ParseError
from minimax_code.mermaid.to_svg import journey_diagram as journey_mod
from minimax_code.mermaid.to_svg.journey_diagram import render_journey_diagram_to_svg
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# Fixture source used across the geometry / face / theme tests: one section,
# two tasks (scores 5 and 1 to exercise both smile and sad mouths), two actors.
_TWO_TASK_SOURCE = (
    "journey\n"
    "title My Journey\n"
    "section First phase\n"
    "Go online: 5: Me\n"
    "Fail task: 1: Me, Other\n"
)


# === dispatch smoke ========================================================


def test_render_journey_returns_svg_string() -> None:
    """The direct renderer returns a structured SVG string for valid input."""
    svg = render_journey_diagram_to_svg("journey\ntitle T\nsection S\nTask: 3: Me", MermaidTheme.light())
    assert isinstance(svg, str)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_render_mermaid_to_svg_journey_dispatches_to_dedicated_arm() -> None:
    """``journey`` end-to-end renders through the dedicated dispatch arm.

    R290 promotes ``journey`` out of the unsupported set (R277 had it listed);
    the crate-root dispatch now routes it to the dedicated renderer instead of
    raising ``UnsupportedDiagramType``.
    """
    svg = render_mermaid_to_svg("journey\ntitle T\nsection S\nTask: 3: Me")
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_journey_no_longer_raises_unsupported() -> None:
    """``journey`` is no longer in the unsupported-diagram set (R290)."""
    svg = render_mermaid_to_svg("journey\nsection S\nTask: 3: Me")
    assert "journey" in svg  # the aria-roledescription echoes the type


def test_render_journey_role_attribute() -> None:
    """The root SVG carries ``aria-roledescription="journey"`` (grok header)."""
    svg = render_journey_diagram_to_svg("journey\nsection S\nTask: 3: Me", MermaidTheme.light())
    assert 'aria-roledescription="journey"' in svg
    assert 'role="graphics-document document"' in svg


def test_render_journey_has_xlinky_namespace() -> None:
    """The root SVG declares both xmlns and xmlns:xlink (grok header L151)."""
    svg = render_journey_diagram_to_svg("journey\nsection S\nTask: 3: Me", MermaidTheme.light())
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert 'xmlns:xlink="http://www.w3.org/1999/xlink"' in svg


# === parse rules ===========================================================


def test_parse_missing_journey_declaration_raises() -> None:
    """A first token other than ``journey`` is a parse error (grok L474-L477)."""
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg("flowchart TD\n  A --> B", MermaidTheme.light())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'journey' declaration"


def test_parse_journey_declaration_after_comments() -> None:
    """Blank / ``%%`` lines before the ``journey`` header are skipped."""
    svg = render_journey_diagram_to_svg(
        "\n%% lead-in\njourney\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert "<svg" in svg


def test_parse_title_is_captured() -> None:
    """``title <text>`` populates the diagram title (rendered as bold text)."""
    svg = render_journey_diagram_to_svg(
        "journey\ntitle Hello World\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert "Hello World" in svg
    assert 'font-weight="bold"' in svg


def test_parse_bare_title_keyword_falls_through_to_task_error() -> None:
    """A bare ``title`` (trimmed, no text) does NOT match the title branch.

    The title branch guards on ``line.startswith("title ")`` (grok L493) -- it
    requires a space-delimited argument. A trimmed bare ``title`` token (from
    e.g. ``"title   "`` or a bare ``title`` line) falls through to task
    parsing, which rejects it as a colon-less task row.
    """
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg(
            "journey\ntitle   \nsection S\nTask: 3: Me", MermaidTheme.light()
        )
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid journey task line: title"


def test_parse_section_captured() -> None:
    """``section <name>`` records the section name (rendered as a labelled rect)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection Onboarding\nTask: 3: Me", MermaidTheme.light()
    )
    assert "Onboarding" in svg


def test_parse_bare_section_keyword_falls_through_to_task_error() -> None:
    """A bare ``section`` (trimmed, no name) does NOT match the section branch.

    The section branch guards on ``line.startswith("section ")`` (grok L501) --
    it requires a space-delimited name. A trimmed bare ``section`` token falls
    through to task parsing, which rejects it as a colon-less task row.
    """
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg(
            "journey\nsection   \nTask: 3: Me", MermaidTheme.light()
        )
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid journey task line: section"


def test_parse_task_score_int_cast() -> None:
    """The score field parses as an int (grok L521-L525)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 4: Me", MermaidTheme.light()
    )
    # score 4 -> smile mouth (score > 3).
    assert "M6.818,0A6.818" in svg


def test_parse_task_actors_comma_separated() -> None:
    """Actors split on ``,`` into separate names (grok L527-L535)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Alice, Bob", MermaidTheme.light()
    )
    # Both actors appear in the legend.
    assert "Alice" in svg
    assert "Bob" in svg


def test_parse_task_actors_optional() -> None:
    """A task without an actors field parses with an empty actor list."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3", MermaidTheme.light()
    )
    assert "<svg" in svg


def test_parse_skips_blank_and_comment_lines() -> None:
    """Blank lines and ``%%`` comments inside the body are skipped."""
    svg = render_journey_diagram_to_svg(
        "journey\n\n%% a comment\nsection S\n\nTask: 3: Me\n%% trailing\n",
        MermaidTheme.light(),
    )
    assert "<svg" in svg


def test_parse_actor_first_appearance_order() -> None:
    """Actors are collected in first-appearance order for the legend."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nFirst: 3: Zoe\nSecond: 3: Zoe\nThird: 3: Alice",
        MermaidTheme.light(),
    )
    # Zoe appears before Alice in the legend (legend y ascends with position).
    zoe_pos = svg.find("Zoe")
    alice_pos = svg.find("Alice")
    assert 0 <= zoe_pos < alice_pos


# === boundary errors =======================================================


def test_parse_invalid_task_line_raises() -> None:
    """A task row with fewer than 2 colon-parts is a parse error (grok L514)."""
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg("journey\nNoColonHere", MermaidTheme.light())
    assert "Invalid journey task line" in exc_info.value.message
    assert "NoColonHere" in exc_info.value.message


def test_parse_invalid_score_raises() -> None:
    """A non-integer score is a parse error (grok L522-L525)."""
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg(
            "journey\nsection S\nTask: abc: Me", MermaidTheme.light()
        )
    assert "Invalid journey score" in exc_info.value.message
    assert "Task: abc: Me" in exc_info.value.message


def test_parse_empty_body_after_journey_raises() -> None:
    """A body with only the ``journey`` header (no rows) is a parse error.

    grok L547-L552: an empty row stream raises at line 1.
    """
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg("journey\n", MermaidTheme.light())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Journey requires at least one section/task"


def test_render_section_only_no_task_raises() -> None:
    """A body with only ``section`` rows (no tasks) raises in the render phase.

    grok L86-L91: parsing succeeds (rows is non-empty) but flattening yields
    zero tasks, which is a render-phase ``ParseError`` at line 1.
    """
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg("journey\nsection S\n", MermaidTheme.light())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Journey requires at least one task"


def test_parse_invalid_task_line_carries_line_number() -> None:
    """The parse error line number reflects the offending source line."""
    with pytest.raises(ParseError) as exc_info:
        render_journey_diagram_to_svg(
            "journey\nsection S\nTask: 3: Me\nBadLine\n", MermaidTheme.light()
        )
    assert exc_info.value.line == 4


# === geometry =============================================================


def test_geometry_task_x_stride() -> None:
    """task.x = i*TASK_MARGIN + i*TASK_WIDTH + LEFT_MARGIN (grok L119-L121).

    LEFT_MARGIN=150, TASK_MARGIN=50, TASK_WIDTH=150 -> stride 200 per task:
    task 0 at x=150, task 1 at x=350, task 2 at x=550.
    """
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nA: 3: Me\nB: 3: Me\nC: 3: Me", MermaidTheme.light()
    )
    # The task rect x attributes carry the stride positions.
    assert 'x="150"' in svg
    assert 'x="350"' in svg
    assert 'x="550"' in svg


def test_geometry_task_y_is_section_v_height() -> None:
    """task_y = TASK_HEIGHT*2 + DIAGRAM_MARGIN_Y = 110 (grok L124-L125)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # The dashed task line starts at y1=110.
    assert 'y1="110"' in svg


def test_geometry_arrow_y_is_two_hundred() -> None:
    """arrow_y = TASK_HEIGHT * 4 = 200 (grok L128)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # The horizontal arrow line sits at y=200.
    assert 'y1="200"' in svg
    assert 'y2="200"' in svg


def test_geometry_face_cy_high_score() -> None:
    """score 5 -> face_cy = MAX_FACE_Y + 0 = 300 (top of the band)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 5: Me", MermaidTheme.light()
    )
    assert 'cy="300"' in svg


def test_geometry_face_cy_low_score() -> None:
    """score 1 -> face_cy = 300 + 4*30 = 420 (bottom of the band)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 1: Me", MermaidTheme.light()
    )
    assert 'cy="420"' in svg


def test_geometry_face_cy_mid_score() -> None:
    """score 3 -> face_cy = 300 + 2*30 = 360."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'cy="360"' in svg


def test_geometry_height_is_four_seventy() -> None:
    """height = TASK_LINE_BOTTOM(450) + 2*DIAGRAM_MARGIN_Y(10) = 470."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # svg_height (no title) = 470 + 25 = 495.
    assert 'height="495"' in svg


def test_geometry_title_adds_seventy_to_viewbox() -> None:
    """A present title adds 70 to the viewBox/svg height (grok L319-L326)."""
    svg_no_title = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    svg_title = render_journey_diagram_to_svg(
        "journey\ntitle T\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # svg_height: 495 (no title) vs 565 (title -> +70).
    assert 'height="495"' in svg_no_title
    assert 'height="565"' in svg_title


def test_geometry_width_single_task() -> None:
    """1 task -> width = 150 + (150+50+50) + 100 = 500."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'style="max-width: 500px;"' in svg
    assert 'viewBox="0 -25 500 ' in svg


def test_geometry_width_two_tasks() -> None:
    """2 tasks -> last_task_x=350, bounds_stopx=450, width=700."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nA: 3: Me\nB: 3: Me", MermaidTheme.light()
    )
    assert 'style="max-width: 700px;"' in svg


def test_geometry_arrow_x2_single_task() -> None:
    """arrow_x2 = width - LEFT_MARGIN - 4 = 500 - 150 - 4 = 346."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # The arrow line x2 for a single-task chart (width 500).
    assert 'x2="346"' in svg


def test_geometry_task_line_bottom_four_fifty() -> None:
    """The dashed task line bottom is TASK_LINE_BOTTOM = 450."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'y2="450"' in svg


# === face icon three states ================================================


def test_face_score_above_three_emits_smile_arc() -> None:
    """score > 3 -> smile mouth via ``_generate_smile_arc`` (grok L358-L367)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 5: Me", MermaidTheme.light()
    )
    # Smile arc path (open upward): starts with positive outer radius.
    assert "M6.818,0A6.818,6.818,0,1,1,-6.818,0L-7.5,0A7.5,7.5,0,1,0,7.5,0Z" in svg
    # Smile is translated to cy + 2.
    assert 'transform="translate(' in svg


def test_face_score_below_three_emits_sad_arc() -> None:
    """score < 3 -> sad mouth via ``_generate_sad_arc`` (grok L369-L378)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 1: Me", MermaidTheme.light()
    )
    # Sad arc path (open downward): starts with negative outer radius.
    assert "M-6.818,0A6.818,6.818,0,1,1,6.818,0L7.5,0A7.5,7.5,0,1,0,-7.5,0Z" in svg


def test_face_score_equal_three_emits_neutral_line() -> None:
    """score == 3 -> a neutral straight ``<line>`` mouth (grok L380-L386)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # Neutral mouth: a <line class="mouth"> with x1 = cx-5, x2 = cx+5.
    assert 'class="mouth"' in svg
    # No smile/sad arc path data when the score is neutral.
    assert "M6.818,0A6.818" not in svg
    assert "M-6.818,0A6.818" not in svg


def test_face_circle_emitted() -> None:
    """The face icon is anchored on a ``class="face"`` circle (grok L340)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'class="face"' in svg
    assert 'r="15"' in svg  # FACE_RADIUS


def test_face_eyes_emitted() -> None:
    """Two eye circles flank the face center (grok L348-L356)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # Eyes are r="1.5" circles.
    assert svg.count('r="1.5"') >= 2


def test_generate_smile_arc_path() -> None:
    """``_generate_smile_arc`` builds the canonical d3.arc smile path."""
    # inner_r = FACE_RADIUS/2 = 7.5, outer_r = FACE_RADIUS/2.2 = 6.818...
    assert journey_mod._generate_smile_arc(7.5, 15.0 / 2.2) == (
        "M6.818,0A6.818,6.818,0,1,1,-6.818,0L-7.5,0A7.5,7.5,0,1,0,7.5,0Z"
    )


def test_generate_sad_arc_path() -> None:
    """``_generate_sad_arc`` builds the canonical d3.arc frown path."""
    assert journey_mod._generate_sad_arc(7.5, 15.0 / 2.2) == (
        "M-6.818,0A6.818,6.818,0,1,1,6.818,0L7.5,0A7.5,7.5,0,1,0,-7.5,0Z"
    )


def test_face_smile_uses_d3_number_format() -> None:
    """The arc radii are formatted via D3's number bridge (3 decimals)."""
    # FACE_RADIUS/2.2 = 6.818181... -> D3 rounds to "6.818" (3 decimals).
    assert "6.818" in journey_mod._generate_smile_arc(7.5, 15.0 / 2.2)
    # FACE_RADIUS/2 = 7.5 -> D3 strips the trailing zero -> "7.5".
    assert "7.5" in journey_mod._generate_smile_arc(7.5, 15.0 / 2.2)


# === theme-unaware boundary ================================================


def test_journey_is_theme_unaware_dark_equals_light() -> None:
    """A dark theme must NOT change the emitted SVG (journey hard-codes palette).

    grok ``render_journey_diagram_to_svg`` accepts ``_theme`` and ignores it;
    the CSS pins mermaid's default light palette. This pins the boundary so a
    future refactor cannot silently make journey theme-aware.
    """
    light = render_journey_diagram_to_svg(_TWO_TASK_SOURCE, MermaidTheme.light())
    dark = render_journey_diagram_to_svg(_TWO_TASK_SOURCE, MermaidTheme.dark())
    assert light == dark


def test_journey_hard_coded_palette_in_css() -> None:
    """The CSS pins the default journey palette regardless of theme (grok L166)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.dark()
    )
    # text fill is hard-coded #333, faces are #FFF8DC -- NOT derived from theme.
    assert "fill:#333" in svg
    assert "#FFF8DC" in svg


# === palette cycling =======================================================


def test_section_svg_fills_cycle() -> None:
    """Section rects cycle through ``SECTION_SVG_FILLS`` by index (grok L177)."""
    # Two distinct sections -> two different section rect fills.
    svg = render_journey_diagram_to_svg(
        "journey\nsection A\nT1: 3: Me\nsection B\nT2: 3: Me", MermaidTheme.light()
    )
    assert "#191970" in svg  # SECTION_SVG_FILLS[0]
    assert "#8B008B" in svg  # SECTION_SVG_FILLS[1]


def test_actor_colours_cycle() -> None:
    """Actor circles cycle through ``ACTOR_COLOURS`` (grok L182-L184)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nT: 3: Alice, Bob", MermaidTheme.light()
    )
    assert "#8FBC8F" in svg  # ACTOR_COLOURS[0]
    assert "#7CFC00" in svg  # ACTOR_COLOURS[1]


def test_css_section_type_classes_emitted() -> None:
    """The CSS emits ``.task-type-N`` / ``.section-type-N`` for each SECTION_FILLS."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    # All 8 SECTION_FILLS produce a CSS rule.
    assert ".task-type-0" in svg
    assert ".section-type-7" in svg
    assert "#ECECFF" in svg  # SECTION_FILLS[0]


# === helpers ===============================================================


def test_fmt_num_drops_trailing_zeros() -> None:
    """``_fmt_num`` mirrors D3: round to 3 decimals, strip trailing zeros."""
    assert journey_mod._fmt_num(7.5) == "7.5"
    assert journey_mod._fmt_num(250.0) == "250"
    assert journey_mod._fmt_num(6.818181) == "6.818"


def test_fmt_num_keeps_three_decimals() -> None:
    """``_fmt_num`` keeps up to 3 decimal places when needed."""
    assert journey_mod._fmt_num(6.818) == "6.818"
    assert journey_mod._fmt_num(0.123456) == "0.123"


def test_fmt0_emits_whole_number() -> None:
    """``_fmt0`` mirrors Rust ``{:.0}`` -- integer-valued floats lose ``.0``."""
    assert journey_mod._fmt0(110.0) == "110"
    assert journey_mod._fmt0(495.0) == "495"
    assert journey_mod._fmt0(0.0) == "0"


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """``_escape_xml`` escapes the five XML-significant chars (grok L557-L563).

    Note the ``'`` -> ``&apos;`` mapping (grok's journey source uses the named
    entity, unlike R289 block's ``&#x27;``).
    """
    assert journey_mod._escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&apos;f"


def test_escape_xml_apos_variant_not_hash_x27() -> None:
    """Journey uses ``&apos;`` (named entity), NOT ``&#x27;`` (hex entity)."""
    assert "&apos;" in journey_mod._escape_xml("'")
    assert "&#x27;" not in journey_mod._escape_xml("'")


def test_escape_xml_empty_string() -> None:
    """An empty string escapes to itself."""
    assert journey_mod._escape_xml("") == ""


def test_position_returns_index() -> None:
    """``_position`` returns the actor's index in the legend order."""
    assert journey_mod._position(["Alice", "Bob", "Carol"], "Bob") == 1


def test_position_returns_none_when_absent() -> None:
    """``_position`` returns ``None`` for an unknown actor (mirrors iter::position)."""
    assert journey_mod._position(["Alice", "Bob"], "Carol") is None


# === structure / sections =================================================


def test_section_rect_emitted_with_rounded_corners() -> None:
    """Section rects carry ``rx="3" ry="3"`` and the ``journey-section`` class."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'class="journey-section section-type-0"' in svg
    assert 'rx="3" ry="3"' in svg


def test_task_rect_emitted_with_class() -> None:
    """Task rects carry the ``task task-type-N`` class."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'class="task task-type-0"' in svg


def test_actor_legend_circles_emitted() -> None:
    """Each actor in the legend gets a circle + label (grok L192-L204)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Alice, Bob", MermaidTheme.light()
    )
    # Legend circles start at cx=20, y=60 with 20px stride.
    assert 'cx="20"' in svg
    assert "Alice" in svg
    assert "Bob" in svg


def test_arrowhead_marker_emitted() -> None:
    """The arrowhead ``<marker>`` def is present (grok L189)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Me", MermaidTheme.light()
    )
    assert 'id="arrowhead"' in svg
    assert 'marker-end="url(#arrowhead)"' in svg


def test_task_actor_circles_carry_title_tooltip() -> None:
    """Per-task actor circles carry a ``<title>`` tooltip (grok L283-L294)."""
    svg = render_journey_diagram_to_svg(
        "journey\nsection S\nTask: 3: Alice", MermaidTheme.light()
    )
    assert "<title>Alice</title>" in svg


# === front-matter dispatch =================================================


def test_render_mermaid_to_svg_journey_strips_frontmatter() -> None:
    """A front-matter block (no theme) is stripped before the journey body runs."""
    source = "---\ntitle: Demo\n---\njourney\nsection S\nTask: 3: Me"
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert 'aria-roledescription="journey"' in svg


def test_render_mermaid_to_svg_journey_ignores_frontmatter_theme() -> None:
    """A front-matter ``theme: dark`` does NOT recolor journey (theme-unaware)."""
    source = "---\nconfig:\n  theme: dark\n---\njourney\nsection S\nTask: 3: Me"
    svg = render_mermaid_to_svg(source)
    # Journey hard-codes #333 text -- dark theme must not change it.
    assert "fill:#333" in svg


# === module / barrel surface ==============================================


def test_journey_module_all_is_single_public_symbol() -> None:
    """The journey leaf exports exactly one public symbol (grok ``pub fn``)."""
    assert journey_mod.__all__ == ["render_journey_diagram_to_svg"]


def test_journey_symbol_not_in_to_svg_barrel() -> None:
    """The journey symbol is NOT re-exported by the barrel (dispatch-only reach).

    Mirrors grok's crate root never re-exporting per-diagram renderers. The
    barrel ``__all__`` stays 18 (R277 baseline); the symbol is reached only via
    the ``render.py`` dispatch arm.
    """
    assert "render_journey_diagram_to_svg" not in to_svg.__all__
    assert len(to_svg.__all__) == 18


def test_render_journey_accepts_theme_without_reading_it() -> None:
    """The ``_theme`` parameter is accepted (dispatch symmetry) but unread.

    Passing different themes yields identical output -- the renderer hard-codes
    the palette. This locks the YAGNI boundary documented in the module
    docstring.
    """
    source = "journey\nsection S\nTask: 3: Me"
    light = render_journey_diagram_to_svg(source, MermaidTheme.light())
    dark = render_journey_diagram_to_svg(source, MermaidTheme.dark())
    custom = MermaidTheme(
        background="#abc123",
        text_color="#000000",
        edge_color="#111111",
        node_fill="#222222",
        node_stroke="#333333",
    )
    other = render_journey_diagram_to_svg(source, custom)
    assert light == dark == other
