"""Gantt diagram renderer -- behavioral-equivalent port of grok's ``gantt_diagram.rs``.

Direction (1) brick 18 (R285). The seventh per-diagram leaf and the seventh
self-contained SVG emitter (same pattern as R279 ``info`` / R281 ``radar`` /
R282 ``pie`` / R283 ``packet`` / R284 ``sankey``; unlike R280 ``stateDiagram``
which rides the dagre stack). The ``gantt`` diagram is mermaid's project
schedule: tasks grouped into sections, laid out as horizontal bars on a
day-scaled timeline, with section background bands, a bottom date axis with
daily grid ticks, inside/outside task labels, a left-side section legend, and
an optional title. Pure calendar arithmetic + time-domain scaling, so this
leaf emits SVG directly from the parsed model -- no AST, no dagre.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_gantt_diagram_to_svg(src, _theme) -> Result<String,
  MermaidError>`` -> ``render_gantt_diagram_to_svg(src, _theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to "return
  the SVG / raise :class:`ParseError`". The ``_theme`` parameter is unused --
  grok hard-codes mermaid 11.12.2's default gantt palette
  (``SECTION_BKG_COLOR`` / ``TASK_BKG_COLOR`` / ``GRID_COLOR`` / ...) and
  ignores the resolved theme, matching R282 pie / R283 packet (NOT R284
  sankey, which IS theme-aware).
* grok's two private structs ``GanttChart`` / ``GanttTask`` ->
  :class:`GanttChart` / :class:`GanttTask` as ``frozen`` dataclasses.
* grok ``parse_gantt_diagram`` -> ``parse_gantt_diagram`` (module-public for
  direct testing; the helpers ``_parse_duration_days`` / ``_parse_ymd_to_day``
  / ``_day_to_ymd`` / ``_day_to_ymd_str`` / ``_days_from_civil`` /
  ``_escape_xml`` stay private with a leading underscore).
* grok ``escape_xml`` maps ``'`` -> ``&apos;`` (XML named entity) -- matches
  R282 pie / R283 packet; R281 radar / R284 sankey use ``&#39;`` (numeric).
  Both faithfully mirror their respective grok source -- the Rust functions
  disagree.

Howard Hinnant civil-from-days
------------------------------

The gantt timeline scales by *day number* (a task's ``start_day`` /
``duration_days`` are integer days), so the renderer must round-trip a day
number through ``(y, m, d)`` for the axis date labels. grok ports Howard
Hinnant's ``days_from_civil`` / ``civil_from_days`` pair (a well-known
proleptic-Gregorian serial-day algorithm) verbatim. The port uses Python's
``//`` (floor division) where grok writes ``if c { a } else { b } / d`` --
that Rust idiom *simulates* floor division by pre-adjusting the dividend to
be non-negative before truncating toward zero; Python has floor division
natively, so ``y // 400`` expresses the same floor semantics directly. This
is the function-not-line intent: same math, idiomatic host language.

Float formatting bridge
-----------------------

Integer-valued floats (viewBox dimensions, ``BAR_HEIGHT``, ``FONT_SIZE``,
``SECTION_FONT_SIZE``, ``TITLE_TOP_MARGIN``, the grid translate, the rect
``rx``/``ry``) route through :func:`_fmt`, which collapses ``784.0`` ->
``"784"`` (Rust ``Display`` drops the trailing ``.0``; Python ``str`` keeps
it). The fixed-precision sites (``:.1`` / ``:.2``) map to Python ``:.1f`` /
``:.2f`` directly.

Byte-length text estimate
-------------------------

grok measures a task name's rendered width with ``task.name.len()``, and Rust
``String::len`` is a *byte* count. The port mirrors that with
``len(name.encode("utf-8"))`` so a multi-byte name produces the same
inside/outside placement decision grok makes (diverges from Python ``len``
only for non-ASCII names -- gantt task names are usually ASCII).

Embedded ``<style>`` block
--------------------------

Unlike R279-R284's inline-attribute emitters, gantt ships a CSS ``<style>``
block (mermaid 11.12.2 ``ganttStyles``) defining ``.section`` / ``.task`` /
``.taskText`` / ``.grid`` / ``.titleText`` / ``.sectionTitle`` classes. The
f-string doubles each CSS brace (``{{`` -> ``{``); the color / font
interpolation sites stay single-braced, matching R282 pie's ``pieStyles``
block pattern.

Dispatch wiring
---------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer, mirroring grok lib.rs L47
(the body-shadow contract lifted in R282). The token ``gantt`` is removed
from :data:`render._UNSUPPORTED_DIAGRAM_TYPES` (18 -> 17 remaining).

Public surface (1 symbol): :func:`render_gantt_diagram_to_svg`. The
dataclasses, parser, and helpers stay out of ``__all__``; the renderer is
reached only through the dispatch arm added in R285.
"""

