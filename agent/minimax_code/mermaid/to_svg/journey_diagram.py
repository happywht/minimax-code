"""Functional clone of grok's ``journey_diagram.rs`` (direction (1) brick 21).

This is the 12th per-diagram leaf and the 11th **renderer** (self-contained SVG
emitter, same lineage as R279 info / R281 radar / R282 pie / R283 packet /
R284 sankey / R285 gantt / R286 kanban / R287 timeline / R288 quadrant /
R289 block; plus R280 state parser that rides the dagre stack). ``journey`` is
mermaid's user-journey map: a titled timeline of tasks grouped into sections,
where each task carries a 1-5 satisfaction score (rendered as a happy/neutral/
sad face icon) and the actors responsible for it. The renderer parses the
source into a section/task model, flattens tasks with their section
assignments, computes column-driven task positions and section widths, and
emits a single SVG string.

Layout (mirrors grok ``render_journey_diagram_to_svg`` L44-L336)
----------------------------------------------------------------

All geometry is pure iterative arithmetic (no AST, no dagre):

1. **Actor collection** -- unique actor names in first-appearance order, used
   both for the legend column on the left and for the per-task actor circles.
2. **Flatten** -- walk ``rows`` tracking the ``current_section``; each task
   inherits the most recently declared section (empty string when none).
3. **Section grouping** -- consecutive tasks sharing a section become one
   :class:`_SectionInfo`; ``section_num = section_idx % len(SECTION_SVG_FILLS)``
   cycles the rect fill palette.
4. **Task positions** -- ``task.x = i * TASK_MARGIN + i * TASK_WIDTH +
   LEFT_MARGIN`` (mermaid's ``taskMargin + width`` stride).
5. **Bounds** -- ``width = LEFT_MARGIN + bounds_stopx + 2 * DIAGRAM_MARGIN_X``
   where ``bounds_stopx = last_task_x + DIAGRAM_MARGIN_X + TASK_MARGIN``;
   ``height = TASK_LINE_BOTTOM + 2 * DIAGRAM_MARGIN_Y``; a present title adds
   ``70`` to the viewBox/svg height.
6. **SVG emission** -- root ``<svg>`` (journey role + ``xMinYMin meet``) +
   ``<style>`` (CSS fills + legend/label/face/mouth classes) + arrowhead
   ``<marker>`` + actor legend + one ``<g>`` per section (rect + centred
   label via ``foreignObject``/``<text>`` ``switch``) + one ``<g>`` per task
   (dashed task line + face icon + rect + actor circles + centred label) +
   optional title + the horizontal arrow.

Face icon (mirrors grok ``draw_face`` L338-L389)
------------------------------------------------

Each task's satisfaction ``score`` selects the mouth shape:

* ``score > 3`` -> smile (a D3 ``arc`` pie-slice path, open upward).
* ``score < 3`` -> frown (the same arc flipped, open downward).
* ``score == 3`` -> a neutral straight ``<line>``.

The smile/frown arcs are emitted via :func:`_format_num` (D3's variable-
precision number format -- round to 3 decimals, drop trailing zeros), the
only site in this renderer that needs non-integer coordinates (the arc radii
``r/2`` and ``r/2.2``). Every other coordinate is integer-valued arithmetic,
so :func:`_fmt0` mirrors Rust's ``{:.0}`` (round to whole) for the geometry.

Theme boundary (the journey family does NOT read the theme)
-----------------------------------------------------------

``journey`` is **theme-unaware**: the ``_theme`` parameter is accepted for
dispatch-site symmetry with the other per-diagram renderers but is never
read. The CSS hard-codes mermaid's default light-theme palette
(``fill:#333`` text, ``#FFF8DC`` faces, the ``SECTION_FILLS`` /
``SECTION_SVG_FILLS`` / ``ACTOR_COLOURS`` tables). This is a faithful clone
of grok, whose ``render_journey_diagram_to_svg`` also takes ``_theme`` and
ignores it -- the mermaid.js journey renderer predates the theme system and
was never ported onto it. Tests pin this boundary: a dark theme must not
change the emitted SVG.

Float-formatting bridge
-----------------------

Two number formatters, used at different sites (same dual-bridge pattern as
R289 block, R288 quadrant):

* :func:`_fmt0` mirrors Rust ``{:.0}`` -- round to a whole number with no
  decimal point. Every coordinate passed through it is an exact integer
  (``150``, ``110``, ``200``, ``face_cy`` ...), so this is a pure scalar
  cast; it exists to keep the emission sites reading ``_fmt0(x)`` rather
  than mixing ``int()`` calls into the format strings.
* :func:`_fmt_num` mirrors D3's number formatting -- round to 3 decimals
  then drop trailing zeros / dangling dot (``7.500`` -> ``"7.5"``,
  ``6.818`` -> ``"6.818"``). Used **only** inside the smile/frown arc path
  data (the arc radii). This is the same D3 bridge R289 ``_fmt_num`` uses.

XML escaping mirrors grok ``escape_xml`` L557-L563 verbatim, including the
``'`` -> ``&apos;`` mapping (R289 block uses ``&#x27;`` -- each renderer is
faithful to its own grok source's choice of apostrophe entity).

Public surface (1 symbol): :func:`render_journey_diagram_to_svg`. Reached
only via the ``render.py`` dispatch arm; the barrel does NOT re-export it
(mirrors grok's crate root never re-exporting per-diagram renderers).

References to grok line numbers are contract references (the Rust source is
read-only under ``grok-build/third_party/mermaid-to-svg/src/journey_diagram.rs``);
this module is a functional clone, not a line-by-line port.
"""

