"""Timeline diagram renderer -- behavioral-equivalent port of grok's ``timeline_diagram.rs``.

Direction (1) brick 20 (R287). The ninth per-diagram leaf and the eighth
self-contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` /
R282 ``pie`` / R283 ``packet`` / R284 ``sankey`` / R285 ``gantt`` / R286
``kanban``; unlike R280 ``stateDiagram`` which rides the dagre stack). The
``timeline`` diagram is mermaid's chronological board: a horizontal axis of
period nodes (one per task) each optionally trailing a vertical stack of event
cards, grouped under optional colored section banners, with a left-to-right
direction arrow underneath. Pure axis-then-stack geometry, so this leaf emits
SVG directly from the parsed model -- no AST, no dagre.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_timeline_diagram_to_svg(src, _theme) -> Result<String,
  MermaidError>`` -> ``render_timeline_diagram_to_svg(src, _theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to "return
  the SVG / raise :class:`ParseError`". The ``_theme`` parameter is unused --
  grok hard-codes mermaid 11.12.2's default timeline palette (``#333`` text
  fill, three fixed ``cScale`` 12-hue palettes) and ignores the resolved
  theme. Same stance as R282 pie / R283 packet / R285 gantt (NOT theme-aware,
  unlike R284 sankey / R286 kanban which consult ``theme``).
* grok's two private structs ``TimelineDiagram`` / ``TimelineTask`` ->
  :class:`TimelineDiagram` / :class:`TimelineTask` as plain (mutable)
  dataclasses -- mutable because grok's parser calls
  ``tasks.last_mut().events.push(...)`` when a ``: event`` continuation line
  appends to the previous task (mirrors R286 kanban's mutable task stance).
* grok ``parse_timeline_diagram`` -> ``_parse_timeline_diagram`` (module-
  private; the public renderer is reached only through the dispatch arm).
* grok ``escape_xml`` maps ``'`` -> ``&apos;`` (XML named entity) -- matches
  R282 pie / R283 packet / R285 gantt / R286 kanban; R281 radar / R284 sankey
  use ``&#39;`` (numeric). Both faithfully mirror their respective grok source
  -- the Rust functions disagree.

Section CSS index quirk
-----------------------

Mermaid's timeline CSS uses class names ``.section--1`` through ``.section-10``
(12 buckets). grok reproduces this by looping ``i`` over ``0..12`` and emitting
``si = i as isize - 1`` (so ``i=0`` -> ``si=-1`` -> ``.section--1``). The port
mirrors that verbatim: the CSS ``<style>`` block emits 12 ``.section-{si}``
rules, each painting ``rect`` / ``path`` / ``circle`` with one fill from
:data:`_CSCALE_FILLS`, ``text`` with one color from :data:`_CSCALE_LABEL`, and
``line`` with one stroke from :data:`_CSCALE_INV` (the hue-shifted-by-180deg
companion palette, used for each node's bottom border line). Indices 0 and 3 of
``_CSCALE_LABEL`` are white (``#ffffff``); the rest are black (``#000000``).

Three ``cScale`` palettes
-------------------------

grok ships three fixed 12-entry palettes (mermaid 11.12.2 default theme, after
darken-by-10): :data:`_CSCALE_FILLS` (node fills), :data:`_CSCALE_INV`
(hue-rotated 180deg, used as the node bottom-line stroke), and
:data:`_CSCALE_LABEL` (text colors, white at indices 0 and 3). The port copies
all 36 hex literals verbatim.

Float formatting bridge
-----------------------

Integer-valued floats (viewBox dimensions, geometry constants, transform
coordinates) route through :func:`_fmt`, which collapses ``200.0`` -> ``"200"``
(Rust ``Display`` drops the trailing ``.0``; Python ``str`` keeps it).
Non-integer floats fall back to ``repr`` (shortest round-trippable decimal,
matching Rust's ``Display`` for f64). Mirrors R285 gantt / R286 kanban's
``_fmt``.

Byte-length text estimate
-------------------------

grok measures rendered text width with ``text.len() * CHAR_WIDTH``
(``CHAR_WIDTH`` = 9.0), and Rust ``String::len`` is a *byte* count. The port
mirrors that with ``len(text.encode("utf-8")) * CHAR_WIDTH`` so a multi-byte
label produces the same line-count / node-height estimate grok computes
(mirrors R286 kanban's byte-length decision).

Node background shape
---------------------

:func:`_render_node_background` emits mermaid's ``defaultBkg`` path: rounded
top corners (radius :data:`NODE_CORNER_RADIUS`) with a flat bottom, plus a
single ``<line>`` stroking that flat bottom. Matches grok L353-L368 through
:func:`_fmt`.

Per-section double-step master_x advance
-----------------------------------------

In the sections-present branch, grok advances ``master_x`` twice per section:
once inside ``render_tasks`` (``NODE_STEP`` = 200 per task) and once more
outside (``200 * max(len, 1)``). The port reproduces this geometry verbatim --
the extra gap spaces consecutive section banners apart. Not a bug to fix: the
output SVG must match grok's layout.

Dispatch wiring
---------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer (the body-shadow contract
lifted in R282). The token ``timeline`` is removed from
:data:`render._UNSUPPORTED_DIAGRAM_TYPES` (16 -> 15 remaining).

Public surface (1 symbol): :func:`render_timeline_diagram_to_svg`. The
dataclasses, parser, and helpers stay out of ``__all__``; the renderer is
reached only through the dispatch arm added in R287.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_timeline_diagram_to_svg"]

# --- Mermaid 11.12.2 timeline layout constants (grok L4-L26) ---
#: Carried as ``float`` because the SVG viewBox / dimensions / transforms are
#: emitted via Rust ``Display`` (drops ``.0``); :func:`_fmt` reproduces that
#: rendering (mirrors R286 kanban).
LEFT_MARGIN: float = 50.0
#: 50 + LEFT_MARGIN (grok L6).
INITIAL_MASTER_X: float = 100.0
INITIAL_MASTER_Y: float = 50.0
NODE_BASE_WIDTH: float = 150.0
NODE_PADDING: float = 20.0
#: NODE_BASE_WIDTH + 2 * NODE_PADDING (grok L10).
NODE_WIDTH: float = 190.0
NODE_STEP: float = 200.0
FONT_SIZE: float = 16.0
EVENT_VERTICAL_GAP: float = 100.0
DASHED_LINE_EXTENSION: float = 100.0
NODE_CORNER_RADIUS: float = 5.0
MAX_SECTIONS: int = 12
ARROW_STROKE_WIDTH: float = 4.0
CONNECTOR_STROKE_WIDTH: float = 2.0
NODE_LINE_STROKE_WIDTH: float = 3.0

FONT_FAMILY: str = '"trebuchet ms", verdana, arial, sans-serif'
TASK_FONT_SIZE: float = 14.0
TASK_FONT_FAMILY: str = "'Open Sans', sans-serif"

#: Approximate rendered char width at 16px (grok L26).
CHAR_WIDTH: float = 9.0

# --- Mermaid 11.12.2 default theme cScale colors (grok L29-L54) ---

#: Node fill palette (after darken-by-10). Index cycles via ``i % 12``.
_CSCALE_FILLS: tuple[str, ...] = (
    "#BABAFF",  # cScale0: periwinkle (primaryColor #ECECFF)
    "#FFFFAC",  # cScale1: yellow (secondaryColor #ffffde)
    "#E8FFB9",  # cScale2: lime green (tertiaryColor)
    "#D4BAFF",  # cScale3
    "#FFBAFF",  # cScale4
    "#FFBADC",  # cScale5
    "#BAFFBA",  # cScale6
    "#BAFFDC",  # cScale7
    "#BAFFFF",  # cScale8
    "#BABAFF",  # cScale9
    "#DCBAFF",  # cScale10
    "#FFBAEF",  # cScale11
)

#: cScaleInv = hue-shifted by 180deg from cScale (node bottom-line stroke).
_CSCALE_INV: tuple[str, ...] = (
    "#FFFFAC",
    "#BABAFF",
    "#FFB9E8",
    "#BAFFD4",
    "#BAFF9A",
    "#BAFFDC",
    "#FFBA9A",
    "#FFBADC",
    "#FFBABA",
    "#FFFFBA",
    "#BAFFBA",
    "#BAFFE0",
)

#: cScaleLabel text colors (indices 0 and 3 = white, rest = black).
_CSCALE_LABEL: tuple[str, ...] = (
    "#ffffff",
    "#000000",
    "#000000",
    "#ffffff",
    "#000000",
    "#000000",
    "#000000",
    "#000000",
    "#000000",
    "#000000",
    "#000000",
    "#000000",
)


@dataclass
class TimelineTask:
    """One timeline period row (grok ``TimelineTask``).

    ``period`` is the task's time label (the node on the horizontal axis);
    ``events`` are the stacked event cards hanging below it; ``section`` is the
    optional banner the task was grouped under. Mutable because grok's parser
    appends continuation ``: event`` lines onto ``tasks.last_mut().events``.
    """

    period: str
    events: list[str] = field(default_factory=list)
    section: str | None = None


@dataclass
class TimelineDiagram:
    """Parsed timeline model (grok ``TimelineDiagram``)."""

    title: str | None
    sections: list[str]
    tasks: list[TimelineTask]


def _fmt(value: int | float) -> str:
    """Render a number the way Rust ``Display`` would (R281 radar bridge).

    Integer-valued floats drop the trailing ``.0`` (``200.0`` -> ``"200"``);
    plain ints render verbatim (``12`` -> ``"12"``); non-integer floats fall
    back to ``repr`` (shortest round-trippable decimal, matching Rust's
    ``Display`` for f64). Mirrors R285 gantt / R286 kanban's ``_fmt``.
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

    Note: grok's ``timeline_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named
    entity) -- matches R282 pie / R283 packet / R285 gantt / R286 kanban; R281
    radar / R284 sankey map ``'`` -> ``&#39;`` (numeric). Both faithfully mirror
    their respective grok source -- the Rust functions disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _estimate_text_height(text: str) -> float:
    """Estimate rendered text height in px (grok ``estimate_text_height``).

    Byte-length width (Rust ``String::len`` semantics) divided by the base node
    width, rounded up to whole lines, each line ``FONT_SIZE * 1.2`` tall.
    """
    text_width = len(text.encode("utf-8")) * CHAR_WIDTH
    num_lines = max(math.ceil(text_width / NODE_BASE_WIDTH), 1.0)
    return num_lines * FONT_SIZE * 1.2