from __future__ import annotations

from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_gantt_diagram_to_svg"]

#: Mermaid 11.12.2 gantt geometry defaults (grok L7-L18, config.schema.yaml).
#: Carried as ``float`` because the SVG viewBox / dimensions are emitted via
#: Rust ``Display`` (drops ``.0``); :func:`_fmt` reproduces that rendering.
BAR_HEIGHT: float = 20.0
BAR_GAP: float = 4.0
TOP_PADDING: float = 50.0
LEFT_PADDING: float = 75.0
RIGHT_PADDING: float = 75.0
GRID_LINE_START_PADDING: float = 35.0
FONT_SIZE: float = 11.0
SECTION_FONT_SIZE: float = 11.0
TITLE_TOP_MARGIN: float = 25.0
BOTTOM_AXIS_HEIGHT: float = 50.0
RX: float = 3.0
RY: float = 3.0

#: Mermaid 11.12.2 default gantt palette (grok L20-L29, theme-default.js).
#: Hard-coded -- the gantt renderer does NOT consult ``_theme``.
SECTION_BKG_COLOR: str = "rgba(102,102,255,0.49)"
ALT_SECTION_BKG_COLOR: str = "white"
TASK_BKG_COLOR: str = "#8a90dd"
TASK_BORDER_COLOR: str = "#534fbc"
TASK_TEXT_COLOR: str = "white"
TASK_TEXT_DARK_COLOR: str = "black"
GRID_COLOR: str = "#333"
TITLE_COLOR: str = "#333"
FONT_FAMILY: str = "'trebuchet ms', verdana, arial, sans-serif"

#: Fixed canvas width (grok L65 ``let w = 784.0_f64``). The plot area is
#: ``_WIDTH - LEFT_PADDING - RIGHT_PADDING``.
_WIDTH: float = 784.0


@dataclass(frozen=True)
class GanttTask:
    """One gantt task bar (grok ``GanttTask``).

    ``section`` is the enclosing ``section <name>`` (None before the first
    section directive); ``name`` is the task label; ``start_day`` /
    ``duration_days`` are integer day offsets on the timeline.
    """

    section: str | None
    name: str
    start_day: int
    duration_days: int


@dataclass(frozen=True)
class GanttChart:
    """Parsed gantt model (grok ``GanttChart``).

    ``title`` is optional (mermaid ``title <text>`` line); ``tasks`` is the
    original-order list of task bars.
    """

    title: str | None
    tasks: list[GanttTask]