from __future__ import annotations

from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

# --- Mermaid 11.12.2 journey config defaults (grok L5-L21) -------------------
DIAGRAM_MARGIN_X: float = 50.0
DIAGRAM_MARGIN_Y: float = 10.0
LEFT_MARGIN: float = 150.0
TASK_WIDTH: float = 150.0
TASK_HEIGHT: float = 50.0
TASK_MARGIN: float = 50.0
SECTION_Y: float = 50.0
FACE_RADIUS: float = 15.0
ACTOR_CIRCLE_R: float = 7.0
MAX_FACE_Y: float = 300.0
FACE_Y_PER_SCORE: float = 30.0
# grok L16: MAX_FACE_Y + 5 * FACE_Y_PER_SCORE == 450 (the dashed task-line bottom).
TASK_LINE_BOTTOM: float = MAX_FACE_Y + 5.0 * FACE_Y_PER_SCORE
# grok L17: conf.height (TASK_HEIGHT) * 4 == 200 (the horizontal arrow's y).
ARROW_Y_MULTIPLIER: float = 4.0
FONT_FAMILY: str = "'trebuchet ms', verdana, arial, sans-serif"
TASK_FONT_SIZE: float = 14.0
TASK_FONT_FAMILY: str = "'Open Sans', sans-serif"
TITLE_FONT_SIZE: str = "4ex"

# CSS section/task fill colors from the Mermaid default theme (grok L24-L33).
SECTION_FILLS: tuple[str, ...] = (
    "#ECECFF",
    "#ffffde",
    "hsl(304, 100%, 96.2745098039%)",
    "hsl(124, 100%, 93.5294117647%)",
    "hsl(176, 100%, 96.2745098039%)",
    "hsl(-4, 100%, 93.5294117647%)",
    "hsl(8, 100%, 96.2745098039%)",
    "hsl(188, 100%, 93.5294117647%)",
)

# SVG fill attributes for section/task rects (grok L36-L38 ``sectionFills``).
SECTION_SVG_FILLS: tuple[str, ...] = (
    "#191970",
    "#8B008B",
    "#4B0082",
    "#2F4F4F",
    "#800000",
    "#8B4513",
    "#00008B",
)

