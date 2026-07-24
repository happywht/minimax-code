"""Black-box tests for the migrated mermaid-to-svg kanban renderer (R286).

Exercises :mod:`minimax_code.mermaid.to_svg.kanban_diagram` -- the behavioral-
equivalent port of grok ``mermaid-to-svg/src/kanban_diagram.rs`` (direction
(1), brick 19). This is the eighth per-diagram leaf and the seventh
self-contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` /
R282 ``pie`` / R283 ``packet`` / R284 ``sankey`` / R285 ``gantt``).

The kanban diagram lays a board out as titled columns of task cards. Coverage
mirrors the four concerns in the module docstring:

* **Parser** (``_parse_kanban``): header detection, column-vs-task indentation,
  blank / ``%%`` skipping, and the three ParseError paths (missing header,
  non-``kanban`` first token, task before any column).
* **Shape-data bridge** (``_split_label_and_shape_data`` /
  ``_apply_shape_data``): the ``@{ ... }`` suffix extraction, the single-line
  vs multi-line YAML wrapping, and the four metadata fields (``label`` /
  ``assigned`` / ``priority`` / ``ticket``). YAML parse failures and non-
  mapping documents raise :class:`ParseError` stamped with the source line.
* **Float / HSL / text-width bridges** (``_fmt`` / ``_section_hsl`` /
  ``_section_text_color`` / ``_color_from_priority`` / ``_estimate_text_width``
  / ``_yaml_get_string``): the Rust ``Display`` / ``rem_euclid`` / byte-length
  semantics that keep the emitted CSS and geometry byte-identical to grok.
* **Renderer + dispatch** (``render_kanban_diagram_to_svg``): canvas sizing,
  theme interpolation (``text_color`` / ``node_stroke``), the 11-section CSS
  palette, the per-card priority indicator stripe, the ``&apos;`` XML escape,
  and the grok ``test_simple_kanban_diagram`` smoke (lib.rs L992-L1007).
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.kanban_diagram import (
    BOTTOM_PADDING,
    COLUMN_GAP,
    COLUMN_WIDTH,
    DIAGRAM_PADDING,
    HEADER_HEIGHT,
    TASK_GAP,
    TASK_HEIGHT,
    TASK_HEIGHT_WITH_ASSIGNED,
    KanbanColumn,
    KanbanTask,
    _apply_shape_data,
    _color_from_priority,
    _estimate_text_width,
    _fmt,
    _parse_kanban,
    _section_hsl,
    _section_text_color,
    _split_label_and_shape_data,
    _task_height,
    _tasks_stack_height,
    _yaml_get_string,
    render_kanban_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# Light palette tokens (theme.MermaidTheme.light) -- the default theme's
# signature colors, interpolated into the kanban CSS.
_LIGHT_TEXT_COLOR = "#333333"
_LIGHT_NODE_STROKE = "#9370DB"
# Dark palette tokens (theme.MermaidTheme.dark).
_DARK_TEXT_COLOR = "#ffffff"
_DARK_NODE_STROKE = "#888888"


# === _fmt (Rust Display bridge, shared with R281 radar / R285 gantt) ========


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


# === _estimate_text_width (byte-length bridge) =============================


def test_estimate_text_width_empty_is_zero() -> None:
    """An empty string has zero estimated width."""
    assert _estimate_text_width("") == 0.0


def test_estimate_text_width_ascii_uses_byte_count() -> None:
    """ASCII chars are 1 byte each -> ``len * 9.5``."""
    assert _estimate_text_width("abc") == 3 * 9.5
    assert _estimate_text_width("hello") == 5 * 9.5


def test_estimate_text_width_multibyte_uses_byte_count() -> None:
    """Multi-byte chars count by bytes, not codepoints (Rust ``String::len``).

    A CJK char is 3 bytes in UTF-8, so ``"中文"`` is 6 bytes -> ``6 * 9.5``.
    """
    assert _estimate_text_width("中文") == 6 * 9.5


# === _color_from_priority ==================================================


@pytest.mark.parametrize(
    "priority, expected",
    [
        ("Very High", "red"),
        ("High", "orange"),
        ("Low", "blue"),
        ("Very Low", "lightblue"),
        ("Medium", None),
        ("Unknown", None),
        ("", None),
    ],
)
def test_color_from_priority(priority: str, expected: str | None) -> None:
    """Priority labels map to indicator-stripe colors; Medium/unknown -> None."""
    assert _color_from_priority(priority) is expected


# === _section_hsl / _section_text_color (CSS palette) ======================


@pytest.mark.parametrize(
    "idx, expected",
    [
        (0, (60, 100.0, 83.5294117647)),
        (1, (80, 100.0, 86.2745098039)),
        (2, (270, 100.0, 86.2745098039)),
        (3, (300, 100.0, 86.2745098039)),
        (4, (330, 100.0, 86.2745098039)),
        (5, (0, 100.0, 86.2745098039)),
        (6, (30, 100.0, 86.2745098039)),
        (7, (90, 100.0, 86.2745098039)),
        (8, (150, 100.0, 86.2745098039)),
        (9, (180, 100.0, 86.2745098039)),
        (10, (210, 100.0, 86.2745098039)),
    ],
)
def test_section_hsl_css_range(idx: int, expected: tuple[int, float, float]) -> None:
    """Sections 0-10 cycle through the 12-hue palette (grok ``section_hsl``)."""
    assert _section_hsl(idx) == expected


def test_section_hsl_wraps_modulo_12() -> None:
    """``idx % 12`` wraps the hue (floor modulo == Rust ``rem_euclid`` for >=0).

    Only the hue wraps -- section 0's darker lightness is NOT inherited by
    section 12 (the ``idx == 0`` arm is literal), so the full tuples differ.
    """
    hue0, _, _ = _section_hsl(0)
    hue12, _, _ = _section_hsl(12)
    hue1, _, _ = _section_hsl(1)
    hue13, _, _ = _section_hsl(13)
    assert hue12 == hue0
    assert hue13 == hue1
    # Full tuples differ because section 0's darker lightness is exclusive to idx==0.
    assert _section_hsl(12) != _section_hsl(0)


def test_section_hsl_section0_uses_darker_lightness() -> None:
    """Section 0 is the only one with the darker lightness literal."""
    _, _, light0 = _section_hsl(0)
    _, _, light1 = _section_hsl(1)
    assert light0 == 83.5294117647
    assert light1 == 86.2745098039
    assert light0 < light1


@pytest.mark.parametrize(
    "idx, expected",
    [
        (-1, "#ffffff"),
        (0, "black"),
        (1, "black"),
        (2, "#ffffff"),
        (3, "black"),
        (10, "black"),
    ],
)
def test_section_text_color(idx: int, expected: str) -> None:
    """``-1`` and ``2`` -> white; everything else -> black (grok match)."""
    assert _section_text_color(idx) == expected


# === _yaml_get_string (serde_yaml Value match bridge) ======================


def test_yaml_get_string_string_passthrough() -> None:
    """A YAML string value passes through unchanged."""
    assert _yaml_get_string({"k": "FOO-123"}, "k") == "FOO-123"


def test_yaml_get_string_int_renders_verbatim() -> None:
    """A YAML int renders via ``str`` (Rust ``Number::to_string``)."""
    assert _yaml_get_string({"k": 123}, "k") == "123"


def test_yaml_get_string_float_uses_fmt() -> None:
    """A YAML float renders via ``_fmt`` (Rust f64 Display)."""
    assert _yaml_get_string({"k": 1.5}, "k") == "1.5"
    assert _yaml_get_string({"k": 42.0}, "k") == "42"


def test_yaml_get_string_bool_renders_lowercase() -> None:
    """A YAML bool renders lowercase (Rust ``true``/``false``, NOT Python ``True``)."""
    assert _yaml_get_string({"k": True}, "k") == "true"
    assert _yaml_get_string({"k": False}, "k") == "false"


def test_yaml_get_string_missing_key_returns_none() -> None:
    """A missing key returns ``None``."""
    assert _yaml_get_string({"k": "v"}, "other") is None


def test_yaml_get_string_explicit_none_returns_none() -> None:
    """An explicit YAML null returns ``None``."""
    assert _yaml_get_string({"k": None}, "k") is None


def test_yaml_get_string_non_scalar_returns_none() -> None:
    """A list / dict value returns ``None`` (grok's ``_`` arm)."""
    assert _yaml_get_string({"k": [1, 2]}, "k") is None
    assert _yaml_get_string({"k": {"nested": 1}}, "k") is None


# === _task_height / _tasks_stack_height ====================================


def test_task_height_without_assigned() -> None:
    """A task without an assignee uses the shorter height."""
    assert _task_height(KanbanTask(label="x")) == TASK_HEIGHT


def test_task_height_with_assigned() -> None:
    """A task with an assignee uses the taller height (room for the name)."""
    assert _task_height(KanbanTask(label="x", assigned="a")) == TASK_HEIGHT_WITH_ASSIGNED


def test_tasks_stack_height_single_task() -> None:
    """A single-task stack is just that task's height (no gap)."""
    assert _tasks_stack_height([KanbanTask(label="x")]) == TASK_HEIGHT


def test_tasks_stack_height_multiple_tasks_adds_gaps() -> None:
    """Each task after the first contributes TASK_GAP before its height."""
    tasks = [KanbanTask(label="a"), KanbanTask(label="b"), KanbanTask(label="c")]
    expected = 3 * TASK_HEIGHT + 2 * TASK_GAP
    assert _tasks_stack_height(tasks) == expected


def test_tasks_stack_height_mixed_assigned() -> None:
    """Mixed assigned / unassigned tasks sum their per-task heights."""
    tasks = [KanbanTask(label="a", assigned="x"), KanbanTask(label="b")]
    expected = TASK_HEIGHT_WITH_ASSIGNED + TASK_GAP + TASK_HEIGHT
    assert _tasks_stack_height(tasks) == expected


def test_tasks_stack_height_empty_is_zero() -> None:
    """An empty task list has zero stack height."""
    assert _tasks_stack_height([]) == 0.0


# === _split_label_and_shape_data ===========================================


def test_split_label_no_shape_data_returns_label_only() -> None:
    """A line without ``@{`` is all label, no shape data."""
    assert _split_label_and_shape_data("Task 1") == ("Task 1", None)


def test_split_label_single_line_shape_data() -> None:
    """A single-line ``@{ ... }`` suffix is split into label + interior."""
    label, shape = _split_label_and_shape_data(
        "Task 1@{ ticket: FOO-123, assigned: reviews }"
    )
    assert label == "Task 1"
    assert shape == "ticket: FOO-123, assigned: reviews"


def test_split_label_multi_line_shape_data() -> None:
    """A multi-line ``@{ ... }`` suffix preserves interior newlines."""
    label, shape = _split_label_and_shape_data("Task 1@{ ticket: FOO-123\nassigned: reviews }")
    assert label == "Task 1"
    assert "ticket: FOO-123" in shape
    assert "assigned: reviews" in shape


def test_split_label_no_closing_brace_returns_label_only() -> None:
    """An ``@{`` without a closing ``}`` is not split (whole line is label)."""
    label, shape = _split_label_and_shape_data("Task 1@{ ticket: FOO-123")
    assert label == "Task 1@{ ticket: FOO-123"
    assert shape is None


# === _apply_shape_data =====================================================


def test_apply_shape_data_single_line_assigns_fields() -> None:
    """A single-line flow mapping stamps the four metadata fields."""
    task = KanbanTask(label="Task 1")
    _apply_shape_data(task, "ticket: FOO-123, assigned: reviews, priority: Very High", 1)
    assert task.ticket == "FOO-123"
    assert task.assigned == "reviews"
    assert task.priority == "Very High"


def test_apply_shape_data_label_overrides_default() -> None:
    """A ``label`` key in the metadata overrides the line-prefix label."""
    task = KanbanTask(label="Original")
    _apply_shape_data(task, "label: Overridden", 1)
    assert task.label == "Overridden"


def test_apply_shape_data_partial_fields() -> None:
    """Only the keys present are stamped; absent keys stay ``None``."""
    task = KanbanTask(label="Task")
    _apply_shape_data(task, "assigned: alice", 1)
    assert task.assigned == "alice"
    assert task.priority is None
    assert task.ticket is None


def test_apply_shape_data_invalid_yaml_raises() -> None:
    """Malformed YAML in the shape data raises ParseError."""
    task = KanbanTask(label="Task")
    with pytest.raises(ParseError):
        _apply_shape_data(task, "label: [unclosed", 1)


def test_apply_shape_data_non_mapping_raises() -> None:
    """A YAML document that is not a mapping raises ParseError."""
    task = KanbanTask(label="Task")
    with pytest.raises(ParseError):
        _apply_shape_data(task, "- item1\n- item2", 1)


# === _parse_kanban =========================================================


def test_parse_kanban_single_column_single_task() -> None:
    """The grok smoke fixture parses to one column with one task."""
    board = _parse_kanban("kanban\nTodo\n  Task 1")
    assert board.columns == [KanbanColumn(title="Todo", tasks=[KanbanTask(label="Task 1")])]


def test_parse_kanban_multiple_columns() -> None:
    """Non-indented lines open columns; indented lines append tasks."""
    board = _parse_kanban("kanban\nTodo\n  Task 1\nDone\n  Task 2\n  Task 3")
    assert len(board.columns) == 2
    assert board.columns[0].title == "Todo"
    assert [t.label for t in board.columns[0].tasks] == ["Task 1"]
    assert board.columns[1].title == "Done"
    assert [t.label for t in board.columns[1].tasks] == ["Task 2", "Task 3"]


def test_parse_kanban_task_with_shape_data() -> None:
    """A task line with ``@{ ... }`` metadata parses the shape data."""
    board = _parse_kanban("kanban\nTodo\n  Task 1@{ assigned: alice, priority: High }")
    task = board.columns[0].tasks[0]
    assert task.label == "Task 1"
    assert task.assigned == "alice"
    assert task.priority == "High"


def test_parse_kanban_skips_blank_lines() -> None:
    """Blank lines are skipped at both the header and body stages."""
    board = _parse_kanban("kanban\n\nTodo\n\n  Task 1\n")
    assert len(board.columns) == 1
    assert [t.label for t in board.columns[0].tasks] == ["Task 1"]


def test_parse_kanban_skips_comment_lines() -> None:
    """``%%`` comment lines are skipped."""
    board = _parse_kanban("%% header comment\nkanban\n%% col comment\nTodo\n  Task 1")
    assert board.columns[0].title == "Todo"


def test_parse_kanban_header_only_returns_empty_board() -> None:
    """A ``kanban`` header with no columns parses to an empty board."""
    board = _parse_kanban("kanban")
    assert board.columns == []


def test_parse_kanban_missing_header_raises() -> None:
    """A body whose first token is not ``kanban`` raises ParseError."""
    with pytest.raises(ParseError):
        _parse_kanban("flowchart TD\n  A --> B")


def test_parse_kanban_empty_body_raises() -> None:
    """An empty body raises ParseError (no ``kanban`` header found)."""
    with pytest.raises(ParseError):
        _parse_kanban("")


def test_parse_kanban_task_before_column_raises() -> None:
    """An indented task line before any column raises ParseError."""
    with pytest.raises(ParseError):
        _parse_kanban("kanban\n  Orphan task\nTodo")


# === render_kanban_diagram_to_svg: grok smoke (lib.rs L992-L1007) ===========


def test_render_grok_simple_kanban_smoke() -> None:
    """The grok ``test_simple_kanban_diagram`` fixture (lib.rs L992-L1007).

    Asserts the three grok invariants: the ``aria-roledescription="kanban"``
    role, the column title, and the task label all appear in the SVG.
    """
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert isinstance(svg, str)
    assert 'aria-roledescription="kanban"' in svg
    assert "Todo" in svg
    assert "Task 1" in svg


# === render_kanban_diagram_to_svg: SVG structure ===========================


def test_render_emits_svg_root_and_closing_tag() -> None:
    """The SVG root carries the kanban role; the document is well-closed."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert svg.startswith("<svg ")
    assert svg.endswith("</svg>")
    assert 'id="my-svg"' in svg
    assert 'width="100%"' in svg


def test_render_single_column_canvas_dimensions() -> None:
    """One column -> ``2*PADDING + COLUMN_WIDTH`` wide; height tracks the column."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    expected_w = 2 * DIAGRAM_PADDING + COLUMN_WIDTH
    expected_h = 2 * DIAGRAM_PADDING + HEADER_HEIGHT + TASK_HEIGHT + BOTTOM_PADDING
    assert f'viewBox="0 0 {expected_w:g} {expected_h:g}"' in svg
    assert f"max-width: {expected_w:g}px" in svg


def test_render_two_columns_canvas_width_includes_gap() -> None:
    """Two columns add a ``COLUMN_GAP`` between them."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nTodo\n  Task 1\nDone\n  Task 2", MermaidTheme.light()
    )
    expected_w = 2 * DIAGRAM_PADDING + 2 * COLUMN_WIDTH + COLUMN_GAP
    assert f"max-width: {expected_w:g}px" in svg


# === render_kanban_diagram_to_svg: theme interpolation =====================


def test_render_light_theme_text_color_in_css() -> None:
    """The light theme's ``text_color`` flows into the root font fill."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert _LIGHT_TEXT_COLOR in svg


def test_render_light_theme_node_stroke_in_css() -> None:
    """The light theme's ``node_stroke`` flows into the node rect stroke."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert _LIGHT_NODE_STROKE in svg


def test_render_dark_theme_colors_override_light() -> None:
    """The dark theme's palette flows into the CSS when explicitly passed."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.dark())
    assert _DARK_TEXT_COLOR in svg
    assert _DARK_NODE_STROKE in svg


def test_render_background_stays_white_regardless_of_theme() -> None:
    """The SVG-root ``background-color`` stays hard-coded ``white`` (NOT theme.background)."""
    svg_dark = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.dark())
    assert "background-color: white" in svg_dark


