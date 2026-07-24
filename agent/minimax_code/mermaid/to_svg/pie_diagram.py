"""Pie diagram renderer -- behavioral-equivalent port of grok's ``pie_diagram.rs``.

Direction (1) brick 15 (R282). The fourth per-diagram leaf and the third
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``
and R281's ``radar``; unlike R280's ``stateDiagram`` parser which rides the
dagre stack). The ``pie`` diagram is mermaid's pie / donut chart: N labeled
slices swept clockwise from 12 o'clock, each a colored wedge with an inline
``pct%`` label, plus a right-side legend listing every slice. Fixed
``450``-tall canvas whose *width* grows with the longest legend label. Pure
polar geometry + d3-pie value sort, so this leaf emits SVG directly from
the parsed model -- no AST, no dagre.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_pie_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_pie_diagram_to_svg(src, theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to
  "return the SVG / raise :class:`ParseError`".
* grok's two private structs ``PieChart`` / ``PieSlice`` ->
  :class:`PieChart` / :class:`PieSlice` as ``frozen`` dataclasses (grok's
  are plain ``struct``s; frozen mirrors their construct-once use).
* grok ``parse_pie_diagram`` / ``polar`` / ``escape_xml`` -> module-level
  Python functions (``parse_pie_diagram`` public for direct testing; the
  other two private with a leading underscore).
* grok ``MERMAID_PIE_COLORS`` (12 mixed hex/hsl literals) -> module tuple
  verbatim. The palette is mermaid 11.12.2's default pie series colors
  (pie1=primaryColor, pie2=secondaryColor, pie3-12 derived via
  adjust/darken on primary/secondary/tertiary).
* d3-pie() semantics: slices filtered to >=1% of total, sorted descending
  by value (the wedges); the legend lists every slice in original order.
  Colors cycle through the palette by *sorted-slice* index
  (d3.scaleOrdinal), and the legend looks its label up in that map.
* grok's ``showData`` header token toggles a ``[value]`` suffix on the
  legend labels (and on the width-estimating longest-label probe).

Float formatting bridge
-----------------------

Like R281's radar, integer-valued floats (radii, dimensions, legend
geometry) route through :func:`_fmt`, which collapses ``450.0`` ->
``"450"`` (Rust ``Display`` drops the trailing ``.0``; Python ``str``
keeps it). The fixed-precision sites (``:.4`` / ``:.3`` / ``:.0``) map to
Python ``:.4f`` / ``:.3f`` / ``:.0f`` directly. The percentage wedge uses
``int(math.floor(x + 0.5))`` to mirror Rust ``f64::round`` (round-half-
away-from-zero) -- Python's built-in ``round`` is banker's rounding and
would disagree on ``x.5`` boundaries.

Byte-length legend estimate
---------------------------

grok measures the longest legend label with ``format!(...).len()``, and
Rust ``String::len`` is a *byte* count. The port mirrors that with
``len(text.encode("utf-8"))`` so a multi-byte label produces the same
canvas-width estimate grok computes (diverges from Python ``len`` only
for non-ASCII labels -- mermaid pie labels are usually ASCII).

Dispatch wiring (R282)
----------------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer, mirroring grok lib.rs
L47 (``let mermaid_source = parsed_source.body.as_ref();`` shadows the
incoming source with the body before any per-diagram arm runs). R282
also lifts the prior info/radar arms to the same ``body`` contract --
they had been passing the raw source, a latent port-unfaithfulness that
only bites sources carrying a ``---`` front-matter block.

Public surface (1 symbol): :func:`render_pie_diagram_to_svg`. The
dataclasses, parser, and helpers stay out of ``__all__``; the renderer is
reached only through the dispatch arm added in R282.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_pie_diagram_to_svg"]

#: Mermaid 11.12.2 default pie chart colors (grok L9-L22). pie1 = primaryColor
#: (#ECECFF), pie2 = secondaryColor (#ffffde), pie3-pie12 derived via
#: adjust/darken on primary/secondary/tertiary. Verbatim mixed hex/hsl tuple.
MERMAID_PIE_COLORS: tuple[str, ...] = (
    "#ECECFF",  # pie1  - primaryColor
    "#ffffde",  # pie2  - secondaryColor
    "hsl(80, 100%, 56.2745098039%)",  # pie3  - adjust(tertiaryColor, l:-40)
    "hsl(240, 60%, 86.2745098039%)",  # pie4  - adjust(primaryColor, l:-10)
    "hsl(120, 100%, 66.2745098039%)",  # pie5  - adjust(secondaryColor, l:-30)
    "hsl(80, 100%, 76.2745098039%)",  # pie6  - adjust(tertiaryColor, l:-20)
    "hsl(300, 60%, 76.2745098039%)",  # pie7  - adjust(primaryColor, h:60, l:-20)
    "hsl(180, 60%, 56.2745098039%)",  # pie8  - adjust(primaryColor, h:-60, l:-40)
    "hsl(0, 60%, 56.2745098039%)",  # pie9  - adjust(primaryColor, h:120, l:-40)
    "hsl(300, 60%, 56.2745098039%)",  # pie10 - adjust(primaryColor, h:60, l:-40)
    "hsl(150, 60%, 56.2745098039%)",  # pie11 - adjust(primaryColor, h:-90, l:-40)
    "hsl(0, 60%, 66.2745098039%)",  # pie12 - adjust(primaryColor, h:120, l:-30)
)

#: Mermaid 11.12.2 pie chart canvas / geometry constants (grok L25-L33,
#: pieRenderer.ts + default config). Carried as ``float`` because the SVG
#: viewBox / radii are emitted via Rust ``Display`` (drops ``.0``);
#: :func:`_fmt` reproduces that rendering.
PIE_HEIGHT: float = 450.0
PIE_WIDTH: float = 450.0
MARGIN: float = 40.0
#: Wedge radius = half the canvas minus the margin (grok L28). 185.0.
RADIUS: float = (PIE_WIDTH / 2.0) - MARGIN
OUTER_STROKE_WIDTH: float = 2.0
#: Outer ring radius = wedge radius + half the stroke (grok L30). 186.0.
OUTER_RADIUS: float = RADIUS + OUTER_STROKE_WIDTH / 2.0
#: Inline ``pct%`` label sits at this fraction of the wedge radius (grok L31).
TEXT_POSITION: float = 0.75
LEGEND_RECT_SIZE: float = 18.0
LEGEND_SPACING: float = 4.0

#: The font-family stack mermaid pins for pie text (grok L35).
FONT_FAMILY: str = "trebuchet ms,verdana,arial,sans-serif"


@dataclass(frozen=True)
class PieSlice:
    """One pie wedge (grok ``PieSlice``).

    ``label`` is the legend key; ``value`` is the raw magnitude, normalized
    to a fraction of the total at render time.
    """

    label: str
    value: float


@dataclass(frozen=True)
class PieChart:
    """Parsed pie model (grok ``PieChart``).

    ``title`` is optional (mermaid ``title <text>`` line); ``show_data``
    toggles the ``[value]`` legend suffix (the ``showData`` header token);
    ``slices`` is the original-order list (the d3-pie descending sort runs
    at render, not parse).
    """

    title: str | None
    show_data: bool
    slices: list[PieSlice]


def _fmt(value: float) -> str:
    """Render a float the way Rust ``Display`` would (R281 radar bridge).

    Integer-valued floats drop the trailing ``.0`` (``450.0`` -> ``"450"``);
    non-integers fall back to ``repr`` (shortest round-trippable decimal,
    matching Rust's ``Display`` for f64).
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note: grok's ``pie_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named
    entity); R281's radar maps ``'`` -> ``&#39;`` (numeric). Both faithfully
    mirror their respective grok source -- the two Rust functions disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _polar(cx: float, cy: float, r: float, angle: float) -> tuple[float, float]:
    """Convert polar (center + radius + angle) to cartesian (grok ``polar``)."""
    return (cx + r * math.cos(angle), cy + r * math.sin(angle))


