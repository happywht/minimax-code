"""Kanban diagram renderer -- behavioral-equivalent port of grok's ``kanban_diagram.rs``.

Direction (1) brick 19 (R286). The eighth per-diagram leaf and the seventh
self-contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` /
R282 ``pie`` / R283 ``packet`` / R284 ``sankey`` / R285 ``gantt``; unlike R280
``stateDiagram`` which rides the dagre stack). The ``kanban`` diagram is
mermaid's board view: titled columns (Todo / In Progress / Done / ...) each
stacking task cards, with optional per-task ``@{...}`` metadata (assigned /
priority / ticket). Pure column-then-card geometry, so this leaf emits SVG
directly from the parsed model -- no AST, no dagre.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_kanban_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_kanban_diagram_to_svg(src, theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to "return
  the SVG / raise :class:`ParseError``. Unlike R285 ``gantt`` / R282 ``pie`` /
  R283 ``packet`` (which hard-code their palette and take ``_theme``), kanban
  IS theme-aware -- it interpolates ``theme.text_color`` into the root font
  fill, the cluster/label text fills, and ``theme.node_stroke`` into the node
  rect / ticket-link strokes (matching R284 ``sankey`` in consulting the
  resolved theme; the SVG-root ``background-color`` stays hard-coded
  ``white`` as in grok, NOT ``theme.background``).
* grok's three private structs ``KanbanBoard`` / ``KanbanColumn`` /
  ``KanbanTask`` -> :class:`KanbanBoard` / :class:`KanbanColumn` /
  :class:`KanbanTask` as plain (mutable) dataclasses -- mutable because grok
  mutates ``&mut KanbanTask`` in-place through ``apply_shape_data``.
* grok ``parse_kanban`` / ``parse_kanban_task`` / ``apply_shape_data`` /
  ``split_label_and_shape_data`` -> ``_parse_kanban`` / ``_parse_kanban_task``
  / ``_apply_shape_data`` / ``_split_label_and_shape_data`` (module-private;
  the public renderer is reached only through the dispatch arm).
* grok ``escape_xml`` maps ``'`` -> ``&apos;`` (XML named entity) -- matches
  R282 pie / R283 packet / R285 gantt; R281 radar / R284 sankey use ``&#39;``
  (numeric). Both faithfully mirror their respective grok source -- the Rust
  functions disagree.

YAML shape-data bridge
----------------------

Each task line may carry an ``@{ ... }`` suffix (mermaid's per-card metadata):
``Task1@{ ticket: FOO-123, assigned: reviews, priority: Very High }``. grok
extracts the bracket interior, wraps it back into a YAML mapping (single-line
-> ``{\n ... \n}`` flow mapping; multi-line -> block mapping verbatim), and
parses with ``serde_yaml``. The port reproduces the wrapping byte-for-byte
and parses with :mod:`yaml` (``safe_load``) -- both libraries resolve a flat
``key: value`` mapping of scalar strings/numbers/bools the same way for the
four kanban keys (``label`` / ``assigned`` / ``priority`` / ``ticket``). The
``yaml_get_string`` helper mirrors grok's ``Value::String | Number | Bool``
match: numbers render via :func:`_fmt` (Rust ``Display``), bools render
lowercase (``true`` / ``false`` -- Rust ``bool::to_string``, NOT Python
``str(True) == "True"``).

Float formatting bridge
-----------------------

Integer-valued floats (viewBox dimensions, geometry constants, transform
coordinates) route through :func:`_fmt`, which collapses ``200.0`` ->
``"200"`` (Rust ``Display`` drops the trailing ``.0``; Python ``str`` keeps
it). Non-integer floats fall back to ``repr`` (shortest round-trippable
decimal, matching Rust's ``Display`` for f64). The HSL lightness literals
(``83.5294117647`` / ``86.2745098039``) and the per-section hues are emitted
through the same bridge so the ``hsl(h, s%, l%)`` CSS matches grok verbatim.

Embedded ``<style>`` block
--------------------------

Like R285 gantt, kanban ships a CSS ``<style>`` block (mermaid 11.12.2's
section / node / label class scheme). The section palette cycles through 12
hues via ``idx.rem_euclid(12)``; Python's ``%`` (floor modulo) is
mathematically identical to ``rem_euclid`` for non-negative ``idx`` (the CSS
loop only feeds ``0..=10``). The f-string doubles each CSS brace (``{{`` ->
``{``); the hue / saturation / lightness / theme-color interpolation sites
stay single-braced.

Byte-length text estimate
-------------------------

grok measures an assigned name's rendered width with ``text.len() * 9.5``,
and Rust ``String::len`` is a *byte* count. The port mirrors that with
``len(name.encode("utf-8")) * 9.5`` so a multi-byte name produces the same
right-alignment offset grok computes.

Dispatch wiring
---------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer, mirroring grok lib.rs L47
(the body-shadow contract lifted in R282). The token ``kanban`` is removed
from :data:`render._UNSUPPORTED_DIAGRAM_TYPES` (17 -> 16 remaining).

Public surface (1 symbol): :func:`render_kanban_diagram_to_svg`. The
dataclasses, parser, and helpers stay out of ``__all__``; the renderer is
reached only through the dispatch arm added in R286.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_kanban_diagram_to_svg"]

#: Mermaid 11.12.2 kanban geometry defaults (grok L5-L24). Carried as ``float``
#: because the SVG viewBox / dimensions / transforms are emitted via Rust
#: ``Display`` (drops ``.0``); :func:`_fmt` reproduces that rendering.
COLUMN_WIDTH: float = 200.0
COLUMN_GAP: float = 5.0
HEADER_HEIGHT: float = 25.0
BOTTOM_PADDING: float = 10.0
TASK_WIDTH: float = 185.0
TASK_HEIGHT: float = 44.0
TASK_HEIGHT_WITH_ASSIGNED: float = 56.0
TASK_GAP: float = 5.0
TASK_TEXT_LINE_HEIGHT: float = 24.0
TASK_INNER_PADDING: float = 10.0
DIAGRAM_PADDING: float = 10.0

#: Per the reference SVG, the priority line is inset 2px from the card rect's
#: left edge, and 2px from the top/bottom of the rect (grok L20-L24).
PRIORITY_LINE_INSET_X: float = 2.0
PRIORITY_LINE_INSET_Y: float = 2.0
PRIORITY_LINE_STROKE_WIDTH: float = 4.0

#: The 12-hue section palette (grok ``section_hsl`` L472). Indexed by
#: ``idx % 12``; the CSS loop only feeds ``0..=10`` so hues[11] is reserved.
_SECTION_HUES: tuple[int, ...] = (60, 80, 270, 300, 330, 0, 30, 90, 150, 180, 210, 240)

#: Section-0 lightness (grok L476). Emitted via :func:`_fmt` so the literal
#: round-trips verbatim (Rust f64 ``Display``).
_SECTION0_LIGHTNESS: float = 83.5294117647
#: Section 1+ lightness (grok L478).
_SECTION_LIGHTNESS: float = 86.2745098039

#: Estimated rendered char width (grok ``estimate_text_width`` L496). Used to
#: right-align the assigned-name label inside the card.
_CHAR_WIDTH: float = 9.5


@dataclass
class KanbanTask:
    """One kanban task card (grok ``KanbanTask``).

    ``label`` is the card title; ``assigned`` / ``priority`` / ``ticket`` are
    the optional ``@{...}`` metadata fields. Mutable because grok's
    ``apply_shape_data`` stamps the parsed YAML onto ``&mut KanbanTask``.
    """

    label: str
    assigned: str | None = None
    priority: str | None = None
    ticket: str | None = None


@dataclass
class KanbanColumn:
    """One kanban column (grok ``KanbanColumn``): a titled stack of tasks."""

    title: str
    tasks: list[KanbanTask]


@dataclass
class KanbanBoard:
    """Parsed kanban model (grok ``KanbanBoard``): ordered columns."""

    columns: list[KanbanColumn]


def _fmt(value: int | float) -> str:
    """Render a number the way Rust ``Display`` would (R281 radar bridge).

    Integer-valued floats drop the trailing ``.0`` (``200.0`` -> ``"200"``);
    plain ints render verbatim (``60`` -> ``"60"``); non-integer floats fall
    back to ``repr`` (shortest round-trippable decimal, matching Rust's
    ``Display`` for f64). Mirrors R285 gantt's ``_fmt`` (signature widened to
    accept the integer hue values from :data:`_SECTION_HUES`).
    """
    if isinstance(value, bool):  # pragma: no cover - defensive; _fmt is never fed bools
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note: grok's ``kanban_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named
    entity) -- matches R282 pie / R283 packet / R285 gantt; R281 radar /
    R284 sankey map ``'`` -> ``&#39;`` (numeric). Both faithfully mirror their
    respective grok source -- the Rust functions disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _estimate_text_width(text: str) -> float:
    """Estimate rendered text width in SVG units (grok ``estimate_text_width``).

    Uses grok's 9.5px/char heuristic. The byte count (Rust ``String::len``)
    is reproduced via ``len(text.encode("utf-8"))`` so multi-byte names get
    the same offset grok computes.
    """
    return len(text.encode("utf-8")) * _CHAR_WIDTH


def _color_from_priority(priority: str) -> str | None:
    """Map a priority label to its indicator-stripe color (grok ``color_from_priority``).

    ``Medium`` and any unknown label return ``None`` (no indicator line).
    """
    colors = {
        "Very High": "red",
        "High": "orange",
        "Low": "blue",
        "Very Low": "lightblue",
    }
    return colors.get(priority)


def _section_hsl(idx: int) -> tuple[int, float, float]:
    """Return ``(hue, saturation, lightness)`` for a section index (grok ``section_hsl``).

    Mirrors grok's ``hues[idx.rem_euclid(12)]`` -- Python's ``idx % 12`` (floor
    modulo) is mathematically identical to ``rem_euclid`` for the non-negative
    ``idx`` the CSS loop feeds (``0..=10``). Section 0 uses the darker
    lightness; sections 1+ use the lighter one.
    """
    hue = _SECTION_HUES[idx % 12]
    if idx == 0:
        return (hue, 100.0, _SECTION0_LIGHTNESS)
    return (hue, 100.0, _SECTION_LIGHTNESS)


def _section_text_color(idx: int) -> str:
    """Return the CSS text fill for a section index (grok ``section_text_color``).

    Mirrors grok's match: ``-1`` and ``2`` use white, everything else black.
    The ``-1`` arm is a dead path at the call site (the CSS loop only feeds
    ``0..=10``) but is preserved verbatim for behavioral equivalence.
    """
    if idx in (-1, 2):
        return "#ffffff"
    return "black"


def _yaml_get_string(mapping: dict, key: str) -> str | None:
    """Read a YAML mapping value as a string (grok ``yaml_get_string``).

    Mirrors grok's ``Value::String | Number | Bool`` match: strings pass
    through, numbers render via :func:`_fmt` (Rust ``Number::to_string``),
    bools render lowercase (``true`` / ``false`` -- Rust ``bool::to_string``,
    NOT Python ``str(True) == "True"``). Any other YAML node type returns
    ``None`` (grok's ``_`` arm).
    """
    value = mapping.get(key)
    if value is None:
        return None
    # bool must be checked before int: in Python ``True isinstance int``.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _fmt(value)
    if isinstance(value, str):
        return value
    return None


def _task_height(task: KanbanTask) -> float:
    """Card height: the taller variant when an assignee is present (grok ``task_height``)."""
    if task.assigned is not None:
        return TASK_HEIGHT_WITH_ASSIGNED
    return TASK_HEIGHT


def _tasks_stack_height(tasks: list[KanbanTask]) -> float:
    """Total stack height of a column's tasks with inter-task gaps (grok ``tasks_stack_height``)."""
    total = 0.0
    for i, task in enumerate(tasks):
        if i > 0:
            total += TASK_GAP
        total += _task_height(task)
    return total


def _split_label_and_shape_data(line: str) -> tuple[str, str | None]:
    """Split a task line into ``(label, shape_data)`` (grok ``split_label_and_shape_data``).

    A ``@{ ... }`` suffix carries the metadata; if there is no ``@{`` marker,
    or the line does not end with ``}``, the whole line is the label and the
    shape data is ``None``. Otherwise the bracket interior is returned as the
    shape data (trimmed) and the prefix (trailing whitespace trimmed) is the
    label.
    """
    start = line.find("@{")
    if start == -1:
        return (line, None)
    if not line.endswith("}"):
        return (line, None)
    label = line[:start]
    rest = line[start:]
    # ``rest`` starts with "@{" by construction (line[start:start+2] == "@{").
    shape_data = rest[2:]
    if shape_data.endswith("}"):
        shape_data = shape_data[:-1]
    return (label.rstrip(), shape_data.strip())


def _apply_shape_data(task: KanbanTask, shape_data: str, line_no: int) -> None:
    """Parse the ``@{ ... }`` interior YAML onto ``task`` (grok ``apply_shape_data``).

    Reproduces grok's wrapping byte-for-byte: multi-line shape data parses as
    a block mapping verbatim (with a trailing newline); single-line shape data
    is wrapped back into ``{\\n ... \\n}`` (a YAML flow mapping, the form
    mermaid users write). A YAML parse failure or a non-mapping document
    raises :class:`ParseError` stamped with the source line number (mirrors
    grok's ``MermaidError::ParseError`` + ``.map_err``).
    """
    if "\n" in shape_data:
        yaml_data = f"{shape_data}\n"
    else:
        yaml_data = "{\n" + shape_data + "\n}"

    try:
        doc = yaml.safe_load(yaml_data)
    except yaml.YAMLError as err:
        raise ParseError(line_no, f"Invalid kanban metadata: {err}") from err

    if not isinstance(doc, dict):
        raise ParseError(
            line_no, "Invalid kanban metadata: expected a YAML mapping"
        )

    label = _yaml_get_string(doc, "label")
    if label is not None:
        task.label = label
    assigned = _yaml_get_string(doc, "assigned")
    if assigned is not None:
        task.assigned = assigned
    priority = _yaml_get_string(doc, "priority")
    if priority is not None:
        task.priority = priority
    ticket = _yaml_get_string(doc, "ticket")
    if ticket is not None:
        task.ticket = ticket


def _parse_kanban_task(line: str, line_no: int) -> KanbanTask:
    """Parse one indented task line into a :class:`KanbanTask` (grok ``parse_kanban_task``)."""
    label, shape_data = _split_label_and_shape_data(line)
    task = KanbanTask(label=label)
    if shape_data is None:
        return task
    _apply_shape_data(task, shape_data, line_no)
    return task


def _parse_kanban(input: str) -> KanbanBoard:
    """Parse mermaid ``kanban`` source into a :class:`KanbanBoard` (grok ``parse_kanban``).

    Requires the first substantial token to be ``kanban``; subsequent
    non-indented lines open new columns (title = the trimmed line); indented
    lines are task rows appended to the current column. Blank / ``%%`` lines
    are skipped at both stages.

    Raises:
        ParseError: when the first substantial token is not ``kanban``; a task
            row appears before any column has been opened; or the source has
            no ``kanban`` header at all.
    """
    lines = input.splitlines()
    found_header = False
    columns: list[KanbanColumn] = []
    current_idx: int | None = None

    for idx, raw in enumerate(lines):
        line_no = idx + 1
        # grok ``trim_end_matches(['\r', '\n'])`` -- drop trailing CR/LF only.
        line = raw.rstrip("\r\n")
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("%%"):
            continue

        if not found_header:
            if trimmed.split()[0] != "kanban":
                raise ParseError(line_no, "Expected 'kanban' declaration")
            found_header = True
            continue

        # Indentation is decided from the raw line's first character (grok
        # ``raw.chars().next().is_some_and(|c| c.is_whitespace())``); the
        # blank/comment skip above guarantees ``raw`` is non-empty here.
        is_indented = raw[0].isspace()
        if not is_indented:
            columns.append(KanbanColumn(title=trimmed, tasks=[]))
            current_idx = len(columns) - 1
            continue

        if current_idx is None:
            raise ParseError(line_no, "Task found before any kanban column")

        columns[current_idx].tasks.append(_parse_kanban_task(trimmed, line_no))

    if not found_header:
        raise ParseError(1, "Expected 'kanban' declaration")

    return KanbanBoard(columns=columns)


def _emit_label_text(svg: list[str], tx: float, ty: float, text: str) -> None:
    """Append a native SVG ``<text>`` label inside ``<g class="label">`` (grok ``emit_label_text``).

    Left-aligned (``text-anchor="start"``), vertically centered within one
    line height (``y = TASK_TEXT_LINE_HEIGHT / 2``) so it sits where the old
    ``foreignObject`` div did.
    """
    text_y = TASK_TEXT_LINE_HEIGHT / 2.0
    svg.append(
        f'<g class="label" transform="translate({_fmt(tx)}, {_fmt(ty)})">'
        f'<text x="0" y="{_fmt(text_y)}" text-anchor="start" '
        f'dominant-baseline="central" '
        f"font-family=\"'trebuchet ms', verdana, arial, sans-serif\">"
        f"{_escape_xml(text)}</text></g>"
    )


def _emit_empty_label(svg: list[str], tx: float, ty: float) -> None:
    """Append an empty placeholder ``<g class="label">`` (grok ``emit_empty_label``)."""
    svg.append(f'<g class="label" transform="translate({_fmt(tx)}, {_fmt(ty)})"/>')


def render_kanban_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render a mermaid ``kanban`` diagram into an SVG string (grok
    ``render_kanban_diagram_to_svg``).

    Parses the source into columns of task cards, measures the column heights
    to size the canvas, and emits a six-block SVG: the ``<style>`` block (root
    font + section / node / label class colors), a full-canvas background
    rect, an empty ``<g/>`` placeholder, the ``sections`` group (one cluster
    rect + cluster-label per column), the ``items`` group (one node card per
    task with label, assigned/empty placeholders, and an optional priority
    indicator stripe), and the closing tag.

    Args:
        mermaid_source: mermaid ``kanban`` source. The dispatch in
            :func:`render.render_mermaid_to_svg` passes the front-matter-
            stripped body (mirroring grok lib.rs L47, which shadows
            ``mermaid_source`` to ``parsed_source.body`` before the kanban
            arm).
        theme: the resolved :class:`MermaidTheme` palette. kanban consults
            ``theme.text_color`` (root font fill + cluster/label text fills)
            and ``theme.node_stroke`` (node rect + ticket-link strokes); the
            SVG-root ``background-color`` and the full-canvas background rect
            stay hard-coded ``white`` (matching grok, NOT ``theme.background``).

    Returns:
        The kanban-board SVG string.

    Raises:
        ParseError: propagated from :func:`_parse_kanban` / YAML shape-data
            parsing.
    """
    board = _parse_kanban(mermaid_source)

    columns_count = max(len(board.columns), 1)
    total_width = (
        DIAGRAM_PADDING * 2.0
        + columns_count * COLUMN_WIDTH
        + (columns_count - 1.0) * COLUMN_GAP
    )

    max_col_height = 0.0
    for col in board.columns:
        tasks_h = _tasks_stack_height(col.tasks)
        col_h = HEADER_HEIGHT + tasks_h + BOTTOM_PADDING
        if col_h > max_col_height:
            max_col_height = col_h
    total_height = DIAGRAM_PADDING * 2.0 + max_col_height

    svg: list[str] = []

    # 1. SVG root (grok L47-L53).
    svg.append(
        '<svg id="my-svg" width="100%" xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'style="max-width: {_fmt(total_width)}px; background-color: white;" '
        f'viewBox="0 0 {_fmt(total_width)} {_fmt(total_height)}" '
        'role="graphics-document document" aria-roledescription="kanban">'
    )

    # 2. Embedded <style> block (grok L56-L109). The f-string doubles each
    # CSS brace ({{ -> {); the theme-color / HSL interpolation sites stay
    # single-braced.
    svg.append("<style>")
    svg.append(
        f'#my-svg{{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        f'font-size:16px;fill:{theme.text_color};}}'
    )
    svg.append("#my-svg p{margin:0;}")

    # Section-specific CSS (sections 0-10). grok emits ``path`` twice in the
    # selector list -- a faithful quirk of the reference CSS, preserved
    # verbatim.
    for i in range(11):
        fill_hue, fill_sat, fill_light = _section_hsl(i)
        text_fill = _section_text_color(i)
        svg.append(
            f"#my-svg .section-{i} rect,#my-svg .section-{i} path,"
            f"#my-svg .section-{i} circle,#my-svg .section-{i} polygon,"
            f"#my-svg .section-{i} path"
            f"{{fill:hsl({_fmt(fill_hue)}, {_fmt(fill_sat)}%, {_fmt(fill_light)}%);"
            f"stroke:hsl({_fmt(fill_hue)}, {_fmt(fill_sat)}%, {_fmt(fill_light)}%);}}"
        )
        svg.append(f"#my-svg .section-{i} text{{fill:{text_fill};}}")

    # Node + ticket-link styling.
    svg.append(
        f"#my-svg .node rect,#my-svg .node circle,#my-svg .node ellipse,"
        f"#my-svg .node polygon,#my-svg .node path"
        f"{{fill:white;stroke:{theme.node_stroke};stroke-width:1px;}}"
    )
    svg.append(
        f"#my-svg .kanban-ticket-link{{fill:white;stroke:{theme.node_stroke};"
        f"text-decoration:underline;}}"
    )

    # Cluster-label and label styling.
    svg.append(
        f"#my-svg .cluster-label,#my-svg .label{{color:{theme.text_color};"
        f"fill:{theme.text_color};}}"
    )
    svg.append(
        f"#my-svg .cluster-label text{{fill:{theme.text_color};font-size:16px;}}"
    )
    svg.append(f"#my-svg .label text{{fill:{theme.text_color};font-size:16px;}}")

    # Kanban-label class.
    svg.append(
        "#my-svg .kanban-label{dy:1em;alignment-baseline:middle;"
        "text-anchor:middle;dominant-baseline:middle;text-align:center;}"
    )
    svg.append("</style>")

    # 3. Background rect (grok L112-L114).
    svg.append(
        f'<rect x="0" y="0" width="{_fmt(total_width)}" '
        f'height="{_fmt(total_height)}" fill="white"/>'
    )

    # 4. Empty g placeholder (grok L117).
    svg.append("<g/>")

    # 5. Sections group -- one cluster rect + cluster-label per column
    # (grok L120-L157). section_idx is 1-based (first column -> section-1);
    # the CSS defines section-0..section-10, so section-0 is never used by a
    # real column.
    svg.append('<g class="sections">')
    for col_idx, col in enumerate(board.columns):
        section_idx = col_idx + 1
        col_x = DIAGRAM_PADDING + col_idx * (COLUMN_WIDTH + COLUMN_GAP)
        col_y = DIAGRAM_PADDING
        tasks_h = _tasks_stack_height(col.tasks)
        col_h = HEADER_HEIGHT + tasks_h + BOTTOM_PADDING

        svg.append(
            f'<g class="cluster undefined section-{section_idx}" '
            f'id="{_escape_xml(col.title)}" data-look="classic">'
        )
        svg.append(
            f'<rect style="" rx="5" ry="5" x="{_fmt(col_x)}" y="{_fmt(col_y)}" '
            f'width="{_fmt(COLUMN_WIDTH)}" height="{_fmt(col_h)}"/>'
        )

        # Cluster label centered horizontally within the column, vertically
        # within the header band.
        label_x = col_x
        label_y = col_y
        text_x = COLUMN_WIDTH / 2.0
        text_y = HEADER_HEIGHT / 2.0
        svg.append(
            f'<g class="cluster-label" transform="translate({_fmt(label_x)}, '
            f'{_fmt(label_y)})">'
        )
        svg.append(
            f'<text x="{_fmt(text_x)}" y="{_fmt(text_y)}" '
            f'text-anchor="middle" dominant-baseline="central" '
            f"font-family=\"'trebuchet ms', verdana, arial, sans-serif\">"
            f"{_escape_xml(col.title)}</text>"
        )
        svg.append("</g>")
        svg.append("</g>")
    svg.append("</g>")

    # 6. Items group -- one node card per task (grok L160-L243).
    svg.append('<g class="items">')
    for col_idx, col in enumerate(board.columns):
        col_x = DIAGRAM_PADDING + col_idx * (COLUMN_WIDTH + COLUMN_GAP)
        col_y = DIAGRAM_PADDING
        col_center_x = col_x + COLUMN_WIDTH / 2.0

        if not col.tasks:
            continue

        # First task center is HEADER_HEIGHT below the column top, plus half
        # the first task's height.
        task_center_y = col_y + HEADER_HEIGHT + _task_height(col.tasks[0]) / 2.0

        for task_idx, task in enumerate(col.tasks):
            if task_idx > 0:
                prev_h = _task_height(col.tasks[task_idx - 1])
                curr_h = _task_height(task)
                task_center_y += prev_h / 2.0 + TASK_GAP + curr_h / 2.0

            t_h = _task_height(task)
            half_w = TASK_WIDTH / 2.0
            half_h = t_h / 2.0

            svg.append(
                f'<g class="node undefined" id="{_escape_xml(task.label)}" '
                f'transform="translate({_fmt(col_center_x)}, {_fmt(task_center_y)})">'
            )

            # Card rect centered at the transform origin.
            svg.append(
                f'<rect class="basic label-container" style="" rx="5" ry="5" '
                f'x="{_fmt(-half_w)}" y="{_fmt(-half_h)}" '
                f'width="{_fmt(TASK_WIDTH)}" height="{_fmt(t_h)}"/>'
            )

            # Task label -- positioned at the inner-left padding; bumped to
            # the top when an assignee is present (room for the name below).
            label_tx = -half_w + TASK_INNER_PADDING
            if task.assigned is not None:
                label_ty = -half_h + 4.0
            else:
                label_ty = -(TASK_TEXT_LINE_HEIGHT / 2.0)
            _emit_label_text(svg, label_tx, label_ty, task.label)

            # Assigned / empty placeholders.
            if task.assigned is not None:
                _emit_empty_label(svg, label_tx, 0.0)
                assigned_w = _estimate_text_width(task.assigned)
                assigned_tx = half_w - TASK_INNER_PADDING - assigned_w
                _emit_label_text(svg, assigned_tx, 0.0, task.assigned)
            else:
                ph_y = TASK_TEXT_LINE_HEIGHT / 2.0
                _emit_empty_label(svg, label_tx, ph_y)
                right_tx = half_w - TASK_INNER_PADDING
                _emit_empty_label(svg, right_tx, ph_y)

            # Priority indicator line (only for Very High / High / Low / Very
            # Low; Medium and unknown labels emit nothing).
            if task.priority is not None:
                priority_color = _color_from_priority(task.priority)
                if priority_color is not None:
                    line_x = -half_w + PRIORITY_LINE_INSET_X
                    line_y1 = -half_h + PRIORITY_LINE_INSET_Y
                    line_y2 = half_h - PRIORITY_LINE_INSET_Y
                    svg.append(
                        f'<line x1="{_fmt(line_x)}" y1="{_fmt(line_y1)}" '
                        f'x2="{_fmt(line_x)}" y2="{_fmt(line_y2)}" '
                        f'stroke-width="{_fmt(PRIORITY_LINE_STROKE_WIDTH)}" '
                        f'stroke="{priority_color}"/>'
                    )

            svg.append("</g>")
    svg.append("</g>")

    svg.append("</svg>")
    return "".join(svg)