# === render_kanban_diagram_to_svg: section CSS palette =====================


def test_render_emits_section0_css_with_darker_lightness() -> None:
    """The section-0 CSS rule carries the darker lightness literal."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert "hsl(60, 100%, 83.5294117647%)" in svg


def test_render_emits_section1_css() -> None:
    """The section-1 CSS rule (first real column) carries hue 80."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert "hsl(80, 100%, 86.2745098039%)" in svg


def test_render_section2_text_is_white() -> None:
    """Section-2 text fill is white (the only white-text section a column uses)."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nA\n  t1\nB\n  t2\nC\n  t3", MermaidTheme.light()
    )
    # section-2 is the third column (col_idx=2 -> section-3? No: section_idx =
    # col_idx + 1, so column 2 -> section-2). The CSS rule for section-2 sets
    # white text.
    assert ".section-2 text{fill:#ffffff;}" in svg


def test_render_first_column_uses_section1_class() -> None:
    """The first column's cluster carries ``section-1`` (1-based, not section-0)."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert 'class="cluster undefined section-1"' in svg


# === render_kanban_diagram_to_svg: priority indicator line ================


@pytest.mark.parametrize(
    "priority, color",
    [
        ("Very High", "red"),
        ("High", "orange"),
        ("Low", "blue"),
        ("Very Low", "lightblue"),
    ],
)
def test_render_priority_indicator_line_color(priority: str, color: str) -> None:
    """A task with a mapped priority emits a ``<line>`` of that color."""
    svg = render_kanban_diagram_to_svg(
        f"kanban\nTodo\n  Task@{{ priority: {priority} }}", MermaidTheme.light()
    )
    assert f'stroke="{color}"' in svg
    assert 'stroke-width="4"' in svg