def parse_pie_diagram(input: str) -> PieChart:
    """Parse mermaid ``pie`` source into a :class:`PieChart` (grok ``parse_pie_diagram``).

    Scans for the ``pie`` header token (optionally followed by ``showData``),
    then reads ``title <text>`` and ``"<label>" : <value>`` / ``<label> :
    <value>`` slice lines. Blank / ``%%`` lines are skipped at both stages.

    Raises:
        ParseError: when the first substantial token is not ``pie``; a slice
            line has no ``:`` separator; a slice value is not a float; or the
            diagram has zero slices.
    """
    lines = input.splitlines()

    # Header scan: find the first substantial line; it must lead with ``pie``.
    i = 0
    show_data = False
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        tokens = line.split()
        first = tokens[0] if tokens else ""
        if first == "pie":
            # grok: tokens.any(|t| t == "showData") over the *remaining* tokens;
            # checking the full list is equivalent since first is "pie".
            show_data = "showData" in tokens
            i += 1
            break
        raise ParseError(i + 1, "Expected 'pie' declaration")

    title: str | None = None
    slices: list[PieSlice] = []

    # Body scan.
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

        # Otherwise the line must be a ``label : value`` slice.
        label_raw, sep, value_raw = line.partition(":")
        if not sep:
            raise ParseError(line_no, f"Invalid pie slice: {line}")

        label = label_raw.strip()
        # Strip a surrounding pair of double-quotes (grok strip_prefix('"')
        # .and_then(strip_suffix('"')) -- only when BOTH ends are quoted).
        if len(label) >= 2 and label.startswith('"') and label.endswith('"'):
            label = label[1:-1]

        value_str = value_raw.strip()
        try:
            value = float(value_str)
        except ValueError as err:
            raise ParseError(line_no, f"Invalid pie value: {value_str}") from err

        slices.append(PieSlice(label=label, value=value))

    if not slices:
        raise ParseError(1, "Pie diagram requires at least one slice")

    return PieChart(title=title, show_data=show_data, slices=slices)