# Actor circle colors (grok L40-L42).
ACTOR_COLOURS: tuple[str, ...] = (
    "#8FBC8F",
    "#7CFC00",
    "#00FFFF",
    "#20B2AA",
    "#B0E0E6",
    "#FFFFE0",
)


# --- parse model (internal, mirrors grok ``JourneyDiagram`` L423-L456) --------


@dataclass
class _JourneyTask:
    """A single journey task (grok ``JourneyTask``)."""

    name: str
    score: int
    actors: list[str]


@dataclass
class _JourneySectionRow:
    """A ``section <name>`` declaration in the row stream (grok ``Section``)."""

    name: str


@dataclass
class _JourneyTaskRow:
    """A task row in the stream wrapping a :class:`_JourneyTask` (grok ``Task``)."""

    task: _JourneyTask


# grok's ``JourneyRow`` enum (Section | Task) -> a tagged union of two dataclasses;
# ``isinstance(row, _JourneyTaskRow)`` replaces the ``if let JourneyRow::Task`` match.
_JourneyRow = _JourneySectionRow | _JourneyTaskRow


@dataclass
class _JourneyDiagram:
    """Parsed journey model (grok ``JourneyDiagram``)."""

    title: str | None
    rows: list[_JourneyRow]


@dataclass
class _FlatTask:
    """A task flattened with its inherited section name (grok ``FlatTask``)."""

    name: str
    score: int
    actors: list[str]
    section: str


@dataclass
class _SectionInfo:
    """A run of consecutive tasks in one section (grok ``SectionInfo``)."""

    name: str
    first_task_idx: int
    task_count: int
    section_num: int