def test_render_medium_priority_emits_no_indicator_line() -> None:
    """A ``Medium`` priority emits no colored indicator line."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nTodo\n  Task@{ priority: Medium }", MermaidTheme.light()
    )
    assert 'stroke="red"' not in svg
    assert 'stroke="orange"' not in svg
    assert 'stroke="blue"' not in svg
    assert 'stroke="lightblue"' not in svg


# === render_kanban_diagram_to_svg: assigned label + XML escape ===========


def test_render_assigned_name_appears_in_svg() -> None:
    """A task with an assignee emits the assignee name as a text label."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nTodo\n  Task@{ assigned: alice }", MermaidTheme.light()
    )
    assert "alice" in svg


def test_render_apostrophe_escaped_to_pos_entity() -> None:
    """An apostrophe in a label is escaped to ``&apos;`` (grok's XML entity)."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nTodo\n  Tom's Task", MermaidTheme.light()
    )
    assert "Tom&apos;s Task" in svg
    assert "Tom's Task" not in svg


def test_render_task_with_assigned_uses_taller_card_height() -> None:
    """An assigned task's rect carries the taller height literal (56)."""
    svg = render_kanban_diagram_to_svg(
        "kanban\nTodo\n  Task@{ assigned: alice }", MermaidTheme.light()
    )
    assert 'height="56"' in svg


