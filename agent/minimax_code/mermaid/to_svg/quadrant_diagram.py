"""Quadrant-chart renderer -- functional clone of Grok Build's
``mermaid-to-svg/src/quadrant_diagram.rs``.

Direction (1) brick 19 (R288). The 10th per-diagram leaf + the 9th
self-contained SVG emitter migrated off the grok Rust source. Like the eight
per-diagram renderers before it (info R279, state R280, radar R281, pie R282,
packet R283, sankey R284, gantt R285, kanban R286, timeline R287), this leaf
parses its own DSL and emits a fixed-size SVG -- the dagre flowchart stack is
not involved.

Layout (mirrors grok ``render_quadrant_chart_to_svg`` L90-L377)
---------------------------------------------------------------

A quadrant chart is a 500x500 canvas split into four equal quadrants by an
internal vertical + horizontal divider. Each quadrant is filled with a
4-step gradient (lighter at the centre, darker at the corners for dark mode;
the inverse for light mode) and may carry a centered label. Up to two
axis-range pairs (``x-axis Low --> High`` / ``y-axis Low --> High``) frame
the chart, and a title can crown it. Data points (``Label: [x, y]`` with
x/y in ``[0.0, 1.0]``) plot as filled circles with a centered label below
each circle.

Axis placement is data-driven (grok L93): when at least one point is
present the x-axis sits at the bottom, otherwise at the top -- so an
empty-point chart (which the parser rejects) is never rendered, but an
axis-only chart flips the x-axis to the top. Quadrant labels sit at the top
of their cell when points are present (``dominant-baseline="hanging"``) and
dead-centre otherwise (``dominant-baseline="middle"``).

Theme awareness (the quadrant / sankey / kanban family)
-------------------------------------------------------

Unlike pie / packet / gantt / timeline (which hard-code mermaid 11.12.2's
default palette), quadrant IS theme-aware. :func:`quadrant_theme_for`
sniffs ``theme.background`` for a dark-mode hex prefix (``#1`` / ``#0``)
and selects one of two 10-field palettes -- dark greys on a dark canvas or
lavender tints on a light canvas -- then ``theme.background`` itself flows
into the full-canvas background ``<rect>``. The two ``MermaidTheme`` defaults
resolve correctly: ``dark().background == "#1e1e1e"`` (prefix ``#1`` -> dark)
and ``light().background == "#ffffff"`` (prefix ``#f`` -> light).

Float-formatting bridge -- the quadrant-specific divergence
-----------------------------------------------------------

This is the one renderer whose dynamic coordinates do NOT use Rust's
``Display`` (drop-the-``.0``) formatting. grok formats every coordinate with
``{:.1}`` (exactly one decimal place: ``19.0``, ``247.5``, ``250.0``), so
this clone carries a second formatter:

* :func:`_fmt1` -- ``f"{v:.1f}"`` for every dynamic coordinate (quadrant
  rects, labels, border lines, axis labels, title x/y, point cx/cy/ty).
* :func:`_fmt` -- the shared ``Display`` bridge (``5.0`` -> ``"5"``) for
  scalar knobs (canvas size, font-size, point radius, stroke-width).

Keeping the two apart is what makes the emitted SVG byte-stable against
grok; conflating them would shift ``"250.0"`` to ``"250"`` and corrupt the
half-pixel placement of the internal dividers.

Public surface (1 symbol): :func:`render_quadrant_chart_to_svg`. The barrel
(:mod:`minimax_code.mermaid.to_svg`) does NOT re-export per-diagram
renderers; the symbol reaches callers only through the dispatch arm in
:mod:`minimax_code.mermaid.to_svg.render`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_quadrant_chart_to_svg"]


# === layout constants (grok quadrant_diagram.rs L57-L74) ====================
# 16 f64 canvas / typography knobs + 1 font-family string. All kept as floats
# so the arithmetic below mirrors grok's f64 math bit-for-bit (no implicit int
# promotion), then formatted via _fmt (Display, drop .0) when emitted as a
# scalar attribute.

CHART_WIDTH: float = 500.0
CHART_HEIGHT: float = 500.0
TITLE_FONT_SIZE: float = 20.0
TITLE_PADDING: float = 10.0
QUADRANT_PADDING: float = 5.0
X_AXIS_LABEL_PADDING: float = 5.0
Y_AXIS_LABEL_PADDING: float = 5.0
X_AXIS_LABEL_FONT_SIZE: float = 16.0
Y_AXIS_LABEL_FONT_SIZE: float = 16.0
QUADRANT_LABEL_FONT_SIZE: float = 16.0
QUADRANT_TEXT_TOP_PADDING: float = 5.0
POINT_TEXT_PADDING: float = 5.0
POINT_LABEL_FONT_SIZE: float = 12.0
POINT_RADIUS: float = 5.0
INTERNAL_BORDER_STROKE_WIDTH: float = 1.0
EXTERNAL_BORDER_STROKE_WIDTH: float = 2.0

#: Shared mermaid font stack (grok L74).
FONT_FAMILY: str = "trebuchet ms,verdana,arial,sans-serif"

#: Strict Rust f64-literal grammar (shared with sankey/packet). Rejects the
#: underscores (``1_000``), inf, and nan that Python's ``float()`` would
#: otherwise accept -- mirrors ``str::parse::<f64>()`` rejection semantics.
_F64_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


# === theme palette (grok QuadrantTheme + quadrant_theme_for L8-L54) =========


@dataclass(frozen=True)
class QuadrantTheme:
    """The 10-field quadrant palette (grok ``QuadrantTheme`` L8-L19).

    Four quadrant fills (lightest at the focus corner, deepening outward), a
    single border stroke for both internal and external dividers, and five
    text fills (title / axis / point dot / point label / quadrant label).
    """

    quadrant1_fill: str
    quadrant2_fill: str
    quadrant3_fill: str
    quadrant4_fill: str
    border_stroke: str
    title_fill: str
    axis_text_fill: str
    point_fill: str
    point_text_fill: str
    quadrant_text_fill: str


def quadrant_theme_for(theme: MermaidTheme) -> QuadrantTheme:
    """Resolve the 10-field palette for ``theme`` (grok ``quadrant_theme_for`` L21-L54).

    Dark-mode detection is grok's background-prefix sniff: a hex starting with
    ``#1`` or ``#0`` selects the dark palette, anything else the light one.
    Both ``MermaidTheme`` defaults resolve correctly (dark ``#1e1e1e`` ->
    prefix ``#1`` -> dark; light ``#ffffff`` -> prefix ``#f`` -> light).
    """
    background = theme.background
    is_dark = background.startswith("#1") or background.startswith("#0")
    if is_dark:
        return QuadrantTheme(
            quadrant1_fill="#1f2020",
            quadrant2_fill="#242525",
            quadrant3_fill="#292a2a",
            quadrant4_fill="#2e2f2f",
            border_stroke="#e0dfdf",
            title_fill="#ccc",
            axis_text_fill="#ccc",
            point_fill="#ccc",
            point_text_fill="#ccc",
            quadrant_text_fill="#ccc",
        )
    return QuadrantTheme(
        quadrant1_fill="#ECECFF",
        quadrant2_fill="#F1F1FF",
        quadrant3_fill="#F6F6FF",
        quadrant4_fill="#FBFBFF",
        border_stroke="#C7C7F1",
        title_fill="#333333",
        axis_text_fill="#333333",
        point_fill="#333333",
        point_text_fill="#333333",
        quadrant_text_fill="#333333",
    )


# === parsed model (grok QuadrantChart / QuadrantPoint L380-L394) ============


@dataclass(frozen=True)
class QuadrantPoint:
    """A plotted data point (grok ``QuadrantPoint`` L389-L394)."""

    label: str
    x: float
    y: float


@dataclass(frozen=True)
class QuadrantChart:
    """The parsed chart (grok ``QuadrantChart`` L380-L387).

    ``quadrants`` is keyed 1-4 (any subset may be labelled); ``x_axis`` /
    ``y_axis`` carry the ``(low, high)`` range pair when declared; ``title``
    is ``None`` until a ``title`` line sets it.
    """

    title: str | None
    x_axis: tuple[str, str] | None
    y_axis: tuple[str, str] | None
    quadrants: dict[int, str]
    points: tuple[QuadrantPoint, ...]


# === scalar formatting bridges (the quadrant-specific split) ===============


def _fmt(value: float) -> str:
    """Format a scalar with Rust ``Display`` semantics (drop the ``.0``).

    Used for canvas size, font-size, point radius, stroke-width -- every
    scalar knob that grok emits with ``{}`` rather than ``{:.1}``. Integer
    floats render bare (``5.0`` -> ``"5"``); non-integers fall back to repr.
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _fmt1(value: float) -> str:
    """Format a coordinate with Rust ``{:.1}`` semantics (exactly one decimal).

    Used for every dynamic coordinate -- the quadrant-specific divergence from
    the other eight renderers. ``247.5`` -> ``"247.5"``, ``250.0`` ->
    ``"250.0"``, ``19.0`` -> ``"19.0"``. The fixed precision is what keeps the
    internal dividers and point dots at their half-pixel grok positions.
    """
    return f"{value:.1f}"


def _escape_xml(s: str) -> str:
    """Escape XML special characters (grok ``escape_xml`` L534-L540).

    The ``&apos;`` named-entity variant -- shared with pie / packet / gantt /
    timeline, distinct from sankey / radar's numeric ``&#39;``.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _parse_f64(raw: str, line: int, original: str) -> float:
    """Parse a point coordinate with strict Rust f64 grammar.

    Rejects the inf / nan / underscore literals that ``float()`` would
    otherwise accept (mirrors ``str::parse::<f64>()``). On rejection the
    ParseError mirrors grok's per-line ``"Invalid quadrant point: {line}"``
    with the trimmed source line.
    """
    if _F64_RE.fullmatch(raw):
        return float(raw)
    raise ParseError(line, f"Invalid quadrant point: {original}")


def _parse_axis(s: str, line_no: int) -> tuple[str, str]:
    """Parse an ``x-axis`` / ``y-axis`` range pair (grok ``parse_axis`` L524-L532).

    Splits on the first ``-->`` and trims both ends; a missing arrow is the
    one axis-shaped parse failure, reported with the verbatim axis text.
    """
    if "-->" not in s:
        raise ParseError(line_no, f"Invalid axis: {s}")
    a, b = s.split("-->", 1)
    return (a.strip(), b.strip())


# === parser (grok parse_quadrant_chart L396-L522) ===========================


def parse_quadrant_chart(input: str) -> QuadrantChart:
    """Parse mermaid quadrant-chart source into a :class:`QuadrantChart`.

    Mirrors grok ``parse_quadrant_chart`` L396-L522. The first non-blank /
    non-``%%`` line's first token must be ``quadrantChart`` (the header
    scan); after that each trimmed line is classified by prefix:

    * ``title <text>`` -- sets the title (empty text is a no-op).
    * ``x-axis <rest>`` / ``y-axis <rest>`` -- an axis range pair.
    * ``quadrant-N <label>`` -- labels quadrant ``N`` (1-4).
    * otherwise -- a point ``Label: [x, y]``.

    Points require at least one entry (grok's final guard, reported at line 1
    so callers see a stable error regardless of where the body ran out).
    """
    lines = input.splitlines()
    i = 0

    # Header scan (grok L399-L416): skip blanks/comments, require the first
    # real token to be the diagram declaration. A non-matching first line is
    # reported at its own 1-based line number.
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        if line.split()[0] == "quadrantChart":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'quadrantChart' declaration")

    title: str | None = None
    x_axis: tuple[str, str] | None = None
    y_axis: tuple[str, str] | None = None
    quadrants: dict[int, str] = {}
    points: list[QuadrantPoint] = []

    while i < len(lines):
        line = lines[i].strip()
        line_no = i + 1
        i += 1

        if not line or line.startswith("%%"):
            continue

        # title: empty text leaves the title unset (grok treats it as a no-op
        # rather than clearing an existing title).
        if line.startswith("title "):
            title_text = line[len("title ") :].strip()
            if title_text:
                title = title_text
            continue

        if line.startswith("x-axis "):
            x_axis = _parse_axis(line[len("x-axis ") :].strip(), line_no)
            continue

        if line.startswith("y-axis "):
            y_axis = _parse_axis(line[len("y-axis ") :].strip(), line_no)
            continue

        # quadrant-N label: a missing space or a non-integer N is the same
        # "Invalid quadrant label" failure shape, reported with the full line.
        if line.startswith("quadrant-"):
            rest = line[len("quadrant-") :]
            if " " not in rest:
                raise ParseError(line_no, f"Invalid quadrant label: {line}")
            n_str, label = rest.split(" ", 1)
            try:
                n = int(n_str.strip())
            except ValueError:
                # ``from None`` mirrors grok's ``map_err(|_| ...)``: the
                # ValueError is discarded rather than chained into the ParseError.
                raise ParseError(line_no, f"Invalid quadrant label: {line}") from None
            quadrants[n] = label.strip()
            continue

        # Point: "Label: [x, y]". Every malformed shape (missing colon,
        # missing brackets, wrong arity, non-numeric coord) collapses to the
        # single "Invalid quadrant point" message with the full line.
        if ":" not in line:
            raise ParseError(line_no, f"Invalid quadrant point: {line}")
        label_raw, coords_raw = line.split(":", 1)
        label = label_raw.strip()
        if len(label) >= 2 and label.startswith('"') and label.endswith('"'):
            label = label[1:-1]

        coords = coords_raw.strip()
        if not (coords.startswith("[") and coords.endswith("]")):
            raise ParseError(line_no, f"Invalid quadrant point: {line}")
        coords = coords[1:-1]

        parts = [p.strip() for p in coords.split(",")]
        if len(parts) != 2:
            raise ParseError(line_no, f"Invalid quadrant point: {line}")

        x = _parse_f64(parts[0], line_no, line)
        y = _parse_f64(parts[1], line_no, line)
        points.append(QuadrantPoint(label, x, y))

    if not points:
        raise ParseError(1, "Quadrant chart requires at least one point")

    return QuadrantChart(title, x_axis, y_axis, quadrants, tuple(points))


# === renderer (grok render_quadrant_chart_to_svg L90-L377) =================


def render_quadrant_chart_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render a quadrant chart into an SVG string (grok L90-L377).

    Parses ``mermaid_source``, resolves the theme-aware palette, computes the
    data-driven layout (x-axis on the bottom when points exist, otherwise the
    top), and emits the SVG in grok's element order: canvas -> background
    rect -> four quadrant fills -> four quadrant labels -> external border
    frame -> internal vertical + horizontal dividers -> x-axis labels ->
    y-axis labels (rotated -90deg) -> title -> data points. Coordinate
    attributes use :func:`_fmt1` (one decimal place); scalar knobs use
    :func:`_fmt` (Display).

    Raises:
        ParseError: when the source is not a valid quadrant chart (propagated
            from :func:`parse_quadrant_chart`).
    """
    chart = parse_quadrant_chart(mermaid_source)
    qt = quadrant_theme_for(theme)

    has_points = len(chart.points) > 0
    show_title = chart.title is not None
    show_x_axis = chart.x_axis is not None
    show_y_axis = chart.y_axis is not None

    # Axis side is data-driven (grok L93): points present -> x-axis at the
    # bottom; otherwise at the top. With points (the only configuration the
    # parser lets through) this is always the bottom, but the branch is kept
    # to mirror grok's layout math for the axis-only degenerate case.
    x_axis_bottom = has_points

    x_axis_space = (
        X_AXIS_LABEL_PADDING * 2.0 + X_AXIS_LABEL_FONT_SIZE if show_x_axis else 0.0
    )
    y_axis_space_left = (
        Y_AXIS_LABEL_PADDING * 2.0 + Y_AXIS_LABEL_FONT_SIZE if show_y_axis else 0.0
    )
    title_space_top = TITLE_FONT_SIZE + TITLE_PADDING * 2.0 if show_title else 0.0

    x_axis_top = 0.0 if x_axis_bottom else x_axis_space
    x_axis_bot = x_axis_space if x_axis_bottom else 0.0

    quadrant_left = QUADRANT_PADDING + y_axis_space_left
    quadrant_top = QUADRANT_PADDING + x_axis_top + title_space_top
    quadrant_width = CHART_WIDTH - QUADRANT_PADDING * 2.0 - y_axis_space_left
    quadrant_height = (
        CHART_HEIGHT - QUADRANT_PADDING * 2.0 - x_axis_top - x_axis_bot - title_space_top
    )
    half_w = quadrant_width / 2.0
    half_h = quadrant_height / 2.0
    half_ext = EXTERNAL_BORDER_STROKE_WIDTH / 2.0

    svg: list[str] = []

    # (1) canvas -- scalar size via Display ("500 500").
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_fmt(CHART_WIDTH)} '
        f'{_fmt(CHART_HEIGHT)}">'
    )

    # (2) full-canvas background -- theme.background flows in (theme-aware).
    svg.append(
        f'<rect x="0" y="0" width="{_fmt(CHART_WIDTH)}" height="{_fmt(CHART_HEIGHT)}" '
        f'fill="{theme.background}"/>'
    )

    # (3) four quadrant fills. Q1 = top-right, Q2 = top-left, Q3 = bottom-left,
    # Q4 = bottom-right (grok L133-L147). All coords via {:.1}.
    quadrant_rects = (
        (quadrant_left + half_w, quadrant_top, qt.quadrant1_fill),
        (quadrant_left, quadrant_top, qt.quadrant2_fill),
        (quadrant_left, quadrant_top + half_h, qt.quadrant3_fill),
        (quadrant_left + half_w, quadrant_top + half_h, qt.quadrant4_fill),
    )
    for rx, ry, fill in quadrant_rects:
        svg.append(
            f'<rect x="{_fmt1(rx)}" y="{_fmt1(ry)}" width="{_fmt1(half_w)}" '
            f'height="{_fmt1(half_h)}" fill="{fill}"/>'
        )

    # (4) quadrant labels (only when the text is non-empty). Anchor at the
    # horizontal centre of the cell; vertically, points present -> hang off
    # the top of the cell, otherwise centre (grok L166-L189).
    quadrant_labels = (
        (chart.quadrants.get(1, ""), quadrant_left + half_w + half_w / 2.0, quadrant_top),
        (chart.quadrants.get(2, ""), quadrant_left + half_w / 2.0, quadrant_top),
        (chart.quadrants.get(3, ""), quadrant_left + half_w / 2.0, quadrant_top + half_h),
        (
            chart.quadrants.get(4, ""),
            quadrant_left + half_w + half_w / 2.0,
            quadrant_top + half_h,
        ),
    )
    for text, tx, ty_base in quadrant_labels:
        if not text:
            continue
        if has_points:
            ty = ty_base + QUADRANT_TEXT_TOP_PADDING
            dominant_baseline = "hanging"
        else:
            ty = ty_base + half_h / 2.0
            dominant_baseline = "middle"
        svg.append(
            f'<text x="{_fmt1(tx)}" y="{_fmt1(ty)}" text-anchor="middle" '
            f'dominant-baseline="{dominant_baseline}" font-family="{FONT_FAMILY}" '
            f'font-size="{_fmt(QUADRANT_LABEL_FONT_SIZE)}" '
            f'fill="{qt.quadrant_text_fill}">{_escape_xml(text)}</text>'
        )

    # (5) external border frame -- four segments with a half-stroke inset so
    # the stroke sits centred on the quadrant edge (grok L192-L209).
    ext_lines = (
        (
            quadrant_left - half_ext,
            quadrant_top,
            quadrant_left + quadrant_width + half_ext,
            quadrant_top,
        ),
        (
            quadrant_left + quadrant_width,
            quadrant_top + half_ext,
            quadrant_left + quadrant_width,
            quadrant_top + quadrant_height - half_ext,
        ),
        (
            quadrant_left - half_ext,
            quadrant_top + quadrant_height,
            quadrant_left + quadrant_width + half_ext,
            quadrant_top + quadrant_height,
        ),
        (
            quadrant_left,
            quadrant_top + half_ext,
            quadrant_left,
            quadrant_top + quadrant_height - half_ext,
        ),
    )
    for x1, y1, x2, y2 in ext_lines:
        svg.append(
            f'<line x1="{_fmt1(x1)}" y1="{_fmt1(y1)}" x2="{_fmt1(x2)}" '
            f'y2="{_fmt1(y2)}" stroke="{qt.border_stroke}" '
            f'stroke-width="{_fmt(EXTERNAL_BORDER_STROKE_WIDTH)}"/>'
        )

    # (6) internal vertical divider (grok L213-L219).
    svg.append(
        f'<line x1="{_fmt1(quadrant_left + half_w)}" y1="{_fmt1(quadrant_top + half_ext)}" '
        f'x2="{_fmt1(quadrant_left + half_w)}" '
        f'y2="{_fmt1(quadrant_top + quadrant_height - half_ext)}" '
        f'stroke="{qt.border_stroke}" stroke-width="{_fmt(INTERNAL_BORDER_STROKE_WIDTH)}"/>'
    )

    # (7) internal horizontal divider (grok L223-L229).
    svg.append(
        f'<line x1="{_fmt1(quadrant_left + half_ext)}" y1="{_fmt1(quadrant_top + half_h)}" '
        f'x2="{_fmt1(quadrant_left + quadrant_width - half_ext)}" '
        f'y2="{_fmt1(quadrant_top + half_h)}" stroke="{qt.border_stroke}" '
        f'stroke-width="{_fmt(INTERNAL_BORDER_STROKE_WIDTH)}"/>'
    )

    # (8) x-axis labels (grok L244-L279). When the high end is non-empty both
    # labels centre over their cell; otherwise the low label left-aligns to
    # the quadrant's left edge.
    if chart.x_axis is not None:
        low, high = chart.x_axis
        draw_x_labels_in_middle = high != ""
        if x_axis_bottom:
            x_axis_y = X_AXIS_LABEL_PADDING + quadrant_top + quadrant_height + QUADRANT_PADDING
        else:
            x_axis_y = X_AXIS_LABEL_PADDING + title_space_top
        low_x = quadrant_left + (half_w / 2.0 if draw_x_labels_in_middle else 0.0)
        text_anchor_low = "middle" if draw_x_labels_in_middle else "start"
        svg.append(
            f'<text x="{_fmt1(low_x)}" y="{_fmt1(x_axis_y)}" '
            f'text-anchor="{text_anchor_low}" dominant-baseline="hanging" '
            f'font-family="{FONT_FAMILY}" font-size="{_fmt(X_AXIS_LABEL_FONT_SIZE)}" '
            f'fill="{qt.axis_text_fill}">{_escape_xml(low)}</text>'
        )
        if high:
            high_x = quadrant_left + half_w + (
                half_w / 2.0 if draw_x_labels_in_middle else 0.0
            )
            svg.append(
                f'<text x="{_fmt1(high_x)}" y="{_fmt1(x_axis_y)}" text-anchor="middle" '
                f'dominant-baseline="hanging" font-family="{FONT_FAMILY}" '
                f'font-size="{_fmt(X_AXIS_LABEL_FONT_SIZE)}" '
                f'fill="{qt.axis_text_fill}">{_escape_xml(high)}</text>'
            )

    # (9) y-axis labels (grok L291-L331). Rotated -90deg via a translate then
    # rotate transform; the low end anchors to the bottom of the quadrant,
    # the high end to the midline when both are present.
    if chart.y_axis is not None:
        low, high = chart.y_axis
        draw_y_labels_in_middle = high != ""
        y_axis_x = Y_AXIS_LABEL_PADDING
        low_y = quadrant_top + quadrant_height - (
            half_h / 2.0 if draw_y_labels_in_middle else 0.0
        )
        svg.append(
            f'<text x="0" y="0" text-anchor="middle" dominant-baseline="hanging" '
            f'font-family="{FONT_FAMILY}" font-size="{_fmt(Y_AXIS_LABEL_FONT_SIZE)}" '
            f'fill="{qt.axis_text_fill}" transform="translate({_fmt1(y_axis_x)}, '
            f'{_fmt1(low_y)}) rotate(-90)">{_escape_xml(low)}</text>'
        )
        if high:
            high_y = quadrant_top + half_h - (
                half_h / 2.0 if draw_y_labels_in_middle else 0.0
            )
            svg.append(
                f'<text x="0" y="0" text-anchor="middle" dominant-baseline="hanging" '
                f'font-family="{FONT_FAMILY}" font-size="{_fmt(Y_AXIS_LABEL_FONT_SIZE)}" '
                f'fill="{qt.axis_text_fill}" transform="translate({_fmt1(y_axis_x)}, '
                f'{_fmt1(high_y)}) rotate(-90)">{_escape_xml(high)}</text>'
            )

    # (10) title -- centred at the top; x/y use {:.1} (so "250.0" / "10.0"),
    # font-size uses Display ("20") (grok L344-L352).
    if chart.title is not None:
        svg.append(
            f'<text x="{_fmt1(CHART_WIDTH / 2.0)}" y="{_fmt1(TITLE_PADDING)}" '
            f'text-anchor="middle" dominant-baseline="hanging" '
            f'font-family="{FONT_FAMILY}" font-size="{_fmt(TITLE_FONT_SIZE)}" '
            f'fill="{qt.title_fill}">{_escape_xml(chart.title)}</text>'
        )

    # (11) data points (grok L356-L373). x/y are clamped to [0, 1] before
    # scaling; y is flipped (SVG origin is top-left). Each dot is a filled
    # circle with a centred label translated to sit just below the dot.
    for point in chart.points:
        px = quadrant_left + max(0.0, min(1.0, point.x)) * quadrant_width
        py = quadrant_top + (1.0 - max(0.0, min(1.0, point.y))) * quadrant_height
        svg.append(
            f'<circle cx="{_fmt1(px)}" cy="{_fmt1(py)}" r="{_fmt(POINT_RADIUS)}" '
            f'fill="{qt.point_fill}" stroke="{qt.point_fill}" stroke-width="0"/>'
        )
        ty = py + POINT_TEXT_PADDING
        svg.append(
            f'<text x="0" y="0" text-anchor="middle" dominant-baseline="hanging" '
            f'font-family="{FONT_FAMILY}" font-size="{_fmt(POINT_LABEL_FONT_SIZE)}" '
            f'fill="{qt.point_text_fill}" transform="translate({_fmt1(px)}, '
            f'{_fmt1(ty)})">{_escape_xml(point.label)}</text>'
        )

    svg.append("</svg>")
    return "".join(svg)