def _fmt(value: float) -> str:
    """Render a float the way Rust ``Display`` would (R281 radar bridge).

    Integer-valued floats drop the trailing ``.0`` (``784.0`` -> ``"784"``);
    non-integers fall back to ``repr`` (shortest round-trippable decimal,
    matching Rust's ``Display`` for f64).
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note: grok's ``gantt_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named
    entity) -- matches R282 pie / R283 packet; R281 radar / R284 sankey map
    ``'`` -> ``&#39;`` (numeric). Both faithfully mirror their respective grok
    source -- the Rust functions disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Proleptic-Gregorian ``(y, m, d)`` -> serial day number (Howard Hinnant).

    Mirrors grok ``days_from_civil`` (gantt_diagram.rs L422-L429). grok writes
    ``if y >= 0 { y } else { y - 399 } / 400`` to *simulate* floor division
    (pre-adjust the dividend non-negative, then truncate toward zero); Python
    has floor division natively, so ``y // 400`` expresses the same floor
    semantics directly.
    """
    y = y - (1 if m <= 2 else 0)
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _day_to_ymd(day_number: int) -> tuple[int, int, int]:
    """Serial day number -> ``(y, m, d)`` (Howard Hinnant, inverse of above).

    Mirrors grok ``day_to_ymd`` (gantt_diagram.rs L403-L415). Same floor-vs-
    truncate note: ``z // 146097`` replaces grok's
    ``if z >= 0 { z } else { z - 146096 } / 146097``.
    """
    z = day_number + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = y + 1 if m <= 2 else y
    return (y, m, d)


def _day_to_ymd_str(day_number: int) -> str:
    """Serial day number -> ``YYYY-MM-DD`` axis label (grok ``day_to_ymd_str``)."""
    y, m, d = _day_to_ymd(day_number)
    return f"{y:04}-{m:02}-{d:02}"


def _parse_ymd_to_day(s: str) -> int:
    """Parse a ``YYYY-MM-DD`` string into a serial day number (grok ``parse_ymd_to_day``).

    Raises ValueError on a malformed date (the caller wraps it into a
    :class:`ParseError` stamped with the source line number -- mirrors grok's
    ``.map_err``).
    """
    parts = s.strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date: {s}")
    try:
        y = int(parts[0])
    except ValueError as err:
        raise ValueError(f"Invalid year: {s}") from err
    try:
        m = int(parts[1])
    except ValueError as err:
        raise ValueError(f"Invalid month: {s}") from err
    try:
        d = int(parts[2])
    except ValueError as err:
        raise ValueError(f"Invalid day: {s}") from err
    return _days_from_civil(y, m, d)


def _parse_duration_days(spec: str) -> int:
    """Parse a gantt duration spec into integer days (grok ``parse_duration_days``).

    ``<n>d`` / ``<n>D`` -> ``n`` days; ``<n>w`` / ``<n>W`` -> ``n * 7`` days.
    Raises ValueError on an empty / non-numeric / unknown-unit spec (the
    caller wraps it into a :class:`ParseError` stamped with the source line
    number).
    """
    s = spec.strip()
    if not s:
        raise ValueError("Empty duration")
    num_str, unit = s[:-1], s[-1]
    try:
        n = int(num_str.strip())
    except ValueError as err:
        raise ValueError(f"Invalid duration: {spec}") from err
    if unit in ("d", "D"):
        return n
    if unit in ("w", "W"):
        return n * 7
    raise ValueError(f"Unsupported duration unit: {spec}")