def _estimate_node_height(text: str, padding: float, max_height: float) -> float:
    """Estimate a node's height (grok ``estimate_node_height``).

    Text height plus a half-line leading plus ``padding``, floored at
    ``max_height`` (grok L339-L343).
    """
    text_h = _estimate_text_height(text)
    h = text_h + FONT_SIZE * 1.1 * 0.5 + padding
    return max(h, max_height)


def _estimate_event_height(text: str) -> float:
    """Estimate an event card's height (grok ``estimate_event_height``).

    Same formula as :func:`_estimate_node_height` with ``NODE_PADDING`` and a
    floor of 50px (grok L345-L349).
    """
    text_h = _estimate_text_height(text)
    h = text_h + FONT_SIZE * 1.1 * 0.5 + NODE_PADDING
    return max(h, 50.0)


def _render_node_background(buf: list[str], width: float, height: float) -> None:
    """Append the node background shape (grok ``render_node_background``).

    Emits mermaid's ``defaultBkg`` path: rounded top corners (radius
    :data:`NODE_CORNER_RADIUS`) with a flat bottom, plus a single ``<line>``
    stroking that flat bottom (grok L353-L368).
    """
    rd = NODE_CORNER_RADIUS
    h_rd = height - rd
    up = -(height - 2.0 * rd)
    across = width - 2.0 * rd
    down = height - rd
    buf.append(
        f'<g><path class="node-bkg" d="M0 {_fmt(h_rd)} v{_fmt(up)} '
        f'q0,-{_fmt(rd)} {_fmt(rd)},-{_fmt(rd)} h{_fmt(across)} '
        f'q{_fmt(rd)},0 {_fmt(rd)},{_fmt(rd)} v{_fmt(down)} H0 Z"/>'
    )
    buf.append(
        f'<line x1="0" y1="{_fmt(height)}" x2="{_fmt(width)}" y2="{_fmt(height)}"/>'
    )
    buf.append("</g>")