def render_journey_diagram_to_svg(
    mermaid_source: str,
    _theme: MermaidTheme,  # noqa: ARG001 -- theme-unaware (see module docstring)
) -> str:
    """Render a mermaid ``journey`` diagram to an SVG string.

    Mirrors grok ``render_journey_diagram_to_svg`` (L44-L336). Parses the
    source, collects unique actors, flattens tasks with section assignments,
    groups consecutive tasks into sections, computes the column-driven task
    positions and overall bounds, and emits the SVG.

    The ``_theme`` parameter is accepted for dispatch symmetry but never
    read -- the journey renderer hard-codes mermaid's default light palette
    (faithful clone of grok, which also ignores ``_theme``).
    """
    journey = _parse_journey_diagram(mermaid_source)

    # Collect unique actors in order of first appearance (grok L51-L60).
    actors: list[str] = []
    for row in journey.rows:
        if isinstance(row, _JourneyTaskRow):
            for actor in row.task.actors:
                if actor not in actors:
                    actors.append(actor)

    # In mermaid.js: leftMargin = conf.leftMargin + maxWidth; maxWidth comes
    # from actor-legend text measurement and is 0 for simple cases (grok L63).
    left_margin = LEFT_MARGIN

    # Flatten tasks with their inherited section (grok L67-L83).
    flat_tasks: list[_FlatTask] = []
    current_section: str | None = None
    for row in journey.rows:
        if isinstance(row, _JourneySectionRow):
            current_section = row.name
        else:
            flat_tasks.append(
                _FlatTask(
                    name=row.task.name,
                    score=row.task.score,
                    actors=row.task.actors,
                    section=current_section if current_section is not None else "",
                )
            )

    num_tasks = len(flat_tasks)
    if num_tasks == 0:
        # grok L86-L91: a diagram with only ``section`` rows (no tasks) is a
        # parse error -- ``ParseError`` at line 1.
        raise ParseError(1, "Journey requires at least one task")

    # Group consecutive same-section tasks into SectionInfo (grok L94-L116).
    sections: list[_SectionInfo] = []
    last_section = ""
    section_idx = 0
    for i, task in enumerate(flat_tasks):
        if task.section != last_section:
            # Count the run of tasks sharing this section starting at i.
            count = 0
            for t in flat_tasks[i:]:
                if t.section == task.section:
                    count += 1
                else:
                    break
            section_num = section_idx % len(SECTION_SVG_FILLS)
            sections.append(
                _SectionInfo(
                    name=task.section,
                    first_task_idx=i,
                    task_count=count,
                    section_num=section_num,
                )
            )
            last_section = task.section
            section_idx += 1

    # task.x = i * taskMargin + i * width + leftMargin (grok L119-L121).
    task_positions = [
        float(i) * TASK_MARGIN + float(i) * TASK_WIDTH + left_margin
        for i in range(num_tasks)
    ]

    # Section vertical height anchors the task y (grok L124-L125):
    # sectionVHeight = TASK_HEIGHT * 2 + DIAGRAM_MARGIN_Y == 110; task_y = 110.
    section_v_height = TASK_HEIGHT * 2.0 + DIAGRAM_MARGIN_Y
    task_y = section_v_height

    # arrow_y = conf.height * 4 = TASK_HEIGHT * 4 == 200 (grok L128).
    arrow_y = TASK_HEIGHT * ARROW_Y_MULTIPLIER

    # Overall dimensions (grok L130-L143): bounds stop-x uses DIAGRAM_MARGIN_X
    # (50) as the effective task width, NOT the visual TASK_WIDTH (150).
    last_task_x = task_positions[-1] if task_positions else left_margin
    bounds_stopx = last_task_x + DIAGRAM_MARGIN_X + TASK_MARGIN

    width = left_margin + bounds_stopx + 2.0 * DIAGRAM_MARGIN_X
    # height = stopy - starty + 2 * diagramMarginY; starty=0, stopy=450.
    height = TASK_LINE_BOTTOM + 2.0 * DIAGRAM_MARGIN_Y

    has_title = journey.title is not None
    extra_vert_for_title = 70.0 if has_title else 0.0
    viewbox_height = height + extra_vert_for_title
    svg_height = height + extra_vert_for_title + 25.0

    # Arrow endpoint: width - leftMargin - 4 (grok L146).
    arrow_x2 = width - left_margin - 4.0

    parts: list[str] = []

    # SVG header (grok L151-L163).
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'style="max-width: {_fmt0(width)}px;" '
        'width="100%" '
        f'viewBox="0 -25 {_fmt0(width)} {_fmt0(viewbox_height)}" '
        'preserveAspectRatio="xMinYMin meet" '
        f'height="{_fmt0(svg_height)}" '
        'role="graphics-document document" '
        'aria-roledescription="journey">'
    )

    # CSS styles matching the mermaid.js default theme (grok L166-L186).
    parts.append("<style>")
    parts.append(f"svg {{font-family:{FONT_FAMILY};font-size:16px;fill:#333;}}")
    parts.append(".mouth{stroke:#666;}")
    parts.append("line{stroke:#333;}")
    parts.append(f".legend{{fill:#333;font-family:{FONT_FAMILY};}}")
    parts.append(".label text{fill:#333;}")
    parts.append(".face{fill:#FFF8DC;stroke:#999;}")

    # Section/task type fills (grok L177-L179).
    for i, fill in enumerate(SECTION_FILLS):
        parts.append(f".task-type-{i},.section-type-{i}{{fill:{fill};}}")

    # Actor colors (grok L182-L184).
    for i, color in enumerate(ACTOR_COLOURS):
        parts.append(f".actor-{i}{{fill:{color};}}")

    parts.append("</style>")

    # Arrowhead marker definition (grok L189).
    parts.append(
        '<defs><marker id="arrowhead" refX="5" refY="2" markerWidth="6" '
        'markerHeight="4" orient="auto">'
        '<path d="M 0,0 V 4 L6,2 Z"/></marker></defs>'
    )

    # Actor legend (grok L192-L204).
    actor_y = 60.0
    for pos, actor in enumerate(actors):
        color = ACTOR_COLOURS[pos % len(ACTOR_COLOURS)]
        parts.append(
            f'<circle cx="20" cy="{_fmt0(actor_y)}" class="actor-{pos}" '
            f'fill="{color}" stroke="#000" r="{_fmt0(ACTOR_CIRCLE_R)}"/>'
        )
        parts.append(
            f'<text x="40" y="{_fmt0(actor_y + 7.0)}" class="legend">'
            f"<tspan x=\"50\">{_escape_xml(actor)}</tspan></text>"
        )
        actor_y += 20.0

    # Sections (grok L207-L240).
    for section in sections:
        section_x = task_positions[section.first_task_idx]
        section_width = (
            TASK_WIDTH * float(section.task_count)
            + DIAGRAM_MARGIN_X * (float(section.task_count) - 1.0)
        )
        fill = SECTION_SVG_FILLS[section.section_num % len(SECTION_SVG_FILLS)]
        num = section.section_num

        parts.append("<g>")
        parts.append(
            f'<rect x="{_fmt0(section_x)}" y="{_fmt0(SECTION_Y)}" fill="{fill}" '
            f'stroke="#666" width="{_fmt0(section_width)}" '
            f'height="{_fmt0(TASK_HEIGHT)}" rx="3" ry="3" '
            f'class="journey-section section-type-{num}"/>'
        )

        # Section label: foreignObject with a <text>/tspan fallback (grok L222-L238).
        center_x = section_x + section_width / 2.0
        center_y = SECTION_Y + TASK_HEIGHT / 2.0
        escaped_name = _escape_xml(section.name)
        parts.append(
            "<switch>"
            f'<foreignObject x="{_fmt0(section_x)}" y="{_fmt0(SECTION_Y)}" '
            f'width="{_fmt0(section_width)}" height="{_fmt0(TASK_HEIGHT)}" '
            'requiredExtensions="http://www.w3.org/1999/xhtml">'
            f'<div class="journey-section section-type-{num}" '
            'xmlns="http://www.w3.org/1999/xhtml" '
            'style="display: table; height: 100%; width: 100%;">'
            '<div class="label" style="display: table-cell; text-align: center; '
            f'vertical-align: middle;">{escaped_name}</div></div></foreignObject>'
            f'<text x="{_fmt0(center_x)}" y="{_fmt0(center_y)}" '
            'dominant-baseline="central" alignment-baseline="central" '
            'class="journey-section" '
            f'style="text-anchor: middle; font-size: {_fmt0(TASK_FONT_SIZE)}px; '
            f'font-family: {TASK_FONT_FAMILY};">'
            f'<tspan x="{_fmt0(center_x)}" dy="0">{escaped_name}</tspan></text>'
            "</switch>"
        )
        parts.append("</g>")

    # Tasks (grok L243-L316).
    current_section_num = 0
    current_section_name = ""
    section_idx = 0
    for i, task in enumerate(flat_tasks):
        task_counter = i
        # Track which section palette index this task belongs to (grok L249-L255).
        if task.section != current_section_name:
            if section_idx < len(sections):
                current_section_num = sections[section_idx].section_num
                section_idx += 1
            current_section_name = task.section

        tx = task_positions[i]
        center = tx + TASK_WIDTH / 2.0
        fill = SECTION_SVG_FILLS[current_section_num % len(SECTION_SVG_FILLS)]
        num = current_section_num

        parts.append("<g>")

        # Dashed task line (grok L265-L269).
        parts.append(
            f'<line id="task{task_counter}" x1="{_fmt0(center)}" y1="{_fmt0(task_y)}" '
            f'x2="{_fmt0(center)}" y2="{_fmt0(TASK_LINE_BOTTOM)}" '
            'class="task-line" stroke-width="1px" stroke-dasharray="4 2" stroke="#666"/>'
        )

        # Face icon (grok L272-L273): score selects the mouth shape.
        face_cy = MAX_FACE_Y + (5.0 - float(task.score)) * FACE_Y_PER_SCORE
        _draw_face(parts, center, face_cy, task.score)

        # Task rectangle (grok L276-L280).
        parts.append(
            f'<rect x="{_fmt0(tx)}" y="{_fmt0(task_y)}" fill="{fill}" stroke="#666" '
            f'width="{_fmt0(TASK_WIDTH)}" height="{_fmt0(TASK_HEIGHT)}" rx="3" ry="3" '
            f'class="task task-type-{num}"/>'
        )

        # Actor circles on the task (grok L283-L294).
        x_pos = tx + 14.0
        for actor_name in task.actors:
            pos = _position(actors, actor_name)
            if pos is not None:
                color = ACTOR_COLOURS[pos % len(ACTOR_COLOURS)]
                parts.append(
                    f'<circle cx="{_fmt0(x_pos)}" cy="{_fmt0(task_y)}" '
                    f'class="actor-{pos}" fill="{color}" stroke="#000" '
                    f'r="{_fmt0(ACTOR_CIRCLE_R)}">'
                    f"<title>{_escape_xml(actor_name)}</title></circle>"
                )
                x_pos += 10.0

        # Task label: foreignObject with a <text>/tspan fallback (grok L297-L313).
        task_center_x = tx + TASK_WIDTH / 2.0
        task_center_y = task_y + TASK_HEIGHT / 2.0
        escaped_task_name = _escape_xml(task.name)
        parts.append(
            "<switch>"
            f'<foreignObject x="{_fmt0(tx)}" y="{_fmt0(task_y)}" '
            f'width="{_fmt0(TASK_WIDTH)}" height="{_fmt0(TASK_HEIGHT)}" '
            'requiredExtensions="http://www.w3.org/1999/xhtml">'
            f'<div class="task" xmlns="http://www.w3.org/1999/xhtml" '
            'style="display: table; height: 100%; width: 100%;">'
            '<div class="label" style="display: table-cell; text-align: center; '
            f'vertical-align: middle;">{escaped_task_name}</div></div></foreignObject>'
            f'<text x="{_fmt0(task_center_x)}" y="{_fmt0(task_center_y)}" '
            'dominant-baseline="central" alignment-baseline="central" '
            'class="task" '
            f'style="text-anchor: middle; font-size: {_fmt0(TASK_FONT_SIZE)}px; '
            f'font-family: {TASK_FONT_FAMILY};">'
            f'<tspan x="{_fmt0(task_center_x)}" dy="0">{escaped_task_name}</tspan></text>'
            "</switch>"
        )

        parts.append("</g>")

    # Title (grok L319-L326).
    if journey.title is not None:
        parts.append(
            f'<text x="{_fmt0(left_margin)}" font-size="{TITLE_FONT_SIZE}" '
            f'font-weight="bold" y="25" fill="#333" '
            f'font-family="{FONT_FAMILY}">{_escape_xml(journey.title)}</text>'
        )

    # Horizontal arrow (grok L329-L332).
    parts.append(
        f'<line x1="{_fmt0(left_margin)}" y1="{_fmt0(arrow_y)}" '
        f'x2="{_fmt0(arrow_x2)}" y2="{_fmt0(arrow_y)}" '
        'stroke-width="4" stroke="black" marker-end="url(#arrowhead)"/>'
    )

    parts.append("</svg>")
    return "".join(parts)