def render_pie_diagram_to_svg(mermaid_source: str, _theme: MermaidTheme) -> str:
    """Render a mermaid ``pie`` diagram into an SVG string (grok
    ``render_pie_diagram_to_svg``).

    Parses the source, filters slices to >=1% of the total, sorts them
    descending by value (d3.pie() default), and emits a fixed-height SVG:
    an outer ring, one colored wedge per surviving slice with an inline
    ``pct%`` label, an optional title above the pie, and a right-side
    legend listing every slice (original order). The canvas width grows
    with the longest legend label.

    Args:
        mermaid_source: mermaid ``pie`` source. The dispatch in
            :func:`render.render_mermaid_to_svg` passes the front-matter-
            stripped body (mirroring grok lib.rs L47, which shadows
            ``mermaid_source`` to ``parsed_source.body`` before the pie arm).
        _theme: the resolved :class:`MermaidTheme` palette. Currently unused
            -- grok's pie renderer hard-codes its own colors / ``black``
            strokes and ignores the theme, so the port matches with ``_theme``.

    Returns:
        The pie-chart SVG string.

    Raises:
        ParseError: propagated from :func:`parse_pie_diagram`, or when the
            slice-value total is not positive.
    """
    chart = parse_pie_diagram(mermaid_source)

    total = sum(s.value for s in chart.slices)
    if total <= 0.0:
        raise ParseError(1, "Pie diagram total must be > 0")

    # Slices >=1% of total, sorted descending by value (d3.pie() default).
    slices = sorted(
        (s for s in chart.slices if s.value / total * 100.0 >= 1.0),
        key=lambda s: s.value,
        reverse=True,
    )

    # All slices for the legend (unfiltered, original order).
    all_slices = list(chart.slices)

    # Pie center in the translated group coordinate system.
    cx = PIE_WIDTH / 2.0
    cy = PIE_HEIGHT / 2.0

    # Estimate legend text width (grok L67-L78): 10px per *byte* at 17px font.
    # Rust String::len is a byte count -> encode("utf-8") mirrors it.
    def _label_bytes(s: PieSlice) -> int:
        text = f"{s.label} [{_fmt(s.value)}]" if chart.show_data else s.label
        return len(text.encode("utf-8"))

    longest_label_len = max((_label_bytes(s) for s in all_slices), default=0)
    legend_text_width = longest_label_len * 10.0
    total_width = PIE_WIDTH + MARGIN + LEGEND_RECT_SIZE + LEGEND_SPACING + legend_text_width

    # d3.pie() default: startAngle=0 (12 o'clock), endAngle=2*pi, clockwise.
    # In SVG (y-down) translated to center, angle 0 points up (-y) -> start
    # at -pi/2 and sweep clockwise (increasing angle).
    angle = -math.pi / 2.0

    svg: list[str] = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_width:.4f} {_fmt(PIE_HEIGHT)}" '
        f'style="max-width: {total_width:.3f}px; background-color: white;" '
        f'role="graphics-document document" aria-roledescription="pie">'
    )

    # Inline CSS matching Mermaid 11.12.2 pieStyles (grok L91-L99). f-string
    # doubles each CSS brace ({{ -> {); the {FONT_FAMILY} interpolation sites
    # stay single-braced.
    svg.append(
        f"<style>"
        f".pieCircle{{stroke:black;stroke-width:2px;opacity:0.7;}}"
        f".pieOuterCircle{{stroke:black;stroke-width:2px;fill:none;}}"
        f".pieTitleText{{text-anchor:middle;font-size:25px;fill:black;"
        f"font-family:{FONT_FAMILY};}}"
        f".slice{{font-family:{FONT_FAMILY};fill:#333;font-size:17px;}}"
        f".legend text{{fill:black;font-family:{FONT_FAMILY};font-size:17px;}}"
        f"</style>"
    )

    # Group translated to pie center (grok L102).
    svg.append(f'<g transform="translate({_fmt(cx)},{_fmt(cy)})">')

    # Outer ring (grok L105-L107).
    svg.append(f'<circle cx="0" cy="0" r="{_fmt(OUTER_RADIUS)}" class="pieOuterCircle"/>')

    # Pie slices (grok L113-L145).
    for idx, piece in enumerate(slices):
        # Rust (x).round() as i64 is round-half-away-from-zero; Python round
        # is banker's -- floor(x + 0.5) mirrors Rust for the non-negative pct.
        pct = int(math.floor(piece.value / total * 100.0 + 0.5))
        if pct == 0:
            continue

        frac = piece.value / total
        sweep = frac * 2.0 * math.pi
        next_angle = angle + sweep

        x0, y0 = _polar(0.0, 0.0, RADIUS, angle)
        x1, y1 = _polar(0.0, 0.0, RADIUS, next_angle)
        large_arc = 1 if sweep > math.pi else 0

        fill = MERMAID_PIE_COLORS[idx % len(MERMAID_PIE_COLORS)]

        # Wedge path: move to center, line to arc start, arc to end, close.
        svg.append(
            f'<path d="M0,0L{x0:.3f},{y0:.3f}A{_fmt(RADIUS)},{_fmt(RADIUS)},0,'
            f"{large_arc},1,{x1:.3f},{y1:.3f}Z\" fill=\"{fill}\" class=\"pieCircle\"/>"
        )

        # Percentage label inside the wedge at TEXT_POSITION of the radius.
        label_r = RADIUS * TEXT_POSITION
        mid = angle + sweep / 2.0
        lx, ly = _polar(0.0, 0.0, label_r, mid)
        svg.append(
            f'<text transform="translate({lx:.3f},{ly:.3f})" class="slice" '
            f'style="text-anchor: middle;">{pct}%</text>'
        )

        angle = next_angle

    # Title above the pie (grok L148-L154).
    if chart.title is not None:
        title_y = -((PIE_HEIGHT - 50.0) / 2.0)
        svg.append(
            f'<text x="0" y="{title_y:.0f}" class="pieTitleText">'
            f"{_escape_xml(chart.title)}</text>"
        )

    # Legend (grok L156-L199).
    legend_h = LEGEND_RECT_SIZE + LEGEND_SPACING
    legend_offset = legend_h * len(all_slices) / 2.0
    legend_x = 12.0 * LEGEND_RECT_SIZE  # 216

    # Color map: labels colored in sorted/filtered slice order (d3.scaleOrdinal).
    color_map: list[tuple[str, str]] = [
        (piece.label, MERMAID_PIE_COLORS[idx % len(MERMAID_PIE_COLORS)])
        for idx, piece in enumerate(slices)
    ]

    for legend_idx, piece in enumerate(all_slices):
        vert = legend_idx * legend_h - legend_offset
        color = next(
            (c for label, c in color_map if label == piece.label),
            MERMAID_PIE_COLORS[legend_idx % len(MERMAID_PIE_COLORS)],
        )

        svg.append(
            f'<g class="legend" transform="translate({_fmt(legend_x)},{_fmt(vert)})">'
        )
        svg.append(
            f'<rect width="{_fmt(LEGEND_RECT_SIZE)}" height="{_fmt(LEGEND_RECT_SIZE)}" '
            f'style="fill: {color}; stroke: {color};"/>'
        )

        label_text = (
            f"{piece.label} [{_fmt(piece.value)}]" if chart.show_data else piece.label
        )
        text_x = LEGEND_RECT_SIZE + LEGEND_SPACING
        text_y = LEGEND_RECT_SIZE - LEGEND_SPACING
        svg.append(
            f'<text x="{_fmt(text_x)}" y="{_fmt(text_y)}">'
            f"{_escape_xml(label_text)}</text>"
        )
        svg.append("</g>")

    svg.append("</g>")  # close main group
    svg.append("</svg>")
    return "".join(svg)