def _render_node_text(buf: list[str], text: str, width: float) -> None:
    """Append centered node text (grok ``render_node_text``).

    Translates to ``(width / 2, NODE_PADDING / 2)`` and emits a
    ``TASK_FONT_SIZE``-px, ``TASK_FONT_FAMILY``-faced, anchor-middle text
    (grok L371-L383).
    """
    x = width / 2.0
    ty = NODE_PADDING / 2.0
    buf.append(
        f'<g transform="translate({_fmt(x)},{_fmt(ty)})">'
        f'<text x="0" y="0" dy="1em" alignment-baseline="middle" '
        f'dominant-baseline="middle" text-anchor="middle" '
        f'style="font-size:{_fmt(TASK_FONT_SIZE)}px;font-family:{TASK_FONT_FAMILY};">'
        f'{_escape_xml(text)}</text></g>'
    )


def _render_tasks(
    buf: list[str],
    tasks: list[TimelineTask],
    initial_section_color: int,
    master_x: float,
    master_y: float,
    max_task_height: float,
    max_event_line_length: float,
    content_right: float,
    content_bottom: float,
    is_multicolor: bool,
) -> tuple[float, float, float]:
    """Append task nodes + their event stacks + dashed connectors.

    Mirrors grok ``render_tasks`` L255-L331. Returns the updated
    ``(master_x, content_right, content_bottom)`` triple -- grok threads the
    first and last two through ``&mut f64``; the port returns them so the
    caller rebinds. ``is_multicolor`` increments the section-color counter per
    task (the no-sections branch paints every task a distinct hue); the
    sections branch passes ``False`` so all tasks under one banner share its
    color.
    """
    section_color = initial_section_color
    for task in tasks:
        section_idx = section_color % MAX_SECTIONS
        section_css_idx = section_idx - 1

        # Task (period) node.
        buf.append(
            f'<g class="taskWrapper"><g class="timeline-node section-{section_css_idx}" '
            f'transform="translate({_fmt(master_x)},{_fmt(master_y)})">'
        )
        _render_node_background(buf, NODE_WIDTH, max_task_height)
        _render_node_text(buf, task.period, NODE_WIDTH)
        buf.append("</g></g>")

        content_right = max(content_right, master_x + NODE_WIDTH)

        # Events stacked below the task.
        if task.events:
            event_y = master_y + EVENT_VERTICAL_GAP + EVENT_VERTICAL_GAP
            for event in task.events:
                event_height = _estimate_event_height(event)
                buf.append(
                    f'<g class="eventWrapper"><g class="timeline-node section-{section_css_idx}" '
                    f'transform="translate({_fmt(master_x)},{_fmt(event_y)})">'
                )
                _render_node_background(buf, NODE_WIDTH, event_height)
                _render_node_text(buf, event, NODE_WIDTH)
                buf.append("</g></g>")
                event_y += event_height + 10.0

            # Dashed vertical connector with arrowhead.
            line_x = master_x + NODE_WIDTH / 2.0
            line_y1 = master_y + max_task_height
            line_y2 = (
                master_y
                + max_task_height
                + EVENT_VERTICAL_GAP
                + max_event_line_length
                + DASHED_LINE_EXTENSION
            )
            buf.append(
                f'<g class="lineWrapper"><line x1="{_fmt(line_x)}" y1="{_fmt(line_y1)}" '
                f'x2="{_fmt(line_x)}" y2="{_fmt(line_y2)}" '
                f'stroke-width="{_fmt(CONNECTOR_STROKE_WIDTH)}" stroke="black" '
                f'marker-end="url(#arrowhead)" stroke-dasharray="5,5"/></g>'
            )
            content_bottom = max(content_bottom, line_y2 + 10.0)

        master_x += NODE_STEP
        if is_multicolor:
            section_color += 1

    return master_x, content_right, content_bottom


