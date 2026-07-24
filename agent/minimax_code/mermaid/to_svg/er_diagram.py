"""ER diagram renderer (R295) -- functional clone of grok ``er_diagram.rs``.

Direction (1) brick 26 -- the 16th self-contained SVG emitter and the *second*
renderer to drive the dagre layout engine directly (after R294
``requirementDiagram``). Mirrors grok ``mermaid-to-svg/src/er_diagram.rs``
(936 lines): an entity-relationship diagram parser that collects entity
tables (header + ``type name`` attribute rows) and binary relationships
annotated with crow's-foot cardinality, then hands the AST to dagre for
ranked placement and emits an SVG whose edges carry the eight classic
ER markers (onlyOne / zeroOrOne / oneOrMore / zeroOrMore x Start / End).

Functional contract preserved, implementation re-expressed Pythonically:

* Rust ``enum`` cardinality/identification -> :class:`enum.Enum`.
* Rust ``struct`` AST nodes -> :class:`dataclasses.dataclass`.
* Rust ``BTreeMap`` / ``BTreeSet`` -> ordered :class:`dict` (rebuilt in
  sorted-key order to match BTreeMap's name ordering) / :class:`set`.
* Rust ``f64`` ``Display`` (``10.0`` -> ``"10"``) -> :func:`_fmt` helper.
* The dagre graph is built with ``compound=False`` (ER diagrams carry no
  subgraphs), the same strategy R294 uses to stay off the
  ``network_simplex._exchange_edges`` ``None -= 1`` pathologised branch.

Public surface: the single symbol :func:`render_er_diagram_to_svg`.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.mod import layout as dagre_layout
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.text_wrap import DEFAULT_CHAR_WIDTH, line_width
from minimax_code.mermaid.to_svg.theme import MermaidTheme

__all__ = ["render_er_diagram_to_svg"]


# === constants (grok L11-L19) ==============================================
#
# Layout metrics inherited verbatim from grok. ER tables use a larger
# ``NODE_SEP`` / ``RANK_SEP`` than flowcharts because the wide two-column
# attribute tables need horizontal breathing room, and ``LINE_HEIGHT`` is a
# non-integer (36.75) so attribute rows tile evenly under the 16px font.
_PADDING: float = 10.0  #: Outer padding around a bare entity name (grok ``PADDING``).
_TEXT_PADDING: float = 6.0  #: Vertical text padding inside a row (grok ``TEXT_PADDING``).
_NODE_SEP: float = 140.0  #: Horizontal gap between entity columns (grok ``NODE_SEP``).
_RANK_SEP: float = 80.0  #: Vertical gap between entity ranks (grok ``RANK_SEP``).
_EDGE_SEP: float = 20.0  #: Gap between parallel edges (grok inline ``edgesep: 20.0``).
_GRAPH_MARGIN: float = 8.0  #: Canvas margin around the bbox (grok ``GRAPH_MARGIN``).
_LINE_HEIGHT: float = 36.75  #: One attribute row's height (grok ``LINE_HEIGHT``).
_MIN_ENTITY_WIDTH: float = 100.0  #: Floor on entity box width (grok ``MIN_ENTITY_WIDTH``).
_FONT_SIZE: int = 16  #: Root CSS font-size (grok ``FONT_SIZE``).
_COLUMN_TEXT_PADDING: float = 8.0  #: Left/right inset of each column's text (grok ``COLUMN_TEXT_PADDING``).


# === AST enums (grok L23-L46) ==============================================


class Cardinality(enum.Enum):
    """The four crow's-foot cardinalities (grok ``Cardinality``).

    Each variant's value is the marker-name fragment spliced into the SVG
    marker ids (``my-svg_er-{name}Start`` / ``{name}End``), reproducing
    grok's ``marker_name()`` match arms without a method.
    """

    ZeroOrOne = "zeroOrOne"
    ZeroOrMore = "zeroOrMore"
    OneOrMore = "oneOrMore"
    OnlyOne = "onlyOne"

    def marker_name(self) -> str:
        """Return the marker-id fragment (grok ``marker_name`` L33-L39)."""
        return self.value


class Identification(enum.Enum):
    """Identifying (``--``) vs non-identifying (``..``) relationship (grok L42-L46)."""

    Identifying = "identifying"
    NonIdentifying = "non_identifying"


# === AST dataclasses (grok L48-L79) ========================================


@dataclass
class _RelSpec:
    """One relationship's parsed cardinality pair + identification kind."""

    card_a: Cardinality
    card_b: Cardinality
    rel_type: Identification


@dataclass
class _Attribute:
    """A single ``type name`` row inside an entity table (grok ``Attribute``)."""

    attr_type: str
    name: str