def parse_gantt_diagram(input: str) -> GanttChart:
    """Parse mermaid ``gantt`` source into a :class:`GanttChart` (grok ``parse_gantt_diagram``).

    Scans for the ``gantt`` header token, then reads ``title <text>``,
    ``dateFormat ...`` (accepted but ignored -- the port hard-codes
    YYYY-MM-DD), ``section <name>`` directives, and task rows of the form
    ``<name> : <id>, <start>, <duration>``. The start may be a ``YYYY-MM-DD``
    date or an ``after <id>`` dependency (resolves to the referenced task's
    ``start_day + duration_days``). Blank / ``%%`` lines are skipped at both
    stages.

    Raises:
        ParseError: when the first substantial token is not ``gantt``; a task
            row has no ``:`` separator; the post-``:`` spec has fewer than 3
            comma-separated parts; the duration / date is malformed; an
            ``after`` dependency names an unknown id; or the diagram has zero
            tasks.
    """
    lines = input.splitlines()

    # Header scan: skip blanks / comments, require the first token to be ``gantt``.
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        if line.split()[0] == "gantt":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'gantt' declaration")

    title: str | None = None
    current_section: str | None = None
    tasks: list[GanttTask] = []
    tasks_by_id: dict[str, tuple[int, int]] = {}

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        line_no = i + 1
        i += 1

        if not line or line.startswith("%%"):
            continue

        # ``title <text>`` sets the chart title (grok strip_prefix("title ")).
        if line.startswith("title "):
            rest = line[len("title ") :].strip()
            if rest:
                title = rest
            continue

        # ``dateFormat ...`` is accepted but ignored.
        if line.startswith("dateFormat "):
            continue

        # ``section <name>`` opens a section (bare ``section`` -> None).
        if line.startswith("section "):
            name = line[len("section ") :].strip()
            current_section = None if not name else name
            continue

        # Otherwise the line must be ``<name> : <id>, <start>, <duration>``.
        name_raw, sep, spec_raw = line.partition(":")
        if not sep:
            raise ParseError(line_no, f"Invalid gantt task line: {line}")

        name = name_raw.strip()
        spec_parts = [p for p in (x.strip() for x in spec_raw.split(",")) if p]
        if len(spec_parts) < 3:
            raise ParseError(line_no, f"Invalid gantt task spec: {spec_raw}")

        task_id = spec_parts[0]
        start_spec = spec_parts[1]
        duration_spec = spec_parts[2]

        try:
            duration_days = _parse_duration_days(duration_spec)
        except ValueError as err:
            raise ParseError(line_no, str(err)) from err

        if start_spec.startswith("after "):
            ref_id = start_spec.removeprefix("after ").strip()
            ref = tasks_by_id.get(ref_id)
            if ref is None:
                raise ParseError(line_no, f"Unknown gantt dependency id: {ref_id}")
            start_day = ref[0] + ref[1]
        else:
            try:
                start_day = _parse_ymd_to_day(start_spec)
            except ValueError as err:
                raise ParseError(line_no, str(err)) from err

        tasks.append(
            GanttTask(
                section=current_section,
                name=name,
                start_day=start_day,
                duration_days=duration_days,
            )
        )
        tasks_by_id[task_id] = (start_day, duration_days)

    if not tasks:
        raise ParseError(1, "Gantt diagram requires at least one task")

    return GanttChart(title=title, tasks=tasks)