def test_render_task_without_assigned_uses_shorter_card_height() -> None:
    """An unassigned task's rect carries the shorter height literal (44)."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo\n  Task 1", MermaidTheme.light())
    assert 'height="44"' in svg


# === render_kanban_diagram_to_svg: edge cases =============================


def test_render_empty_board_still_emits_valid_svg() -> None:
    """A header-only board (no columns) still emits a well-formed SVG."""
    svg = render_kanban_diagram_to_svg("kanban", MermaidTheme.light())
    assert svg.startswith("<svg ")
    assert svg.endswith("</svg>")
    assert 'aria-roledescription="kanban"' in svg


def test_render_column_with_no_tasks_emits_cluster_only() -> None:
    """A column with zero tasks emits its cluster rect but no item nodes."""
    svg = render_kanban_diagram_to_svg("kanban\nTodo", MermaidTheme.light())
    assert 'class="sections"' in svg
    assert "Todo" in svg
    assert 'class="items">' in svg


# === dispatch (render_mermaid_to_svg) ======================================


def test_dispatch_kanban_no_longer_raises_unsupported() -> None:
    """The ``kanban`` token routes to the dedicated renderer (R286 arm)."""
    svg = render_mermaid_to_svg("kanban\nTodo\n  Task 1")
    assert 'aria-roledescription="kanban"' in svg
    assert "Todo" in svg


def test_dispatch_kanban_strips_frontmatter_before_render() -> None:
    """A ``---...---`` front-matter block is stripped before the kanban arm runs."""
    source = "---\ntitle: Board\n---\nkanban\nTodo\n  Task 1"
    svg = render_mermaid_to_svg(source)
    assert "Task 1" in svg
    assert 'aria-roledescription="kanban"' in svg


def test_dispatch_kanban_respects_frontmatter_theme() -> None:
    """A front-matter ``config.theme`` preset resolves into the kanban CSS."""
    source = "---\nconfig:\n  theme: dark\n---\nkanban\nTodo\n  Task 1"
    svg = render_mermaid_to_svg(source)
    assert _DARK_TEXT_COLOR in svg