def _parse_timeline_diagram(input: str) -> TimelineDiagram:
    """Parse mermaid timeline source (grok ``parse_timeline_diagram``).

    Scans for the ``timeline`` header (the first non-empty / non-``%%`` line's
    first token must be ``timeline``, else :class:`ParseError`). Then collects
    ``title``, ``section`` banners (de-duplicated), ``: event`` continuation
    lines (appended to the previous task's events), and ``period : event`` /
    bare ``period`` lines into tasks (grok L403-L505).
    """
    lines = input.splitlines()

    # Locate the ``timeline`` header.
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        if line.split()[0] == "timeline":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'timeline' declaration")

    title: str | None = None
    sections: list[str] = []
    tasks: list[TimelineTask] = []
    current_section: str | None = None

    while i < n:
        line = lines[i].strip()
        i += 1

        if not line or line.startswith("%%") or line.startswith("#"):
            continue

        # Title directive.
        if line.startswith("title "):
            t = line.removeprefix("title ").strip()
            if t:
                title = t
            continue

        # Section directive.
        if line.startswith("section "):
            s = line.removeprefix("section ").strip()
            if s:
                current_section = s
                if s not in sections:
                    sections.append(s)
            continue

        # Event continuation (``: <text>`` appends to the previous task).
        if line.startswith(": "):
            event_text = line.removeprefix(": ").strip()
            if event_text and tasks:
                tasks[-1].events.append(event_text)
            continue

        # ``period : event`` or bare ``period`` (split on the first colon).
        if ":" in line:
            period, _, event = line.partition(":")
            period = period.strip()
            event = event.strip()
            if period:
                events = [event] if event else []
                tasks.append(
                    TimelineTask(period=period, events=events, section=current_section)
                )
                continue

        # Bare text line -- a period with no events.
        tasks.append(TimelineTask(period=line, events=[], section=current_section))

    return TimelineDiagram(title=title, sections=sections, tasks=tasks)


