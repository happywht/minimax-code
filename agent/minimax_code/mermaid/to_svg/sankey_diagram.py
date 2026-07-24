"""Sankey diagram renderer -- behavioral-equivalent port of grok's ``sankey_diagram.rs``.

Direction (1) brick 17 (R284). The sixth per-diagram leaf and the fifth
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``,
R281's ``radar``, R282's ``pie``, and R283's ``packet``; unlike R280's
``stateDiagram`` parser which rides the dagre stack). The ``sankey-beta``
diagram is mermaid's flow / Sankey visualizer: a set of weighted directed
links ``source,target,value`` between named nodes, laid out left-to-right by
longest-path depth, with each node a vertical bar whose height is proportional
to its throughput and each link a gradient-stroked cubic Bezier ribbon whose
thickness is proportional to its flow value. Pure iterative geometry (no
dagre, no AST), so this leaf emits SVG directly from the parsed model.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_sankey_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_sankey_diagram_to_svg(src, theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to "return
  the SVG / raise :class:`ParseError`".
* grok's five private structs ``SankeyDiagram`` / ``SankeyLink`` /
  ``SankeyNodeLayout`` / ``SankeyLinkLayout`` / ``SankeyLayout`` -> the five
  like-named ``frozen`` dataclasses (grok's are plain ``#[derive(Debug,
  Clone)] struct``s; frozen mirrors their construct-once use).
* grok ``parse_sankey`` / ``compute_layout`` / ``escape_xml`` /
  ``format_sankey_node_label`` -> module-level Python functions
  (``parse_sankey_diagram`` / ``_compute_layout`` public/private split;
  helpers private with a leading underscore).
* grok's six geometry constants (``WIDTH`` / ``HEIGHT`` / ``NODE_WIDTH`` /
  ``NODE_PADDING`` / ``LABEL_OFFSET`` + the 10-entry ``NODE_COLORS`` palette)
  -> module constants verbatim.

Theme wiring (unlike R282 pie / R283 packet)
--------------------------------------------

Unlike the pie and packet renderers (which hard-code their palette and take an
unused ``_theme``), sankey **is** theme-aware: grok threads
``theme.background`` into the SVG root ``style="background-color: {...}"`` and
the full-canvas ``<rect fill="{...}"/>``. So the ``theme`` param is bound
(named ``theme``, not ``_theme``) and its ``background`` field is read in two
emission sites -- mirrors grok L27-L28 and L34-L35.

Float formatting bridge
-----------------------

Like R281's radar, R282's pie, and R283's packet, integer-valued floats
(viewBox dims, node / link geometry, Bezier control points) route through
:func:`_fmt`, which collapses ``600.0`` -> ``"600"`` (Rust ``Display`` drops
the trailing ``.0``; Python ``str`` keeps it). Node ``depth`` and
``SankeyLayout.max_depth`` are ``usize`` in grok and stay Python ``int`` in
the port, so they never reach :func:`_fmt`.

f64 parse bridge
----------------

grok parses link values with ``str::parse::<f64>()``, which accepts the
standard decimal / exponent grammar but **rejects** digit-group underscores
(``1_000`` -- Python ``float()`` accepts them per PEP 515). :func:`_parse_f64`
gates on a strict full-match so the two agree on every token a mermaid
``sankey-beta`` body can carry. grok's ``parse::<f64>`` also accepts the
``inf`` / ``nan`` special names; the port rejects them (mermaid flow values
are finite positives by semantics -- a documented non-issue).

f64 fract / EPSILON bridge
--------------------------

grok's ``format_sankey_node_label`` picks the integer arm when
``value.fract().abs() < f64::EPSILON`` (a tolerance check, not an exact
``is_integer``). Python's ``float.is_integer()`` is exact, so it would
disagree on a throughput that accumulated to a near-integer with sub-EPSILON
drift. :func:`_format_node_label` mirrors the tolerance check verbatim
(``abs(value - trunc(value)) < EPSILON``), and the integer arm formats via
``int(value)`` (truncation, mirroring grok's ``value as i64``).

Ordered-collection bridge
-------------------------

grok leans on three ordered collection types whose Python analogues differ:

* ``BTreeSet<String> node_seen`` -- only used for its ``insert`` boolean
  (was-new?) to drive ``node_order`` push. A Python ``set`` + ``not in``
  check is the behavioral twin (iteration order of ``node_seen`` is never
  observed; ``node_order`` is the canonical order).
* ``BTreeMap<usize, Vec<&str>> nodes_by_depth`` -- keyed by depth, iterated
  in ascending key order to assign per-layer ``y`` origins and ``x`` columns.
  A Python ``dict`` preserves insertion order, not key order, so the layout
  loop iterates ``sorted(nodes_by_depth.items())`` to reproduce BTreeMap
  traversal. Within a depth the node list is ``node_order`` order (grok
  pushes in that order; Python ``setdefault(...).append(...)`` matches).
* ``HashMap`` for ``in_sum`` / ``out_sum`` / ``value_by_node`` / ``preds`` /
  ``depth`` / offsets -- iteration order is not observed (each is read by
  key), so a Python ``dict`` is a faithful twin.

Dispatch wiring (R284)
----------------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer, mirroring grok lib.rs L47
(the per-diagram dispatch contract lifted in R282). The token
``sankey-beta`` leaves ``_UNSUPPORTED_DIAGRAM_TYPES`` (19 -> 18 tokens).

Public surface (1 symbol): :func:`render_sankey_diagram_to_svg`. The
dataclasses, parser, layout, and helpers stay out of ``__all__``; the
renderer is reached only through the dispatch arm added in R284.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_sankey_diagram_to_svg"]

#: Canvas geometry (grok L6-L10). Carried as ``float`` because the SVG
#: viewBox / node / link geometry is emitted via Rust ``Display`` (drops
#: ``.0``); :func:`_fmt` reproduces that rendering.
WIDTH: float = 600.0
HEIGHT: float = 400.0
#: Vertical bar width for each node (grok ``NODE_WIDTH``).
NODE_WIDTH: float = 10.0
#: Vertical gap between nodes in the same depth column (grok ``NODE_PADDING``).
NODE_PADDING: float = 25.0
#: Horizontal gap between a node bar and its text label (grok ``LABEL_OFFSET``).
LABEL_OFFSET: float = 6.0

#: The 10-color node palette (grok L12-L15). Indexed by a node's position in
#: ``node_order``; positions >= 10 fall back to the first entry (grok
#: ``NODE_COLORS.get(idx).unwrap_or(NODE_COLORS[0])`` -- a fallback, not a
#: modulo wrap).
NODE_COLORS: tuple[str, ...] = (
    "#4e79a7",
    "#f28e2c",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#9c755f",
    "#bab0ab",
    "#ff9da7",
)

#: ``f64::EPSILON`` (2.220446049250313e-16) -- the tolerance
#: :func:`_format_node_label` uses to detect an integer-valued throughput,
#: mirroring grok's ``value.fract().abs() < f64::EPSILON`` arm.
_EPSILON: float = 2.220446049250313e-16

#: Strict Rust-``f64`` grammar (no digit-group underscores, no ``inf`` /
#: ``nan``). Optional sign, then ``digits.digits`` / ``.digits`` /
#: ``digits``, then an optional exponent. :func:`_parse_f64` gates on a
#: full-match so ``"1_000"`` rejects the way grok's ``parse::<f64>()`` does.
_F64_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class SankeyLink:
    """One weighted directed edge (grok ``SankeyLink``).

    ``value`` is the flow magnitude (``f64`` in grok, ``float`` here); the
    link ribbon's stroke thickness is ``value * ky`` after layout.
    """

    source: str
    target: str
    value: float


@dataclass(frozen=True)
class SankeyDiagram:
    """Parsed sankey model (grok ``SankeyDiagram``).

    ``links`` is the raw edge list in source order; ``node_order`` is every
    distinct endpoint in first-seen order (drives palette indexing and the
    layout's column assignment).
    """

    links: list[SankeyLink]
    node_order: list[str]


@dataclass(frozen=True)
class SankeyNodeLayout:
    """Per-node layout product (grok ``SankeyNodeLayout``).

    ``depth`` is the longest-path hop count from a source (``usize`` in
    grok, ``int`` here); ``x`` / ``y`` / ``height`` are canvas coordinates;
    ``color`` is the resolved palette entry; ``dom_id`` is the SVG element
    id (``node-{global_idx+1}``); ``display_label`` is the text under the
    bar (name + throughput).
    """

    name: str
    display_label: str
    dom_id: str
    depth: int
    x: float
    y: float
    height: float
    color: str


@dataclass(frozen=True)
class SankeyLinkLayout:
    """Per-link geometry (grok ``SankeyLinkLayout``).

    ``x0`` / ``y0`` anchor the cubic Bezier at the source bar's right edge;
    ``x1`` / ``y1`` anchor it at the target bar's left edge; ``thickness``
    is the stroke width; the two colors drive the per-link linear gradient.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    thickness: float
    source_color: str
    target_color: str


@dataclass(frozen=True)
class SankeyLayout:
    """Full layout (grok ``SankeyLayout``).

    ``nodes`` is sorted by ``(depth, y, name)`` (grok L408-L413); ``links``
    preserves source order; ``max_depth`` drives the label-anchor choice
    (``end`` for the rightmost column, ``start`` otherwise).
    """

    nodes: list[SankeyNodeLayout]
    links: list[SankeyLinkLayout]
    max_depth: int


def _fmt(value: float) -> str:
    """Render a float the way Rust ``Display`` would (R281/R282/R283 bridge).

    Integer-valued floats drop the trailing ``.0`` (``600.0`` -> ``"600"``);
    non-integers fall back to ``repr`` (shortest round-trippable decimal,
    matching Rust's ``Display`` for f64).
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note: grok's ``sankey_diagram.rs`` maps ``'`` -> ``&#39;`` (numeric
    character reference), matching R281's radar and unlike R282's pie /
    R283's packet (``&apos;``). All three faithfully mirror their respective
    grok source -- the Rust functions disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _parse_f64(raw: str, line: int) -> float:
    """Parse a link value the way Rust ``str::parse::<f64>()`` would.

    Accepts the standard decimal / exponent grammar (``5``, ``3.5``, ``.5``,
    ``5.``, ``1e3``, ``+1.5``, ``-5``) but rejects digit-group underscores
    (``1_000``) and the ``inf`` / ``nan`` specials -- mirroring grok's
    strictness on the tokens a mermaid body carries. The error message embeds
    ``raw`` verbatim (grok formats ``parts[2]`` post-``.trim()``; the caller
    passes the already-trimmed token).
    """
    if _F64_RE.fullmatch(raw):
        return float(raw)
    raise ParseError(line, f"Invalid sankey value: {raw}")


def _format_node_label(name: str, value: float) -> str:
    """Format a node's display label (grok ``format_sankey_node_label``).

    Integer-valued throughputs (``value.fract().abs() < f64::EPSILON`` in
    grok) render as ``"{name} {value as i64}"`` (truncated, no decimal);
    everything else renders as ``"{name} {value}"``. The tolerance check
    (not Python's exact ``is_integer()``) mirrors grok so a throughput that
    accumulated to a sub-EPSILON-near integer lands on the same arm.
    """
    if abs(value - math.trunc(value)) < _EPSILON:
        return f"{name} {int(value)}"
    return f"{name} {_fmt(value)}"


def parse_sankey_diagram(input: str) -> SankeyDiagram:
    """Parse mermaid ``sankey-beta`` source into a :class:`SankeyDiagram` (grok ``parse_sankey``).

    Scans for the ``sankey-beta`` header token, then reads ``source,target,
    value`` link lines. Blank / ``%%`` lines are skipped. Each link line
    must split into exactly three comma-separated parts (else ``Invalid
    sankey link``); the value must parse as ``f64`` (else ``Invalid sankey
    value``). Endpoints enter ``node_order`` in first-seen order. At least
    one link is required.

    Raises:
        ParseError: when the first substantial token is not ``sankey-beta``;
            a link line does not have exactly three comma-separated parts;
            the value is not a valid ``f64``; or the diagram has zero links.
    """
    found_header = False
    links: list[SankeyLink] = []
    node_seen: set[str] = set()
    node_order: list[str] = []

    for idx, raw in enumerate(input.splitlines()):
        line_no = idx + 1
        line = raw.strip()

        if not line or line.startswith("%%"):
            continue

        if not found_header:
            # grok L179: ``line.split_whitespace().next()`` -- the first
            # whitespace token must be the ``sankey-beta`` declaration.
            if line.split()[0] != "sankey-beta":
                raise ParseError(line_no, "Expected 'sankey-beta' declaration")
            found_header = True
            continue

        # grok L189: split on ``,`` and trim each part -> exactly 3 fields.
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            raise ParseError(line_no, f"Invalid sankey link: {line}")

        source = parts[0]
        target = parts[1]
        value = _parse_f64(parts[2], line_no)

        # grok L204-L209: BTreeSet.insert returns whether the value was new;
        # Python's set has no such return, so the ``not in`` check mirrors it.
        if source not in node_seen:
            node_seen.add(source)
            node_order.append(source)
        if target not in node_seen:
            node_seen.add(target)
            node_order.append(target)

        links.append(SankeyLink(source=source, target=target, value=value))

    if not found_header:
        raise ParseError(1, "Expected 'sankey-beta' declaration")

    if not links:
        raise ParseError(1, "sankey diagram requires at least one link")

    return SankeyDiagram(links=links, node_order=node_order)


def _compute_layout(diagram: SankeyDiagram) -> SankeyLayout:
    """Lay the sankey model out on the fixed canvas (grok ``compute_layout``).

    Computes each node's throughput (max of in-sum / out-sum), its longest-
    path depth from a source, a per-layer vertical scale ``ky`` (the tightest
    fit across all depth columns), and per-link Bezier anchors with stroke
    thickness proportional to flow value. Mirrors grok L235-L420.
    """
    # Throughput accumulation (grok L236-L249).
    in_sum: dict[str, float] = {}
    out_sum: dict[str, float] = {}
    for link in diagram.links:
        out_sum[link.source] = out_sum.get(link.source, 0.0) + link.value
        in_sum[link.target] = in_sum.get(link.target, 0.0) + link.value

    value_by_node: dict[str, float] = {}
    for node in diagram.node_order:
        v_in = in_sum.get(node, 0.0)
        v_out = out_sum.get(node, 0.0)
        value_by_node[node] = max(v_in, v_out)

    # Predecessor adjacency (grok L251-L258). Note the ``or_default()`` on the
    # source too -- every node gets a key, even source-only nodes with an
    # empty predecessor list.
    preds: dict[str, list[str]] = {}
    for link in diagram.links:
        preds.setdefault(link.target, []).append(link.source)
        preds.setdefault(link.source, [])

    # Longest-path depth via iterative relaxation (grok L260-L283). At most
    # ``2 * node_count`` passes with an early-out once no depth changes.
    depth: dict[str, int] = {node: 0 for node in diagram.node_order}
    changed = True
    for _ in range(2 * len(diagram.node_order)):
        if not changed:
            break
        changed = False
        for node in diagram.node_order:
            p = preds.get(node, [])
            d = 0
            for pred in p:
                d = max(d, depth.get(pred, 0) + 1)
            if depth.get(node, 0) != d:
                depth[node] = d
                changed = True

    max_depth = max(depth.values(), default=0)
    layers = max(max_depth, 1) + 1

    # Group nodes by depth (grok L288-L292). BTreeMap ascending key order is
    # reproduced below via ``sorted``; within a depth, ``node_order`` order.
    nodes_by_depth: dict[int, list[str]] = {}
    for node in diagram.node_order:
        d = depth.get(node, 0)
        nodes_by_depth.setdefault(d, []).append(node)

    # Per-layer vertical scale ``ky`` (grok L294-L309): the minimum over all
    # depth columns of ``available_height / throughput_sum``, skipping empty
    # columns. Non-finite (all columns empty) -> 1.0.
    ky = math.inf
    for nodes in nodes_by_depth.values():
        s = sum(value_by_node.get(n, 0.0) for n in nodes)
        if s <= 0.0:
            continue
        n = float(len(nodes))
        available = HEIGHT - max(n - 1.0, 0.0) * NODE_PADDING
        ky = min(ky, available / s)
    if not math.isfinite(ky):
        ky = 1.0

    # Per-node geometry (grok L311-L358). Each depth column is centered
    # vertically (``y`` starts at ``(HEIGHT - used) / 2``); ``x`` is the
    # column's horizontal slot (``0`` when there is a single layer).
    node_layout: dict[str, SankeyNodeLayout] = {}
    for d in sorted(nodes_by_depth.keys()):
        nodes = nodes_by_depth[d]
        s = sum(value_by_node.get(n, 0.0) for n in nodes)
        used = s * ky + max(len(nodes) - 1, 0) * NODE_PADDING
        y = (HEIGHT - used) / 2.0

        if layers <= 1:
            x = 0.0
        else:
            x = (WIDTH - NODE_WIDTH) * float(d) / float(layers - 1)

        for name in nodes:
            v = value_by_node.get(name, 0.0)
            h = v * ky

            # grok L331-L335: position in ``node_order`` (always found;
            # ``unwrap_or(0)`` is defensive -- mirrored with a try/except).
            try:
                global_idx = diagram.node_order.index(name)
            except ValueError:
                global_idx = 0
            dom_id = f"node-{global_idx + 1}"
            # grok L337-L341: palette fallback to the first entry (not modulo).
            if global_idx < len(NODE_COLORS):
                color = NODE_COLORS[global_idx]
            else:
                color = NODE_COLORS[0]

            node_layout[name] = SankeyNodeLayout(
                name=name,
                display_label=_format_node_label(name, v),
                dom_id=dom_id,
                depth=d,
                x=x,
                y=y,
                height=h,
                color=color,
            )
            y += h + NODE_PADDING

    # Per-link geometry (grok L360-L400). Thickness = value * ky; out / in
    # offsets accumulate so parallel links stack without overlap; the Bezier
    # anchors sit at the source bar's right edge and the target bar's left
    # edge, vertically centered on the link's slice of each node.
    out_offset: dict[str, float] = {node: 0.0 for node in diagram.node_order}
    in_offset: dict[str, float] = {node: 0.0 for node in diagram.node_order}

    link_layouts: list[SankeyLinkLayout] = []
    for link in diagram.links:
        source_node = node_layout.get(link.source)
        if source_node is None:
            continue
        target_node = node_layout.get(link.target)
        if target_node is None:
            continue

        thickness = link.value * ky

        so = out_offset.get(link.source, 0.0)
        ti = in_offset.get(link.target, 0.0)

        y0 = source_node.y + so + thickness / 2.0
        y1 = target_node.y + ti + thickness / 2.0

        out_offset[link.source] = so + thickness
        in_offset[link.target] = ti + thickness

        x0 = source_node.x + NODE_WIDTH
        x1 = target_node.x

        link_layouts.append(
            SankeyLinkLayout(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                thickness=thickness,
                source_color=source_node.color,
                target_color=target_node.color,
            )
        )

    # Final node ordering: (depth, y, name) -- grok L402-L413.
    nodes_vec = [node_layout[n] for n in diagram.node_order if n in node_layout]
    nodes_vec.sort(key=lambda nd: (nd.depth, nd.y, nd.name))

    return SankeyLayout(nodes=nodes_vec, links=link_layouts, max_depth=max_depth)


def render_sankey_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render a mermaid ``sankey-beta`` diagram into an SVG string (grok
    ``render_sankey_diagram_to_svg``).

    Parses the source, lays the flow graph out on the fixed 600x400 canvas
    (longest-path depth columns, throughput-proportional bar heights,
    flow-proportional ribbon thicknesses), and emits the SVG: a theme-tinted
    background, one ``<linearGradient>`` per link, one vertical-bar ``<g>``
    per node with its label, and one cubic-Bezier ``<path>`` per link stroked
    with its gradient.

    Args:
        mermaid_source: mermaid ``sankey-beta`` source. The dispatch in
            :func:`render.render_mermaid_to_svg` passes the front-matter-
            stripped body (mirroring grok lib.rs L47, which shadows
            ``mermaid_source`` to ``parsed_source.body`` before the sankey
            arm).
        theme: the resolved :class:`MermaidTheme` palette. Unlike the pie /
            packet renderers, sankey **is** theme-aware: ``theme.background``
            flows into the SVG root ``style`` and the full-canvas background
            rect fill (grok L27-L28, L34-L35).

    Returns:
        The sankey-diagram SVG string.

    Raises:
        ParseError: propagated from :func:`parse_sankey_diagram`.
    """
    diagram = parse_sankey_diagram(mermaid_source)
    layout = _compute_layout(diagram)

    svg: list[str] = []

    svg.append(
        f'<svg aria-roledescription="sankey" role="graphics-document document" '
        f'viewBox="0 0 {_fmt(WIDTH)} {_fmt(HEIGHT)}" '
        f'style="max-width: {_fmt(WIDTH)}px; background-color: {theme.background};" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" width="100%" id="my-svg">'
    )

    svg.append("<g/>")

    svg.append(
        f'<rect x="0" y="0" width="{_fmt(WIDTH)}" height="{_fmt(HEIGHT)}" '
        f'fill="{theme.background}"/>'
    )

    # One linear gradient per link (grok L38-L56): source color at 0%,
    # target color at 100%, oriented along the link's x-span.
    svg.append("<defs>")
    for idx, link in enumerate(layout.links):
        grad_id = f"linearGradient-{idx}"
        svg.append(
            f'<linearGradient id="{grad_id}" gradientUnits="userSpaceOnUse" '
            f'x1="{_fmt(link.x0)}" x2="{_fmt(link.x1)}">'
        )
        svg.append(f'<stop offset="0%" stop-color="{_escape_xml(link.source_color)}"/>')
        svg.append(f'<stop offset="100%" stop-color="{_escape_xml(link.target_color)}"/>')
        svg.append("</linearGradient>")
    svg.append("</defs>")

    # Node bars (grok L58-L76): one translated ``<g>`` per node with a
    # palette-filled rectangle whose height is the throughput.
    svg.append('<g class="nodes">')
    for node in layout.nodes:
        svg.append(
            f'<g class="node" id="{_escape_xml(node.dom_id)}" '
            f'transform="translate({_fmt(node.x)},{_fmt(node.y)})" '
            f'x="{_fmt(node.x)}" y="{_fmt(node.y)}">'
        )
        svg.append(
            f'<rect height="{_fmt(node.height)}" width="{_fmt(NODE_WIDTH)}" '
            f'fill="{_escape_xml(node.color)}"/>'
        )
        svg.append("</g>")
    svg.append("</g>")

    # Node labels (grok L78-L95): rightmost column anchored ``end`` (label
    # left of the bar), every other column anchored ``start`` (label right
    # of the bar); both vertically centered on the bar.
    svg.append('<g class="node-labels" font-size="14">')
    for node in layout.nodes:
        center_y = node.y + node.height / 2.0
        if node.depth == layout.max_depth:
            x = node.x - LABEL_OFFSET
            svg.append(
                f'<text x="{_fmt(x)}" y="{_fmt(center_y)}" dy="0em" '
                f'text-anchor="end">{_escape_xml(node.display_label)}</text>'
            )
        else:
            x = node.x + NODE_WIDTH + LABEL_OFFSET
            svg.append(
                f'<text x="{_fmt(x)}" y="{_fmt(center_y)}" dy="0em" '
                f'text-anchor="start">{_escape_xml(node.display_label)}</text>'
            )
    svg.append("</g>")

    # Link ribbons (grok L97-L116): one cubic Bezier per link with control
    # points at the column midpoint, stroked with the link's gradient and a
    # width equal to the flow thickness; ``mix-blend-mode: multiply`` gives
    # the overlapping-ribbon darkening sankey diagrams are known for.
    svg.append('<g class="links" fill="none" stroke-opacity="0.5">')
    for idx, link in enumerate(layout.links):
        grad_id = f"linearGradient-{idx}"
        mx = (link.x0 + link.x1) / 2.0
        d = (
            f"M{_fmt(link.x0)},{_fmt(link.y0)}"
            f"C{_fmt(mx)},{_fmt(link.y0)},"
            f"{_fmt(mx)},{_fmt(link.y1)},"
            f"{_fmt(link.x1)},{_fmt(link.y1)}"
        )
        svg.append('<g class="link" style="mix-blend-mode: multiply;">')
        svg.append(
            f'<path d="{d}" stroke="url(#{grad_id})" stroke-width="{_fmt(link.thickness)}"/>'
        )
        svg.append("</g>")
    svg.append("</g>")

    svg.append("</svg>")
    return "".join(svg)