def _draw_face(parts: list[str], cx: float, cy: float, score: int) -> None:
    """Append the face icon (circle + eyes + mouth) for one task.

    Mirrors grok ``draw_face`` (L338-L389). The mouth shape is selected by
    ``score``: smile when ``> 3``, frown when ``< 3``, a neutral line at ``3``.
    """
    # Face circle (grok L340-L343).
    parts.append(
        f'<circle cx="{_fmt0(cx)}" cy="{_fmt0(cy)}" class="face" r="{_fmt0(FACE_RADIUS)}" '
        'stroke-width="2" overflow="visible"/>'
    )

    parts.append("<g>")

    # Eyes (grok L348-L356).
    eye_y = cy - FACE_RADIUS / 3.0
    left_eye_x = cx - FACE_RADIUS / 3.0
    right_eye_x = cx + FACE_RADIUS / 3.0
    parts.append(
        f'<circle cx="{_fmt0(left_eye_x)}" cy="{_fmt0(eye_y)}" r="1.5" '
        'stroke-width="2" fill="#666" stroke="#666"/>'
    )
    parts.append(
        f'<circle cx="{_fmt0(right_eye_x)}" cy="{_fmt0(eye_y)}" r="1.5" '
        'stroke-width="2" fill="#666" stroke="#666"/>'
    )

    # Mouth based on score (grok L358-L386).
    if score > 3:
        # Happy: smile arc.
        inner_r = FACE_RADIUS / 2.0
        outer_r = FACE_RADIUS / 2.2
        arc_path = _generate_smile_arc(inner_r, outer_r)
        parts.append(
            f'<path class="mouth" d="{arc_path}" '
            f'transform="translate({_fmt0(cx)},{_fmt0(cy + 2.0)})"/>'
        )
    elif score < 3:
        # Sad: frown arc.
        inner_r = FACE_RADIUS / 2.0
        outer_r = FACE_RADIUS / 2.2
        arc_path = _generate_sad_arc(inner_r, outer_r)
        parts.append(
            f'<path class="mouth" d="{arc_path}" '
            f'transform="translate({_fmt0(cx)},{_fmt0(cy + 7.0)})"/>'
        )
    else:
        # Neutral: straight line.
        parts.append(
            f'<line class="mouth" stroke="#666" x1="{_fmt0(cx - 5.0)}" y1="{_fmt0(cy + 7.0)}" '
            f'x2="{_fmt0(cx + 5.0)}" y2="{_fmt0(cy + 7.0)}" stroke-width="1px"/>'
        )

    parts.append("</g>")


