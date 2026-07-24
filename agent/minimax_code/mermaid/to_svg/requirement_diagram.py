"""R294 -- ``requirementDiagram`` renderer (direction (1), brick 25).

This is the mermaid-to-svg per-diagram leaf fused from grok
``mermaid-to-svg/src/requirement_diagram.rs`` (874 lines). It is the
**first per-diagram renderer in the mermaid-to-svg stack that consumes the
dagre layout engine** (``minimax_code.dagre``) -- every prior per-diagram
leaf shipped in R279-R293 (``info`` / ``radar`` / ``pie`` / ``packet`` /
``sankey`` / ``gantt`` / ``kanban`` / ``timeline`` / ``quadrant`` /
``block`` / ``journey`` / ``gitGraph`` / ``mindmap`` / ``xychart``) computed
its geometry by hand; ``requirementDiagram`` delegates node placement and
edge routing to dagre, the same engine the flowchart stack rides
(:mod:`minimax_code.mermaid.to_svg.layout`). This is the start of phase 2
of direction (1): activating dagre across the remaining per-diagram
renderers (``erDiagram`` / ``classDiagram`` / ``C4*`` / ``sequenceDiagram``
follow in R295+).

Functional contract (faithful clone of grok, by behaviour not by line):

* **Parse** a ``requirementDiagram`` source into a typed AST: a header
  line, an optional ``direction TB|TD|BT|LR|RL`` clause, a body of node
  declarations (7 kinds: ``element`` / ``requirement`` /
  ``functionalrequirement`` / ``interfacerequirement`` /
  ``performancerequirement`` / ``physicalrequirement`` /
  ``designconstraint``) each with a ``{ key: value }`` property block, and
  relations ``src -rel-> dst`` / ``dst <-rel- src``. Mirrors grok
  ``parse_requirement_diagram`` (L122-L191) + ``try_parse_*`` helpers
  (L210-L366).
* **Lay out** the AST by handing node boxes + edge labels to dagre
  (``compound: false`` -- requirement diagrams carry no subgraphs),
  reading back node centres, edge poly-points, and edge-label centres,
  then translating the lot into ``[margin, margin)`` space. Mirrors grok
  ``compute_layout`` (L426-L573) + ``compute_bounds`` (L575-L624) +
  ``compute_requirement_box_layout`` (L626-L707).
* **Emit** an SVG whose root carries ``id="my-svg"`` last, a 7-channel
  theme-aware ``<style>`` block, two marker ``<defs>`` (``containsStart``
  circle-and-cross + ``arrowEnd`` chevron), dashed edge paths that switch
  to solid + no marker for ``contains`` relations, ``#E8E8E8`` edge-label
  rectangles, and sorted node ``<g>`` groups with an optional divider
  ``<line>``. Mirrors grok ``render_svg`` (L709-L853).

Unlike ``journey`` (R290), ``requirementDiagram`` is **theme-aware**: the
resolved :class:`MermaidTheme` flows into 5 channels (background ->
root ``style`` bg; ``text_color`` -> CSS fill + every label fill;
``edge_color`` -> marker strokes + ``relationshipLine`` stroke;
``node_fill`` -> node rect fill; ``node_stroke`` -> node rect stroke +
divider line stroke). The dispatch parameter is therefore ``theme`` (not
``_theme``).

Public surface (1 symbol): :func:`render_requirement_diagram_to_svg`.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import TypeAlias

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.mod import layout as dagre_layout
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.text_wrap import DEFAULT_CHAR_WIDTH, line_width
from minimax_code.mermaid.to_svg.theme import MermaidTheme

__all__ = ["render_requirement_diagram_to_svg"]


# === constants (grok requirement_diagram.rs L10-L16) =======================

#: Padding inside each requirement/element box (grok ``BOX_PADDING``).
_BOX_PADDING: float = 20.0
#: Vertical gap between the header lines and the body lines inside a box
#: (grok ``BOX_GAP``).
_BOX_GAP: float = 20.0
#: Height of one text line inside a box (grok ``LINE_HEIGHT``).
_LINE_HEIGHT: float = 24.0
#: dagre ``nodesep`` -- horizontal gap between nodes in the same rank
#: (grok ``NODE_SEP``).
_NODE_SEP: float = 50.0
#: dagre ``ranksep`` -- gap between ranks (grok ``RANK_SEP``).
_RANK_SEP: float = 50.0
#: Margin dagre leaves around the laid-out graph (grok ``GRAPH_MARGIN``);
#: also the edge-label rect fill opacity target palette anchor.
_GRAPH_MARGIN: float = 8.0
#: dagre ``edgesep`` (hard-coded 20.0 in grok ``compute_layout`` L442).
_EDGE_SEP: float = 20.0


# === direction (grok L27-L44) ==============================================


class _Direction(enum.Enum):
    """The four layout directions a requirement diagram may declare.

    Mirrors grok ``Direction`` (L27-L34); :meth:`as_rankdir` projects to the
    dagre ``rankdir`` string (grok ``as_rankdir`` L36-L43).
    """

    Tb = "tb"
    Bt = "bt"
    Lr = "lr"
    Rl = "rl"

    def as_rankdir(self) -> str:
        return self.value


# === data structures (grok L46-L120) =======================================


@dataclass
class _RequirementNode:
    """A ``requirement``-family node (grok ``RequirementNode`` L48-L62)."""

    name: str
    requirement_id: str
    text: str
    risk: str
    verify_method: str
    req_type: str


@dataclass
class _ElementNode:
    """An ``element`` node (grok ``ElementNode`` L64-L70)."""

    name: str
    element_type: str
    doc_ref: str


#: Tagged union of the two node kinds (grok ``enum ReqNode`` L46-L47).
_ReqNode: TypeAlias = _RequirementNode | _ElementNode


@dataclass
class _Relation:
    """A ``src -rel-> dst`` / ``dst <-rel- src`` relation (grok L72-L78)."""

    src: str
    dst: str
    rel_type: str


@dataclass
class _LabelLayout:
    """A positioned text line inside a node box (grok ``LabelLayout`` L99-L106)."""

    text: str
    x: float
    y: float
    anchor: str  # "middle" | "start"
    bold: bool


@dataclass
class _NodeLayout:
    """A laid-out node box (grok ``NodeLayout`` L80-L90)."""

    id: str
    x: float
    y: float
    width: float
    height: float
    labels: list[_LabelLayout]
    divider_y: float | None


@dataclass
class _EdgeLayout:
    """A laid-out relation edge (grok ``EdgeLayout`` L92-L98)."""

    id: str
    rel_type: str
    label: str
    points: list[tuple[float, float]]
    label_pos: tuple[float, float] | None
    label_width: float
    label_height: float


@dataclass
class _DiagramLayout:
    """The complete laid-out diagram (grok ``DiagramLayout`` L108-L113)."""

    nodes: dict[str, _NodeLayout]
    edges: list[_EdgeLayout]
    width: float
    height: float


@dataclass
class _RequirementDiagram:
    """The parsed AST (grok ``RequirementDiagram`` L27-L44)."""

    direction: _Direction
    nodes: dict[str, _ReqNode]
    relations: list[_Relation]


# === node-kind vocabulary (grok L229-L239) =================================

#: The seven valid ``kind`` tokens that introduce a node (grok L230-L236).
_VALID_NODE_KINDS: frozenset[str] = frozenset(
    {
        "element",
        "requirement",
        "functionalrequirement",
        "interfacerequirement",
        "performancerequirement",
        "physicalrequirement",
        "designconstraint",
    }
)

#: ``kind`` -> human-readable ``req_type`` label (grok L303-L311). The bare
#: ``requirement`` kind and any unrecognised kind fall through to
#: ``"Requirement"`` (handled separately, not in this table).
_REQ_TYPE_MAP: dict[str, str] = {
    "functionalrequirement": "Functional Requirement",
    "interfacerequirement": "Interface Requirement",
    "performancerequirement": "Performance Requirement",
    "physicalrequirement": "Physical Requirement",
    "designconstraint": "Design Constraint",
}


# === public entry (grok lib.rs per-diagram arm consumes this) ==============


def render_requirement_diagram_to_svg(
    mermaid_source: str, theme: MermaidTheme
) -> str:
    """Render a ``requirementDiagram`` source into an SVG string.

    Faithful clone of grok ``render_requirement_diagram`` (the crate-root
    dispatch hands the front-matter-stripped body + resolved theme to this
    entry). Parses the body into a typed AST, lays it out via dagre, and
    emits the theme-aware SVG.

    Args:
        mermaid_source: The ``requirementDiagram`` body (front-matter already
            stripped by :func:`render_mermaid_to_svg`).
        theme: The resolved :class:`MermaidTheme` (5 channels consumed:
            ``background`` / ``text_color`` / ``edge_color`` / ``node_fill``
            / ``node_stroke``).

    Returns:
        The SVG document as a string.

    Raises:
        ParseError: On any structural violation of the requirement-diagram
            grammar (missing header, bad direction, unnamed node, malformed
            property line, unrecognised line).
    """
    diagram = _parse_requirement_diagram(mermaid_source)
    layout = _compute_layout(diagram)
    return _render_svg(layout, theme)


# === parser (grok L122-L366) ===============================================


def _parse_requirement_diagram(source: str) -> _RequirementDiagram:
    """Parse a requirement-diagram body into the typed AST (grok L122-L191).

    Walks lines with an index cursor ``i``; the first non-empty / non-``%%``
    line must carry the ``requirementDiagram`` header, an optional
    ``direction`` clause sets the rankdir, and the remaining lines are
    node declarations, relations, or a structural error.
    """
    lines = source.splitlines()
    found_header = False
    direction = _Direction.Tb
    nodes: dict[str, _ReqNode] = {}
    relations: list[_Relation] = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        line_no = i + 1
        line = raw.strip()
        i += 1

        if not line or line.startswith("%%"):
            continue

        if not found_header:
            if _first_diagram_type_token(line) != "requirementDiagram":
                raise ParseError(
                    line_no, "Expected 'requirementDiagram' declaration"
                )
            found_header = True
            continue

        if line.startswith("direction "):
            rest = line[len("direction ") :].strip()
            direction = _parse_direction(rest, line_no)
            continue

        parsed = _try_parse_requirement_or_element(lines, i - 1)
        if parsed is not None:
            node, new_i = parsed
            nodes[node.name] = node
            i = new_i
            continue

        relation = _try_parse_relation(line, line_no)
        if relation is not None:
            relations.append(relation)
            continue

        raise ParseError(
            line_no, f"Unrecognized requirementDiagram line: {line}"
        )

    if not found_header:
        raise ParseError(1, "Expected 'requirementDiagram' declaration")

    return _RequirementDiagram(
        direction=direction, nodes=nodes, relations=relations
    )


def _first_diagram_type_token(line: str) -> str | None:
    """Return the first whitespace token of ``line`` (grok L193-L195)."""
    parts = line.split()
    return parts[0] if parts else None


def _parse_direction(s: str, line_no: int) -> _Direction:
    """Map a ``TB|TD|BT|LR|RL`` token to a :class:`_Direction` (grok L197-L208)."""
    match s.upper():
        case "TB" | "TD":
            return _Direction.Tb
        case "BT":
            return _Direction.Bt
        case "LR":
            return _Direction.Lr
        case "RL":
            return _Direction.Rl
        case _:
            raise ParseError(line_no, f"Invalid direction: {s}")


def _try_parse_requirement_or_element(
    lines: list[str], start_idx: int
) -> tuple[_ReqNode, int] | None:
    """Parse a node declaration starting at ``lines[start_idx]`` (grok L210-L330).

    Returns ``(node, new_i)`` where ``new_i`` is the cursor position just
    past the closing ``}`` of the property block, or ``None`` when the line
    is not a node declaration (blank / comment / unknown ``kind`` / a
    non-brace content line where a ``{`` was expected). Structural errors
    (named node missing, malformed property line) raise :class:`ParseError`.
    """
    line = lines[start_idx].strip()
    if not line or line.startswith("%%"):
        return None

    kind, rest = _split_once_ws(line)
    name_raw, tail = _split_once_ws(rest)
    name = name_raw.strip()
    has_open_brace = "{" in tail or name.endswith("{")
    name = name.rstrip("{").strip()

    kind_lower = kind.lower()
    if kind_lower not in _VALID_NODE_KINDS:
        return None

    if not name:
        raise ParseError(
            start_idx + 1, f"Expected name after '{kind}'"
        )

    i = start_idx + 1
    if not has_open_brace:
        # Scan forward for the opening brace (grok L249-L262). A non-brace,
        # non-blank content line means this was not a node declaration.
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate or candidate.startswith("%%"):
                i += 1
                continue
            if candidate.startswith("{"):
                i += 1
                break
            return None

    props: dict[str, str] = {}
    while i < len(lines):
        raw = lines[i]
        line_no = i + 1
        body_line = raw.strip()
        i += 1

        if not body_line or body_line.startswith("%%"):
            continue
        if body_line.startswith("}"):
            break

        if ":" not in body_line:
            raise ParseError(
                line_no, f"Invalid property line: {body_line}"
            )
        key_part, value_part = body_line.split(":", 1)
        key = key_part.strip()
        value = value_part.strip().rstrip(",").strip()
        value = _strip_quotes(value)
        props[key] = value

    if kind_lower == "element":
        node: _ReqNode = _ElementNode(
            name=name,
            element_type=props.get("type", ""),
            doc_ref=props.get("docref") or props.get("docRef", ""),
        )
        return node, i

    req_type = _REQ_TYPE_MAP.get(kind_lower, "Requirement")
    raw_risk = props.get("risk", "")
    raw_verify = props.get("verifyMethod") or props.get("verifymethod", "")
    node = _RequirementNode(
        name=name,
        requirement_id=props.get("id", ""),
        text=props.get("text", ""),
        risk=_normalize_risk(raw_risk),
        verify_method=_normalize_verify_method(raw_verify),
        req_type=req_type,
    )
    return node, i


def _try_parse_relation(line: str, line_no: int) -> _Relation | None:
    """Parse a ``src -rel-> dst`` / ``dst <-rel- src`` line (grok L369-L424).

    Returns ``None`` for non-relation lines (caller then reports an
    unrecognised line). Malformed relation shapes raise :class:`ParseError`.
    """
    if not line or line.startswith("%%"):
        return None

    if "->" in line:
        lhs, rhs = line.split("->", 1)
        dst = rhs.strip()
        tokens = [t for t in lhs.split() if t != "-"]
        if len(tokens) < 2 or not dst:
            raise ParseError(line_no, f"Invalid relationship: {line}")
        src = tokens[0]
        rel = tokens[1]
        if not src or not rel:
            raise ParseError(line_no, f"Invalid relationship: {line}")
        return _Relation(src=src, dst=dst, rel_type=rel)

    if "<-" in line:
        lhs, rhs = line.split("<-", 1)
        dst = lhs.strip()
        tokens = [t for t in rhs.split() if t != "-"]
        if len(tokens) < 2 or not dst:
            raise ParseError(line_no, f"Invalid relationship: {line}")
        rel = tokens[0]
        src = tokens[1]
        if not src or not rel:
            raise ParseError(line_no, f"Invalid relationship: {line}")
        return _Relation(src=src, dst=dst, rel_type=rel)

    return None


def _normalize_risk(s: str) -> str:
    """Canonicalise a ``risk`` value (grok L332-L339)."""
    match s.strip().lower():
        case "low":
            return "Low"
        case "medium":
            return "Medium"
        case "high":
            return "High"
        case _:
            return s.strip()


def _normalize_verify_method(s: str) -> str:
    """Canonicalise a ``verifyMethod`` value (grok L341-L349)."""
    match s.strip().lower():
        case "analysis":
            return "Analysis"
        case "demonstration":
            return "Demonstration"
        case "inspection":
            return "Inspection"
        case "test":
            return "Test"
        case _:
            return s.strip()


def _strip_quotes(s: str) -> str:
    """Strip a matching pair of surrounding ``"`` or ``'`` (grok L351-L360)."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    return s


def _split_once_ws(s: str) -> tuple[str, str]:
    """Split ``s`` on the first whitespace (grok ``split_once_ws`` L362-L367).

    Returns ``(first_token, rest_trimmed)``. An input with no whitespace
    yields ``(s, "")``; an empty input yields ``("", "")``. The second
    component is trimmed (matching grok ``b.trim()``).
    """
    for j, ch in enumerate(s):
        if ch.isspace():
            return s[:j], s[j + 1 :].strip()
    return s, ""


# === layout (grok L426-L707) ===============================================


def _compute_layout(diagram: _RequirementDiagram) -> _DiagramLayout:
    """Hand the AST to dagre and read back positions (grok L426-L573).

    Builds a dagre :class:`Graph` with ``compound=False`` (requirement
    diagrams carry no subgraphs), feeds each node box's measured size and
    each relation's ``<<rel>>`` edge label, runs the layout, then extracts
    node centres, edge poly-points, and edge-label centres. Finally the
    whole drawing is translated into ``[margin, margin)`` space and the
    canvas size is the span plus twice the margin.
    """
    # Per-node measured geometry cached for the post-layout pass
    # (grok ``node_metrics`` L432-L436).
    node_metrics: dict[
        str, tuple[float, float, list[_LabelLayout], float | None]
    ] = {}

    graph: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    graph.set_graph(
        GraphConfig(
            rankdir=diagram.direction.as_rankdir(),
            nodesep=_NODE_SEP,
            ranksep=_RANK_SEP,
            edgesep=_EDGE_SEP,
            marginx=_GRAPH_MARGIN,
            marginy=_GRAPH_MARGIN,
        )
    )

    for node_id, node in diagram.nodes.items():
        width, height, labels, divider_y = _compute_requirement_box_layout(node)
        node_metrics[node_id] = (width, height, labels, divider_y)
        graph.set_node(node_id, GraphNode(width=width, height=height))

    for relation in diagram.relations:
        edge_label_text = f"<<{relation.rel_type}>>"
        label_width = line_width(edge_label_text, DEFAULT_CHAR_WIDTH)
        label_height = _LINE_HEIGHT
        edge_label = GraphEdge(
            labelpos="c", width=label_width, height=label_height
        )
        graph.set_edge(relation.src, relation.dst, edge_label, None)

    dagre_layout(graph)

    nodes_layout: dict[str, _NodeLayout] = {}
    for node_id, (width, height, labels, divider_y) in node_metrics.items():
        placed = graph.node(node_id)
        if placed is not None:
            x = placed.x
            y = placed.y
        else:
            x = 0.0
            y = 0.0
        nodes_layout[node_id] = _NodeLayout(
            id=node_id,
            x=x,
            y=y,
            width=width,
            height=height,
            labels=labels,
            divider_y=divider_y,
        )

    edges_layout: list[_EdgeLayout] = []
    for idx, relation in enumerate(diagram.relations):
        edge = graph.edge(relation.src, relation.dst, None)
        label_text = f"<<{relation.rel_type}>>"
        if edge is None:
            points: list[tuple[float, float]] = []
            label_pos: tuple[float, float] | None = None
            label_width = 0.0
            label_height = 0.0
        else:
            points = (
                [(p.x, p.y) for p in edge.points] if edge.points else []
            )
            edge_width = edge.width or 0.0
            edge_height = edge.height or 0.0
            if edge_width > 0.0 or edge_height > 0.0:
                label_pos = (edge.x, edge.y)
            else:
                label_pos = None
            label_width = edge_width
            label_height = edge_height
        edges_layout.append(
            _EdgeLayout(
                id=f"{relation.src}-{relation.dst}-{idx}",
                rel_type=relation.rel_type,
                label=label_text,
                points=points,
                label_pos=label_pos,
                label_width=label_width,
                label_height=label_height,
            )
        )

    # Translate the bounding box into [margin, margin) space (grok L552-L571).
    min_x, min_y, max_x, max_y = _compute_bounds(nodes_layout, edges_layout)
    if not math.isfinite(min_x):
        min_x = 0.0
        max_x = 0.0
    if not math.isfinite(min_y):
        min_y = 0.0
        max_y = 0.0
    dx = _GRAPH_MARGIN - min_x
    dy = _GRAPH_MARGIN - min_y
    for node in nodes_layout.values():
        node.x += dx
        node.y += dy
    for edge in edges_layout:
        edge.points = [(x + dx, y + dy) for x, y in edge.points]
        if edge.label_pos is not None:
            lx, ly = edge.label_pos
            edge.label_pos = (lx + dx, ly + dy)
    width = (max_x - min_x) + _GRAPH_MARGIN * 2
    height = (max_y - min_y) + _GRAPH_MARGIN * 2

    return _DiagramLayout(
        nodes=nodes_layout, edges=edges_layout, width=width, height=height
    )


def _compute_bounds(
    nodes: dict[str, _NodeLayout], edges: list[_EdgeLayout]
) -> tuple[float, float, float, float]:
    """Compute the ``(min_x, min_y, max_x, max_y)`` span (grok L575-L624).

    Nodes contribute their rect half-extents; edges contribute every
    poly-point and, when present, the label rect half-extents. An empty
    graph leaves the bounds at ``+/-inf``; the caller replaces those with
    zero (grok L555-L561).
    """
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    for node in nodes.values():
        left = node.x - node.width / 2
        right = node.x + node.width / 2
        top = node.y - node.height / 2
        bottom = node.y + node.height / 2
        if left < min_x:
            min_x = left
        if right > max_x:
            max_x = right
        if top < min_y:
            min_y = top
        if bottom > max_y:
            max_y = bottom

    for edge in edges:
        for x, y in edge.points:
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
        if edge.label_pos is not None:
            lx, ly = edge.label_pos
            half_w = edge.label_width / 2
            half_h = edge.label_height / 2
            if lx - half_w < min_x:
                min_x = lx - half_w
            if lx + half_w > max_x:
                max_x = lx + half_w
            if ly - half_h < min_y:
                min_y = ly - half_h
            if ly + half_h > max_y:
                max_y = ly + half_h

    return min_x, min_y, max_x, max_y


def _compute_requirement_box_layout(
    node: _ReqNode,
) -> tuple[float, float, list[_LabelLayout], float | None]:
    """Measure one node box and place its text lines (grok L626-L707).

    Returns ``(width, height, labels, divider_y)``. The box stacks a centred
    ``<<type>>`` header line, a centred bold name line, an optional divider,
    then left-anchored body lines (``ID:`` / ``Text:`` / ``Risk:`` /
    ``Verification:`` for requirements; ``Type:`` / ``Doc Ref:`` for
    elements). The divider appears only when there is at least one body
    line (grok L699-L705).
    """
    if isinstance(node, _RequirementNode):
        body: list[str] = []
        if node.requirement_id:
            body.append(f"ID: {node.requirement_id}")
        if node.text:
            body.append(f"Text: {node.text}")
        if node.risk:
            body.append(f"Risk: {node.risk}")
        if node.verify_method:
            body.append(f"Verification: {node.verify_method}")
        type_line = f"<<{node.req_type}>>"
        name_line = node.name
    else:
        body = []
        if node.element_type:
            body.append(f"Type: {node.element_type}")
        if node.doc_ref:
            body.append(f"Doc Ref: {node.doc_ref}")
        type_line = "<<Element>>"
        name_line = node.name

    type_width = line_width(type_line, DEFAULT_CHAR_WIDTH)
    name_width = line_width(name_line, DEFAULT_CHAR_WIDTH)
    body_widths = [line_width(b, DEFAULT_CHAR_WIDTH) for b in body]
    max_width = max([type_width, name_width, *body_widths])

    content_height = _LINE_HEIGHT + _LINE_HEIGHT + _BOX_GAP + len(body) * _LINE_HEIGHT
    total_width = max_width + _BOX_PADDING
    total_height = content_height + _BOX_PADDING

    labels: list[_LabelLayout] = []
    # ``<<type>>`` header line (centred, not bold) -- grok L680-L685.
    labels.append(
        _LabelLayout(
            text=type_line,
            x=0.0,
            y=-content_height / 2 + _BOX_PADDING / 2,
            anchor="middle",
            bold=False,
        )
    )
    # Name line (centred, bold) -- grok L686-L692.
    labels.append(
        _LabelLayout(
            text=name_line,
            x=0.0,
            y=_LINE_HEIGHT - content_height / 2 + _BOX_PADDING / 2,
            anchor="middle",
            bold=True,
        )
    )
    # Body lines (left-anchored, not bold) -- grok L693-L698.
    left_x = -total_width / 2 + _BOX_PADDING / 2
    y_offset = _LINE_HEIGHT + _LINE_HEIGHT + _BOX_GAP
    for body_line in body:
        labels.append(
            _LabelLayout(
                text=body_line,
                x=left_x,
                y=y_offset - content_height / 2 + _BOX_PADDING / 2,
                anchor="start",
                bold=False,
            )
        )
        y_offset += _LINE_HEIGHT

    divider_y: float | None = None
    if y_offset > _LINE_HEIGHT + _LINE_HEIGHT + _BOX_GAP:
        divider_y = -total_height / 2 + (
            _LINE_HEIGHT + _LINE_HEIGHT + _BOX_GAP
        )

    return total_width, total_height, labels, divider_y


# === SVG emitter (grok L709-L874) ==========================================


def _render_svg(layout: _DiagramLayout, theme: MermaidTheme) -> str:
    """Emit the theme-aware SVG document (grok ``render_svg`` L709-L853)."""
    parts: list[str] = []

    # Root <svg>: ``id="my-svg"`` is the LAST attribute (grok L711-L714).
    parts.append(
        f'<svg aria-roledescription="requirement" '
        f'role="graphics-document document" '
        f'viewBox="0 0 {_fmt(layout.width)} {_fmt(layout.height)}" '
        f'style="max-width: {_fmt(layout.width)}px; '
        f'background-color: {theme.background};" '
        f'class="requirementDiagram" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" id="my-svg">'
    )

    # 7-channel theme-aware <style> block (grok L716-L722).
    parts.append(
        f"<style>"
        f'#my-svg{{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        f"font-size:16px;fill:{theme.text_color};}}"
        f"#my-svg .relationshipLine{{stroke:{theme.edge_color};stroke-width:1;}}"
        f"#my-svg .node rect{{fill:{theme.node_fill};"
        f"stroke:{theme.node_stroke};stroke-width:1.3;}}"
        f'#my-svg .label{{font-family:"trebuchet ms",verdana,arial,'
        f"sans-serif;color:{theme.text_color};}}"
        f"#my-svg .label text,#my-svg span{{fill:{theme.text_color};"
        f"color:{theme.text_color};}}"
        f"#my-svg .labelBkg{{background-color:rgba(232,232,232, 0.8);}}"
        f"</style>"
    )

    parts.append("<g>")

    # ``containsStart`` marker: circle + crosshair, refX=0 (grok L726-L733).
    parts.append(
        f'<defs><marker orient="auto" markerHeight="20" markerWidth="20" '
        f'refY="10" refX="0" '
        f'id="my-svg_requirement-requirement_containsStart">'
        f'<g fill="none" stroke="{theme.edge_color}" stroke-width="1">'
        f'<circle r="9" cy="10" cx="10"/>'
        f'<line y2="10" y1="10" x2="19" x1="1"/>'
        f'<line x2="10" x1="10" y2="19" y1="1"/>'
        f"</g></marker></defs>"
    )
    # ``arrowEnd`` marker: open chevron, refX=20 (grok L734-L737).
    parts.append(
        f'<defs><marker orient="auto" markerHeight="20" markerWidth="20" '
        f'refY="10" refX="20" '
        f'id="my-svg_requirement-requirement_arrowEnd">'
        f'<path d="M0,0 L20,10 M20,10 L0,20" fill="none" '
        f'stroke="{theme.edge_color}" stroke-width="1"/>'
        f"</marker></defs>"
    )

    parts.append('<g class="root">')
    parts.append('<g class="clusters"/>')

    # Edge paths: ``contains`` -> solid, no marker; else dashed + arrowEnd
    # (grok L741-L755).
    for edge in layout.edges:
        is_contains = edge.rel_type == "contains"
        d = _points_to_path_d(edge.points)
        dash = "" if is_contains else "stroke-dasharray: 10,7;"
        marker_end = (
            ""
            if is_contains
            else ' marker-end="url(#my-svg_requirement-requirement_arrowEnd)"'
        )
        parts.append(
            f'<path{marker_end} style="fill:none;{dash}" '
            f'class="edge-thickness-normal edge-pattern-dashed relationshipLine" '
            f'id="{_escape_xml(edge.id)}" d="{d}"/>'
        )

    # Edge labels: ``#E8E8E8`` fill-opacity 0.8 rect + centred text
    # (grok L758-L766).
    for edge in layout.edges:
        if edge.label_pos is None:
            continue
        lx, ly = edge.label_pos
        half_w = edge.label_width / 2
        half_h = edge.label_height / 2
        parts.append(
            f'<g transform="translate({_fmt(lx)}, {_fmt(ly)})" '
            f'class="edgeLabel">'
            f'<rect x="{_fmt(-half_w)}" y="{_fmt(-half_h)}" '
            f'width="{_fmt(edge.label_width)}" '
            f'height="{_fmt(edge.label_height)}" fill="#E8E8E8" '
            f'fill-opacity="0.8" stroke="none"/>'
            f'<text x="0" y="0" text-anchor="middle" '
            f'dominant-baseline="middle" fill="{theme.text_color}">'
            f"{_escape_xml(edge.label)}</text>"
            f"</g>"
        )

    # Nodes: sorted by id; rect + optional divider <line> + label texts
    # (grok L769-L849).
    for node_id in sorted(layout.nodes.keys()):
        node = layout.nodes[node_id]
        parts.append(
            f'<g transform="translate({_fmt(node.x)},{_fmt(node.y)})" '
            f'id="{_escape_xml(node.id)}" class="node default">'
            f'<rect x="{_fmt(-node.width / 2)}" y="{_fmt(-node.height / 2)}" '
            f'width="{_fmt(node.width)}" height="{_fmt(node.height)}"/>'
        )
        if node.divider_y is not None:
            parts.append(
                f'<line x1="{_fmt(-node.width / 2)}" '
                f'y1="{_fmt(node.divider_y)}" '
                f'x2="{_fmt(-node.width / 2 + node.width)}" '
                f'y2="{_fmt(node.divider_y)}" '
                f'stroke="{theme.node_stroke}" stroke-width="1.3"/>'
            )
        for label in node.labels:
            font_weight = ' font-weight="bold"' if label.bold else ""
            parts.append(
                f'<text x="{_fmt(label.x)}" y="{_fmt(label.y)}" '
                f'text-anchor="{label.anchor}" '
                f'dominant-baseline="middle" fill="{theme.text_color}"'
                f"{font_weight}>{_escape_xml(label.text)}</text>"
            )
        parts.append("</g>")

    parts.append("</g></g></svg>")
    return "".join(parts)


def _points_to_path_d(points: list[tuple[float, float]]) -> str:
    """Build an SVG path ``d`` from poly-points (grok ``points_to_path_d`` L855-L866).

    Empty input yields the empty string (``d=""``); otherwise an ``M`` move
    to the first point followed by ``L`` lines.
    """
    if not points:
        return ""
    x0, y0 = points[0]
    segments = [f"M{_fmt(x0)},{_fmt(y0)}"]
    for x, y in points[1:]:
        segments.append(f"L{_fmt(x)},{_fmt(y)}")
    return " ".join(segments)


def _escape_xml(s: str) -> str:
    """Escape the five XML special chars (grok ``escape_xml`` L868-L874).

    ``&`` is replaced first so the entities it introduces are not re-escaped.
    Uses the ``&apos;`` named entity for the apostrophe (grok's choice).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _fmt(n: float) -> str:
    """Format a float to mirror Rust's ``f64`` Display.

    A whole-valued float drops its fractional part (``200.0`` -> ``"200"``);
    any other value uses Python's shortest round-trip representation, which
    matches Rust's Ryu-based shortest display for the pixel-range numbers
    dagre emits.
    """
    if n == int(n):
        return str(int(n))
    return repr(n)
