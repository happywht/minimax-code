"""Class diagram renderer (R296) -- functional clone of grok ``class_diagram.rs``.

Direction (1) brick 27 -- the 17th self-contained SVG emitter and the *third*
renderer to drive the dagre layout engine directly (after R294
``requirementDiagram`` and R295 ``erDiagram``). Mirrors grok
``mermaid-to-svg/src/class_diagram.rs`` (1144 lines): a UML class-diagram
parser that collects class boxes (name + optional ``<<stereotype>>`` +
``attributes`` / ``methods`` member partitions) and binary relationships
annotated with one of eight UML relation operators, then hands the AST to
dagre for ranked placement and emits an SVG whose nodes carry the
title / attribute-divider / method-divider three-band layout and whose
edges carry the classic UML markers
(extension / composition / aggregation / dependency x Start / End).

Functional contract preserved, implementation re-expressed Pythonically:

* Rust ``enum`` Direction / RelationType -> :class:`enum.Enum` (with the
  ``is_dashed`` / ``start_marker`` / ``end_marker`` / ``as_rankdir``
  methods ported as Python methods on the enum).
* Rust ``struct`` AST nodes -> :class:`dataclasses.dataclass`.
* Rust ``BTreeMap`` / ``BTreeSet`` -> ordered :class:`dict` (rebuilt in
  sorted-key order to match BTreeMap's name-ascending traversal) /
  :class:`set` (rebuilt sorted to match BTreeSet's deterministic order).
* Rust ``f64`` ``Display`` (``10.0`` -> ``"10"``) -> :func:`_fmt` helper
  for node / rect / line / text / transform coordinates; edge path
  points keep grok's explicit ``{:.1}`` one-decimal form via
  :func:`_fmt1`.
* ``xml_escape`` uses the numeric ``&#39;`` entity (grok variant) -- NOT
  the ``&apos;`` named entity that er / gitgraph / journey use.
* The dagre graph is built with ``compound=False`` (class diagrams carry
  no subgraphs), the same strategy R294 / R295 use to stay off the
  ``network_simplex._exchange_edges`` ``None -= 1`` pathologised branch.

Public surface: the single symbol :func:`render_class_diagram_to_svg`.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.mod import layout as dagre_layout
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.mermaid.to_svg.error import InvalidDirection, ParseError
from minimax_code.mermaid.to_svg.text_wrap import DEFAULT_CHAR_WIDTH, line_width
from minimax_code.mermaid.to_svg.theme import MermaidTheme

__all__ = ["render_class_diagram_to_svg"]


# === constants (grok L14-L34) ==============================================
#
# Layout metrics inherited verbatim from grok. Class boxes use a tighter
# ``NODE_SEP`` / ``RANK_SEP`` (50) than ER tables (140 / 80) because the
# single-column member lists are narrow; ``LINE_HEIGHT`` is a clean 24 so
# member rows tile evenly under the 14px member font.
_PADDING: float = 12.0  #: Outer padding around the class box content (grok ``PADDING``).
_GAP: float = 12.0  #: Internal gap between title / member / method bands (grok ``GAP``).
_TEXT_PADDING: float = 3.0  #: Vertical text padding inside a member row (grok ``TEXT_PADDING``).
_LINE_HEIGHT: float = 24.0  #: One member row's height (grok ``LINE_HEIGHT``).
_TITLE_FONT_SIZE: float = 18.0  #: Class-name font size (grok ``TITLE_FONT_SIZE``).
_MEMBER_FONT_SIZE: float = 14.0  #: Attribute / method font size (grok ``MEMBER_FONT_SIZE``).
_NODE_FILL: str = "#ECECFF"  #: Default node fill when theme is empty (grok ``NODE_FILL``).
_NODE_STROKE: str = "#9370DB"  #: Default node stroke when theme is empty (grok ``NODE_STROKE``).
_NODE_STROKE_WIDTH: float = 1.0  #: Node border + divider stroke width (grok ``NODE_STROKE_WIDTH``).
_EDGE_COLOR: str = "#333333"  #: Default edge color when theme is empty (grok ``EDGE_COLOR``).
_TEXT_COLOR: str = "#333"  #: Default text color when theme is empty (grok ``TEXT_COLOR``).
_GRAPH_MARGIN: float = 8.0  #: Canvas margin around the bbox (grok ``GRAPH_MARGIN``).
_NODE_SEP: float = 50.0  #: Horizontal gap between class columns (grok ``NODE_SEP``).
_RANK_SEP: float = 50.0  #: Vertical gap between class ranks (grok ``RANK_SEP``).
_EDGE_SEP: float = 20.0  #: Gap between parallel edges (grok inline ``edgesep: 20.0``).
_EXTENSION_MARKER_OFFSET: float = 17.0  #: Start-marker pull-back for extension / realization.
_DEPENDENCY_MARKER_OFFSET: float = 6.0  #: End-marker pull-back for dependency arrows.
_COMPOSITION_MARKER_OFFSET: float = 17.0  #: Start-marker pull-back for composition diamonds.
_AGGREGATION_MARKER_OFFSET: float = 17.0  #: Start-marker pull-back for aggregation diamonds.


# === AST enums (grok L47-L64, L81-L118) ====================================


class Direction(enum.Enum):
    """Layout direction (grok ``Direction``); ``as_rankdir`` maps to dagre."""

    TB = "tb"
    BT = "bt"
    LR = "lr"
    RL = "rl"

    def as_rankdir(self) -> str:
        """Return the dagre ``rankdir`` string (identical to the value)."""
        return self.value


class RelationType(enum.Enum):
    """UML relation operator (grok ``RelationType``, 8 variants).

    The ``value`` is a stable lowercase slug used only for debugging; the
    marker / dash behaviour is driven by the methods below, which mirror
    grok ``is_dashed`` / ``start_marker`` / ``end_marker``.
    """

    EXTENSION = "extension"  # ``<|--`` / ``--|>``
    COMPOSITION = "composition"  # ``*--`` / ``--*`` / ``*..`` / ``..*``
    AGGREGATION = "aggregation"  # ``o--`` / ``--o`` / ``o..`` / ``..o``
    DEPENDENCY = "dependency"  # ``-->`` / ``<--``
    REALIZATION = "realization"  # ``<|..`` / ``..|>``
    DASHED_DEP = "dashed_dep"  # ``..>`` / ``<..``
    ASSOCIATION = "association"  # ``--``
    DASHED_ASSOC = "dashed_assoc"  # ``..``

    def is_dashed(self) -> bool:
        """Realization / dashed-dependency / dashed-association render dashed."""
        return self in (
            RelationType.REALIZATION,
            RelationType.DASHED_DEP,
            RelationType.DASHED_ASSOC,
        )

    def start_marker(self) -> str | None:
        """The ``marker-start`` id, or ``None`` for plain edges (grok ``start_marker``)."""
        if self in (RelationType.EXTENSION, RelationType.REALIZATION):
            return "extensionStart"
        if self is RelationType.COMPOSITION:
            return "compositionStart"
        if self is RelationType.AGGREGATION:
            return "aggregationStart"
        return None

    def end_marker(self) -> str | None:
        """The ``marker-end`` id, or ``None`` (grok ``end_marker``)."""
        if self in (RelationType.DEPENDENCY, RelationType.DASHED_DEP):
            return "dependencyEnd"
        return None


# === AST dataclasses (grok L66-L126) =======================================


@dataclass
class ClassInfo:
    """One UML class box (grok ``ClassInfo``)."""

    name: str
    stereotype: str | None = None
    attributes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


@dataclass
class Relationship:
    """One binary class relationship (grok ``Relationship``)."""

    from_: str
    to: str
    label: str | None
    rel_type: RelationType


@dataclass
class ClassDiagram:
    """The parsed class-diagram AST (grok ``ClassDiagram``)."""

    direction: Direction = Direction.TB
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)


# === layout dataclasses (grok L130-L170) ===================================


@dataclass
class ClassNodeLayout:
    """A placed class box with pre-computed band Y coordinates."""

    id: str
    x: float
    y: float
    width: float
    height: float
    info: ClassInfo
    title_y: float
    annotation_y: float | None
    attr_divider_y: float
    method_divider_y: float
    attr_start_y: float
    method_start_y: float


@dataclass
class EdgeLayout:
    """A placed relationship edge with clip / label geometry."""

    rel_type: RelationType
    label: str | None
    points: list[tuple[float, float]]
    label_pos: tuple[float, float] | None
    label_width: float
    src_cx: float
    src_cy: float
    src_hw: float
    src_hh: float
    tgt_cx: float
    tgt_cy: float
    tgt_hw: float
    tgt_hh: float


@dataclass
class DiagramLayout:
    """The full laid-out diagram (grok ``DiagramLayout``)."""

    nodes: list[ClassNodeLayout]
    edges: list[EdgeLayout]
    width: float
    height: float


# === parser (grok L174-L424) ===============================================


def _parse_class_diagram(input_source: str) -> ClassDiagram:
    """Parse a mermaid ``classDiagram`` body into the AST (grok ``parse_class_diagram``)."""
    lines = input_source.split("\n")

    # Locate the ``classDiagram`` declaration (skipping blanks / ``%%``).
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        first = line.split()
        if first and first[0] == "classDiagram":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'classDiagram' declaration")

    direction = Direction.TB
    classes: dict[str, ClassInfo] = {}
    referenced: set[str] = set()
    relationships: list[Relationship] = []

    while i < len(lines):
        line = lines[i].strip()
        line_no = i + 1
        i += 1

        if not line or line.startswith("%%"):
            continue

        # ``direction <TD|TB|BT|LR|RL>``
        if line.startswith("direction "):
            dir_token = line[len("direction ") :].strip()
            try:
                direction = _parse_direction(dir_token)
            except ValueError:
                raise InvalidDirection(dir_token) from None
            continue

        # ``class Name {`` block or ``class Name`` declaration.
        if line.startswith("class "):
            rest = line[len("class ") :].strip()
            if rest.endswith("{"):
                name = rest[:-1].strip()
                if not name:
                    raise ParseError(line_no, "Expected class name before '{'")

                members: list[str] = []
                stereotype: str | None = None
                while i < len(lines):
                    member = lines[i].strip()
                    i += 1
                    if not member or member.startswith("%%"):
                        continue
                    if member == "}":
                        break
                    if member.startswith("<<") and member.endswith(">>"):
                        stereotype = member
                        continue
                    members.append(member)

                info = classes.get(name)
                if info is None:
                    info = ClassInfo(name=name)
                    classes[name] = info
                info.stereotype = stereotype
                attrs: list[str] = []
                methods: list[str] = []
                _classify_members(attrs, methods, members)
                info.attributes = attrs
                info.methods = methods
                continue

            if not rest:
                raise ParseError(line_no, "Expected class name")

            classes.setdefault(rest, ClassInfo(name=rest))
            continue

        # Split optional ``: label`` suffix.
        head: str
        label: str | None
        if ":" in line:
            a, b = line.split(":", 1)
            label_text = b.strip()
            head = a.strip()
            label = label_text if label_text else None
        else:
            head = line
            label = None

        parts = head.split()

        # Relationship line ``[card] A op B [card]``.
        op_pos = None
        for pos, p in enumerate(parts):
            if _is_relationship_operator(p) and pos >= 1 and pos + 1 < len(parts):
                op_pos = pos
                break

        if op_pos is not None:
            pos = op_pos

            def _quoted(s: str) -> bool:
                return len(s) >= 2 and s.startswith('"') and s.endswith('"')

            # from-side cardinality (quoted) eats one extra token.
            if _quoted(parts[pos - 1]):
                if pos < 2:
                    raise ParseError(line_no, f"Unrecognized classDiagram line: {line}")
                from_idx = pos - 2
                card_from: str | None = parts[pos - 1].strip('"')
            else:
                from_idx = pos - 1
                card_from = None

            # to-side cardinality (quoted) eats one extra token.
            if _quoted(parts[pos + 1]):
                if pos + 2 >= len(parts):
                    raise ParseError(line_no, f"Unrecognized classDiagram line: {line}")
                to_idx = pos + 2
                card_to: str | None = parts[pos + 1].strip('"')
            else:
                to_idx = pos + 1
                card_to = None

            # Reject stray tokens around the relationship core.
            if from_idx != 0 or to_idx + 1 != len(parts):
                raise ParseError(line_no, f"Unrecognized classDiagram line: {line}")

            from_name = parts[from_idx]
            to_name = parts[to_idx]
            rel_type = _classify_relationship(parts[pos])

            pieces: list[str] = []
            if card_from is not None:
                pieces.append(card_from)
            if label is not None:
                pieces.append(label)
            if card_to is not None:
                pieces.append(card_to)
            rel_label: str | None = " ".join(pieces) if pieces else None

            referenced.add(from_name)
            referenced.add(to_name)
            relationships.append(
                Relationship(
                    from_=from_name,
                    to=to_name,
                    label=rel_label,
                    rel_type=rel_type,
                )
            )
            continue

        # ``Class.member()`` single-line member append.
        if len(parts) == 1 and label is not None:
            name = parts[0]
            info = classes.get(name)
            if info is None:
                info = ClassInfo(name=name)
                classes[name] = info
            single_attrs: list[str] = []
            single_methods: list[str] = []
            _classify_members(single_attrs, single_methods, [label])
            info.attributes.extend(single_attrs)
            info.methods.extend(single_methods)
            continue

        raise ParseError(line_no, f"Unrecognized classDiagram line: {line}")

    # Auto-create classes referenced only in relationships (grok ``referenced``).
    for name in referenced:
        classes.setdefault(name, ClassInfo(name=name))

    # Match BTreeMap name-ascending traversal order.
    sorted_classes = {key: classes[key] for key in sorted(classes)}

    return ClassDiagram(
        direction=direction,
        classes=sorted_classes,
        relationships=relationships,
    )


def _parse_direction(dir_token: str) -> Direction:
    """Map a direction token to :class:`Direction` (grok ``parse_direction``).

    Raises :class:`ValueError` (caught by the caller and re-raised as
    :class:`InvalidDirection`) when the token is not one of
    ``TD`` / ``TB`` / ``BT`` / ``LR`` / ``RL`` (case-insensitive).
    """
    upper = dir_token.upper()
    if upper in ("TD", "TB"):
        return Direction.TB
    if upper == "BT":
        return Direction.BT
    if upper == "LR":
        return Direction.LR
    if upper == "RL":
        return Direction.RL
    raise ValueError(f"unknown direction: {dir_token}")


def _classify_members(
    attrs: list[str], methods: list[str], members: list[str]
) -> None:
    """Partition member lines into attributes / methods (grok ``classify_members``).

    A member containing ``(`` is a method signature; anything else is an
    attribute. The two output lists are appended in place.
    """
    for m in members:
        if "(" in m:
            methods.append(m)
        else:
            attrs.append(m)


def _is_relationship_operator(op: str) -> bool:
    """Whether ``op`` is one of the 17 UML relation operators (grok ``is_relationship_operator``)."""
    return op in (
        "<|--",
        "<|..",
        "..|>",
        "--|>",
        "--",
        "-->",
        "<--",
        "..",
        "..>",
        "<..",
        "*--",
        "o--",
        "--*",
        "--o",
        "*..",
        "o..",
        "..*",
        "..o",
    )


def _classify_relationship(op: str) -> RelationType:
    """Map a relation operator to a :class:`RelationType` (grok ``classify_relationship``)."""
    if op in ("<|--", "--|>"):
        return RelationType.EXTENSION
    if op in ("<|..", "..|>"):
        return RelationType.REALIZATION
    if op in ("*--", "--*", "*..", "..*"):
        return RelationType.COMPOSITION
    if op in ("o--", "--o", "o..", "..o"):
        return RelationType.AGGREGATION
    if op in ("-->", "<--"):
        return RelationType.DEPENDENCY
    if op in ("..>", "<.."):
        return RelationType.DASHED_DEP
    if op == "..":
        return RelationType.DASHED_ASSOC
    return RelationType.ASSOCIATION


# === layout computation (grok L468-L729) ===================================


def _compute_class_box_size(
    info: ClassInfo,
) -> tuple[float, float, float, float, float, float, float, float | None]:
    """Compute a class box's geometry (grok ``compute_class_box_size``).

    Returns ``(width, height, title_y, attr_divider_y, method_divider_y,
    attr_start_y, method_start_y, annotation_y)`` -- the eight metrics
    ported from Mermaid 11.12.2 ``shapeUtil.ts`` (``textHelper`` group
    positions) plus ``classBox.ts`` (outer ``PADDING`` on every side).
    """
    max_text_width = 0.0

    # Title (class name) -- rendered at the larger title font, so its
    # char-width measurement is scaled to the member font's metric.
    title_width = line_width(info.name, DEFAULT_CHAR_WIDTH) * (
        _TITLE_FONT_SIZE / _MEMBER_FONT_SIZE
    )
    max_text_width = max(max_text_width, title_width)

    # Stereotype annotation width (guillemet form).
    annotation_height = _LINE_HEIGHT if info.stereotype is not None else 0.0
    if info.stereotype is not None:
        guillemet_text = _stereotype_to_guillemet(info.stereotype)
        max_text_width = max(
            max_text_width, line_width(guillemet_text, DEFAULT_CHAR_WIDTH)
        )

    for attr in info.attributes:
        max_text_width = max(max_text_width, line_width(attr, DEFAULT_CHAR_WIDTH))
    for method in info.methods:
        max_text_width = max(max_text_width, line_width(method, DEFAULT_CHAR_WIDTH))

    # Width: widest text line + outer padding on each side.
    box_width = max_text_width + _PADDING * 2.0

    # Internal content heights (textHelper group positioning).
    label_height = _LINE_HEIGHT  # title text bounding box
    members_height = (
        _GAP / 2.0
        if not info.attributes
        else len(info.attributes) * (_LINE_HEIGHT + _TEXT_PADDING)
    )
    methods_height = (
        _GAP / 2.0
        if not info.methods
        else len(info.methods) * (_LINE_HEIGHT + _TEXT_PADDING)
    )

    methods_y = annotation_height + label_height + (
        members_height + _GAP * 4.0 if info.attributes else _GAP * 2.0
    )
    content_height = methods_y + methods_height

    # classBox adds an extra gap when one band is empty.
    if not info.attributes and not info.methods:
        extra_h = _GAP
    elif info.attributes and not info.methods:
        extra_h = _GAP * 2.0
    else:
        extra_h = 0.0

    box_height = content_height + extra_h + _PADDING * 2.0
    half_h = box_height / 2.0

    # Position elements relative to box centre (classBox translate logic):
    # text groups start at y = -h/2 + PADDING (top of content area).
    content_top = -half_h + _PADDING

    annotation_y = (
        content_top + _LINE_HEIGHT / 2.0 if info.stereotype is not None else None
    )
    title_y = content_top + annotation_height + label_height / 2.0

    # Divider positions ported from classBox.ts (box-local coords):
    # first divider at content_top + annotH + labelH; second divider
    # sits members_height + GAP*2 below the first.
    attr_divider_y = content_top + annotation_height + label_height
    method_divider_y = attr_divider_y + members_height + _GAP * 2.0

    attr_start_y = attr_divider_y + _GAP + _LINE_HEIGHT / 2.0
    method_start_y = method_divider_y + _GAP + _LINE_HEIGHT / 2.0

    return (
        box_width,
        box_height,
        title_y,
        attr_divider_y,
        method_divider_y,
        attr_start_y,
        method_start_y,
        annotation_y,
    )


def _compute_layout(diagram: ClassDiagram) -> DiagramLayout:
    """Lay out the diagram via dagre (grok ``compute_layout``)."""
    node_sizes: dict[str, tuple[float, float, float, float, float, float, float, float | None]] = {}
    for cid, info in diagram.classes.items():
        node_sizes[cid] = _compute_class_box_size(info)

    graph = Graph(
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

    for cid, metrics in node_sizes.items():
        width = metrics[0]
        height = metrics[1]
        graph.set_node(cid, GraphNode(width=width, height=height))

    edge_keys: list[tuple[str, str, int]] = []
    for idx, rel in enumerate(diagram.relationships):
        edge_label = GraphEdge()
        if rel.label is not None:
            label_w = line_width(rel.label, DEFAULT_CHAR_WIDTH)
            edge_label.width = label_w
            edge_label.height = _LINE_HEIGHT
            edge_label.labelpos = "c"
        graph.set_edge(rel.from_, rel.to, edge_label, None)
        edge_keys.append((rel.from_, rel.to, idx))

    dagre_layout(graph)

    node_positions: dict[str, tuple[float, float]] = {}
    for node_id in graph.nodes():
        node = graph.node(node_id)
        if node is not None:
            node_positions[node_id] = (float(node.x), float(node.y))

    nodes: list[ClassNodeLayout] = []
    for cid, info in diagram.classes.items():
        (
            width,
            height,
            title_y,
            attr_div_y,
            meth_div_y,
            attr_start_y,
            meth_start_y,
            annotation_y,
        ) = node_sizes[cid]
        cx, cy = node_positions.get(cid, (0.0, 0.0))
        nodes.append(
            ClassNodeLayout(
                id=cid,
                x=cx,
                y=cy,
                width=width,
                height=height,
                info=info,
                title_y=title_y,
                annotation_y=annotation_y,
                attr_divider_y=attr_div_y,
                method_divider_y=meth_div_y,
                attr_start_y=attr_start_y,
                method_start_y=meth_start_y,
            )
        )

    edges: list[EdgeLayout] = []
    for from_id, to_id, idx in edge_keys:
        edge = graph.edge(from_id, to_id, None)
        if edge is None:
            continue
        raw_points = edge.points or []
        points = [(float(p.x), float(p.y)) for p in raw_points]

        rel = diagram.relationships[idx]
        label_pos = (
            (float(edge.x), float(edge.y)) if rel.label is not None else None
        )
        label_width = (
            line_width(rel.label, DEFAULT_CHAR_WIDTH) if rel.label is not None else 0.0
        )

        src_cx, src_cy = node_positions.get(from_id, (0.0, 0.0))
        src_metrics = node_sizes.get(from_id)
        src_w = src_metrics[0] if src_metrics else 0.0
        src_h = src_metrics[1] if src_metrics else 0.0
        tgt_cx, tgt_cy = node_positions.get(to_id, (0.0, 0.0))
        tgt_metrics = node_sizes.get(to_id)
        tgt_w = tgt_metrics[0] if tgt_metrics else 0.0
        tgt_h = tgt_metrics[1] if tgt_metrics else 0.0

        edges.append(
            EdgeLayout(
                rel_type=rel.rel_type,
                label=rel.label,
                points=points,
                label_pos=label_pos,
                label_width=label_width,
                src_cx=src_cx,
                src_cy=src_cy,
                src_hw=src_w / 2.0,
                src_hh=src_h / 2.0,
                tgt_cx=tgt_cx,
                tgt_cy=tgt_cy,
                tgt_hw=tgt_w / 2.0,
                tgt_hh=tgt_h / 2.0,
            )
        )

    min_x = math.inf
    max_x = -math.inf
    min_y = math.inf
    max_y = -math.inf
    for n in nodes:
        min_x = min(min_x, n.x - n.width / 2.0)
        max_x = max(max_x, n.x + n.width / 2.0)
        min_y = min(min_y, n.y - n.height / 2.0)
        max_y = max(max_y, n.y + n.height / 2.0)

    width = (max_x - min_x) + _GRAPH_MARGIN * 2.0
    height = (max_y - min_y) + _GRAPH_MARGIN * 2.0

    return DiagramLayout(
        nodes=nodes,
        edges=edges,
        width=max(width, 100.0),
        height=max(height, 100.0),
    )


# === SVG rendering (grok L733-L1128) =======================================


def _render_svg(layout: DiagramLayout, theme: MermaidTheme) -> str:
    """Render the laid-out diagram to an SVG string (grok ``render_svg``)."""
    fill = _NODE_FILL if not theme.node_fill else theme.node_fill
    stroke = _NODE_STROKE if not theme.node_stroke else theme.node_stroke
    edge_col = _EDGE_COLOR if not theme.edge_color else theme.edge_color
    txt_col = _TEXT_COLOR if not theme.text_color else theme.text_color

    width = layout.width
    height = layout.height

    svg: list[str] = []
    svg.append(
        '<svg xmlns="http://www.w3.org/2000/svg" class="classDiagram" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" '
        'role="graphics-document document" aria-roledescription="class">\n'
    )

    svg.append(
        "<style>\n"
        f'svg {{ font-family: "trebuchet ms", verdana, arial, sans-serif; '
        f"font-size: 16px; fill: {txt_col}; }}\n"
        f".classTitle {{ font-weight: bolder; font-size: {_TITLE_FONT_SIZE}px; }}\n"
        f".classMember {{ font-size: {_MEMBER_FONT_SIZE}px; }}\n"
        f".classAnnotation {{ font-size: {_MEMBER_FONT_SIZE}px; }}\n"
        "</style>\n"
    )

    svg.append(_render_marker_defs(fill, edge_col))

    for node in layout.nodes:
        _render_class_node(svg, node, fill, stroke, txt_col)

    for edge in layout.edges:
        _render_edge(svg, edge, edge_col, fill, txt_col)

    svg.append("</svg>\n")
    return "".join(svg)


def _render_marker_defs(fill: str, edge_color: str) -> str:
    """Emit the 9 UML marker definitions (grok ``render_marker_defs``)."""
    return (
        "<defs>\n"
        # Extension (open/hollow triangle) -- for inheritance.
        f'  <marker id="extensionStart" class="marker extension" refX="18" refY="7" '
        f'markerWidth="190" markerHeight="240" orient="auto">'
        f'<path d="M 1,7 L18,13 V 1 Z" fill="transparent" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        f'  <marker id="extensionEnd" class="marker extension" refX="1" refY="7" '
        f'markerWidth="20" markerHeight="28" orient="auto">'
        f'<path d="M 1,1 V 13 L18,7 Z" fill="transparent" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        # Dependency (filled arrowhead).
        f'  <marker id="dependencyEnd" class="marker dependency" refX="13" refY="7" '
        f'markerWidth="20" markerHeight="28" orient="auto">'
        f'<path d="M 18,7 L9,13 L14,7 L9,1 Z" fill="{edge_color}" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        f'  <marker id="dependencyStart" class="marker dependency" refX="6" refY="7" '
        f'markerWidth="190" markerHeight="240" orient="auto">'
        f'<path d="M 5,7 L9,13 L1,7 L9,1 Z" fill="{edge_color}" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        # Composition (filled diamond).
        f'  <marker id="compositionStart" class="marker composition" refX="18" refY="7" '
        f'markerWidth="190" markerHeight="240" orient="auto">'
        f'<path d="M 18,7 L9,13 L1,7 L9,1 Z" fill="{edge_color}" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        f'  <marker id="compositionEnd" class="marker composition" refX="1" refY="7" '
        f'markerWidth="20" markerHeight="28" orient="auto">'
        f'<path d="M 18,7 L9,13 L1,7 L9,1 Z" fill="{edge_color}" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        # Aggregation (open diamond).
        f'  <marker id="aggregationStart" class="marker aggregation" refX="18" refY="7" '
        f'markerWidth="190" markerHeight="240" orient="auto">'
        f'<path d="M 18,7 L9,13 L1,7 L9,1 Z" fill="transparent" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        f'  <marker id="aggregationEnd" class="marker aggregation" refX="1" refY="7" '
        f'markerWidth="20" markerHeight="28" orient="auto">'
        f'<path d="M 18,7 L9,13 L1,7 L9,1 Z" fill="transparent" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        # Lollipop (circle).
        f'  <marker id="lollipopStart" class="marker lollipop" refX="13" refY="7" '
        f'markerWidth="190" markerHeight="240" orient="auto">'
        f'<circle cx="7" cy="7" r="6" fill="{fill}" '
        f'stroke="{edge_color}" stroke-width="1"/></marker>\n'
        "</defs>\n"
    )


def _render_class_node(
    svg: list[str],
    node: ClassNodeLayout,
    fill: str,
    stroke: str,
    text_color: str,
) -> None:
    """Emit one class box (grok ``render_class_node``)."""
    width = node.width
    height = node.height
    hw = width / 2.0
    hh = height / 2.0

    svg.append(
        f'<g class="node default" id="classId-{_xml_escape(node.id)}" '
        f'transform="translate({_fmt(node.x)},{_fmt(node.y)})">\n'
    )

    # Background fill rect.
    svg.append(
        f'  <rect x="{_fmt(-hw)}" y="{_fmt(-hh)}" width="{_fmt(width)}" '
        f'height="{_fmt(height)}" fill="{fill}" stroke="none"/>\n'
    )

    # Border.
    svg.append(
        f'  <rect x="{_fmt(-hw)}" y="{_fmt(-hh)}" width="{_fmt(width)}" '
        f'height="{_fmt(height)}" fill="none" stroke="{stroke}" '
        f'stroke-width="{_fmt(_NODE_STROKE_WIDTH)}"/>\n'
    )

    # Stereotype annotation (if present).
    if node.annotation_y is not None and node.info.stereotype is not None:
        guillemet = _stereotype_to_guillemet(node.info.stereotype)
        svg.append(
            f'  <text x="0" y="{_fmt(node.annotation_y)}" text-anchor="middle" '
            f'dominant-baseline="central" class="classAnnotation" '
            f'fill="{text_color}">{_xml_escape(guillemet)}</text>\n'
        )

    # Class name (bold, centred).
    svg.append(
        f'  <text x="0" y="{_fmt(node.title_y)}" text-anchor="middle" '
        f'dominant-baseline="central" class="classTitle" '
        f'fill="{text_color}">{_xml_escape(node.info.name)}</text>\n'
    )

    # Attribute divider line.
    svg.append(
        f'  <line x1="{_fmt(-hw)}" y1="{_fmt(node.attr_divider_y)}" '
        f'x2="{_fmt(hw)}" y2="{_fmt(node.attr_divider_y)}" stroke="{stroke}" '
        f'stroke-width="{_fmt(_NODE_STROKE_WIDTH)}"/>\n'
    )

    # Attributes (left-aligned).
    for idx, attr in enumerate(node.info.attributes):
        ay = node.attr_start_y + idx * (_LINE_HEIGHT + _TEXT_PADDING)
        svg.append(
            f'  <text x="{_fmt(-hw + _PADDING)}" y="{_fmt(ay)}" text-anchor="start" '
            f'dominant-baseline="central" class="classMember" '
            f'fill="{text_color}">{_xml_escape(attr)}</text>\n'
        )

    # Method divider line.
    svg.append(
        f'  <line x1="{_fmt(-hw)}" y1="{_fmt(node.method_divider_y)}" '
        f'x2="{_fmt(hw)}" y2="{_fmt(node.method_divider_y)}" stroke="{stroke}" '
        f'stroke-width="{_fmt(_NODE_STROKE_WIDTH)}"/>\n'
    )

    # Methods (left-aligned).
    for idx, method in enumerate(node.info.methods):
        my = node.method_start_y + idx * (_LINE_HEIGHT + _TEXT_PADDING)
        svg.append(
            f'  <text x="{_fmt(-hw + _PADDING)}" y="{_fmt(my)}" text-anchor="start" '
            f'dominant-baseline="central" class="classMember" '
            f'fill="{text_color}">{_xml_escape(method)}</text>\n'
        )

    svg.append("</g>\n")


def _intersect_rect(
    cx: float,
    cy: float,
    hw: float,
    hh: float,
    target_x: float,
    target_y: float,
) -> tuple[float, float]:
    """Intersection of a centre -> target ray with a rectangle (grok ``intersect_rect``)."""
    dx = target_x - cx
    dy = target_y - cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return (cx, cy - hh)
    t_right = hw / dx if dx > 0.0 else math.inf
    t_left = -hw / dx if dx < 0.0 else math.inf
    t_bottom = hh / dy if dy > 0.0 else math.inf
    t_top = -hh / dy if dy < 0.0 else math.inf
    t = max(0.0, min(t_right, t_left, t_bottom, t_top))
    return (cx + dx * t, cy + dy * t)


def _clip_and_offset_edge(
    points: list[tuple[float, float]],
    rel_type: RelationType,
    src_cx: float,
    src_cy: float,
    src_hw: float,
    src_hh: float,
    tgt_cx: float,
    tgt_cy: float,
    tgt_hw: float,
    tgt_hh: float,
) -> list[tuple[float, float]]:
    """Clip edge endpoints to node boundaries, then offset for markers (grok ``clip_and_offset_edge``)."""
    if len(points) < 2:
        return list(points)
    out = list(points)

    # Clip start point to source node boundary.
    next_pt = out[1]
    out[0] = _intersect_rect(src_cx, src_cy, src_hw, src_hh, next_pt[0], next_pt[1])

    # Clip end point to target node boundary.
    prev = out[-2]
    out[-1] = _intersect_rect(tgt_cx, tgt_cy, tgt_hw, tgt_hh, prev[0], prev[1])

    # Offset start for start markers (pull toward the last edge point to
    # avoid short-segment capping issues with intermediate dagre points).
    if rel_type.start_marker() is not None:
        offset = _start_marker_offset(rel_type)
        x0, y0 = out[0]
        last = out[-1]
        dx = last[0] - x0
        dy = last[1] - y0
        length = math.hypot(dx, dy)
        if length > 0.0:
            out[0] = (x0 + dx / length * offset, y0 + dy / length * offset)

    # Offset end for end markers (pull toward the first edge point).
    if rel_type.end_marker() is not None:
        offset = _end_marker_offset(rel_type)
        xn, yn = out[-1]
        first = out[0]
        dx = first[0] - xn
        dy = first[1] - yn
        length = math.hypot(dx, dy)
        if length > 0.0:
            out[-1] = (xn + dx / length * offset, yn + dy / length * offset)

    return out


def _start_marker_offset(rel_type: RelationType) -> float:
    """Start-marker pull-back distance (grok ``start_marker_offset``)."""
    if rel_type in (RelationType.EXTENSION, RelationType.REALIZATION):
        return _EXTENSION_MARKER_OFFSET
    if rel_type is RelationType.COMPOSITION:
        return _COMPOSITION_MARKER_OFFSET
    if rel_type is RelationType.AGGREGATION:
        return _AGGREGATION_MARKER_OFFSET
    return 0.0


def _end_marker_offset(rel_type: RelationType) -> float:
    """End-marker pull-back distance (grok ``end_marker_offset``)."""
    if rel_type in (RelationType.DEPENDENCY, RelationType.DASHED_DEP):
        return _DEPENDENCY_MARKER_OFFSET
    return 0.0


def _render_edge(
    svg: list[str],
    edge: EdgeLayout,
    edge_color: str,
    fill: str,
    text_color: str,
) -> None:
    """Emit one relationship edge + optional label (grok ``render_edge``)."""
    if not edge.points:
        return

    dash = ' stroke-dasharray="3"' if edge.rel_type.is_dashed() else ""

    points = _clip_and_offset_edge(
        edge.points,
        edge.rel_type,
        edge.src_cx,
        edge.src_cy,
        edge.src_hw,
        edge.src_hh,
        edge.tgt_cx,
        edge.tgt_cy,
        edge.tgt_hw,
        edge.tgt_hh,
    )

    path_parts: list[str] = []
    for idx, (x, y) in enumerate(points):
        if idx == 0:
            path_parts.append(f"M{_fmt1(x)},{_fmt1(y)}")
        else:
            path_parts.append(f"L{_fmt1(x)},{_fmt1(y)}")
    path = "".join(path_parts)

    marker_start = ""
    marker_end = ""
    start_marker = edge.rel_type.start_marker()
    end_marker = edge.rel_type.end_marker()
    if start_marker is not None:
        marker_start = f' marker-start="url(#{start_marker})"'
    if end_marker is not None:
        marker_end = f' marker-end="url(#{end_marker})"'

    svg.append(
        f'<path d="{path}" class="relation" stroke="{edge_color}" stroke-width="1" '
        f'fill="none"{dash}{marker_start}{marker_end}/>\n'
    )

    # Edge label (rect backdrop + centred text).
    if edge.label is not None and edge.label_pos is not None:
        lx, ly = edge.label_pos
        lw = edge.label_width
        lh = _LINE_HEIGHT
        svg.append(
            f'<rect x="{_fmt(lx - lw / 2.0 - 4.0)}" y="{_fmt(ly - lh / 2.0)}" '
            f'width="{_fmt(lw + 8.0)}" height="{_fmt(lh)}" fill="{fill}" '
            f'stroke="none" opacity="0.75"/>\n'
        )
        svg.append(
            f'<text x="{_fmt(lx)}" y="{_fmt(ly)}" text-anchor="middle" '
            f'dominant-baseline="central" fill="{text_color}">'
            f"{_xml_escape(edge.label)}</text>\n"
        )


# === helpers (grok L1130-L1144) ============================================


def _stereotype_to_guillemet(s: str) -> str:
    """Wrap a ``<<x>>`` stereotype in U+00AB / U+00BB guillemets (grok ``stereotype_to_guillemet``)."""
    if s.startswith("<<"):
        rest = s[2:]
        if rest.endswith(">>"):
            inner: str = rest[:-2]
        else:
            inner = s
    else:
        inner = s
    return f"«{inner}»"


def _xml_escape(s: str) -> str:
    """Escape XML special chars using the numeric ``&#39;`` entity (grok ``xml_escape``)."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _fmt(x: float) -> str:
    """Format an f64 like Rust ``Display``: integers drop ``.0`` (grok node coords)."""
    if x == math.inf:
        return "inf"
    if x == -math.inf:
        return "-inf"
    if math.isnan(x):
        return "NaN"
    if x == int(x):
        return str(int(x))
    return repr(x)


def _fmt1(x: float) -> str:
    """Format an f64 to one decimal place (grok edge-path ``{:.1}``)."""
    return f"{x:.1f}"


# === public entry (grok L36-L43) ===========================================


def render_class_diagram_to_svg(
    mermaid_source: str, theme: MermaidTheme | None = None
) -> str:
    """Render a mermaid ``classDiagram`` body to an SVG string.

    Mirrors grok ``render_class_diagram_to_svg``: parse -> compute_layout ->
    render_svg. ``theme`` defaults to :meth:`MermaidTheme.default` (light)
    when ``None``, matching the crate-root dispatch resolver.
    """
    if theme is None:
        theme = MermaidTheme.default()
    diagram = _parse_class_diagram(mermaid_source)
    layout = _compute_layout(diagram)
    return _render_svg(layout, theme)