def render_gantt_diagram_to_svg(mermaid_source: str, _theme: MermaidTheme) -> str:
    """Render a mermaid ``gantt`` diagram into an SVG string (grok
    ``render_gantt_diagram_to_svg``).

    Parses the source, groups tasks by section, scales the day domain to a
    fixed-width canvas, and emits a six-block SVG: section background bands,
    a bottom date axis with daily grid ticks, one task bar per task, task
    labels (centered inside the bar or shifted outside when they overflow),
    left-side section labels, and an optional title.

    Args:
        mermaid_source: mermaid ``gantt`` source. The dispatch in
            :func:`render.render_mermaid_to_svg` passes the front-matter-
            stripped body (mirroring grok lib.rs L47, which shadows
            ``mermaid_source`` to ``parsed_source.body`` before the gantt arm).
        _theme: the resolved :class:`MermaidTheme` palette. Currently unused
            -- grok's gantt renderer hard-codes its own colors / strokes and
            ignores the theme, so the port matches with ``_theme``.

    Returns:
        The gantt-chart SVG string.

    Raises:
        ParseError: propagated from :func:`parse_gantt_diagram`.
    """
    chart = parse_gantt_diagram(mermaid_source)

    # Unique categories (sections) in first-appearance order (grok L38-L44).
    categories: list[str] = []
    for task in chart.tasks:
        cat = task.section if task.section is not None else ""
        if cat not in categories:
            categories.append(cat)

    # Per-category task counts (grok's BTreeMap is read by key only, never
    # iterated -- a plain dict is faithful; ordering comes from ``categories``).
    category_heights: dict[str, int] = {}
    for task in chart.tasks:
        cat = task.section if task.section is not None else ""
        category_heights[cat] = category_heights.get(cat, 0) + 1

    num_tasks = len(chart.tasks)
    gap = BAR_HEIGHT + BAR_GAP
    h = 2.0 * TOP_PADDING + num_tasks * gap

    # Time domain: earliest start .. latest end (start + duration). Tasks is
    # non-empty (parse guarantees >= 1), so min/max over generators is safe.
    min_day = min(t.start_day for t in chart.tasks)
    max_day = max(t.start_day + t.duration_days for t in chart.tasks)

    plot_width = _WIDTH - LEFT_PADDING - RIGHT_PADDING
    span_days = max(max_day - min_day, 1)
    px_per_day = plot_width / span_days

    svg: list[str] = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_fmt(_WIDTH)} {_fmt(h)}">'
    )

    # Embedded <style> matching Mermaid 11.12.2 ganttStyles (grok L78-L96).
    # f-string doubles each CSS brace ({{ -> {); the color / font interpolation
    # sites stay single-braced.
    svg.append(
        "<style>"
        f".section {{ stroke: none; opacity: 0.2; }}"
        f".section0 {{ fill: {SECTION_BKG_COLOR}; }}"
        f".section1 {{ fill: {ALT_SECTION_BKG_COLOR}; opacity: 0.2; }}"
        f".grid .tick line {{ stroke: {GRID_COLOR}; opacity: 0.8; shape-rendering: crispEdges; }}"
        f".grid .tick text {{ font-family: {FONT_FAMILY}; fill: #000; font-size: 10px; }}"
        f".grid path {{ stroke-width: 0; }}"
        f".task {{ stroke-width: 2; }}"
        f".task0 {{ fill: {TASK_BKG_COLOR}; stroke: {TASK_BORDER_COLOR}; }}"
        f".taskText {{ text-anchor: middle; font-family: {FONT_FAMILY}; }}"
        f".taskText0 {{ fill: {TASK_TEXT_COLOR}; }}"
        f".taskTextOutsideRight {{ fill: {TASK_TEXT_DARK_COLOR}; text-anchor: start; font-family: {FONT_FAMILY}; }}"
        f".taskTextOutsideLeft {{ fill: {TASK_TEXT_DARK_COLOR}; text-anchor: end; }}"
        f".titleText {{ text-anchor: middle; font-size: 18px; font-family: {FONT_FAMILY}; fill: {TITLE_COLOR}; }}"
        f".sectionTitle {{ text-anchor: start; font-family: {FONT_FAMILY}; font-size: {_fmt(SECTION_FONT_SIZE)}px; }}"
        f".sectionTitle0, .sectionTitle1 {{ fill: {TITLE_COLOR}; }}"
        "</style>"
    )

    # 1. Section background bands (grok L99-L115).
    task_idx = 0
    for cat_order, cat in enumerate(categories):
        count = category_heights.get(cat, 0)
        if count == 0:
            continue
        y = task_idx * gap + TOP_PADDING - 2.0
        rect_h = count * gap
        section_class = f"section section{cat_order % 2}"
        w_rect = _WIDTH - RIGHT_PADDING / 2.0
        svg.append(
            f'<rect x="0" y="{y:.1f}" width="{w_rect:.1f}" height="{rect_h:.1f}" '
            f'class="{section_class}"/>'
        )
        task_idx += count

    # 2. Bottom date axis + daily grid ticks (grok L117-L139).
    axis_y = h - BOTTOM_AXIS_HEIGHT
    svg.append(
        f'<g class="grid" transform="translate({_fmt(LEFT_PADDING)}, {_fmt(axis_y)})">'
    )
    total_days = max_day - min_day
    tick_top = -h + TOP_PADDING + GRID_LINE_START_PADDING
    for d in range(total_days + 1):
        x = d * px_per_day
        label = _day_to_ymd_str(min_day + d)
        svg.append(
            f'<g class="tick" transform="translate({x:.2f}, 0)">'
            f'<line y2="{tick_top:.1f}"/>'
            f'<text dy="1em" text-anchor="middle">{label}</text>'
            f"</g>"
        )
    svg.append("</g>")

    # 3. Task bars (grok L141-L158). sec_num = category position % 4.
    for i, task in enumerate(chart.tasks):
        x = (task.start_day - min_day) * px_per_day + LEFT_PADDING
        bar_w = task.duration_days * px_per_day
        y = i * gap + TOP_PADDING
        pos = (
            next(
                (idx for idx, c in enumerate(categories) if c == task.section),
                0,
            )
            if task.section is not None
            else 0
        )
        sec_num = pos % 4
        svg.append(
            f'<rect rx="{_fmt(RX)}" ry="{_fmt(RY)}" x="{x:.2f}" y="{y:.2f}" '
            f'width="{bar_w:.2f}" height="{_fmt(BAR_HEIGHT)}" '
            f'class="task task{sec_num}"/>'
        )

    # 4. Task text -- centered inside the bar, or shifted outside on overflow
    # (grok L160-L185). Text width is estimated by *byte* length (Rust
    # String::len counts bytes).
    for i, task in enumerate(chart.tasks):
        start_x = (task.start_day - min_day) * px_per_day
        end_x = start_x + task.duration_days * px_per_day
        bar_w = end_x - start_x
        text_width = len(task.name.encode("utf-8")) * FONT_SIZE * 0.6
        if text_width > bar_w:
            if end_x + text_width + 1.5 * LEFT_PADDING > _WIDTH - LEFT_PADDING:
                tx = start_x + LEFT_PADDING - 5.0
                text_class = "taskTextOutsideLeft"
            else:
                tx = end_x + LEFT_PADDING + 5.0
                text_class = "taskTextOutsideRight"
        else:
            tx = bar_w / 2.0 + start_x + LEFT_PADDING
            text_class = "taskText taskText0"
        ty = i * gap + BAR_HEIGHT / 2.0 + (FONT_SIZE / 2.0 - 2.0) + TOP_PADDING
        svg.append(
            f'<text x="{tx:.2f}" y="{ty:.2f}" font-size="{_fmt(FONT_SIZE)}" '
            f'class="{text_class}">{_escape_xml(task.name)}</text>'
        )

    # 5. Section labels (grok L187-L213) -- empty section names skipped.
    ordered_cats = [(c, category_heights.get(c, 0)) for c in categories]
    prev_total = 0
    for i, (cat_name, count) in enumerate(ordered_cats):
        if cat_name == "":
            prev_total += count
            continue
        if i > 0:
            y = (count * gap) / 2.0 + prev_total * gap + TOP_PADDING
        else:
            y = (count * gap) / 2.0 + TOP_PADDING
        sec_num = i % 4
        svg.append(
            f'<text x="10" y="{y:.2f}" font-size="{_fmt(SECTION_FONT_SIZE)}" '
            f'class="sectionTitle sectionTitle{sec_num}">{_escape_xml(cat_name)}</text>'
        )
        prev_total += count

    # 6. Title (grok L215-L222).
    if chart.title is not None:
        title_x = _WIDTH / 2.0
        svg.append(
            f'<text x="{title_x:.1f}" y="{_fmt(TITLE_TOP_MARGIN)}" '
            f'class="titleText">{_escape_xml(chart.title)}</text>'
        )

    svg.append("</svg>")
    return "".join(svg)