@dataclass
class _Entity:
    """An entity table: a header name plus its attribute rows (grok ``Entity``)."""

    id: str
    attributes: list[_Attribute] = field(default_factory=list)


@dataclass
class _Relationship:
    """A binary relationship ``A <spec> B : role`` (grok ``Relationship``)."""

    entity_a: str
    entity_b: str
    role: str
    rel_spec: _RelSpec


@dataclass
class _ErDiagram:
    """The whole parsed ER diagram (grok ``ErDiagram``).

    ``entities`` is kept in sorted-key order to mirror grok's ``BTreeMap``
    traversal (entity name ascending), which fixes both the node-insertion
    order fed to dagre and the node-draw order in the SVG.
    """

    entities: dict[str, _Entity] = field(default_factory=dict)
    relationships: list[_Relationship] = field(default_factory=list)


# === layout dataclasses (grok L83-L116) ====================================


@dataclass
class _EntityLayout:
    """A placed entity: centre + measured box + row geometry (grok ``EntityLayout``)."""

    id: str
    x: float
    y: float
    width: float
    height: float
    header_height: float
    max_type_width: float
    attributes: list[_Attribute]
    row_heights: list[float]


@dataclass
class _EdgeLayout:
    """A placed relationship: polyline points + optional label centre (grok ``EdgeLayout``)."""

    from_: str
    to: str
    role: str
    rel_spec: _RelSpec
    points: list[tuple[float, float]]
    label_pos: tuple[float, float] | None
    label_width: float
    label_height: float


@dataclass
class _DiagramLayout:
    """All placed entities + edges + canvas size (grok ``DiagramLayout``)."""

    entities: dict[str, _EntityLayout]
    edges: list[_EdgeLayout]
    width: float
    height: float


# === public entry (grok L120-L127) =========================================


