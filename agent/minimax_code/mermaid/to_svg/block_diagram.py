"""Functional clone of grok's ``block_diagram.rs`` (direction (1) brick 20).

This is the 11th per-diagram leaf and the 10th **renderer** (self-contained SVG
emitter, same lineage as R279 info / R281 radar / R282 pie / R283 packet /
R284 sankey / R285 gantt / R286 kanban / R287 timeline / R288 quadrant; plus
R280 state parser that rides the dagre stack). ``block-beta`` is mermaid's
grid-laid block diagram: a set of labelled rectangular nodes arranged on a
column-driven grid, connected by curved (D3 ``curveBasis``) edges. The renderer
parses the source into a node/edge model, measures each node from its label,
normalizes every node to the max child size (mermaid's ``setBlockSizes``),
positions nodes on a ``columns``-driven grid, computes the content bounds, and
emits a single SVG string.

Layout (mirrors grok ``render_block_diagram_to_svg`` L29-L248)
--------------------------------------------------------------

Five phases, all pure iterative geometry (no AST, no dagre):

1. **Node sizing** -- ``w = line_width(label, BLOCK_CHAR_WIDTH) + BLOCK_PADDING``
   and ``h = BLOCK_TEXT_HEIGHT + BLOCK_PADDING`` (mermaid inserts the node into
   the DOM, calls ``getBBox()``, then ``rect2()`` adds ``block.padding`` (=8) to
   both dimensions; we approximate the label bbox with ``BLOCK_CHAR_WIDTH`` /
   ``BLOCK_TEXT_HEIGHT`` derived from Chromium foreignObject measurements).
2. **Normalize** -- every node's size is overwritten with ``(max_w, max_h)``
   (mermaid's ``setBlockSizes`` normalizes children to ``maxChildSize``).
3. **Grid layout** -- ``columns`` drives how many nodes sit per row;
   ``calculate_block_position(columns, position)`` returns the ``(px, py)``
   grid cell; ``x_size`` is ``columns`` when ``0 < columns < num_items`` else
   ``num_items``; ``y_size = ceil(num_items / x_size)``; the root block height
   ``root_h`` anchors each child's ``cy``; ``cx`` walks left-to-right with a
   per-row reset.
4. **findBounds** -- scan every node's half-extents to derive
   ``(min_x, min_y, max_x, max_y)``; the viewBox is the bounds plus
   ``VB_MARGIN`` (5) on every side.
5. **SVG emission** -- root ``<svg>`` + ``<style>`` (the block CSS, themed) + a
   placeholder ``<g/>`` + six arrow markers + a ``<g class="block">`` wrapper
   + one ``<g class="node ...">`` per node (``<rect>`` + centred ``<text>``)
   + one ``<path>`` per edge (three points ``[start, mid, offset_end]`` clipped
   via ``rect_intersect`` then drawn as a D3 ``curveBasis`` spline, with the
   end point shifted back by ``ARROW_POINT_OFFSET`` so the arrowhead lands on
   the target node's edge).

Theme awareness (the block family reads every theme channel)
-------------------------------------------------------------

block-beta is **fully theme-aware**: all five ``MermaidTheme`` channels flow
into the SVG. ``theme.background`` -> the SVG root ``background-color`` (with
mermaid's ``#ffffff`` -> ``white`` normalization); ``theme.text_color`` -> the
CSS ``#my-svg`` font fill + every label/cluster text fill (with ``#333333`` ->
``#333`` normalization); ``theme.edge_color`` -> the CSS marker / arrowhead /
flowchart-link / edgePath strokes; ``theme.node_fill`` + ``theme.node_stroke``
-> the CSS ``.node rect`` fill/stroke. This is a superset of the sankey /
kanban / quadrant theme-aware family -- those read 1-2 channels, block reads
all five. The resolved theme is threaded straight through; no palette sniff
is needed.

Float-formatting bridge (the block-unique dual bridge)
-------------------------------------------------------

block is the first renderer to carry **two** number formatters, used at
different sites:

* ``_fmt(value)`` mirrors Rust ``f64::Display`` -- integer floats drop the
  trailing ``.0`` (``250.0`` -> ``"250"``), non-integers use Python's shortest
  ``repr`` (which matches Rust's shortest-round-trip Display for layout-scale
  values). Used for the viewBox coordinates, the node ``transform`` / ``rect``
  geometry, and the CSS ``max-width``. This is the same Display bridge the
  other renderers use for scalar knobs; block reuses it for coordinates too.
* ``_fmt_num(v)`` mirrors D3's number formatting -- round to 3 decimals, drop
  the trailing zeros (``9.485`` -> ``"9.485"``, ``250.0`` -> ``"250"``,
  ``-29.5`` -> ``"-29.5"``). Used **only** inside the edge ``curveBasis`` path
  data. This diverges from quadrant's ``_fmt1`` (``{:.1}``, always one decimal)
  -- D3 uses variable precision, so ``_fmt_num`` strips trailing zeros rather
  than pinning one decimal place.

The two formatters are NOT interchangeable: using ``_fmt`` inside the path
data would emit ``repr``-long coordinates that diverge from D3's rounded
output; using ``_fmt_num`` for the viewBox would drop precision mermaid keeps.
Both bridge through ``_round_half_away`` so the rounding matches Rust's
``f64::round()`` (half away from zero) rather than Python's banker's rounding
-- the difference only lands on exact ``.5`` boundaries, but those occur in
layout coordinates (e.g. half of an odd-width node), so the helper is load-
bearing.

Public surface (1 symbol): ``render_block_diagram_to_svg``. Reached only via
the ``render.py`` dispatch arm; the barrel does NOT re-export it (mirrors
grok's crate root never re-exporting per-diagram renderers).

References to grok line numbers are contract references (the Rust source is
read-only under ``grok-build/third_party/mermaid-to-svg/src/block_diagram.rs``);
this module is a behavioral-equivalent port, not a line-by-line clone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .error import ParseError
from .text_wrap import line_width
from .theme import MermaidTheme

__all__ = ["render_block_diagram_to_svg"]

# === constants (grok L7-L27, mermaid 11.12.2 block defaults) =================

#: Layout padding between sibling blocks AND the node shape padding added to
#: the label bbox in both dimensions (mermaid ``block.padding ?? 8``).
BLOCK_PADDING: float = 8.0

#: Approximate per-character width for 16px Trebuchet MS rendered in Chromium
#: (derived from reference SVG foreignObject measurements: "A" -> 10.953).
#: Replaces the narrower DEFAULT_CHAR_WIDTH (8.0) used by the flowchart stack.
BLOCK_CHAR_WIDTH: float = 10.97

#: Approximate text height for a single line of 16px Trebuchet MS in a
#: Chromium foreignObject (reference SVG height = 19 for single-line labels).
BLOCK_TEXT_HEIGHT: float = 19.0

#: ViewBox margin around content bounds (mermaid ``bounds2.x - 5, ... +10``).
VB_MARGIN: float = 5.0

#: Arrow-point marker offset (mermaid ``markerOffsets.arrow_point = 4``).
#: Applied via ``getLineFunctionsWithOffset`` to shift the last edge point
#: backward so the arrowhead marker tip lands near the target node's edge.
ARROW_POINT_OFFSET: float = 4.0


# === float-formatting bridges ===============================================


def _round_half_away(x: float) -> int:
    """Round half away from zero, mirroring Rust ``f64::round()``.

    Python's built-in ``round`` uses banker's rounding (round-half-to-even),
    which disagrees with Rust on exact ``.5`` boundaries (``round(2.5) == 2``
    in Python vs ``2.5_f64.round() == 3`` in Rust). Layout coordinates hit
    those boundaries (half of an odd-width node), so the D3 rounding in
    ``_fmt_num`` must match Rust to stay byte-equivalent with grok.
    """
    if x >= 0:
        return math.floor(x + 0.5)
    return math.ceil(x - 0.5)


def _fmt(value: float) -> str:
    """Mirror Rust ``f64::Display`` for layout-scale coordinates.

    Integer floats drop the trailing ``.0`` (``250.0`` -> ``"250"``,
    ``-5.0`` -> ``"-5"``); non-integers use Python's shortest ``repr``, which
    agrees with Rust's shortest-round-trip Display for the magnitudes this
    renderer emits (viewBox / node transform / rect geometry). Used at every
    site where grok writes ``format!("{vb_x}")`` / ``format!("{cx}")`` -- i.e.
    Rust Display, NOT D3 rounding.
    """
    ivalue = int(value)
    if value == ivalue:
        return str(ivalue)
    return repr(value)


def _fmt_num(v: float) -> str:
    """D3 number formatting: round to 3 decimals, strip trailing zeros.

    Mirrors grok ``fmt_num`` (L375-L383): ``rounded = round(v*1000)/1000``;
    if ``rounded`` is effectively an integer emit ``{:0}`` (no decimal),
    else emit ``{:.3}`` and strip trailing zeros / dangling dot. Used ONLY
    inside the edge ``curveBasis`` path data (D3's number format), never for
    the viewBox or node geometry (those use Rust Display via ``_fmt``).
    """
    rounded = _round_half_away(v * 1000.0) / 1000.0
    nearest = _round_half_away(rounded)
    if abs(rounded - nearest) < 1e-9:
        return f"{nearest:d}"
    s = f"{rounded:.3f}"
    return s.rstrip("0").rstrip(".")


# === XML / quote helpers ====================================================


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml`` L541).

    ``&`` is replaced first; ``'`` maps to ``&apos;`` (the named-entity variant
    shared with pie / packet / gantt / kanban / timeline / quadrant, unlike
    radar / sankey which use ``&#39;``).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _strip_quotes(s: str) -> str:
    """Strip a matching leading/trailing single or double quote (grok L524).

    A quote char only peels when it is paired: ``'"a"'`` -> ``"a"``, but
    ``'"a'`` (unpaired) is returned verbatim, mirroring grok's
    ``strip_prefix(...).and_then(strip_suffix(...))`` chain.
    """
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    return s


# === model ==================================================================


@dataclass
class BlockDiagram:
    """Parsed ``block-beta`` model (grok ``BlockDiagram`` struct L385-L393).

    ``nodes`` maps id -> label; ``node_order`` preserves declaration order so
    the grid layout walks nodes in source order; ``edges`` is a list of
    ``(from_id, to_id)`` pairs; ``columns`` is the grid column count (-1 =
    auto, meaning a single row).
    """

    nodes: dict[str, str] = field(default_factory=dict)
    node_order: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    columns: int = -1


# === layout primitives ======================================================


def _calculate_block_position(columns: int, position: int) -> tuple[int, int]:
    """Return the ``(px, py)`` grid cell for a column position (grok L252).

    ``columns < 0`` (auto) -> single row ``(position, 0)``; ``columns == 1``
    -> single column ``(0, position)``; otherwise ``(position % columns,
    position // columns)``.
    """
    if columns < 0:
        return (position, 0)
    if columns == 1:
        return (0, position)
    return (position % columns, position // columns)


def _rect_intersect(
    cx: float, cy: float, w: float, h: float, ox: float, oy: float
) -> tuple[float, float]:
    """Intersect a ray from ``(cx, cy)`` toward ``(ox, oy)`` with a rect boundary.

    The rect is centred at ``(cx, cy)`` with half-extents ``(w/2, h/2)``.
    Mirrors grok ``rect_intersect`` L266-L299: try the left/right edge first
    (if the ray reaches it within the rect's vertical span), then the top/
    bottom edge; degenerate rays (target == centre) hit the right edge.
    """
    hw = w / 2.0
    hh = h / 2.0
    dx = ox - cx
    dy = oy - cy

    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return (cx + hw, cy)

    if abs(dx) > 1e-9:
        t_x = hw / dx if dx > 0.0 else -hw / dx
        y_at_edge = cy + dy * t_x
        if abs(y_at_edge - cy) <= hh + 1e-9:
            if dx > 0.0:
                return (cx + hw, y_at_edge)
            return (cx - hw, y_at_edge)

    if abs(dy) > 1e-9:
        t_y = hh / dy if dy > 0.0 else -hh / dy
        x_at_edge = cx + dx * t_y
        if dy > 0.0:
            return (x_at_edge, cy + hh)
        return (x_at_edge, cy - hh)

    return (cx + hw, cy)


def _curve_basis_path(points: list[tuple[float, float]]) -> str:
    """Emit an SVG path using D3's ``curveBasis`` (uniform cubic B-spline).

    Mirrors grok ``curve_basis_path`` L302-L373. D3's curveBasis emits a
    leading ``L`` to a one-third point, one ``C`` per interior triple, and a
    trailing ``L`` to the last point; the final interior triple uses a
    one-third end point instead of the midpoint used by earlier triples.
    Numbers flow through ``_fmt_num`` (D3 3-decimal rounding), NOT ``_fmt``.
    """
    if not points:
        return ""
    if len(points) == 1:
        return f"M{_fmt_num(points[0][0])},{_fmt_num(points[0][1])}"
    if len(points) == 2:
        x0, y0 = points[0]
        x1, y1 = points[1]
        return (
            f"M{_fmt_num(x0)},{_fmt_num(y0)}"
            f"L{_fmt_num(x1)},{_fmt_num(y1)}"
        )

    parts: list[str] = []
    n = len(points)

    x0, y0 = points[0]
    parts.append(f"M{_fmt_num(x0)},{_fmt_num(y0)}")

    x1, y1 = points[1]
    lx = (2.0 * x0 + x1) / 3.0
    ly = (2.0 * y0 + y1) / 3.0
    parts.append(f"L{_fmt_num(lx)},{_fmt_num(ly)}")

    for i in range(1, n - 1):
        px, py = points[i - 1]
        cx, cy = points[i]
        nx, ny = points[i + 1]

        cp1x = (2.0 * cx + px) / 3.0
        cp1y = (2.0 * cy + py) / 3.0
        cp2x = (2.0 * cx + nx) / 3.0
        cp2y = (2.0 * cy + ny) / 3.0

        if i == n - 2:
            end_x = (2.0 * cx + nx) / 3.0
            end_y = (2.0 * cy + ny) / 3.0
            parts.append(
                f"C{_fmt_num(cp1x)},{_fmt_num(cp1y)}"
                f",{_fmt_num(cp2x)},{_fmt_num(cp2y)}"
                f",{_fmt_num(end_x)},{_fmt_num(end_y)}"
            )
        else:
            epx = (cx + nx) / 2.0
            epy = (cy + ny) / 2.0
            parts.append(
                f"C{_fmt_num(cp1x)},{_fmt_num(cp1y)}"
                f",{_fmt_num(cp2x)},{_fmt_num(cp2y)}"
                f",{_fmt_num(epx)},{_fmt_num(epy)}"
            )

    xn, yn = points[n - 1]
    parts.append(f"L{_fmt_num(xn)},{_fmt_num(yn)}")

    return "".join(parts)


# === CSS template (grok block_css L535-L539) =================================

#: The block-diagram CSS stylesheet, verbatim from grok ``block_css`` L537.
#: Four ``{theme_field}`` placeholders are substituted by ``_block_css`` via
#: ``str.replace`` (avoids f-string/format escaping of the CSS literal braces).
#: The placeholders are mutually non-overlapping, so sequential replace is
#: equivalent to grok's single-pass ``format!``.
_CSS_TEMPLATE = (
    '#my-svg{font-family:"trebuchet ms",verdana,arial,sans-serif;'
    "font-size:16px;fill:{text_color};}"
    "@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}"
    "@keyframes dash{to{stroke-dashoffset:0;}}"
    "#my-svg .edge-animation-slow{stroke-dasharray:9,5!important;"
    "stroke-dashoffset:900;animation:dash 50s linear infinite;"
    "stroke-linecap:round;}"
    "#my-svg .edge-animation-fast{stroke-dasharray:9,5!important;"
    "stroke-dashoffset:900;animation:dash 20s linear infinite;"
    "stroke-linecap:round;}"
    "#my-svg .error-icon{fill:#552222;}"
    "#my-svg .error-text{fill:#552222;stroke:#552222;}"
    "#my-svg .edge-thickness-normal{stroke-width:1px;}"
    "#my-svg .edge-thickness-thick{stroke-width:3.5px;}"
    "#my-svg .edge-pattern-solid{stroke-dasharray:0;}"
    "#my-svg .edge-thickness-invisible{stroke-width:0;fill:none;}"
    "#my-svg .edge-pattern-dashed{stroke-dasharray:3;}"
    "#my-svg .edge-pattern-dotted{stroke-dasharray:2;}"
    "#my-svg .marker{fill:{edge_color};stroke:{edge_color};}"
    "#my-svg .marker.cross{stroke:{edge_color};}"
    '#my-svg svg{font-family:"trebuchet ms",verdana,arial,sans-serif;'
    "font-size:16px;}"
    "#my-svg p{margin:0;}"
    '#my-svg .label{font-family:"trebuchet ms",verdana,arial,sans-serif;'
    "color:{text_color};}"
    "#my-svg .cluster-label text{fill:{text_color};}"
    "#my-svg .cluster-label span,#my-svg p{color:{text_color};}"
    "#my-svg .label text,#my-svg span,#my-svg p{fill:{text_color};"
    "color:{text_color};}"
    "#my-svg .node rect,#my-svg .node circle,#my-svg .node ellipse,"
    "#my-svg .node polygon,#my-svg .node path{fill:{node_fill};"
    "stroke:{node_stroke};stroke-width:1px;}"
    "#my-svg .flowchart-label text{text-anchor:middle;}"
    "#my-svg .node .label{text-align:center;}"
    "#my-svg .node.clickable{cursor:pointer;}"
    "#my-svg .arrowheadPath{fill:{edge_color};}"
    "#my-svg .edgePath .path{stroke:{edge_color};stroke-width:2.0px;}"
    "#my-svg .flowchart-link{stroke:{edge_color};fill:none;}"
    "#my-svg .edgeLabel{background-color:rgba(232,232,232, 0.8);"
    "text-align:center;}"
    "#my-svg .edgeLabel rect{opacity:0.5;"
    "background-color:rgba(232,232,232, 0.8);"
    "fill:rgba(232,232,232, 0.8);}"
    "#my-svg .labelBkg{background-color:rgba(232, 232, 232, 0.5);}"
    "#my-svg .node .cluster{fill:rgba(255, 255, 222, 0.5);"
    "stroke:rgba(170, 170, 51, 0.2);"
    "box-shadow:rgba(50, 50, 93, 0.25) 0px 13px 27px -5px,"
    "rgba(0, 0, 0, 0.3) 0px 8px 16px -8px;stroke-width:1px;}"
    "#my-svg .cluster text{fill:{text_color};}"
    "#my-svg .cluster span,#my-svg p{color:{text_color};}"
    "#my-svg div.mermaidTooltip{position:absolute;text-align:center;"
    "max-width:200px;padding:2px;"
    'font-family:"trebuchet ms",verdana,arial,sans-serif;font-size:12px;'
    "background:hsl(80, 100%, 96.2745098039%);border:1px solid #aaaa33;"
    "border-radius:2px;pointer-events:none;z-index:100;}"
    "#my-svg .flowchartTitleText{text-anchor:middle;font-size:18px;"
    "fill:{text_color};}"
    '#my-svg :root{--mermaid-font-family:"trebuchet ms",verdana,arial,'
    "sans-serif;}"
)


def _block_css(
    text_color: str, edge_color: str, node_fill: str, node_stroke: str
) -> str:
    """Substitute the four theme channels into the block CSS template.

    Mirrors grok ``block_css`` L535-L539 (a single ``format!``). Python uses
    sequential ``str.replace`` so the CSS literal braces need no escaping --
    the four placeholders are mutually non-overlapping, so the result is
    identical to grok's single-pass substitution (theme channel values are
    hex strings, none of which contain a ``{...}`` placeholder substring).
    """
    return (
        _CSS_TEMPLATE.replace("{text_color}", text_color)
        .replace("{edge_color}", edge_color)
        .replace("{node_fill}", node_fill)
        .replace("{node_stroke}", node_stroke)
    )


# === parser =================================================================


def _parse_block_node(s: str, line: int) -> tuple[str, str]:
    """Parse a single block node reference into ``(id, label)`` (grok L498).

    ``id[label]`` -> ``(id, strip_quotes(label))``; a bare token without
    brackets -> ``(token, token)`` (id doubles as label). An empty input
    raises ``ParseError`` ("Empty node"); ``[label]`` with no id raises
    ``ParseError`` ("Missing node id").
    """
    s = s.strip()
    if not s:
        raise ParseError(line, "Empty node")

    bracket = s.find("[")
    if bracket != -1:
        id_ = s[:bracket].strip()
        inner = s[bracket + 1 :].strip()
        if inner.endswith("]"):
            inner = inner[:-1].strip()
        label = _strip_quotes(inner)
        if not id_:
            raise ParseError(line, f"Missing node id in '{s}'")
        return (id_, label)

    return (s, s)


def _insert_node(
    id_: str,
    label: str,
    nodes: dict[str, str],
    order: list[str],
) -> None:
    """Insert a node, preserving first-seen order (grok ``insert_node`` L402).

    A new id is appended to ``order`` and mapped to ``label``. An existing id
    keeps its order slot but its label is updated -- but only when the new
    label is explicit (differs from the bare id); this stops a later bare
    reference from clobbering an earlier ``id[explicit label]``.
    """
    if id_ not in nodes:
        order.append(id_)
        nodes[id_] = label
    elif label != id_:
        nodes[id_] = label


def _parse_block_beta(input_str: str) -> BlockDiagram:
    """Parse a ``block-beta`` source string into a :class:`BlockDiagram`.

    Mirrors grok ``parse_block_beta`` L395-L496. The first non-blank /
    non-``%%`` line must start with the ``block-beta`` token; ``columns N``
    (or ``columns auto``) sets the grid column count; ``block:``/``block ``/
    ``end`` group markers, ``style``/``classDef``/``class``/``linkStyle``
    directives, and ``space``/``space:N`` placeholders are skipped; lines
    containing ``-->`` are edges (both endpoints parsed and registered); any
    other non-empty line is a standalone node declaration. A standalone node
    declaration that fails to parse (e.g. ``[label]`` with no id) is silently
    skipped -- grok's ``if let Ok`` swallows the error -- whereas an edge
    endpoint that fails to parse propagates the ``ParseError`` (grok's ``?``).
    """
    found_header = False
    nodes: dict[str, str] = {}
    node_order: list[str] = []
    edges: list[tuple[str, str]] = []
    columns = -1

    for idx, raw in enumerate(input_str.splitlines()):
        line_no = idx + 1
        line = raw.strip()

        if not line or line.startswith("%%"):
            continue

        if not found_header:
            # grok L424: split_whitespace().next() must be "block-beta".
            first_token = line.split()[0]
            if first_token != "block-beta":
                raise ParseError(line_no, "Expected 'block-beta' declaration")
            found_header = True
            continue

        # columns directive (grok L435-L443): "columns N" / "columns auto".
        # A non-numeric, non-"auto" rest leaves columns unchanged (grok's
        # ``if let Ok`` branch simply does not fire).
        if line.startswith("columns"):
            rest = line[len("columns") :].strip()
            if rest == "auto":
                columns = -1
            else:
                try:
                    columns = int(rest)
                except ValueError:
                    pass
            continue

        # Skip block:/end group markers (grok L447-L449); nested groups are
        # tolerated but not modelled.
        if line == "end" or line.startswith("block:") or line.startswith("block "):
            continue

        # Skip style/classDef/class/linkStyle directives (grok L452-L458).
        if (
            line.startswith("style ")
            or line.startswith("classDef ")
            or line.startswith("class ")
            or line.startswith("linkStyle ")
        ):
            continue

        # Space directive (grok L461-L464): invisible placeholder, skipped.
        if line == "space" or line.startswith("space:"):
            continue

        # Edge line (grok L467-L474): split at the first "-->", parse both
        # endpoints, register nodes, append the edge. Errors propagate.
        if "-->" in line:
            lhs, rhs = line.split("-->", 1)
            from_id, from_label = _parse_block_node(lhs.strip(), line_no)
            to_id, to_label = _parse_block_node(rhs.strip(), line_no)
            _insert_node(from_id, from_label, nodes, node_order)
            _insert_node(to_id, to_label, nodes, node_order)
            edges.append((from_id, to_id))
            continue

        # Standalone node declaration (grok L477-L480): a parse failure here
        # is swallowed (grok's ``if let Ok``), so the offending line is just
        # skipped rather than aborting the whole parse.
        try:
            id_, label = _parse_block_node(line, line_no)
        except ParseError:
            continue
        _insert_node(id_, label, nodes, node_order)

    if not found_header:
        raise ParseError(1, "Expected 'block-beta' declaration")

    return BlockDiagram(
        nodes=nodes,
        node_order=node_order,
        edges=edges,
        columns=columns,
    )


# === renderer ===============================================================


def render_block_diagram_to_svg(
    mermaid_source: str, theme: MermaidTheme
) -> str:
    """Render a ``block-beta`` source into an SVG string (grok L29-L248).

    Parses the source, measures each node from its label, normalizes every
    node to the max child size, positions nodes on a ``columns``-driven grid,
    computes the content bounds, and emits a single SVG with the themed CSS,
    six arrow markers, one ``<rect>`` + centred ``<text>`` per node, and one
    D3 ``curveBasis`` ``<path>`` per edge. Fully theme-aware: all five
    ``MermaidTheme`` channels (background / text_color / edge_color /
    node_fill / node_stroke) flow into the SVG.

    Raises:
        ParseError: when the source is not a valid ``block-beta`` diagram
            (missing header, missing node id on an edge endpoint, empty node
            on an edge endpoint).
    """
    diagram = _parse_block_beta(mermaid_source)

    ordered_nodes = diagram.node_order

    # --- Phase 1: node sizes (grok L37-L49) ---
    node_sizes: dict[str, tuple[float, float]] = {}
    for id_ in ordered_nodes:
        label = diagram.nodes.get(id_, id_)
        text_w = line_width(label, BLOCK_CHAR_WIDTH)
        w = text_w + BLOCK_PADDING
        h = BLOCK_TEXT_HEIGHT + BLOCK_PADDING
        node_sizes[id_] = (w, h)

    # --- Phase 2: setBlockSizes -- normalize children to max size (grok L51-L57) ---
    max_w = 0.0
    max_h = 0.0
    for w, h in node_sizes.values():
        if w > max_w:
            max_w = w
        if h > max_h:
            max_h = h
    for id_ in node_sizes:
        node_sizes[id_] = (max_w, max_h)

    # --- Phase 3: layoutBlocks -- grid positioning (grok L59-L98) ---
    columns = diagram.columns
    num_items = len(ordered_nodes)
    x_size = columns if 0 < columns < num_items else num_items
    if x_size > 0:
        y_size = math.ceil(num_items / x_size)
    else:
        y_size = 1
    root_h = y_size * (max_h + BLOCK_PADDING) + BLOCK_PADDING

    node_layout: dict[str, tuple[float, float]] = {}
    half_w = max_w / 2.0
    starting_pos_x = -BLOCK_PADDING
    current_row = 0

    for col_pos, id_ in enumerate(ordered_nodes):
        _px, py = _calculate_block_position(columns, col_pos)
        if py != current_row:
            current_row = py
            starting_pos_x = -BLOCK_PADDING
        cx = starting_pos_x + BLOCK_PADDING + half_w
        cy = -root_h / 2.0 + py * (max_h + BLOCK_PADDING) + max_h / 2.0 + BLOCK_PADDING
        node_layout[id_] = (cx, cy)
        starting_pos_x = cx + half_w

    # --- Phase 4: findBounds (grok L100-L121) ---
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf
    for id_ in ordered_nodes:
        cx, cy = node_layout[id_]
        w, h = node_sizes[id_]
        if cx - w / 2.0 < min_x:
            min_x = cx - w / 2.0
        if cy - h / 2.0 < min_y:
            min_y = cy - h / 2.0
        if cx + w / 2.0 > max_x:
            max_x = cx + w / 2.0
        if cy + h / 2.0 > max_y:
            max_y = cy + h / 2.0
    bounds_w = max_x - min_x
    bounds_h = max_y - min_y

    vb_x = min_x - VB_MARGIN
    vb_y = min_y - VB_MARGIN
    vb_w = bounds_w + VB_MARGIN * 2.0
    vb_h = bounds_h + VB_MARGIN * 2.0

    # --- theme channel normalization (grok L123-L132) ---
    background_color = "white" if theme.background == "#ffffff" else theme.background
    text_color = "#333" if theme.text_color == "#333333" else theme.text_color

    # --- SVG emission (grok L134-L245) ---
    svg: list[str] = []

    svg.append(
        '<svg aria-roledescription="block" role="graphics-document document" '
        f'viewBox="{_fmt(vb_x)} {_fmt(vb_y)} {_fmt(vb_w)} {_fmt(vb_h)}" '
        f'style="max-width: {_fmt(vb_w)}px; background-color: {background_color};" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns="http://www.w3.org/2000/svg" width="100%" id="my-svg">'
    )

    svg.append(
        f"<style>{_block_css(text_color, theme.edge_color, theme.node_fill, theme.node_stroke)}</style>"
    )

    svg.append("<g/>")
    svg.append(
        '<marker orient="auto" markerHeight="12" markerWidth="12" '
        'markerUnits="userSpaceOnUse" refY="5" refX="6" viewBox="0 0 10 10" '
        'class="marker block" id="my-svg_block-pointEnd">'
        '<path style="stroke-width: 1; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" d="M 0 0 L 10 5 L 0 10 z"/></marker>'
    )
    svg.append(
        '<marker orient="auto" markerHeight="12" markerWidth="12" '
        'markerUnits="userSpaceOnUse" refY="5" refX="4.5" viewBox="0 0 10 10" '
        'class="marker block" id="my-svg_block-pointStart">'
        '<path style="stroke-width: 1; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" d="M 0 5 L 10 10 L 10 0 z"/></marker>'
    )
    svg.append(
        '<marker orient="auto" markerHeight="11" markerWidth="11" '
        'markerUnits="userSpaceOnUse" refY="5" refX="11" viewBox="0 0 10 10" '
        'class="marker block" id="my-svg_block-circleEnd">'
        '<circle style="stroke-width: 1; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" r="5" cy="5" cx="5"/></marker>'
    )
    svg.append(
        '<marker orient="auto" markerHeight="11" markerWidth="11" '
        'markerUnits="userSpaceOnUse" refY="5" refX="-1" viewBox="0 0 10 10" '
        'class="marker block" id="my-svg_block-circleStart">'
        '<circle style="stroke-width: 1; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" r="5" cy="5" cx="5"/></marker>'
    )
    svg.append(
        '<marker orient="auto" markerHeight="11" markerWidth="11" '
        'markerUnits="userSpaceOnUse" refY="5.2" refX="12" viewBox="0 0 11 11" '
        'class="marker cross block" id="my-svg_block-crossEnd">'
        '<path style="stroke-width: 2; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" d="M 1,1 l 9,9 M 10,1 l -9,9"/></marker>'
    )
    svg.append(
        '<marker orient="auto" markerHeight="11" markerWidth="11" '
        'markerUnits="userSpaceOnUse" refY="5.2" refX="-1" viewBox="0 0 11 11" '
        'class="marker cross block" id="my-svg_block-crossStart">'
        '<path style="stroke-width: 2; stroke-dasharray: 1, 0;" '
        'class="arrowMarkerPath" d="M 1,1 l 9,9 M 10,1 l -9,9"/></marker>'
    )

    svg.append('<g class="block">')

    # --- render nodes (grok L172-L195) ---
    for id_ in ordered_nodes:
        pos = node_layout.get(id_)
        if pos is None:
            continue
        cx, cy = pos
        w, h = node_sizes.get(id_, (0.0, 0.0))
        label = diagram.nodes.get(id_, id_)

        svg.append(
            f'<g class="node default default flowchart-label" id="{_escape_xml(id_)}" '
            f'transform="translate({_fmt(cx)}, {_fmt(cy)})">'
        )
        svg.append(
            f'<rect class="basic label-container" style="" rx="0" ry="0" '
            f'x="{_fmt(-w / 2.0)}" y="{_fmt(-h / 2.0)}" '
            f'width="{_fmt(w)}" height="{_fmt(h)}"/>'
        )
        svg.append(
            '<g class="label" style="">'
            '<text text-anchor="middle" dominant-baseline="central" '
            f'class="nodeLabel" dy="0">{_escape_xml(label)}</text></g>'
        )
        svg.append("</g>")

    # --- render edges (grok L200-L243) ---
    # Three points [start_center, midpoint, end_center], clipped via
    # rect_intersect, end shifted back by ARROW_POINT_OFFSET, then drawn as a
    # D3 curveBasis spline.
    for idx, (frm, to) in enumerate(diagram.edges):
        fpos = node_layout.get(frm)
        if fpos is None:
            continue
        tpos = node_layout.get(to)
        if tpos is None:
            continue
        fx, fy = fpos
        tx, ty = tpos
        fw, fh = node_sizes.get(frm, (0.0, 0.0))
        tw, th = node_sizes.get(to, (0.0, 0.0))

        mid_x = fx + (tx - fx) / 2.0
        mid_y = fy + (ty - fy) / 2.0

        start = _rect_intersect(fx, fy, fw, fh, mid_x, mid_y)
        end = _rect_intersect(tx, ty, tw, th, mid_x, mid_y)

        # Apply arrow_point marker offset to the end point (grok L216-L229).
        edge_dx = end[0] - start[0]
        edge_dy = end[1] - start[1]
        edge_len = math.sqrt(edge_dx * edge_dx + edge_dy * edge_dy)
        if edge_len > 1e-9:
            offset_end = (
                end[0] - ARROW_POINT_OFFSET * edge_dx / edge_len,
                end[1] - ARROW_POINT_OFFSET * edge_dy / edge_len,
            )
        else:
            offset_end = end

        points = [start, (mid_x, mid_y), offset_end]
        d = _curve_basis_path(points)

        edge_no = idx + 1
        ls = f"{frm.lower()}1"
        le = f"{to.lower()}1"

        svg.append(
            f'<path marker-end="url(#my-svg_block-pointEnd)" '
            f'class="edge-thickness-normal edge-pattern-solid flowchart-link LS-{ls} LE-{le}" '
            f'id="{edge_no}-{_escape_xml(frm)}-{_escape_xml(to)}" d="{d}"/>'
        )

    svg.append("</g></svg>")

    return "".join(svg)