def _generate_smile_arc(inner_r: float, outer_r: float) -> str:
    """Build the smile arc path (grok ``generate_smile_arc`` L393-L401).

    Matches the d3.arc geometry with ``startAngle=PI/2``, ``endAngle=3*PI/2``,
    ``innerRadius=r/2``, ``outerRadius=r/2.2`` -- the canonical mermaid smile
    path ``M{or},0A{or},{or},0,1,1,-{or},0L-{ir},0A{ir},{ir},0,1,0,{ir},0Z``.
    """
    or_ = _fmt_num(outer_r)
    ir = _fmt_num(inner_r)
    return f"M{or_},0A{or_},{or_},0,1,1,-{or_},0L-{ir},0A{ir},{ir},0,1,0,{ir},0Z"


def _generate_sad_arc(inner_r: float, outer_r: float) -> str:
    """Build the sad (frown) arc path (grok ``generate_sad_arc`` L405-L411).

    Matches d3.arc with ``startAngle=3*PI/2``, ``endAngle=5*PI/2`` -- the
    canonical mermaid frown path
    ``M-{or},0A{or},{or},0,1,1,{or},0L{ir},0A{ir},{ir},0,1,0,-{ir},0Z``.
    """
    or_ = _fmt_num(outer_r)
    ir = _fmt_num(inner_r)
    return f"M-{or_},0A{or_},{or_},0,1,1,{or_},0L{ir},0A{ir},{ir},0,1,0,-{ir},0Z"


