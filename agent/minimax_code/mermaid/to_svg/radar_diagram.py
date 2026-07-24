"""Radar diagram renderer -- behavioral-equivalent port of grok's ``radar_diagram.rs``.

Direction (1) brick 12 (R281). The third per-diagram leaf and the second
per-diagram *renderer* (a self-contained SVG emitter, like the ``info``
renderer in R279 -- as opposed to R280's ``stateDiagram`` parser, which
rides the dagre stack). The ``radar-beta`` diagram is mermaid's radar /
spider chart: N axes radiating from a center, one closed Catmull-Rom
curve per series, a fixed ``700 x 700`` canvas with a 5-ring graticule.
No flowchart AST, no dagre layout, no edge routing -- the geometry is
pure polar math, so this leaf emits SVG directly from the parsed model.

Behavioral-equivalence mapping (function-not-line, same framework as
R279 / R280)
--------------------------------------------------------------

* grok ``pub fn render_radar_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_radar_diagram_to_svg(src, theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to
  "return the SVG / raise :class:`ParseError`" -- the Pythonic shape for
  a fallible render, identical to R279's mapping.
* grok's two private structs ``RadarDiagram`` / ``RadarCurve`` ->
  :class:`RadarDiagram` / :class:`RadarCurve` as ``frozen`` dataclasses.
  Rust structs are immutable by default; ``frozen=True`` mirrors that.
  The ``list`` fields are construction-only (never mutated after the
  parse), matching grok's "build then read" usage.
* grok ``parse_radar`` / ``parse_curve`` / ``closed_round_curve`` /
  ``escape_xml`` -> module-level Python functions with the same names
  (parse_radar is public for direct testing; the other three stay
  module-private with a leading underscore). Their contracts are
  unchanged -- see each docstring.
* grok's header scan ``line.split_whitespace().next() != Some("radar-beta")``
  -> ``line.split()[0] != "radar-beta"``. Same "first whitespace token of
  the first substantial line" semantics.
* grok's body loop has NO ``else`` arm: a body line that is neither
  ``axis ...`` nor ``curve ...`` is **silently skipped** (state_diagram
  raises "Unrecognized"; radar does not). The port preserves that --
  silently skipping is the contract, not a bug to "fix".

Float formatting bridge (the one real Rust/Python divergence)
-------------------------------------------------------------

Rust's ``format!("{}", x)`` (Display for ``f64``) and Python's
``str(x)`` / ``repr(x)`` both emit the *shortest round-trippable*
decimal for the same IEEE-754 bits, so for non-integer coordinates
(cosines / sines of polar angles) the two outputs agree byte-for-byte.
The two languages diverge only on **integer-valued** floats: Rust prints
``350`` for ``350.0_f64`` (drops the trailing ``.0``), Python prints
``350.0``. The radar canvas is built from integer-valued floats
(``700``, ``350``, graticule radii ``60``/``120``/``180``/``240``/``300``),
so every numeric substitution routes through :func:`_fmt`, which collapses
integer-valued floats to their ``int`` form and leaves everything else to
``repr``. That single helper reproduces grok's Display output exactly
without per-site branching.

Why no inline ``_first_diagram_type_token`` (like R279's info renderer)
----------------------------------------------------------------------

grok's ``info_diagram.rs`` inlines a copy of the crate-root token helper
because it defends its public entry against a non-``info`` first token.
``radar_diagram.rs`` does the same job *inline* via its own header scan
in :func:`parse_radar` -- it never calls ``first_diagram_type_token``.
The port mirrors that (no inline copy, no circular-import edge to
sidestep), exactly like R280's state parser.

Public surface (1 symbol): :func:`render_radar_diagram_to_svg` (grok
``pub fn``). The dataclasses, the parser, and the three helpers stay
out of ``__all__``; grok's crate root never re-exports them, and neither
does the :mod:`.to_svg` barrel -- the renderer is reached only through
the dispatch arm added in R281.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_radar_diagram_to_svg"]

# --- canvas + geometry constants (grok radar_diagram.rs L6-L20) ------------
#
# Kept as ``float`` (not ``int``) because they feed floating-point polar
# math (``radius * cos(angle)``); carrying them as ``int`` would force a
# ``float()`` cast at every use site for no fidelity gain. :func:`_fmt`
# collapses the integer-valued ones to bare ``"350"`` at emission time.

#: Plotting area width (grok ``WIDTH: f64 = 600.0``).
WIDTH: float = 600.0
#: Plotting area height (grok ``HEIGHT: f64 = 600.0``).
HEIGHT: float = 600.0
#: Canvas margin (grok ``MARGIN: f64 = 50.0``).
MARGIN: float = 50.0
#: Axis line scale factor (grok ``AXIS_SCALE_FACTOR: f64 = 1.0``).
AXIS_SCALE_FACTOR: float = 1.0
#: Axis label scale factor (grok ``AXIS_LABEL_FACTOR: f64 = 1.05``).
AXIS_LABEL_FACTOR: float = 1.05
#: Catmull-Rom -> Bezier tension (grok ``CURVE_TENSION: f64 = 0.17``).
CURVE_TENSION: float = 0.17
#: Concentric graticule ring count (grok ``DEFAULT_TICKS: usize = 5``).
DEFAULT_TICKS: int = 5
#: Lower bound of the value axis (grok ``DEFAULT_MIN: f64 = 0.0``).
DEFAULT_MIN: float = 0.0
#: Axis line / label color (grok ``AXIS_COLOR``).
AXIS_COLOR: str = "#333333"
#: Graticule ring fill / stroke (grok ``GRATICULE_COLOR``).
GRATICULE_COLOR: str = "#DEDEDE"
#: Graticule ring fill opacity (grok ``GRATICULE_OPACITY: f64 = 0.3``).
GRATICULE_OPACITY: float = 0.3
#: The single series color grok pins for ``radarCurve-0``
#: (grok ``CURVE_COLOR_0``).
CURVE_COLOR_0: str = "hsl(240, 100%, 76.2745098039%)"


@dataclass(frozen=True)
class RadarCurve:
    """One radar series -- a name plus a value per axis (grok ``RadarCurve``)."""

    name: str
    values: list[float]


@dataclass(frozen=True)
class RadarDiagram:
    """A parsed radar diagram -- its axes plus zero or more curves (grok ``RadarDiagram``)."""

    axes: list[str]
    curves: list[RadarCurve]


def _fmt(value: float) -> str:
    """Format a float the way Rust's ``{}`` (Display) formats an ``f64``.

    Rust's ``Display`` drops the trailing ``.0`` on integer-valued floats
    (``350.0_f64`` -> ``"350"``); Python's ``str`` keeps it
    (``str(350.0)`` -> ``"350.0"``). For every other value both languages
    emit the shortest round-trippable decimal for the same IEEE-754 bits,
    so ``repr`` reproduces Rust's ryu output exactly. This helper is the
    single bridge that makes the emitted SVG match grok byte-for-byte on
    the canvas coordinates (which are integer-valued) without branching
    at every emission site.
    """
    # ``value == int(value)`` holds exactly for integer-valued floats
    # (and is False for any non-integer). Radar coordinates are finite,
    # so inf/nan never reach here; ``-0.0`` does not arise from the polar
    # math (all radii are non-negative, all offsets are non-zero), so the
    # ``"-0"`` edge case Rust prints for ``-0.0`` is never exercised.
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(text: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Order matters: ``&`` first so it does not double-escape the entities
    introduced for ``<`` / ``>`` / ``"`` / ``'``. Verbatim match of grok
    radar_diagram.rs L275-L281.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _closed_round_curve(
    points: list[tuple[float, float]], tension: float
) -> str:
    """Build a closed rounded SVG path through ``points`` (grok ``closed_round_curve``).

    A Catmull-Rom spline converted to cubic Bezier segments, closed
    (wrapping the point index modulo ``n``). ``tension`` is the
    Catmull-Rom scaling factor; grok passes ``CURVE_TENSION`` (0.17).

    Empty input yields the empty string (grok L242-L244). For ``n >= 1``
    the path opens with ``M{x},{y}`` at the first point, emits ``n``
    ``C`` segments (one per vertex, each pulling control points from the
    vertex's neighbors), and closes with ``Z``. The neighbor indexing is
    ``p0 = points[(i-1) % n]``, ``p1 = points[i]``, ``p2 = points[(i+1) % n]``,
    ``p3 = points[(i+2) % n]`` -- identical to grok L250-L268.
    """
    if not points:
        return ""

    n = len(points)
    parts: list[str] = [f"M{_fmt(points[0][0])},{_fmt(points[0][1])}"]

    for i in range(n):
        p0 = points[(i + n - 1) % n]
        p1 = points[i]
        p2 = points[(i + 1) % n]
        p3 = points[(i + 2) % n]

        cp1_x = p1[0] + (p2[0] - p0[0]) * tension
        cp1_y = p1[1] + (p2[1] - p0[1]) * tension
        cp2_x = p2[0] - (p3[0] - p1[0]) * tension
        cp2_y = p2[1] - (p3[1] - p1[1]) * tension

        parts.append(
            f" C{_fmt(cp1_x)},{_fmt(cp1_y)} "
            f"{_fmt(cp2_x)},{_fmt(cp2_y)} "
            f"{_fmt(p2[0])},{_fmt(p2[1])}"
        )

    parts.append(" Z")
    return "".join(parts)


def _parse_curve(s: str, line: int) -> tuple[str, list[float]]:
    """Parse a ``name { v1, v2, ... }`` curve body (grok ``parse_curve``).

    Splits at the first ``{`` (name before, payload after), requires a
    trailing ``}`` to close, then parses each comma-separated token as a
    float (empty tokens skipped). The name is trimmed; the payload is
    trimmed inside the braces.

    Args:
        s: the curve body -- everything after ``curve `` on the line,
            already trimmed (grok passes ``rest.trim()``).
        line: the 1-based source line number, carried onto any
            :class:`ParseError` (grok threads ``line`` for diagnostics).

    Returns:
        The ``(name, values)`` pair.

    Raises:
        ParseError: ``"Invalid curve: {s}"`` when there is no ``{`` or no
            closing ``}``; ``"Invalid curve value: {p}"`` when a token
            fails to parse as a float.
    """
    if "{" not in s:
        raise ParseError(line, f"Invalid curve: {s}")
    name, _, rest = s.partition("{")
    name = name.strip()
    if not rest.endswith("}"):
        raise ParseError(line, f"Invalid curve: {s}")
    inner = rest[:-1].strip()

    values: list[float] = []
    for part in inner.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError as err:
            raise ParseError(line, f"Invalid curve value: {token}") from err
    return name, values


def parse_radar(input: str) -> RadarDiagram:
    """Parse a ``radar-beta`` body into a :class:`RadarDiagram` (grok ``parse_radar``).

    Walks the lines, skipping blanks and ``%%`` comments, demanding the
    first substantial line's leading token be ``radar-beta``. After the
    header, each ``axis A, B, C`` line REPLACES the axis list (last one
    wins), and each ``curve Name { ... }`` line appends a
    :class:`RadarCurve`. Any other body line is silently skipped (grok
    has no ``else`` arm here -- silent skip is the contract).

    Args:
        input: the mermaid source (the dispatch passes the raw
            ``mermaid_source`` -- front-matter and all -- mirroring grok
            lib.rs L95; the header scan here finds ``radar-beta`` itself).

    Returns:
        The parsed :class:`RadarDiagram`.

    Raises:
        ParseError: ``"Expected 'radar-beta' declaration"`` when the first
            substantial line's token is not ``radar-beta`` (carries that
            line's 1-based number, or ``1`` when every line is blank /
            comment).
    """
    found_header = False
    axes: list[str] = []
    curves: list[RadarCurve] = []

    for idx, raw in enumerate(input.splitlines()):
        line_no = idx + 1
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue

        if not found_header:
            if line.split()[0] != "radar-beta":
                raise ParseError(line_no, "Expected 'radar-beta' declaration")
            found_header = True
            continue

        # ``axis A, B, C`` -- replace the axis list (grok L181-L189).
        if line.startswith("axis "):
            rest = line[len("axis "):]
            axes = [p.strip() for p in rest.split(",") if p.strip()]
            continue

        # ``curve Name { v1, v2 }`` -- append a curve (grok L191-L195).
        if line.startswith("curve "):
            rest = line[len("curve "):].strip()
            name, values = _parse_curve(rest, line_no)
            curves.append(RadarCurve(name=name, values=values))
            continue

        # grok has no ``else`` arm: unrecognized body lines are silently
        # skipped (NOT an error -- this is the divergent contract vs.
        # state_diagram's "Unrecognized" raise).

    if not found_header:
        raise ParseError(1, "Expected 'radar-beta' declaration")

    return RadarDiagram(axes=axes, curves=curves)


def render_radar_diagram_to_svg(
    mermaid_source: str, theme: MermaidTheme
) -> str:
    """Render a ``radar-beta`` diagram into a radar-chart SVG (grok ``render_radar_diagram_to_svg``).

    Direction (1) brick 12 (R281). Mirrors grok ``radar_diagram.rs``
    ``render_radar_diagram_to_svg``: parse the source into a
    :class:`RadarDiagram`, require at least one axis (else
    :class:`ParseError` at line 1), then emit a fixed ``700 x 700`` SVG
    with a 5-ring concentric graticule, one line + label per axis (polar
    angles evenly spaced from the top), and one closed Catmull-Rom curve
    per series whose value count matches the axis count.

    The curve with a mismatched value count is skipped silently (grok
    L106-L108). The single series color is pinned
    (:data:`CURVE_COLOR_0`); only ``radarCurve-0`` / ``radarLegendBox-0``
    CSS rules ship (later series reuse class 0 in grok's current
    implementation).

    Args:
        mermaid_source: mermaid source whose first diagram-type token is
            ``radar-beta`` (the caller -- typically
            :func:`render.render_mermaid_to_svg` dispatch -- passes the
            source verbatim, mirroring grok lib.rs L95).
        theme: the resolved :class:`MermaidTheme` palette (only
            ``background`` feeds the SVG root style; the axis / graticule
            / curve colors are pinned constants, matching grok).

    Returns:
        The radar-chart SVG string.

    Raises:
        ParseError: when the source's first substantial token is not
            ``radar-beta`` (``"Expected 'radar-beta' declaration"``), or
            when the parsed diagram has no axes (``"radar diagram requires
            at least one axis"``, line 1), or when a ``curve`` line is
            malformed (propagated from :func:`_parse_curve`).
    """
    diagram = parse_radar(mermaid_source)

    if not diagram.axes:
        raise ParseError(1, "radar diagram requires at least one axis")

    # Series max folds from 0.0, then is clamped to at least 1.0 so a
    # degenerate all-zero radar still divides by a non-zero span (grok
    # L36-L41). Empty curves -> max_value == 1.0.
    all_values = [v for curve in diagram.curves for v in curve.values]
    max_value = max(max(all_values, default=0.0), 1.0)

    total_width = WIDTH + 2.0 * MARGIN
    total_height = HEIGHT + 2.0 * MARGIN
    center_x = MARGIN + WIDTH / 2.0
    center_y = MARGIN + HEIGHT / 2.0
    radius = min(WIDTH, HEIGHT) / 2.0

    parts: list[str] = []
    parts.append(
        '<svg aria-roledescription="radar" role="graphics-document document" '
        f'height="{_fmt(total_height)}" '
        f'viewBox="0 0 {_fmt(total_width)} {_fmt(total_height)}" '
        'xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{_fmt(total_width)}" id="my-svg" '
        f'style="background-color: {theme.background};">'
    )

    # CSS rules (grok L57-L76). The CSS ``{`` / ``}`` are escaped as
    # ``{{`` / ``}}`` in the f-string (Rust's ``format!`` escapes them
    # the same way); the embedded constants are plain substitutions.
    parts.append("<style>")
    parts.append(
        f"#my-svg .radarAxisLine{{stroke:{AXIS_COLOR};stroke-width:2;}}"
    )
    parts.append(
        "#my-svg .radarAxisLabel{dominant-baseline:middle;"
        f"text-anchor:middle;font-size:12px;color:{AXIS_COLOR};}}"
    )
    parts.append(
        "#my-svg .radarGraticule{"
        f"fill:{GRATICULE_COLOR};"
        f"fill-opacity:{_fmt(GRATICULE_OPACITY)};"
        f"stroke:{GRATICULE_COLOR};stroke-width:1;}}"
    )
    parts.append(
        "#my-svg .radarLegendText{"
        "text-anchor:start;font-size:12px;dominant-baseline:hanging;}"
    )
    parts.append(
        "#my-svg .radarCurve-0{"
        f"color:{CURVE_COLOR_0};"
        f"fill:{CURVE_COLOR_0};"
        "fill-opacity:0.5;"
        f"stroke:{CURVE_COLOR_0};stroke-width:2;}}"
    )
    parts.append(
        "#my-svg .radarLegendBox-0{"
        f"fill:{CURVE_COLOR_0};"
        "fill-opacity:0.5;"
        f"stroke:{CURVE_COLOR_0};}}"
    )
    parts.append("</style>")

    parts.append("<g/>")
    parts.append(
        f'<g transform="translate({_fmt(center_x)}, {_fmt(center_y)})">'
    )

    # Concentric graticule rings (grok L83-L86): radii 60/120/180/240/300.
    for i in range(DEFAULT_TICKS):
        ring_r = radius * (float(i) + 1.0) / float(DEFAULT_TICKS)
        parts.append(
            f'<circle class="radarGraticule" r="{_fmt(ring_r)}"/>'
        )

    # Axis spokes + labels (grok L88-L103). Angle for axis ``i`` of ``n``
    # is ``2*pi*i/n - pi/2`` (the ``-pi/2`` rotates axis 0 to the top).
    n_axes = len(diagram.axes)
    for i, axis_label in enumerate(diagram.axes):
        angle = 2.0 * float(i) * math.pi / float(n_axes) - math.pi / 2.0
        x2 = radius * AXIS_SCALE_FACTOR * math.cos(angle)
        y2 = radius * AXIS_SCALE_FACTOR * math.sin(angle)
        parts.append(
            '<line class="radarAxisLine" '
            f'y2="{_fmt(y2)}" x2="{_fmt(x2)}" y1="0" x1="0"/>'
        )
        lx = radius * AXIS_LABEL_FACTOR * math.cos(angle)
        ly = radius * AXIS_LABEL_FACTOR * math.sin(angle)
        parts.append(
            '<text class="radarAxisLabel" '
            f'y="{_fmt(ly)}" x="{_fmt(lx)}">{_escape_xml(axis_label)}</text>'
        )

    # Series curves + legend (grok L105-L137). A curve whose value count
    # does not match the axis count is skipped (grok L106-L108).
    for curve_idx, curve in enumerate(diagram.curves):
        if len(curve.values) != n_axes:
            continue

        points: list[tuple[float, float]] = []
        for i, v in enumerate(curve.values):
            angle = 2.0 * float(i) * math.pi / float(n_axes) - math.pi / 2.0
            # Clamp the value to [DEFAULT_MIN, max_value] then scale to the
            # radius span (grok L113-L114).
            r = (
                radius
                * (min(max(v, DEFAULT_MIN), max_value) - DEFAULT_MIN)
                / (max_value - DEFAULT_MIN)
            )
            points.append((r * math.cos(angle), r * math.sin(angle)))

        d = _closed_round_curve(points, CURVE_TENSION)
        parts.append(f'<path class="radarCurve-{curve_idx}" d="{d}"/>')

        legend_x = (WIDTH / 2.0 + MARGIN) * 3.0 / 4.0
        legend_y = -(HEIGHT / 2.0 + MARGIN) * 3.0 / 4.0
        item_y = legend_y + float(curve_idx) * 20.0
        parts.append(
            f'<g transform="translate({_fmt(legend_x)}, {_fmt(item_y)})">'
        )
        parts.append(
            f'<rect class="radarLegendBox-{curve_idx}" '
            'height="12" width="12"/>'
        )
        parts.append(
            '<text class="radarLegendText" y="0" x="16">'
            f"{_escape_xml(curve.name)}</text>"
        )
        parts.append("</g>")

    # Empty title node (grok L139) -- mermaid's title slot, left blank.
    parts.append('<text y="-350" x="0" class="radarTitle"/>')
    parts.append("</g></svg>")

    return "".join(parts)
