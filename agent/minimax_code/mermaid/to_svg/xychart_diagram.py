"""Xychart-beta renderer -- functional clone of Grok Build's
``mermaid-to-svg/src/xychart_diagram.rs`` (R293, direction (1) brick 24).

The 15th per-diagram leaf of the R269--R292 render-stack migration and the
14th self-contained SVG emitter: a cartesian line-chart engine that parses
an ``xychart-beta`` source (title / axes / categorical or numeric x-domain /
``line`` series), computes a d3-style tick layout, and emits a fixed
``700x500`` canvas with two axes, ticks, optional titles, and one polyline
path per series. Mirrors grok ``xychart_diagram.rs`` (867 lines).

Layout
------
A fixed ``CHART_WIDTH x CHART_HEIGHT`` (``700 x 500``) canvas. Margins are
reserved top-down from the resolved content: the chart title eats
``title_height`` from the top; the bottom axis (line + ticks + labels + an
optional x-axis title) eats ``bottom_axis_height`` from the bottom; the left
axis (line + ticks + the widest y-tick label + an optional rotated y-axis
title) eats ``left_axis_width`` from the left; ``PLOT_RIGHT_MARGIN`` (12)
from the right. Whatever remains is the plot rectangle
``[plot_x, plot_x+plot_w] x [plot_y, plot_y+plot_h]`` (floored at 1 so a
degenerate margin never inverts the axis).

The x-domain is either categorical (evenly spaced bands, one per category,
font shrunk-to-fit down to ``MIN_X_LABEL_FONT_SIZE``) or numeric (d3 ticks
evenly distributed across an inner range padded by half the widest tick
label, capped at 20% of the plot width). Every series shares the longest
series' point count so the same index maps to the same x across overlaid
lines. The y-domain always uses d3 ticks mapped through a linear scale that
inverts SVG's downward y-axis (``y_min -> y_bottom``, ``y_max -> y_top``).

Theme awareness
---------------
Two channels (fewer than gitgraph's 4 / block's 5, more than journey's /
mindmap's 0): ``text_color`` drives every axis line stroke + every text
fill (so axes stay legible on a dark surface), and ``background`` paints
both the ``<svg>``'s ``background-color`` style and the ``main`` group's
``background`` rect fill. grok deliberately does NOT theme the series
colors -- those come from the fixed Tableau-10 ``SERIES_PALETTE`` cycled by
series index.

Float-formatting bridge
-----------------------
Rust's default ``f64`` Display drops the trailing ``.0`` on integer-valued
floats (``5.0 -> "5"``); :func:`_fmt` mirrors that so coordinates render as
``"M350,21"`` not ``"M350.0,21.0"``. Rust's ``f64::round`` rounds half away
from zero; :func:`_round` mirrors that (Python's builtin ``round`` is
banker's, which would diverge on ``.5`` boundaries). Tick labels flow
through :func:`_format_tick` (integer ticks via ``{:.0}``, fractional via
``{:.6}`` trimmed), not :func:`_fmt`.

Public surface
--------------
A single symbol :func:`render_xychart_diagram_to_svg` -- the leaf the
crate-root dispatcher (R277 :mod:`render`) routes the ``xychart-beta`` token
to. The parser, layout, and numeric helpers stay module-private (``_``
prefix) so the leaf presents the same one-symbol face as every other
per-diagram renderer in the stack.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .error import ParseError
from .text_wrap import display_width_units
from .theme import MermaidTheme

__all__ = ["render_xychart_diagram_to_svg"]

# === layout constants (grok xychart_diagram.rs L4-L34) =====================
#: Fixed canvas width (grok ``CHART_WIDTH``).
CHART_WIDTH: float = 700.0
#: Fixed canvas height (grok ``CHART_HEIGHT``).
CHART_HEIGHT: float = 500.0

#: Chart-title font size (grok ``CHART_TITLE_FONT_SIZE``).
CHART_TITLE_FONT_SIZE: float = 20.0
#: Chart-title vertical padding (grok ``CHART_TITLE_PADDING``).
CHART_TITLE_PADDING: float = 10.0

#: Axis label (tick) font size (grok ``AXIS_LABEL_FONT_SIZE``).
AXIS_LABEL_FONT_SIZE: float = 14.0
#: Axis label padding (grok ``AXIS_LABEL_PADDING``).
AXIS_LABEL_PADDING: float = 5.0

#: Axis title font size (grok ``AXIS_TITLE_FONT_SIZE``).
AXIS_TITLE_FONT_SIZE: float = 16.0
#: Axis title padding (grok ``AXIS_TITLE_PADDING``).
AXIS_TITLE_PADDING: float = 5.0

#: Axis tick mark length (grok ``AXIS_TICK_LENGTH``).
AXIS_TICK_LENGTH: float = 5.0
#: Axis tick mark stroke width (grok ``AXIS_TICK_WIDTH``).
AXIS_TICK_WIDTH: float = 2.0

#: Axis baseline stroke width (grok ``AXIS_LINE_WIDTH``).
AXIS_LINE_WIDTH: float = 2.0

#: Target tick count for the d3 tick layout (grok ``DEFAULT_TICK_COUNT``).
DEFAULT_TICK_COUNT: int = 10

#: Floor for the auto-shrunk categorical x-axis label font (grok
#: ``MIN_X_LABEL_FONT_SIZE``). This port has no label rotation, so a busy
#: categorical axis shrinks-to-fit down to here, then overflows.
MIN_X_LABEL_FONT_SIZE: float = 8.0

#: Right margin inside the canvas, left of the plot (grok ``PLOT_RIGHT_MARGIN``).
PLOT_RIGHT_MARGIN: float = 12.0

#: Per-series colors (Tableau 10), cycled by series index (grok
#: ``SERIES_PALETTE``). Mid-tone hues stay legible on both the light and
#: dark surfaces this engine renders onto.
SERIES_PALETTE: tuple[str, ...] = (
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
)


# === data model (grok L226-L242) ============================================
@dataclass
class _NumericXAxis:
    """A numeric x-domain ``[min, max]`` (grok ``XAxis::Numeric``)."""

    min: float
    max: float


@dataclass
class _CategoryXAxis:
    """A categorical x-domain of evenly-spaced named bands (grok
    ``XAxis::Category``)."""

    categories: list[str]


#: Tagged union of the two x-axis kinds (grok ``enum XAxis``).
_XAxis = _NumericXAxis | _CategoryXAxis


@dataclass
class _XyChart:
    """A parsed xychart-beta document (grok ``struct XyChart``)."""

    title: str
    x_title: str
    y_title: str
    x_axis: _XAxis
    y_min: float
    y_max: float
    series: list[list[float]]


@dataclass
class _CategoryGeom:
    """Band-scale geometry: points/ticks at band centers (grok
    ``XGeom::Category``)."""

    plot_left: float
    band_w: float


@dataclass
class _NumericGeom:
    """Linear-scale geometry: points evenly distributed across
    ``[x0, x1]`` (grok ``XGeom::Numeric``)."""

    x0: float
    x1: float


#: Tagged union of the two x-geometry kinds (grok ``enum XGeom``).
_XGeom = _CategoryGeom | _NumericGeom


@dataclass
class _XAxisLayout:
    """The resolved x-axis layout -- tick positions/labels, the (possibly
    shrunk) label font, the shared point count, and the geometry tag (grok
    ``struct XAxisLayout``)."""

    tick_positions: list[float]
    tick_labels: list[str]
    label_font: float
    #: Longest series length, shared by every series so the same index maps
    #: to the same x across series (overlaid lines stay on one x domain).
    point_count: int
    geom: _XGeom

    def series_point_x(self, i: int) -> float:
        """Map series index ``i`` to an x pixel (grok ``series_point_x``).

        Categorical: band center ``plot_left + (i + 0.5) * band_w``. Numeric:
        evenly distributed across ``[x0, x1]`` using the shared point count
        (a single point sits at ``x0``); the per-series length is ignored so
        every series overlays on one x domain.
        """
        if isinstance(self.geom, _CategoryGeom):
            return self.geom.plot_left + (i + 0.5) * self.geom.band_w
        if self.point_count <= 1:
            return self.geom.x0
        span = self.geom.x1 - self.geom.x0
        return self.geom.x0 + i / (self.point_count - 1) * span


# === parser (grok L244-L326) ================================================
def _parse_xychart(source: str) -> _XyChart:
    """Parse an ``xychart-beta`` document into an :class:`_XyChart`.

    Mirrors grok ``parse_xychart``: skip blank / ``%%`` lines; the first
    content line's first token must be ``xychart-beta`` (else ParseError);
    ``title`` / ``x-axis`` / ``y-axis`` / ``line`` prefixes populate the
    matching field; unknown lines (e.g. an unsupported ``bar`` series) are
    ignored. A document whose every series is empty raises
    ``"xychart requires at least one plot"``; a missing x-axis defaults to a
    degenerate ``Numeric{0, 0}``; a missing y-range auto-ranges from the data.
    """
    found_header = False
    title = ""
    x_title = ""
    y_title = ""
    x_axis: _XAxis | None = None
    y_range: tuple[float, float] | None = None
    series: list[list[float]] = []

    for idx, raw in enumerate(source.splitlines()):
        line_no = idx + 1
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue

        if not found_header:
            # grok: first whitespace token must be exactly "xychart-beta".
            if line.split(maxsplit=1)[0] != "xychart-beta":
                raise ParseError(line_no, "Expected 'xychart-beta' declaration")
            found_header = True
            continue

        if line.startswith("title "):
            title = _unquote(line[len("title ") :])
            continue

        if line.startswith("x-axis "):
            label, axis = _parse_x_axis(line[len("x-axis ") :].strip(), line_no)
            x_title = label
            x_axis = axis
            continue

        if line.startswith("y-axis "):
            label, rng = _parse_y_axis(line[len("y-axis ") :].strip(), line_no)
            y_title = label
            if rng is not None:
                y_range = rng
            continue

        values = _parse_series_line(line, line_no)
        if values is not None:
            series.append(values)
            continue
        # Unknown lines (e.g. an unsupported `bar` series) are ignored.

    if not found_header:
        raise ParseError(1, "Expected 'xychart-beta' declaration")

    if all(len(values) == 0 for values in series):
        raise ParseError(1, "xychart requires at least one plot")

    if x_axis is None:
        x_axis = _NumericXAxis(0.0, 0.0)
    if y_range is None:
        y_range = _auto_y_range(series)

    return _XyChart(title, x_title, y_title, x_axis, y_range[0], y_range[1], series)


def _parse_x_axis(rest: str, line: int) -> tuple[str, _XAxis]:
    """Parse an ``x-axis`` body (grok ``parse_x_axis``).

    A ``[`` opens a category list (title is everything left of the bracket);
    a ``-->`` signals a numeric range; otherwise the body is a bare title
    with an empty category list.
    """
    open_bracket = rest.find("[")
    if open_bracket != -1:
        title = _unquote(rest[:open_bracket].strip())
        close = rest.rfind("]")
        if close == -1 or close <= open_bracket:
            raise ParseError(line, f"Invalid x-axis categories: {rest}")
        categories = _parse_category_list(rest[open_bracket + 1 : close])
        return title, _CategoryXAxis(categories)
    if "-->" in rest:
        title, mn, mx = _parse_labeled_range(rest, line)
        return title, _NumericXAxis(mn, mx)
    return _unquote(rest), _CategoryXAxis([])


def _parse_y_axis(rest: str, line: int) -> tuple[str, tuple[float, float] | None]:
    """Parse a ``y-axis`` body (grok ``parse_y_axis``).

    A ``-->`` signals an explicit range; otherwise the body is a bare title
    and the range is left unset (the caller auto-ranges from the data).
    """
    if "-->" in rest:
        title, mn, mx = _parse_labeled_range(rest, line)
        return title, (mn, mx)
    return _unquote(rest), None


def _parse_labeled_range(s: str, line: int) -> tuple[str, float, float]:
    """Parse a ``[title] min --> max`` body (grok ``parse_labeled_range``).

    The title is everything left of the last whitespace-separated token
    (``min``); an empty left side yields an empty title.
    """
    arrow = s.find("-->")
    if arrow == -1:
        raise ParseError(line, f"Invalid axis range: {s}")
    left = s[:arrow]
    right = s[arrow + len("-->") :]

    right_trimmed = right.strip()
    try:
        mx = float(right_trimmed)
    except ValueError as exc:
        raise ParseError(line, f"Invalid axis max: {right_trimmed}") from exc

    left_trimmed = left.strip()
    parts = left_trimmed.rsplit(None, 1)
    if len(parts) == 2:
        title_str, min_str = parts[0].strip(), parts[1].strip()
    else:
        title_str, min_str = "", left_trimmed
    try:
        mn = float(min_str)
    except ValueError as exc:
        raise ParseError(line, f"Invalid axis min: {min_str}") from exc

    return _unquote(title_str), mn, mx


def _parse_series_line(line: str, line_no: int) -> list[float] | None:
    """Parse a ``line [..]`` series; a non-``line`` declaration returns
    ``None`` (grok ``parse_series_line``)."""
    rest = _strip_keyword(line, "line")
    if rest is None:
        return None
    return _parse_bracketed_number_list(rest.strip(), line_no)


def _strip_keyword(line: str, keyword: str) -> str | None:
    """Strip ``keyword`` only when it stands alone -- end, whitespace, or
    ``[`` follows -- so ``line`` matches but ``linear`` does not (grok
    ``strip_keyword``)."""
    if not line.startswith(keyword):
        return None
    rest = line[len(keyword) :]
    if not rest:
        return rest
    c = rest[0]
    if c.isspace() or c == "[":
        return rest
    return None


def _parse_category_list(inner: str) -> list[str]:
    """Split on top-level (quote-aware) commas, then unquote/trim each entry
    and drop empties (grok ``parse_category_list``)."""
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for c in inner:
        if quote is not None:
            if c == quote:
                quote = None
            current.append(c)
        elif c in ('"', "'"):
            quote = c
            current.append(c)
        elif c == ",":
            out.append("".join(current))
            current = []
        else:
            current.append(c)
    out.append("".join(current))
    return [s for s in (_unquote(x.strip()) for x in out) if s]


def _unquote(s: str) -> str:
    """Strip one pair of matching surrounding quotes (``"..."`` or
    ``'...'``); otherwise return the trimmed string verbatim (grok
    ``unquote``)."""
    t = s.strip()
    if len(t) >= 2:
        if (t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'"):
            return t[1:-1]
    return t


def _auto_y_range(series: list[list[float]]) -> tuple[float, float]:
    """Compute ``[min, max]`` across every series value (grok
    ``auto_y_range``).

    Empty data yields ``(0, 0)``; a flat (single-value) range is padded by
    one on each side so the linear scale does not collapse.
    """
    mn = math.inf
    mx = -math.inf
    for values in series:
        for v in values:
            if v < mn:
                mn = v
            if v > mx:
                mx = v
    if not (math.isfinite(mn) and math.isfinite(mx)):
        return (0.0, 0.0)
    if abs(mx - mn) < 1e-12:
        return (mn - 1.0, mx + 1.0)
    return (mn, mx)


def _parse_bracketed_number_list(s: str, line: int) -> list[float]:
    """Parse a ``[v, v, ...]`` number list (grok
    ``parse_bracketed_number_list``). Empty parts are skipped; a non-numeric
    part raises ParseError."""
    start = s.find("[")
    if start == -1:
        raise ParseError(line, f"Invalid plot data: {s}")
    end = s.rfind("]")
    if end == -1:
        raise ParseError(line, f"Invalid plot data: {s}")
    inner = s[start + 1 : end]
    out: list[float] = []
    for part in inner.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError as exc:
            raise ParseError(line, f"Invalid plot value: {p}") from exc
    return out


# === layout (grok L469-L556) ================================================
def _layout_x_axis(
    x_axis: _XAxis, plot_x: float, plot_w: float, point_count: int
) -> _XAxisLayout:
    """Resolve the x-axis layout for the given plot rectangle and shared
    point count (grok ``layout_x_axis``)."""
    if isinstance(x_axis, _CategoryXAxis):
        categories = x_axis.categories
        # Size bands to whichever is larger so every series point lands in a
        # band (a series longer than the category list still stays on-plot).
        n = max(len(categories), point_count, 1)
        band_w = plot_w / n
        # Shrink the font so the widest category fits its band (to a floor).
        widest_units = 0.0
        for c in categories:
            w = display_width_units(c)
            if w > widest_units:
                widest_units = w
        if widest_units > 0.0:
            fit = (band_w * 0.95) / (widest_units * 0.525)
            label_font = max(MIN_X_LABEL_FONT_SIZE, min(AXIS_LABEL_FONT_SIZE, fit))
        else:
            label_font = AXIS_LABEL_FONT_SIZE
        tick_positions = [plot_x + (i + 0.5) * band_w for i in range(len(categories))]
        return _XAxisLayout(
            tick_positions=tick_positions,
            tick_labels=list(categories),
            label_font=label_font,
            point_count=point_count,
            geom=_CategoryGeom(plot_left=plot_x, band_w=band_w),
        )

    # Numeric: d3 ticks evenly distributed across an inner range padded by
    # half the widest tick label (capped at 20% of the plot width).
    ticks = _d3_ticks(x_axis.min, x_axis.max, DEFAULT_TICK_COUNT)
    labels = [_format_tick(v) for v in ticks]
    label_max_width = 0.0
    for s in labels:
        w = _approx_text_width(s, AXIS_LABEL_FONT_SIZE)
        if w > label_max_width:
            label_max_width = w
    outer = min(label_max_width / 2.0, 0.2 * plot_w)
    x0 = plot_x + outer
    x1 = plot_x + plot_w - outer
    tick_positions = [_scale_linear(v, x_axis.min, x_axis.max, x0, x1) for v in ticks]
    return _XAxisLayout(
        tick_positions=tick_positions,
        tick_labels=labels,
        label_font=AXIS_LABEL_FONT_SIZE,
        point_count=point_count,
        geom=_NumericGeom(x0=x0, x1=x1),
    )


# === numeric algorithms (grok L590-L696) ====================================
def _points_to_path_d(points: list[tuple[float, float]]) -> str:
    """Build an SVG path ``d`` from points: ``M{x},{y}`` then ``L{x},{y}``
    per subsequent point (grok ``points_to_path_d``)."""
    if not points:
        return ""
    x0, y0 = points[0]
    d = [f"M{_fmt(x0)},{_fmt(y0)}"]
    for x, y in points[1:]:
        d.append(f"L{_fmt(x)},{_fmt(y)}")
    return "".join(d)


def _scale_linear(
    value: float,
    domain_min: float,
    domain_max: float,
    range_min: float,
    range_max: float,
) -> float:
    """Linear map ``value`` from ``[domain_min, domain_max]`` onto
    ``[range_min, range_max]`` (grok ``scale_linear``). A degenerate
    (zero-width) domain returns ``range_min``."""
    if abs(domain_max - domain_min) < 1e-12:
        return range_min
    t = (value - domain_min) / (domain_max - domain_min)
    return range_min + t * (range_max - range_min)


def _d3_ticks(start: float, stop: float, count: int) -> list[float]:
    """d3-style "nice" ticks across ``[start, stop]`` (grok ``d3_ticks``).

    A zero count or non-finite bound yields no ticks; an equal start/stop
    yields the single value. Otherwise the range is stepped by
    :func:`_tick_step`, snapped to the step grid (ceil start, floor stop),
    and reversed if the range runs downhill.
    """
    if count == 0:
        return []
    if not (math.isfinite(start) and math.isfinite(stop)):
        return []
    if start == stop:
        return [start]

    reverse = stop < start
    if reverse:
        a, b = stop, start
    else:
        a, b = start, stop

    step = _tick_step(a, b, float(count))
    if not math.isfinite(step) or step == 0.0:
        return []

    start0 = math.ceil(a / step)
    stop0 = math.floor(b / step)
    n = max(0, int(stop0 - start0 + 1.0))
    ticks = [(start0 + i) * step for i in range(n)]
    if reverse:
        ticks.reverse()
    return ticks


def _tick_step(start: float, stop: float, count: float) -> float:
    """d3 tick-step: a "nice" step (1/2/5 x 10^n) for ``[start, stop]`` over
    ``count`` ticks (grok ``tick_step``).

    ``step0 = range / count``; ``step1`` drops it to its leading power of
    ten; the error ratio selects a ``2x`` / ``5x`` / ``10x`` bump via the
    ``sqrt(2)`` / ``sqrt(10)`` / ``sqrt(50)`` thresholds. Negated when the
    range runs downhill.
    """
    step0 = abs(stop - start) / max(count, 1.0)
    step1 = 10.0 ** math.floor(math.log10(step0))
    error = step0 / step1

    e10 = math.sqrt(50.0)
    e5 = math.sqrt(10.0)
    e2 = math.sqrt(2.0)

    if error >= e10:
        step = step1 * 10.0
    elif error >= e5:
        step = step1 * 5.0
    elif error >= e2:
        step = step1 * 2.0
    else:
        step = step1

    return -step if stop < start else step


def _format_tick(value: float) -> str:
    """Format a tick value (grok ``format_tick``).

    Near-integer values render via ``{:.0}`` (``5.0 -> "5"``); fractional
    values render via ``{:.6}`` with trailing zeros / dot trimmed
    (``2.5 -> "2.5"``).
    """
    rounded = _round(value)
    if abs(value - rounded) < 1e-9:
        return f"{rounded:.0f}"
    s = f"{value:.6f}"
    return s.rstrip("0").rstrip(".")


# === text measurement (grok L698-L705) ======================================
def _approx_text_width(text: str, font_size: float) -> float:
    """Approximate text pixel width -- display-width units x font size x
    ``0.525`` (grok ``approx_text_width``)."""
    return display_width_units(text) * font_size * 0.525


def _approx_text_height(font_size: float) -> int:
    """Approximate text pixel height -- ``round(font_size * 1.15)`` (grok
    ``approx_text_height``)."""
    return _round(font_size * 1.15)


# === float / xml bridges ====================================================
def _round(value: float) -> int:
    """Round half away from zero (Rust ``f64::round`` -> integer semantics).

    Python's builtin ``round`` is banker's (round-half-to-even); grok rounds
    half away from zero, so this mirror keeps text-height and tick rounding
    byte-aligned with the Rust output.
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _fmt(value: float) -> str:
    """Format an f64 the way Rust's default ``Display`` does -- an
    integer-valued float drops its trailing ``.0`` (``5.0 -> "5"``,
    ``-19.0 -> "-19"``); any other value uses its shortest round-trip
    representation (``5.5 -> "5.5"``, ``0.625 -> "0.625"``)."""
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note the ``'`` -> ``&#39;`` *numeric* entity (not ``&apos;``): this
    renderer diverges from the journey / block / gitgraph leaves, which use
    the named entity. The divergence is deliberate -- it is grok's verbatim
    behavior, preserved for output parity.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# === public entry (grok L36-L224) ===========================================
def render_xychart_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render an ``xychart-beta`` source string to a self-contained SVG.

    Functional clone of grok ``render_xychart_diagram_to_svg``: parse ->
    compute the d3 y-tick layout and the resolved x-axis layout -> derive the
    plot rectangle from the title / axis / title margins -> emit the fixed
    ``700x500`` canvas with background, optional chart title, one polyline
    per series, the bottom + left axes (lines, ticks, labels), and the
    optional x / y axis titles.

    Two theme channels flow in: ``theme.text_color`` strokes every axis line
    and fills every text node; ``theme.background`` paints the ``<svg>``
    background-color style and the ``main`` group's background rect. Series
    colors come from the fixed :data:`SERIES_PALETTE`, cycled by series index.
    """
    chart = _parse_xychart(mermaid_source)

    # Theme text color (not a fixed near-black) so axes stay visible on dark.
    axis_color = theme.text_color

    y_ticks = _d3_ticks(chart.y_min, chart.y_max, DEFAULT_TICK_COUNT)
    y_tick_labels = [_format_tick(v) for v in y_ticks]
    label_text_height = _approx_text_height(AXIS_LABEL_FONT_SIZE)
    y_label_max_width = 0.0
    for s in y_tick_labels:
        w = _approx_text_width(s, AXIS_LABEL_FONT_SIZE)
        if w > y_label_max_width:
            y_label_max_width = w

    title_height = (
        0.0
        if not chart.title
        else _approx_text_height(CHART_TITLE_FONT_SIZE) + 2.0 * CHART_TITLE_PADDING
    )
    y_title_width = (
        0.0
        if not chart.y_title
        else _approx_text_height(AXIS_TITLE_FONT_SIZE) + 2.0 * AXIS_TITLE_PADDING
    )
    x_title_height = (
        0.0
        if not chart.x_title
        else _approx_text_height(AXIS_TITLE_FONT_SIZE) + 2.0 * AXIS_TITLE_PADDING
    )

    left_axis_width = (
        AXIS_LINE_WIDTH + AXIS_TICK_LENGTH + (y_label_max_width + 2.0 * AXIS_LABEL_PADDING)
    )
    plot_x = y_title_width + left_axis_width
    plot_y = title_height
    plot_w = max(1.0, CHART_WIDTH - plot_x - PLOT_RIGHT_MARGIN)

    point_count = max((len(values) for values in chart.series), default=0)
    x_layout = _layout_x_axis(chart.x_axis, plot_x, plot_w, point_count)

    # Bottom band depends on the resolved (possibly shrunk) x-label font.
    x_label_height = _approx_text_height(x_layout.label_font)
    bottom_axis_height = (
        AXIS_LINE_WIDTH
        + AXIS_TICK_LENGTH
        + (x_label_height + 2.0 * AXIS_LABEL_PADDING)
        + x_title_height
    )
    plot_h = max(1.0, CHART_HEIGHT - plot_y - bottom_axis_height)

    y_outer_padding = min(label_text_height / 2.0, 0.2 * plot_h)
    y_top = plot_y + y_outer_padding
    y_bottom = plot_y + plot_h - y_outer_padding

    def y_at(v: float) -> float:
        # Invert SVG's downward y: domain min -> bottom, max -> top.
        return _scale_linear(v, chart.y_min, chart.y_max, y_bottom, y_top)

    bg = theme.background
    parts: list[str] = []

    parts.append(
        f'<svg aria-roledescription="xychart" role="graphics-document document" '
        f'viewBox="0 0 {_fmt(CHART_WIDTH)} {_fmt(CHART_HEIGHT)}" '
        f'style="max-width: {_fmt(CHART_WIDTH)}px; background-color: {bg};" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" width="100%" id="my-svg">'
    )

    parts.append("<g/><g class=\"main\">")
    parts.append(
        f'<rect fill="{bg}" class="background" '
        f'height="{_fmt(CHART_HEIGHT)}" width="{_fmt(CHART_WIDTH)}"/>'
    )

    if chart.title:
        title_y = title_height / 2.0
        title_x = CHART_WIDTH / 2.0
        parts.append("<g class=\"chart-title\">")
        parts.append(
            f'<text transform="translate({_fmt(title_x)}, {_fmt(title_y)}) rotate(0)" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-size="{_fmt(CHART_TITLE_FONT_SIZE)}" fill="{axis_color}" '
            f'y="0" x="0">{_escape_xml(chart.title)}</text>'
        )
        parts.append("</g>")

    parts.append("<g class=\"plot\">")
    for idx, values in enumerate(chart.series):
        points = [
            (x_layout.series_point_x(i), y_at(v)) for i, v in enumerate(values)
        ]
        if not points:
            continue
        d = _points_to_path_d(points)
        stroke = SERIES_PALETTE[idx % len(SERIES_PALETTE)]
        parts.append(f'<g class="line-plot-{idx}">')
        parts.append(
            f'<path stroke-width="2" stroke="{stroke}" fill="none" d="{d}"/>'
        )
        parts.append("</g>")
    parts.append("</g>")

    bottom_axis_y = plot_y + plot_h
    parts.append("<g class=\"bottom-axis\">")
    parts.append("<g class=\"axis-line\">")
    axis_line_y = bottom_axis_y + AXIS_LINE_WIDTH / 2.0
    parts.append(
        f'<path stroke-width="{_fmt(AXIS_LINE_WIDTH)}" stroke="{axis_color}" '
        f'fill="none" d="M {_fmt(plot_x)},{_fmt(axis_line_y)} '
        f'L {_fmt(plot_x + plot_w)},{_fmt(axis_line_y)}"/>'
    )
    parts.append("</g>")

    parts.append("<g class=\"label\">")
    x_label_y = bottom_axis_y + AXIS_LABEL_PADDING + AXIS_TICK_LENGTH + AXIS_LINE_WIDTH
    # strict=False mirrors grok's Rust zip (takes the shorter iter); the two
    # lists are co-generated by _layout_x_axis and are always equal-length.
    for pos, label in zip(x_layout.tick_positions, x_layout.tick_labels, strict=False):
        parts.append(
            f'<text transform="translate({_fmt(pos)}, {_fmt(x_label_y)}) rotate(0)" '
            f'text-anchor="middle" dominant-baseline="text-before-edge" '
            f'font-size="{_fmt(x_layout.label_font)}" fill="{axis_color}" '
            f'y="0" x="0">{_escape_xml(label)}</text>'
        )
    parts.append("</g>")

    parts.append("<g class=\"ticks\">")
    tick_y0 = bottom_axis_y + AXIS_LINE_WIDTH
    tick_y1 = tick_y0 + AXIS_TICK_LENGTH
    for pos in x_layout.tick_positions:
        parts.append(
            f'<path stroke-width="{_fmt(AXIS_TICK_WIDTH)}" stroke="{axis_color}" '
            f'fill="none" d="M {_fmt(pos)},{_fmt(tick_y0)} '
            f'L {_fmt(pos)},{_fmt(tick_y1)}"/>'
        )
    parts.append("</g>")
    parts.append("</g>")

    parts.append("<g class=\"left-axis\">")
    # NOTE: the class is spelled "axisl-line" (grok L169 typo), not
    # "axis-line" -- preserved verbatim for output parity.
    parts.append("<g class=\"axisl-line\">")
    axis_x = plot_x - AXIS_LINE_WIDTH / 2.0
    parts.append(
        f'<path stroke-width="{_fmt(AXIS_LINE_WIDTH)}" stroke="{axis_color}" '
        f'fill="none" d="M {_fmt(axis_x)},{_fmt(plot_y)} '
        f'L {_fmt(axis_x)},{_fmt(plot_y + plot_h)}"/>'
    )
    parts.append("</g>")

    parts.append("<g class=\"label\">")
    y_label_x = plot_x - AXIS_LABEL_PADDING - AXIS_TICK_LENGTH - AXIS_LINE_WIDTH
    for tick_value, tick_label in zip(y_ticks, y_tick_labels, strict=False):
        yy = y_at(tick_value)
        parts.append(
            f'<text transform="translate({_fmt(y_label_x)}, {_fmt(yy)}) rotate(0)" '
            f'text-anchor="end" dominant-baseline="middle" '
            f'font-size="{_fmt(AXIS_LABEL_FONT_SIZE)}" fill="{axis_color}" '
            f'y="0" x="0">{_escape_xml(tick_label)}</text>'
        )
    parts.append("</g>")

    parts.append("<g class=\"ticks\">")
    tick_x0 = plot_x - AXIS_LINE_WIDTH
    tick_x1 = tick_x0 - AXIS_TICK_LENGTH
    for tick_value in y_ticks:
        yy = y_at(tick_value)
        parts.append(
            f'<path stroke-width="{_fmt(AXIS_TICK_WIDTH)}" stroke="{axis_color}" '
            f'fill="none" d="M {_fmt(tick_x0)},{_fmt(yy)} '
            f'L {_fmt(tick_x1)},{_fmt(yy)}"/>'
        )
    parts.append("</g>")
    parts.append("</g>")

    if chart.x_title:
        tx = plot_x + plot_w / 2.0
        ty = CHART_HEIGHT - x_title_height / 2.0
        parts.append("<g class=\"x-axis-title\">")
        parts.append(
            f'<text transform="translate({_fmt(tx)}, {_fmt(ty)}) rotate(0)" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-size="{_fmt(AXIS_TITLE_FONT_SIZE)}" fill="{axis_color}" '
            f'y="0" x="0">{_escape_xml(chart.x_title)}</text>'
        )
        parts.append("</g>")

    if chart.y_title:
        tx = y_title_width / 2.0
        ty = plot_y + plot_h / 2.0
        parts.append("<g class=\"y-axis-title\">")
        parts.append(
            f'<text transform="translate({_fmt(tx)}, {_fmt(ty)}) rotate(-90)" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-size="{_fmt(AXIS_TITLE_FONT_SIZE)}" fill="{axis_color}" '
            f'y="0" x="0">{_escape_xml(chart.y_title)}</text>'
        )
        parts.append("</g>")

    parts.append("</g><g class=\"mermaid-tmp-group\"/></svg>")

    return "".join(parts)