def render_timeline_diagram_to_svg(
    mermaid_source: str, _theme: MermaidTheme
) -> str:
    """Render a mermaid timeline diagram into an SVG string.

    Mirrors grok ``render_timeline_diagram_to_svg`` (L56-L253). The ``_theme``
    parameter is accepted for dispatch symmetry but ignored -- grok hard-codes
    mermaid 11.12.2's default timeline palette (``#333`` text, three fixed
    ``cScale`` palettes) and so does this port.

    Raises:
        ParseError: when the source has no ``timeline`` header, or when the
            parsed model contains no tasks.
    """
    timeline = _parse_timeline_diagram(mermaid_source)

    has_sections = bool(timeline.sections)
    tasks = timeline.tasks
    if not tasks:
        raise ParseError(1, "Timeline requires at least one entry")

    # --- Layout metrics (grok L71-L95) ---
    max_section_height = 0.0
    if has_sections:
        for section_name in timeline.sections:
            h = _estimate_node_height(section_name, NODE_PADDING, 0.0)
            max_section_height = max(max_section_height, h + 20.0)

    max_task_height = 0.0
    max_event_line_length = 0.0
    for task in tasks:
        h = _estimate_node_height(task.period, NODE_PADDING, 0.0)
        max_task_height = max(max_task_height, h + 20.0)
        event_line_len = 0.0
        for event in task.events:
            event_line_len += _estimate_node_height(event, NODE_PADDING, 50.0)
        if len(task.events) > 1:
            event_line_len += (len(task.events) - 1) * 10.0
        max_event_line_length = max(max_event_line_length, event_line_len)

    # --- CSS (grok L102-L121) ---
    css_parts: list[str] = [
        f"svg{{font-family:{FONT_FAMILY};font-size:{_fmt(FONT_SIZE)}px;fill:#333;}}"
    ]
    for i in range(MAX_SECTIONS):
        si = i - 1
        fill = _CSCALE_FILLS[i % len(_CSCALE_FILLS)]
        label = _CSCALE_LABEL[i % len(_CSCALE_LABEL)]
        inv = _CSCALE_INV[i % len(_CSCALE_INV)]
        css_parts.append(
            f".section-{si} rect,.section-{si} path,.section-{si} circle{{fill:{fill};}}"
        )
        css_parts.append(f".section-{si} text{{fill:{label};}}")
        css_parts.append(
            f".section-{si} line{{stroke:{inv};stroke-width:{_fmt(NODE_LINE_STROKE_WIDTH)};}}"
        )
    css_parts.append(".eventWrapper{filter:brightness(120%);}")
    css_parts.append(".lineWrapper line{stroke:black;}")
    css = "".join(css_parts)

    # --- defs: arrowhead marker (grok L123-L124) ---
    defs = (
        '<defs><marker id="arrowhead" refX="5" refY="2" markerWidth="6" '
        'markerHeight="4" orient="auto"><path d="M 0,0 V 4 L6,2 Z"/></marker></defs>'
    )

    # --- Body: sections + tasks + events (grok L126-L187) ---
    body: list[str] = []
    content_right = 0.0
    content_bottom = 0.0
    master_x = INITIAL_MASTER_X
    master_y = INITIAL_MASTER_Y
    section_begin_y = INITIAL_MASTER_Y
    section_number = 0

    if has_sections:
        for section_name in timeline.sections:
            tasks_for_section = [t for t in tasks if t.section == section_name]
            section_width = 200.0 * max(len(tasks_for_section), 1) - 50.0
            section_css_idx = section_number % MAX_SECTIONS - 1

            body.append(
                f'<g class="timeline-node section-{section_css_idx}" '
                f'transform="translate({_fmt(master_x)},{_fmt(section_begin_y)})">'
            )
            _render_node_background(body, section_width, max_section_height)
            _render_node_text(body, section_name, section_width)
            body.append("</g>")

            task_y = section_begin_y + max_section_height + 50.0
            if tasks_for_section:
                master_x, content_right, content_bottom = _render_tasks(
                    body,
                    tasks_for_section,
                    section_number,
                    master_x,
                    task_y,
                    max_task_height,
                    max_event_line_length,
                    content_right,
                    content_bottom,
                    False,
                )
            master_x += 200.0 * max(len(tasks_for_section), 1)
            section_number += 1
    else:
        master_x, content_right, content_bottom = _render_tasks(
            body,
            tasks,
            section_number,
            master_x,
            master_y,
            max_task_height,
            max_event_line_length,
            content_right,
            content_bottom,
            True,
        )

    # --- Horizontal direction arrow (grok L189-L208) ---
    depth_y = (
        max_section_height + max_task_height + 150.0
        if has_sections
        else max_task_height + 100.0
    )
    nodes_box_width = content_right
    arrow_x1 = LEFT_MARGIN
    arrow_x2 = nodes_box_width + 3.0 * LEFT_MARGIN
    content_right = max(content_right, arrow_x2 + 10.0)
    body.append(
        f'<g class="lineWrapper"><line x1="{_fmt(arrow_x1)}" y1="{_fmt(depth_y)}" '
        f'x2="{_fmt(arrow_x2)}" y2="{_fmt(depth_y)}" '
        f'stroke-width="{_fmt(ARROW_STROKE_WIDTH)}" stroke="black" '
        f'marker-end="url(#arrowhead)"/></g>'
    )

    # --- Title (grok L210-L224) ---
    title_content = ""
    if timeline.title is not None:
        title = timeline.title
        title_x = nodes_box_width / 2.0 - LEFT_MARGIN
        approx_title_width = len(title.encode("utf-8")) * 24.0
        content_right = max(content_right, title_x + approx_title_width + 20.0)
        title_content = (
            f'<text x="{_fmt(title_x)}" y="20" font-size="4ex" '
            f'font-weight="bold" fill="#333">{_escape_xml(title)}</text>'
        )

    content_bottom = max(content_bottom, depth_y + 20.0)

    # --- Assemble final SVG (grok L228-L252) ---
    vb_padding = 50.0
    vb_width = content_right + vb_padding
    vb_height = content_bottom + vb_padding
    vh = vb_height + 25.0
    sh = vb_height + 50.0

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'style="max-width: {_fmt(vb_width)}px;" width="100%" '
        f'viewBox="0 -25 {_fmt(vb_width)} {_fmt(vh)}" '
        f'preserveAspectRatio="xMinYMin meet" height="{_fmt(sh)}" '
        f'role="graphics-document document" aria-roledescription="timeline">',
        f"<style>{css}</style>",
        defs,
        title_content,
        *body,
        "</svg>",
    ]
    return "".join(svg)