def _fmt_num(n: float) -> str:
    """Format a number the D3 way: 3 decimals, then drop trailing zeros.

    Mirrors grok ``format_num`` (L413-L421). ``7.500`` -> ``"7.5"``,
    ``6.818`` -> ``"6.818"``, ``250.000`` -> ``"250"``. Used only for the
    arc radii in the smile/frown path data.
    """
    s = f"{n:.3f}"
    if "." in s:
        return s.rstrip("0").rstrip(".")
    return s


def _fmt0(value: float) -> str:
    """Format a coordinate as a whole number (mirrors Rust ``{:.0}``).

    Every coordinate passed through this is an exact integer (the layout
    arithmetic never produces fractional pixels), so this is a pure scalar
    cast -- it exists to keep the emission sites reading ``_fmt0(x)`` rather
    than inlining ``int()`` / f-string specifiers.
    """
    return f"{value:.0f}"


def _position(actors: list[str], name: str) -> int | None:
    """Return the index of ``name`` in ``actors``, or ``None`` (mirrors ``iter::position``)."""
    for i, a in enumerate(actors):
        if a == name:
            return i
    return None


def _parse_journey_diagram(input_: str) -> _JourneyDiagram:
    """Parse mermaid journey source into a :class:`_JourneyDiagram`.

    Mirrors grok ``parse_journey_diagram`` (L458-L555). Skips blank / ``%%``
    lines, requires a leading ``journey`` declaration, then collects an
    optional ``title``, ``section`` declarations, and ``name: score: actors``
    task rows. Raises :class:`ParseError` on any structural violation, with
    the 1-based source line and a message that reproduces grok's wording.
    """
    lines = input_.split("\n")
    # grok uses ``lines()`` which does not include the lines collection's
    # index directly; we keep a parallel index over the trimmed lines.

    i = 0
    # Locate the ``journey`` declaration (grok L461-L478).
    while i < len(lines):
        line = lines[i].strip()
        if line == "" or line.startswith("%%"):
            i += 1
            continue
        first = line.split()[0] if line.split() else ""
        if first == "journey":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'journey' declaration")

    title: str | None = None
    rows: list[_JourneyRow] = []

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        line_no = i + 1
        i += 1

        if line == "" or line.startswith("%%"):
            continue

        # title <text> (grok L493-L499).
        if line.startswith("title "):
            t = line[len("title ") :].strip()
            if t != "":
                title = t
            continue

        # section <name> (grok L501-L507).
        if line.startswith("section "):
            name = line[len("section ") :].strip()
            if name != "":
                rows.append(_JourneySectionRow(name))
            continue

        # Task row: ``name : score [: actors]`` (grok L509-L544).
        parts = [p.strip() for p in line.split(":") if p.strip() != ""]
        if len(parts) < 2:
            raise ParseError(line_no, f"Invalid journey task line: {line}")

        name = parts[0]
        try:
            score = int(parts[1])
        except ValueError as exc:
            raise ParseError(line_no, f"Invalid journey score: {line}") from exc

        if len(parts) >= 3:
            # Actors field may contain comma-separated names (e.g. "Alice, Bob").
            # Re-join any parts beyond [2] with ": " in case an actor name holds
            # a colon, then split on commas (grok L527-L535).
            joined = ": ".join(parts[2:])
            actors = [s.strip() for s in joined.split(",") if s.strip() != ""]
        else:
            actors = []

        rows.append(_JourneyTaskRow(_JourneyTask(name=name, score=score, actors=actors)))

    if not rows:
        # grok L547-L552: empty row stream is a parse error.
        raise ParseError(1, "Journey requires at least one section/task")

    return _JourneyDiagram(title=title, rows=rows)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (mirrors grok ``escape_xml`` L557-L563).

    Note the ``'`` -> ``&apos;`` mapping -- grok's journey source uses the
    named entity (R289 ``block_diagram`` uses ``&#x27``; each renderer is
    faithful to its own grok source's apostrophe entity).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


__all__ = ["render_journey_diagram_to_svg"]