def render_er_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render an ``erDiagram`` source string to a self-contained SVG.

    Functional clone of grok ``render_er_diagram_to_svg`` (L120-L127):
    parse -> dagre layout -> SVG emit. The ``ParseError`` raised by the
    parser propagates to the caller verbatim (grok returns it as
    ``Err(MermaidError::ParseError)``).
    """
    diagram = _parse_er_diagram(mermaid_source)
    layout = _compute_layout(diagram)
    return _render_svg(layout, theme)


# === parser (grok L131-L324) ===============================================


def _parse_er_diagram(input: str) -> _ErDiagram:
    """Parse an ER diagram source into the AST (grok ``parse_er_diagram`` L131-L245).

    Grammar (lines, ``%%`` comments and blanks skipped throughout):

    * Header -- the first non-blank / non-comment line whose first
      whitespace token is ``erDiagram``. Anything else is a parse error.
    * Entity block -- ``NAME {`` opens a table; subsequent ``type name``
      rows are attributes until a bare ``}`` closes it.
    * Relationship -- any line containing ``--`` or ``..``; parsed as
      ``ENTITY_A <spec> ENTITY_B : role``.
    * Entities referenced only by a relationship are auto-created empty.
    """
    lines = input.split("\n")
    i = 0

    # Find the ``erDiagram`` header (grok L136-L150).
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        first_token = line.split()[0] if line.split() else ""
        if first_token == "erDiagram":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'erDiagram' declaration")

    entities: dict[str, _Entity] = {}
    referenced_entities: set[str] = set()
    relationships: list[_Relationship] = []

    while i < len(lines):
        line = lines[i].strip()
        line_no = i + 1
        i += 1

        if not line or line.startswith("%%"):
            continue

        # Entity block: ``ENTITY_NAME {`` (grok L167-L211).
        if line.endswith("{"):
            name = line[:-1].strip()
            if not name:
                raise ParseError(line_no, "Expected entity name before '{'")

            attrs: list[_Attribute] = []
            while i < len(lines):
                attr_line = lines[i].strip()
                i += 1
                if not attr_line or attr_line.startswith("%%"):
                    continue
                if attr_line == "}":
                    break
                # Parse ``type name`` pairs (grok splitn(2, whitespace)).
                parts = attr_line.split(None, 1)
                if len(parts) >= 2:
                    attrs.append(_Attribute(attr_type=parts[0], name=parts[1].strip()))
                elif parts:
                    attrs.append(_Attribute(attr_type=parts[0], name=""))

            entities[name] = _Entity(id=name, attributes=attrs)
            continue

        if line == "}":
            continue

        # Relationship: ``ENTITY_A ||--o{ ENTITY_B : label`` (grok L218-L225).
        if "--" in line or ".." in line:
            rel = _parse_relationship(line, line_no)
            referenced_entities.add(rel.entity_a)
            referenced_entities.add(rel.entity_b)
            relationships.append(rel)
            continue

        raise ParseError(line_no, f"Unrecognized erDiagram line: {line}")

    # Auto-create empty entities for any referenced-only name (grok L233-L239).
    for eid in referenced_entities:
        if eid not in entities:
            entities[eid] = _Entity(id=eid, attributes=[])

    # Match grok's ``BTreeMap`` name-ascending traversal order.
    entities = dict(sorted(entities.items()))

    return _ErDiagram(entities=entities, relationships=relationships)


def _parse_relationship(line: str, line_no: int) -> _Relationship:
    """Parse ``ENTITY_A <spec> ENTITY_B : role`` (grok ``parse_relationship`` L247-L284).

    The optional ``: role`` suffix is split off first (empty when absent);
    the left-hand side is then three whitespace tokens -- entity_a, the
    cardinality spec, entity_b.
    """
    # Split on ``:`` to peel off the role label (grok split_once(':')).
    if ":" in line:
        lhs_raw, role_raw = line.split(":", 1)
        lhs = lhs_raw.strip()
        label = role_raw.strip()
        role = "" if not label else label
    else:
        lhs = line.strip()
        role = ""

    parts = lhs.split()
    if len(parts) < 3:
        raise ParseError(line_no, f"Invalid erDiagram relationship: {line}")

    entity_a = parts[0]
    rel_str = parts[1]
    entity_b = parts[2]
    rel_spec = _parse_rel_spec(rel_str, line_no)

    return _Relationship(
        entity_a=entity_a, entity_b=entity_b, role=role, rel_spec=rel_spec
    )


def _parse_rel_spec(spec: str, line_no: int) -> _RelSpec:
    """Parse ``cardA<sep>cardB`` where ``<sep>`` is ``--`` or ``..`` (grok L286-L311).

    ``--`` -> :attr:`Identification.Identifying`, ``..`` ->
    :attr:`Identification.NonIdentifying`; the left/right halves are each a
    two-character crow's-foot cardinality token.
    """
    # ``--`` = IDENTIFYING, ``..`` = NON_IDENTIFYING (grok L292-L301).
    idx = spec.find("--")
    if idx != -1:
        left_part = spec[:idx]
        rel_type = Identification.Identifying
        right_part = spec[idx + 2 :]
    else:
        idx = spec.find("..")
        if idx != -1:
            left_part = spec[:idx]
            rel_type = Identification.NonIdentifying
            right_part = spec[idx + 2 :]
        else:
            raise ParseError(line_no, f"Invalid relationship spec: {spec}")

    card_a = _parse_cardinality(left_part, line_no)
    card_b = _parse_cardinality(right_part, line_no)
    return _RelSpec(card_a=card_a, card_b=card_b, rel_type=rel_type)


def _parse_cardinality(token: str, line_no: int) -> Cardinality:
    """Map a two-char crow's-foot token to its :class:`Cardinality` (grok L313-L324).

    ``||`` -> OnlyOne; ``|o`` / ``o|`` -> ZeroOrOne; ``|{`` / ``}|`` ->
    OneOrMore; ``o{`` / ``}o`` -> ZeroOrMore.
    """
    if token == "||":
        return Cardinality.OnlyOne
    if token in ("|o", "o|"):
        return Cardinality.ZeroOrOne
    if token in ("|{", "}|"):
        return Cardinality.OneOrMore
    if token in ("o{", "}o"):
        return Cardinality.ZeroOrMore
    raise ParseError(line_no, f"Invalid cardinality: {token}")


# === layout computation (grok L328-L575) ===================================


def _compute_entity_metrics(
    entity: _Entity,
) -> tuple[float, float, float, float, list[float]]:
    """Measure one entity's box (grok ``compute_entity_metrics`` L328-L366).

    Returns ``(total_width, total_height, header_height, max_type_width,
    row_heights)``. The header is one ``LINE_HEIGHT + TEXT_PADDING`` row;
    each attribute contributes a fixed-height row whose width is the max
    type/name column across all rows (each column padded by
    ``COLUMN_TEXT_PADDING`` on both sides).
    """
    header_width = line_width(entity.id, DEFAULT_CHAR_WIDTH)
    header_height = _LINE_HEIGHT + _TEXT_PADDING

    if not entity.attributes:
        width = max(header_width + _PADDING * 2.0, _MIN_ENTITY_WIDTH)
        height = header_height + _PADDING
        return (width, height, header_height, 0.0, [])

    max_type_width = 0.0
    max_name_width = 0.0
    row_heights: list[float] = []
    for attr in entity.attributes:
        type_w = line_width(attr.attr_type, DEFAULT_CHAR_WIDTH)
        name_w = line_width(attr.name, DEFAULT_CHAR_WIDTH)
        max_type_width = max(max_type_width, type_w + _COLUMN_TEXT_PADDING * 2.0)
        max_name_width = max(max_name_width, name_w + _COLUMN_TEXT_PADDING * 2.0)
        row_heights.append(_LINE_HEIGHT + _TEXT_PADDING)

    attr_total_width = max_type_width + max_name_width
    content_width = max(attr_total_width, header_width + _PADDING * 2.0)
    total_width = max(content_width, _MIN_ENTITY_WIDTH)
    total_height = header_height + sum(row_heights)
    return (total_width, total_height, header_height, max_type_width, row_heights)


def _compute_layout(diagram: _ErDiagram) -> _DiagramLayout:
    """Hand the AST to dagre and read back positions (grok ``compute_layout`` L368-L524).

    Builds a dagre :class:`Graph` with ``compound=False`` (ER diagrams have
    no subgraphs), feeds each entity's measured box and each relationship's
    role label, runs the layout, then extracts entity centres, edge
    polylines, and edge-label centres. The whole drawing is finally
    translated into ``[margin, margin)`` space with the canvas sized to the
    span plus twice the margin.
    """
    # Per-entity measured geometry cached for the post-layout pass.
    entity_metrics: dict[str, tuple[float, float, float, float, list[float]]] = {
        eid: _compute_entity_metrics(entity)
        for eid, entity in diagram.entities.items()
    }

    # ``compound=False`` keeps dagre on the simple simplex branch (grok L375-L379).
    graph: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    graph.set_graph(
        GraphConfig(
            rankdir="tb",
            nodesep=_NODE_SEP,
            ranksep=_RANK_SEP,
            edgesep=_EDGE_SEP,
            marginx=_GRAPH_MARGIN,
            marginy=_GRAPH_MARGIN,
        )
    )

    for eid, (w, h, _hh, _mtw, _rh) in entity_metrics.items():
        graph.set_node(eid, GraphNode(width=w, height=h))

    edge_keys: list[tuple[str, str]] = []
    for rel in diagram.relationships:
        label_text = rel.role
        label_width = 0.0 if not label_text else line_width(label_text, DEFAULT_CHAR_WIDTH)
        label_height = 0.0 if not label_text else _LINE_HEIGHT
        edge_label = GraphEdge(
            labelpos="c", width=label_width, height=label_height
        )
        graph.set_edge(rel.entity_a, rel.entity_b, edge_label, None)
        edge_keys.append((rel.entity_a, rel.entity_b))

    dagre_layout(graph)

    # Read back node centres (grok L429-L434).
    positions: dict[str, tuple[float, float]] = {}
    for eid in entity_metrics:
        placed = graph.node(eid)
        if placed is not None:
            positions[eid] = (placed.x, placed.y)

    # Read back edge polylines + label centres (grok L436-L468).
    edges: list[_EdgeLayout] = []
    for idx, (src, dst) in enumerate(edge_keys):
        edge = graph.edge(src, dst, None)
        if edge is None:
            continue
        points = (
            [(p.x, p.y) for p in edge.points] if edge.points else []
        )
        edge_width = edge.width or 0.0
        edge_height = edge.height or 0.0
        if edge_width > 0.0 or edge_height > 0.0:
            label_pos: tuple[float, float] | None = (edge.x, edge.y)
        else:
            label_pos = None
        rel = diagram.relationships[idx]
        edges.append(
            _EdgeLayout(
                from_=src,
                to=dst,
                role=rel.role,
                rel_spec=rel.rel_spec,
                points=points,
                label_pos=label_pos,
                label_width=edge_width,
                label_height=edge_height,
            )
        )

    # Build entity layouts from cached metrics + dagre centres (grok L470-L494).
    entity_layouts: dict[str, _EntityLayout] = {}
    for eid, (x, y) in positions.items():
        metrics = entity_metrics.get(eid)
        if metrics is None:
            continue
        w, h, header_h, max_type_w, row_heights = metrics
        entity = diagram.entities.get(eid)
        attrs = entity.attributes if entity is not None else []
        entity_layouts[eid] = _EntityLayout(
            id=eid,
            x=x,
            y=y,
            width=w,
            height=h,
            header_height=header_h,
            max_type_width=max_type_w,
            attributes=attrs,
            row_heights=row_heights,
        )

    # Translate the bounding box into [margin, margin) space (grok L496-L516).
    min_x, min_y, max_x, max_y = _compute_bounds(entity_layouts, edges)
    if not math.isfinite(min_x):
        min_x = 0.0
        max_x = 0.0
    if not math.isfinite(min_y):
        min_y = 0.0
        max_y = 0.0
    dx = _GRAPH_MARGIN - min_x
    dy = _GRAPH_MARGIN - min_y
    for entity in entity_layouts.values():
        entity.x += dx
        entity.y += dy
    for edge in edges:
        edge.points = [(x + dx, y + dy) for x, y in edge.points]
        if edge.label_pos is not None:
            lx, ly = edge.label_pos
            edge.label_pos = (lx + dx, ly + dy)
    width = (max_x - min_x) + _GRAPH_MARGIN * 2.0
    height = (max_y - min_y) + _GRAPH_MARGIN * 2.0

    return _DiagramLayout(
        entities=entity_layouts, edges=edges, width=width, height=height
    )


def _compute_bounds(
    entities: dict[str, _EntityLayout], edges: list[_EdgeLayout]
) -> tuple[float, float, float, float]:
    """Bounding box over entity boxes + edge points + edge labels (grok L526-L575).

    Returns ``(min_x, min_y, max_x, max_y)``; infinities (empty diagram)
    collapse to ``0`` -- the post-layout normaliser relies on this.
    """
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    for entity in entities.values():
        left = entity.x - entity.width / 2.0
        right = entity.x + entity.width / 2.0
        top = entity.y - entity.height / 2.0
        bottom = entity.y + entity.height / 2.0
        min_x = min(min_x, left)
        min_y = min(min_y, top)
        max_x = max(max_x, right)
        max_y = max(max_y, bottom)

    for edge in edges:
        for px, py in edge.points:
            min_x = min(min_x, px)
            min_y = min(min_y, py)
            max_x = max(max_x, px)
            max_y = max(max_y, py)
        if edge.label_pos is not None:
            lx, ly = edge.label_pos
            left = lx - edge.label_width / 2.0
            right = lx + edge.label_width / 2.0
            top = ly - edge.label_height / 2.0
            bottom = ly + edge.label_height / 2.0
            min_x = min(min_x, left)
            min_y = min(min_y, top)
            max_x = max(max_x, right)
            max_y = max(max_y, bottom)

    if not math.isfinite(min_x):
        min_x = 0.0
        max_x = 0.0
    if not math.isfinite(min_y):
        min_y = 0.0
        max_y = 0.0

    return (min_x, min_y, max_x, max_y)


# === SVG rendering (grok L579-L888) ========================================


def _render_svg(layout: _DiagramLayout, theme: MermaidTheme) -> str:
    """Emit the full ER SVG: root + 7-channel style + defs + 3 groups (grok L579-L643)."""
    svg: list[str] = []

    svg.append(
        f'<svg aria-roledescription="er" role="graphics-document document" '
        f'viewBox="0 0 {_fmt(layout.width)} {_fmt(layout.height)}" '
        f'style="max-width: {_fmt(layout.width)}px; '
        f'background-color: {theme.background};" '
        f'class="erDiagram" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns="http://www.w3.org/2000/svg" width="100%" id="my-svg">'
    )

    # 7-channel CSS theme block (grok L593-L610).
    svg.append(
        "<style>"
        f'#my-svg {{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        f"font-size:{_FONT_SIZE}px;fill:{theme.text_color};}}"
        f"#my-svg .entityBox {{fill:{theme.node_fill};stroke:{theme.node_stroke};}}"
        f"#my-svg .relationshipLine {{stroke:{theme.edge_color};stroke-width:1;fill:none;}}"
        f"#my-svg .marker {{fill:none !important;stroke:{theme.edge_color} !important;"
        "stroke-width:1;}}"
        f"#my-svg .edgeLabel .label {{fill:{theme.node_stroke};font-size:14px;}}"
        f'#my-svg .label {{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        f"color:{theme.text_color};}}"
        f"#my-svg .label text, #my-svg span {{fill:{theme.text_color};"
        f"color:{theme.text_color};}}"
        "#my-svg .node rect, #my-svg .node circle, #my-svg .node ellipse, "
        f"#my-svg .node polygon {{fill:{theme.node_fill};stroke:{theme.node_stroke};"
        "stroke-width:1px;}}"
        f"#my-svg .divider {{stroke:{theme.node_stroke};stroke-width:1;}}"
        "</style>"
    )

    # ER-specific crow's-foot marker definitions (grok L612-L615).
    svg.append("<defs>")
    _render_er_markers(svg, theme)
    svg.append("</defs>")

    svg.append("<g>")

    # Edge polylines (grok L620-L624).
    svg.append('<g class="edgePaths">')
    for edge in layout.edges:
        _render_edge_path(svg, edge)
    svg.append("</g>")

    # Edge labels (grok L627-L631).
    svg.append('<g class="edgeLabels">')
    for edge in layout.edges:
        _render_edge_label(svg, edge, theme)
    svg.append("</g>")

    # Entity tables (grok L634-L638).
    svg.append('<g class="nodes">')
    for entity in layout.entities.values():
        _render_entity_node(svg, entity, theme)
    svg.append("</g>")

    svg.append("</g></svg>")
    return "".join(svg)


def _render_er_markers(svg: list[str], theme: MermaidTheme) -> None:
    """Emit the 8 crow's-foot markers (grok ``render_er_markers`` L645-L707).

    Four cardinalities x Start/End = 8 markers, each with its own
    ``refX`` / ``refY`` / ``markerWidth`` / ``markerHeight`` and path or
    circle geometry. IDs are ``my-svg_er-{name}Start`` / ``{name}End``
    where ``{name}`` is the :meth:`Cardinality.marker_name` fragment.
    """
    edge = theme.edge_color

    # onlyOne markers: two perpendicular bars (grok L648-L660).
    svg.append(
        f'<marker id="my-svg_er-onlyOneStart" class="marker onlyOne" '
        f'refX="0" refY="9" markerWidth="18" markerHeight="18" orient="auto">'
        f'<path d="M9,0 L9,18 M15,0 L15,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )
    svg.append(
        f'<marker id="my-svg_er-onlyOneEnd" class="marker onlyOne" '
        f'refX="18" refY="9" markerWidth="18" markerHeight="18" orient="auto">'
        f'<path d="M3,0 L3,18 M9,0 L9,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )

    # zeroOrOne markers: circle + perpendicular bar (grok L662-L676).
    svg.append(
        f'<marker id="my-svg_er-zeroOrOneStart" class="marker zeroOrOne" '
        f'refX="0" refY="9" markerWidth="30" markerHeight="18" orient="auto">'
        f'<circle fill="white" cx="21" cy="9" r="6" stroke="{edge}"/>'
        f'<path d="M9,0 L9,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )
    svg.append(
        f'<marker id="my-svg_er-zeroOrOneEnd" class="marker zeroOrOne" '
        f'refX="30" refY="9" markerWidth="30" markerHeight="18" orient="auto">'
        f'<circle fill="white" cx="9" cy="9" r="6" stroke="{edge}"/>'
        f'<path d="M21,0 L21,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )

    # oneOrMore markers: crow's foot + perpendicular bar (grok L678-L690).
    svg.append(
        f'<marker id="my-svg_er-oneOrMoreStart" class="marker oneOrMore" '
        f'refX="18" refY="18" markerWidth="45" markerHeight="36" orient="auto">'
        f'<path d="M0,18 Q 18,0 36,18 Q 18,36 0,18 M42,9 L42,27" '
        f'stroke="{edge}" fill="none"/>'
        "</marker>"
    )
    svg.append(
        f'<marker id="my-svg_er-oneOrMoreEnd" class="marker oneOrMore" '
        f'refX="27" refY="18" markerWidth="45" markerHeight="36" orient="auto">'
        f'<path d="M3,9 L3,27 M9,18 Q27,0 45,18 Q27,36 9,18" '
        f'stroke="{edge}" fill="none"/>'
        "</marker>"
    )

    # zeroOrMore markers: circle + crow's foot (grok L692-L706).
    svg.append(
        f'<marker id="my-svg_er-zeroOrMoreStart" class="marker zeroOrMore" '
        f'refX="18" refY="18" markerWidth="57" markerHeight="36" orient="auto">'
        f'<circle fill="white" cx="48" cy="18" r="6" stroke="{edge}"/>'
        f'<path d="M0,18 Q18,0 36,18 Q18,36 0,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )
    svg.append(
        f'<marker id="my-svg_er-zeroOrMoreEnd" class="marker zeroOrMore" '
        f'refX="39" refY="18" markerWidth="57" markerHeight="36" orient="auto">'
        f'<circle fill="white" cx="9" cy="18" r="6" stroke="{edge}"/>'
        f'<path d="M21,18 Q39,0 57,18 Q39,36 21,18" stroke="{edge}" fill="none"/>'
        "</marker>"
    )


def _render_edge_path(svg: list[str], edge: _EdgeLayout) -> None:
    """Emit one relationship's polyline + start/end markers (grok L709-L730).

    ``marker-start`` decorates the entity_a end with ``card_a``'s marker,
    ``marker-end`` decorates the entity_b end with ``card_b``'s marker;
    non-identifying relationships get an ``8,8`` dash pattern.
    """
    d = _points_to_path_d(edge.points)
    dash = ' stroke-dasharray="8,8"' if edge.rel_spec.rel_type == Identification.NonIdentifying else ""

    marker_start = edge.rel_spec.card_a.marker_name()
    marker_end = edge.rel_spec.card_b.marker_name()

    svg.append(
        f'<path class="edge-thickness-normal relationshipLine" '
        f'd="{d}" '
        f'marker-start="url(#my-svg_er-{marker_start}Start)" '
        f'marker-end="url(#my-svg_er-{marker_end}End)" '
        f'style="fill:none;"{dash}/>'
    )


def _render_edge_label(svg: list[str], edge: _EdgeLayout, theme: MermaidTheme) -> None:
    """Emit the centred role label on a relationship (grok L732-L751).

    Skipped when the role is empty or dagre produced no label centre.
    """
    if not edge.role:
        return
    if edge.label_pos is None:
        return
    x, y = edge.label_pos
    svg.append(
        f'<g transform="translate({_fmt(x)}, {_fmt(y)})" class="edgeLabel">'
        f'<text x="0" y="0" text-anchor="middle" dominant-baseline="middle" '
        f'fill="{theme.text_color}" style="font-size:14px">'
        f"{_escape_xml(edge.role)}</text>"
        "</g>"
    )


def _render_entity_node(svg: list[str], entity: _EntityLayout, theme: MermaidTheme) -> None:
    """Emit one entity table: box + header + divider + zebra rows (grok L753-L888).

    The table is centred on ``(entity.x, entity.y)``; the header text sits
    in the top ``header_height`` band, a horizontal ``.divider`` separates
    header from attributes, and each attribute row gets a zebra-striped
    background (``attributeBoxOdd`` / ``attributeBoxEven``) with type text
    in the left column and name text in the right, divided by a vertical
    ``.divider`` drawn last so the row fills don't cover it.
    """
    x_offset = -entity.width / 2.0
    y_offset = -entity.height / 2.0

    svg.append(
        f'<g transform="translate({_fmt(entity.x)},{_fmt(entity.y)})" '
        f'id="entity-{_escape_xml(entity.id)}" class="node default">'
    )

    # Outer rectangle (entity box) (grok L765-L771).
    svg.append(
        f'<rect x="{_fmt(x_offset)}" y="{_fmt(y_offset)}" '
        f'width="{_fmt(entity.width)}" height="{_fmt(entity.height)}" '
        f'class="entityBox" rx="0" ry="0"/>'
    )

    # Header text -- entity name, centred (grok L773-L781).
    header_text_y = y_offset + entity.header_height / 2.0
    svg.append(
        f'<text x="0" y="{_fmt(header_text_y)}" text-anchor="middle" '
        f'dominant-baseline="middle" fill="{theme.text_color}" '
        f'class="er entityLabel">{_escape_xml(entity.id)}</text>'
    )

    if entity.attributes:
        # Horizontal divider between header and attributes (grok L784-L791).
        divider_y = y_offset + entity.header_height
        svg.append(
            f'<line x1="{_fmt(x_offset)}" y1="{_fmt(divider_y)}" '
            f'x2="{_fmt(x_offset + entity.width)}" y2="{_fmt(divider_y)}" '
            f'class="divider"/>'
        )

        col_divider_x = x_offset + entity.max_type_width
        attr_start_y = y_offset + entity.header_height
        attr_end_y = y_offset + entity.height
        current_y = attr_start_y

        # Alternating row fills matching mermaid.js erBox behaviour (grok L800-L812).
        # Light themes: rowOdd ~= white, rowEven = lighten(node_fill, 0.25).
        # Dark themes: both stripes derived from the background so text keeps contrast.
        if _is_dark_hex(theme.background):
            row_odd_fill = _lighten_hex(theme.background, 0.08)
            row_even_fill = _lighten_hex(theme.background, 0.16)
        else:
            row_odd_fill = "#ffffff"
            row_even_fill = _lighten_hex(theme.node_fill, 0.25)

        for i, attr in enumerate(entity.attributes):
            row_h = (
                entity.row_heights[i]
                if i < len(entity.row_heights)
                else _LINE_HEIGHT + _TEXT_PADDING
            )
            text_y = current_y + row_h / 2.0

            # Zebra striping: contentRowIndex = i + 1; even when % 2 == 0 && i > 0.
            is_even = (i + 1) % 2 == 0 and i > 0
            row_fill = row_even_fill if is_even else row_odd_fill
            parity = "Even" if is_even else "Odd"
            svg.append(
                f'<rect x="{_fmt(x_offset)}" y="{_fmt(current_y)}" '
                f'width="{_fmt(entity.width)}" height="{_fmt(row_h)}" '
                f'style="fill:{row_fill};stroke:{theme.node_stroke}" '
                f'class="er attributeBox{parity}"/>'
            )

            # Faint horizontal separator between attribute rows (grok L843-L850).
            if i > 0:
                svg.append(
                    f'<line x1="{_fmt(x_offset)}" y1="{_fmt(current_y)}" '
                    f'x2="{_fmt(x_offset + entity.width)}" y2="{_fmt(current_y)}" '
                    f'class="divider" style="stroke-opacity:0.3"/>'
                )

            # Type text -- left column (grok L852-L861).
            type_text_x = x_offset + _COLUMN_TEXT_PADDING
            svg.append(
                f'<text x="{_fmt(type_text_x)}" y="{_fmt(text_y)}" '
                f'text-anchor="start" dominant-baseline="middle" '
                f'fill="{theme.text_color}" class="er entityLabel">'
                f"{_escape_xml(attr.attr_type)}</text>"
            )

            # Name text -- right column (grok L863-L872).
            name_text_x = col_divider_x + _COLUMN_TEXT_PADDING
            svg.append(
                f'<text x="{_fmt(name_text_x)}" y="{_fmt(text_y)}" '
                f'text-anchor="start" dominant-baseline="middle" '
                f'fill="{theme.text_color}" class="er entityLabel">'
                f"{_escape_xml(attr.name)}</text>"
            )

            current_y += row_h

        # Vertical divider between type and name columns (grok L877-L884).
        # Drawn after the row rects so their fill doesn't cover it.
        svg.append(
            f'<line x1="{_fmt(col_divider_x)}" y1="{_fmt(attr_start_y)}" '
            f'x2="{_fmt(col_divider_x)}" y2="{_fmt(attr_end_y)}" '
            f'class="divider"/>'
        )

    svg.append("</g>")


# === helpers (grok L890-L936) ==============================================


def _points_to_path_d(points: list[tuple[float, float]]) -> str:
    """Build an SVG path ``d`` from polyline points (grok L890-L901).

    Uses the comma form ``M{x},{y}`` then ``L{x},{y}`` (ER's style; the
    flowchart renderer uses the space form instead).
    """
    if not points:
        return ""
    x0, y0 = points[0]
    d = [f"M{_fmt(x0)},{_fmt(y0)}"]
    for x, y in points[1:]:
        d.append(f"L{_fmt(x)},{_fmt(y)}")
    return "".join(d)


def _parse_hex_byte(text: str, default: int) -> int:
    """Parse a 2-char hex byte, falling back to ``default`` (grok ``unwrap_or``).

    Grok parses each channel with ``u8::from_str_radix(...).unwrap_or(d)``
    independently, so a malformed channel takes its default while its
    siblings still parse; this helper mirrors that per-channel fallback
    (``is_dark_hex`` defaults to 255, ``lighten_hex`` to 0).
    """
    try:
        return int(text, 16)
    except ValueError:
        return default


def _is_dark_hex(hex_color: str) -> bool:
    """Return True when a hex color's luminance is below 128 (grok L905-L914).

    Uses the Rec. 601 weights (``0.2126 R + 0.7152 G + 0.0722 B``); short
    or malformed inputs default to "not dark" so a light theme renders.
    """
    body = hex_color.lstrip("#")
    if len(body) < 6:
        return False
    r = _parse_hex_byte(body[0:2], 255)
    g = _parse_hex_byte(body[2:4], 255)
    b = _parse_hex_byte(body[4:6], 255)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128.0


def _lighten_hex(hex_color: str, amount: float) -> str:
    """Blend a hex color toward white by ``amount`` (grok L916-L928).

    ``amount`` is 0.0 (no change) to 1.0 (white); the result is upper-cased
    ``#RRGGBB`` to match grok's ``{:02X}`` formatting.
    """
    body = hex_color.lstrip("#")
    if len(body) < 6:
        return f"#{body}"
    r = _parse_hex_byte(body[0:2], 0)
    g = _parse_hex_byte(body[2:4], 0)
    b = _parse_hex_byte(body[4:6], 0)
    lr = r + (255 - r) * amount
    lg = g + (255 - g) * amount
    lb = b + (255 - b) * amount
    return f"#{int(lr):02X}{int(lg):02X}{int(lb):02X}"


def _escape_xml(text: str) -> str:
    """Escape the 5 XML significant characters (grok L930-L936).

    Uses ``&apos;`` for the apostrophe, the named-entity variant shared
    with the gitgraph / journey / block renderers.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _fmt(value: float) -> str:
    """Format a float like Rust's ``f64`` Display (grok relies on this).

    Integer-valued floats drop the trailing ``.0`` (``10.0`` -> ``"10"``);
    non-integers keep their natural representation (``36.75`` ->
    ``"36.75"``). This is the same bridge the gitgraph renderer uses.
    """
    if not math.isfinite(value):
        return str(value)
    ivalue = int(value)
    if value == ivalue:
        return str(ivalue)
    return str(value)
