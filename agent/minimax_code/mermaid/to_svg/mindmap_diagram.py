"""Functional clone of grok's ``mindmap_diagram.rs`` (direction (1) brick 23).

This is the 14th per-diagram leaf and the 13th **self-contained SVG emitter**
of the R269--R291 render stack. Mirrors grok ``mindmap_diagram.rs`` (670 lines):
a radial mindmap renderer that parses an indented node tree, assigns each root
branch a section colour, lays the tree out around a central root, and emits an
SVG with quadratic-bezier edges, six node shapes, and underline decoration.

The grok renderer is **theme-unaware**: the ``_theme`` parameter is accepted
for dispatch-site symmetry with the other per-diagram renderers but is never
read. The palette hard-codes mermaid's default mindmap colours (``ROOT_FILL``
navy + the 8-hue ``SECTION_COLORS`` table) -- faithful clone of grok, whose
``render_mindmap_to_svg`` also takes ``_theme`` and ignores it. Tests pin this
boundary: a dark theme must not change the emitted SVG.

Dependency mapping (Rust crate -> Python stdlib)
-------------------------------------------------

* ``MindmapNodeType`` enum -> :class:`_NodeType` (7 variants; ``Cloud`` mirrors
  grok's ``#[allow(dead_code)]`` -- the parser never emits it).
* ``MindmapNode`` / ``PlacedNode`` / ``PlacedEdge`` structs ->
  :class:`_MindmapNode` / :class:`_PlacedNode` / :class:`_PlacedEdge`.
* ``std::f64::consts::{TAU, FRAC_PI_2}`` -> :data:`math.tau` / :data:`math.pi`.
* ``saturating_sub`` is unnecessary: every ``ends_with`` guard guarantees the
  string is long enough, so plain subtraction is safe.
* ``f64`` Display (``{}``) for svg width / height / viewBox / font-size ->
  :func:`_fmt` (drops the trailing ``.0`` on integer-valued floats); the
  coordinate fields use ``{:.1}`` (one decimal), matching grok's format specs.
* :func:`crate::text_wrap::display_width_units` is reused verbatim (R273) -- a
  label's text width is its display-width units x 8.5 px.

The barrel (:mod:`minimax_code.mermaid.to_svg`) does NOT grow: this leaf is
reached only through :func:`render.render_mermaid_to_svg`'s ``mindmap`` arm,
mirroring the journey / gitGraph leaves. ``__all__`` exports one symbol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .error import ParseError
from .text_wrap import display_width_units
from .theme import MermaidTheme

# === palette + geometry constants (grok L24-L89, L278-L280) ================

# Root node fill + text -- grok ROOT_FILL / ROOT_TEXT (navy + white).
_ROOT_FILL: str = "hsl(240, 100%, 46.27%)"
_ROOT_TEXT: str = "#ffffff"

# Fallback colour for a node whose section is still ``None`` after layout
# (defensive: assign_sections stamps every non-root node, so this only fires
# for the root, which is handled separately -- kept for parity with grok's
# ``None => ("#ECECFF", "#333333")`` arm).
_DEFAULT_NODE_FILL: str = "#ECECFF"
_DEFAULT_NODE_TEXT: str = "#333333"

# Eight section hues (grok SECTION_COLORS L40-L89). Section 0 carries a warmer
# 73.53% lightness; sections 1-7 sit at 76.27%. Section 2 (purple) carries
# white text; the rest carry black. Edge == fill for every section.
_SECTION_FILLS: tuple[str, ...] = (
    "hsl(60, 100%, 73.53%)",   # 0: yellow
    "hsl(80, 100%, 76.27%)",   # 1: yellow-green
    "hsl(270, 100%, 76.27%)",  # 2: purple (white text)
    "hsl(300, 100%, 76.27%)",  # 3: magenta
    "hsl(330, 100%, 76.27%)",  # 4: pink
    "hsl(0, 100%, 76.27%)",    # 5: red
    "hsl(30, 100%, 76.27%)",   # 6: orange
    "hsl(90, 100%, 76.27%)",   # 7: green
)
_SECTION_TEXTS: tuple[str, ...] = (
    "black",
    "black",
    "#ffffff",
    "black",
    "black",
    "black",
    "black",
    "black",
)

# Geometry (grok L278-L280, L287). The average-character width estimate for
# 16 px Trebuchet MS -- used by estimate_text_width (grok L285-L289).
FONT_SIZE: float = 16.0
NODE_PADDING: float = 15.0
ROOT_PADDING: float = 20.0
_AVG_CHAR_WIDTH: float = 8.5

# Normalize + svg-canvas padding (grok layout_mindmap L377 + render L501).
_NORMALIZE_PADDING: float = 20.0
_SVG_PADDING: float = 20.0


# === Rust Display bridge + XML escape (grok html_escape L665-L669) =========


def _fmt(value: float) -> str:
    """Mirror Rust ``f64`` Display for svg width / height / font-size.

    Rust's ``{}`` formatter drops the trailing ``.0`` on integer-valued floats
    (``16.0`` -> ``"16"``); :func:`repr` keeps the fraction otherwise
    (``-1.5`` -> ``"-1.5"``). Duplicated here so the leaf stays self-contained
    (same pattern as the gitGraph / journey leaves).
    """
    ivalue = int(value)
    if value == ivalue:
        return str(ivalue)
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the four XML chars grok's ``html_escape`` maps (L665-L669).

    Unlike the gitGraph / journey ``_escape_xml`` variant, mindmap uses
    ``&quot;`` for ``"`` and does NOT escape ``'`` (grok mindmap never emits
    ``&apos;``).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# === types (grok MindmapNodeType L6-L15 + structs L95-L119) ================


class _NodeType(Enum):
    """Mindmap node shape (grok ``MindmapNodeType``).

    ``Cloud`` mirrors grok's ``#[allow(dead_code)]`` variant: the parser has no
    ``)text(`` branch, so ``Cloud`` is never constructed -- it exists only so
    the render dispatch (``Cloud | Bang -> ellipse``) stays faithful to grok.
    """

    DEFAULT = "default"
    RECT = "rect"
    ROUNDED_RECT = "rounded_rect"
    CIRCLE = "circle"
    CLOUD = "cloud"  # dead variant (grok #[allow(dead_code)])
    BANG = "bang"
    HEXAGON = "hexagon"


@dataclass
class _MindmapNode:
    """One parsed mindmap node: label, shape, children, section stamp."""

    id: str
    label: str
    node_type: _NodeType
    children: list[_MindmapNode] = field(default_factory=list)
    section: int | None = None


@dataclass
class _PlacedNode:
    """A laid-out node carrying its absolute centre + measured box.

    ``id`` is kept for parity with grok's ``PlacedNode`` (``#[allow(dead_code)]``
    there too); the renderer reads only the geometry + section + label.
    """

    id: str
    label: str
    node_type: _NodeType
    x: float
    y: float
    width: float
    height: float
    section: int | None
    is_root: bool


@dataclass
class _PlacedEdge:
    """A laid-out quadratic-bezier edge: parent centre -> child centre."""

    from_x: float
    from_y: float
    to_x: float
    to_y: float
    section: int | None
    depth: int


# === section colour (grok section_color L91-L93) ==========================


def _section_fill(section: int) -> str:
    """The 8-hue palette wraps modulo 8 (grok ``SECTION_COLORS[section % 8]``)."""
    return _SECTION_FILLS[section % len(_SECTION_FILLS)]


def _section_text(section: int) -> str:
    return _SECTION_TEXTS[section % len(_SECTION_TEXTS)]


def _section_edge(section: int) -> str:
    """Edge colour == fill colour for every section (grok SectionColors.edge)."""
    return _section_fill(section)


# === parsing (grok parse_mindmap_tree L123-L212) ==========================


def _parse_mindmap_tree(input_str: str) -> _MindmapNode:
    """Parse a ``mindmap`` block into a root :class:`_MindmapNode`.

    Two-pass indent-stack parser (grok L123-L212):

    1. Skip blank / ``%%`` lines until the ``mindmap`` header is found; a
       non-``mindmap`` first token raises ``Expected 'mindmap' declaration``.
    2. For each remaining line, compute the leading-space/tab indent, skip
       ``::icon(...)`` decoration lines, classify the label's shape, then pop
       stack entries whose indent >= the current indent (collapsing them into
       their parent's children). The root is re-pushed if it would be popped
       (so the stack always bottoms out at the root). A root whose parsed
       shape is ``Default`` is forced to ``Circle`` (mermaid's mindmap root is
       always a circle).
    3. Collapse the residual stack to a single root. An empty body (no nodes
       after the header) raises ``No nodes found in mindmap`` (line 0).
    """
    lines = input_str.splitlines()
    i = 0
    # Find the ``mindmap`` declaration (grok L128-L142).
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("%%"):
            i += 1
            continue
        parts = stripped.split()
        if parts and parts[0] == "mindmap":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'mindmap' declaration")

    stack: list[tuple[int, _MindmapNode]] = []
    next_id = 0

    while i < len(lines):
        raw = lines[i]
        i += 1
        trimmed = raw.strip()
        if not trimmed or raw.lstrip().startswith("%%"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        # Skip ``::icon(...)`` decoration lines (grok L159-L161).
        if trimmed.startswith("::"):
            continue
        label, node_type = _extract_label_and_type(trimmed)
        node_id = f"n{next_id}"
        next_id += 1
        # ``is_root`` is computed BEFORE the pop loop (grok L167): a node is the
        # root iff the stack is empty when it arrives.
        is_root = not stack
        # Pop stack entries with indent >= current (grok L170-L183). The popped
        # node attaches to the new stack top; if the stack empties, the popped
        # node was the root and is re-pushed so it stays at the bottom.
        while stack and stack[-1][0] >= indent:
            _, child = stack.pop()
            if stack:
                stack[-1][1].children.append(child)
            else:
                stack.append((indent, child))
                break
        # A root whose shape is Default is forced to Circle (grok L188-L192).
        effective_type = (
            _NodeType.CIRCLE
            if is_root and node_type == _NodeType.DEFAULT
            else node_type
        )
        node = _MindmapNode(
            id=node_id,
            label=label,
            node_type=effective_type,
        )
        stack.append((indent, node))

    # Collapse the residual stack to a single root (grok L201-L206).
    while len(stack) > 1:
        _, child = stack.pop()
        if stack:
            stack[-1][1].children.append(child)

    if not stack:
        raise ParseError(0, "No nodes found in mindmap")
    return stack[0][1]


def _extract_label_and_type(text: str) -> tuple[str, _NodeType]:
    """Order-sensitive delimiter match (grok extract_label_and_type L214-L259).

    The double-bracket shapes are tested first (``((`` / ``{{`` / ``))``), then
    the single-bracket shapes (``[`` / ``(``), then the Default fall-through.
    Each branch requires the open delimiter, the matching close delimiter at the
    string end, and a non-empty interior (``start + N < len - N``).
    """
    t = text.strip()

    # (( ... )) -> Circle
    start = t.find("((")
    if start != -1 and t.endswith("))") and start + 2 < len(t) - 2:
        return (t[start + 2 : len(t) - 2].strip(), _NodeType.CIRCLE)

    # {{ ... }} -> Hexagon
    start = t.find("{{")
    if start != -1 and t.endswith("}}") and start + 2 < len(t) - 2:
        return (t[start + 2 : len(t) - 2].strip(), _NodeType.HEXAGON)

    # )) ... (( -> Bang
    start = t.find("))")
    if start != -1 and t.endswith("((") and start + 2 < len(t) - 2:
        return (t[start + 2 : len(t) - 2].strip(), _NodeType.BANG)

    # [ ... ] -> Rect
    start = t.find("[")
    if start != -1 and t.endswith("]") and start + 1 < len(t) - 1:
        return (t[start + 1 : len(t) - 1].strip(), _NodeType.RECT)

    # ( ... ) -> RoundedRect
    start = t.find("(")
    if start != -1 and t.endswith(")") and start + 1 < len(t) - 1:
        return (t[start + 1 : len(t) - 1].strip(), _NodeType.ROUNDED_RECT)

    # Default (no delimiter)
    return (t, _NodeType.DEFAULT)


def _assign_sections(node: _MindmapNode, section: int | None) -> None:
    """Recursively stamp each node's section index (grok assign_sections L263).

    The root carries ``None``; each direct child of the root starts its own
    section (its child index); deeper descendants inherit their ancestor's
    section.
    """
    node.section = section
    for i, child in enumerate(node.children):
        child_section = i if section is None else section
        _assign_sections(child, child_section)


# === node sizing (grok estimate_text_width + measure_node L285-L316) ======


def _estimate_text_width(text: str) -> float:
    """Display-width units x 8.5 px (grok estimate_text_width L285-L289)."""
    return display_width_units(text) * _AVG_CHAR_WIDTH


def _measure_node(node: _MindmapNode) -> tuple[float, float]:
    """Measure a node's ``(width, height)`` box (grok measure_node L291-L316).

    ``text_height`` is always :data:`FONT_SIZE` (single-line labels). Each shape
    pads the text box differently; the floor guards keep tiny labels visible.
    """
    text_width = _estimate_text_width(node.label)
    text_height = FONT_SIZE
    nt = node.node_type
    if nt == _NodeType.CIRCLE:
        diameter = max(max(text_width, text_height) + ROOT_PADDING * 2.0, 60.0)
        return (diameter, diameter)
    if nt in (_NodeType.RECT, _NodeType.ROUNDED_RECT, _NodeType.DEFAULT):
        w = text_width + NODE_PADDING * 2.0
        h = text_height + NODE_PADDING * 2.0
        return (max(w, 40.0), max(h, 36.0))
    if nt == _NodeType.HEXAGON:
        w = text_width + NODE_PADDING * 3.0
        h = text_height + NODE_PADDING * 2.0
        return (max(w, 50.0), max(h, 40.0))
    # Cloud | Bang (Cloud is dead in practice but kept for parity).
    w = text_width + NODE_PADDING * 2.5
    h = text_height + NODE_PADDING * 2.5
    return (max(w, 50.0), max(h, 40.0))


# === radial layout (grok layout_mindmap + layout_subtree L323-L482) =======


def _subtree_weight(node: _MindmapNode) -> float:
    """Leaf = 1.0; internal = max(sum(children), 1.0) (grok L405-L411)."""
    if not node.children:
        return 1.0
    child_weight = sum(_subtree_weight(child) for child in node.children)
    return max(child_weight, 1.0)


def _layout_mindmap(root: _MindmapNode) -> tuple[list[_PlacedNode], list[_PlacedEdge]]:
    """Place the root at the origin, fan children radially, then normalize.

    Each root child is allocated an angular sweep proportional to its subtree
    weight over ``TAU`` (full circle), starting at ``-pi/2`` (top). The child
    sits at ``base_radius`` (``120 + n_children * 20``) on its sweep's midpoint
    angle. After all nodes are placed, the bounding box is shifted by
    ``padding`` (20) so every node sits in the positive quadrant; edges shift
    with their endpoints.
    """
    placed_nodes: list[_PlacedNode] = []
    placed_edges: list[_PlacedEdge] = []

    root_w, root_h = _measure_node(root)
    # Root is placed at the origin (shifted into the positive quadrant later).
    placed_nodes.append(
        _PlacedNode(
            id=root.id,
            label=root.label,
            node_type=root.node_type,
            x=0.0,
            y=0.0,
            width=root_w,
            height=root_h,
            section=root.section,
            is_root=True,
        )
    )

    n_children = len(root.children)
    if n_children == 0:
        return (placed_nodes, placed_edges)

    weights = [_subtree_weight(child) for child in root.children]
    total_weight = sum(weights)

    # Distribute branches around the root, starting at the top (-pi/2).
    current_angle = -math.pi / 2.0
    base_radius = 120.0 + n_children * 20.0

    for i, child in enumerate(root.children):
        weight_fraction = weights[i] / total_weight
        sweep = math.tau * weight_fraction
        mid_angle = current_angle + sweep / 2.0
        _layout_subtree(
            child,
            0.0,
            0.0,
            mid_angle,
            base_radius,
            1,
            placed_nodes,
            placed_edges,
        )
        current_angle += sweep

    # Normalize positions into the positive quadrant (grok L376-L400).
    min_x = min(n.x - n.width / 2.0 for n in placed_nodes)
    min_y = min(n.y - n.height / 2.0 for n in placed_nodes)
    shift_x = -min_x + _NORMALIZE_PADDING
    shift_y = -min_y + _NORMALIZE_PADDING
    for node in placed_nodes:
        node.x += shift_x
        node.y += shift_y
    for edge in placed_edges:
        edge.from_x += shift_x
        edge.from_y += shift_y
        edge.to_x += shift_x
        edge.to_y += shift_y

    return (placed_nodes, placed_edges)


def _layout_subtree(
    node: _MindmapNode,
    parent_x: float,
    parent_y: float,
    angle: float,
    radius: float,
    depth: int,
    placed_nodes: list[_PlacedNode],
    placed_edges: list[_PlacedEdge],
) -> None:
    """Place ``node`` at ``parent + (cos, sin) * radius`` and recurse (grok L414).

    The node's children fan out around the parent->child direction within
    ``fan_spread`` radians (``min(pi/2, 0.8 * sqrt(n_children))``), each on its
    own weighted midpoint angle, at ``child_radius`` (``100 + depth * 10``).
    """
    node_w, node_h = _measure_node(node)
    x = parent_x + math.cos(angle) * radius
    y = parent_y + math.sin(angle) * radius

    placed_nodes.append(
        _PlacedNode(
            id=node.id,
            label=node.label,
            node_type=node.node_type,
            x=x,
            y=y,
            width=node_w,
            height=node_h,
            section=node.section,
            is_root=False,
        )
    )
    placed_edges.append(
        _PlacedEdge(
            from_x=parent_x,
            from_y=parent_y,
            to_x=x,
            to_y=y,
            section=node.section,
            depth=depth,
        )
    )

    n_children = len(node.children)
    if n_children == 0:
        return

    weights = [_subtree_weight(child) for child in node.children]
    total_weight = sum(weights)
    fan_spread = min(math.pi / 2.0, 0.8 * math.sqrt(n_children))
    child_radius = 100.0 + depth * 10.0
    current_angle = angle - fan_spread / 2.0

    for i, child in enumerate(node.children):
        weight_fraction = weights[i] / total_weight
        sweep = fan_spread * weight_fraction
        mid_angle = current_angle + sweep / 2.0
        _layout_subtree(
            child,
            x,
            y,
            mid_angle,
            child_radius,
            depth + 1,
            placed_nodes,
            placed_edges,
        )
        current_angle += sweep


# === SVG rendering (grok render_mindmap_to_svg + render_edge/node L486-L663) ===


def render_mindmap_diagram_to_svg(
    mermaid_source: str,
    _theme: MermaidTheme,  # noqa: ARG001 -- theme-unaware (see module docstring)
) -> str:
    """Render a mermaid ``mindmap`` diagram to an SVG string.

    Faithful clone of grok ``render_mindmap_to_svg`` (L486-L529): parse the
    tree, assign sections, lay it out radially, then emit an SVG whose canvas
    is sized to the placed bounding box plus padding. Edges render first
    (behind nodes); each node renders its shape, an underline decoration
    (non-root, non-circle), and its label.

    The ``_theme`` parameter is accepted for dispatch symmetry but never read
    -- the mindmap renderer hard-codes mermaid's default palette (faithful
    clone of grok, which also ignores ``_theme``).
    """
    root = _parse_mindmap_tree(mermaid_source)
    _assign_sections(root, None)
    placed_nodes, placed_edges = _layout_mindmap(root)

    # Canvas bounds: the maximum right/bottom edge of any node box, plus
    # padding (grok L492-L503). The root sits at the origin pre-normalize, so
    # the ``fold(0.0, max)`` seed is correct (no node sits in negative space
    # after the normalize shift).
    max_x = max(n.x + n.width / 2.0 for n in placed_nodes)
    max_y = max(n.y + n.height / 2.0 for n in placed_nodes)
    svg_width = max_x + _SVG_PADDING
    svg_height = max_y + _SVG_PADDING

    parts: list[str] = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_fmt(svg_width)}" height="{_fmt(svg_height)}" '
        f'viewBox="0 0 {_fmt(svg_width)} {_fmt(svg_height)}" '
        'aria-roledescription="mindmap" '
        'role="graphics-document document" '
        'style="max-width: 100%;">'
    )

    # Edges render first so nodes sit on top (grok L517-L520).
    for edge in placed_edges:
        _render_edge(parts, edge)
    for node in placed_nodes:
        _render_node(parts, node)

    parts.append("</svg>")
    return "".join(parts)


def _render_edge(parts: list[str], edge: _PlacedEdge) -> None:
    """Quadratic-bezier edge whose stroke fades with depth (grok L532-L551).

    ``stroke_width = max(17 - 3 * depth, 2)``: depth-1 edges are 14 px, depth-5+
    edges floor at 2 px. The control point is the segment midpoint (a straight
    line dressed as a bezier -- grok's choice, preserved verbatim).
    """
    color = _section_edge(edge.section) if edge.section is not None else "#333333"
    stroke_width = max(17.0 - 3.0 * edge.depth, 2.0)
    mx = (edge.from_x + edge.to_x) / 2.0
    my = (edge.from_y + edge.to_y) / 2.0
    parts.append(
        f'<path d="M {edge.from_x:.1f},{edge.from_y:.1f} '
        f'Q {mx:.1f},{my:.1f} {edge.to_x:.1f},{edge.to_y:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width:.1f}" '
        'stroke-linecap="round" />'
    )


def _render_node(parts: list[str], node: _PlacedNode) -> None:
    """Render a node's shape + underline + label (grok render_node L553-L663).

    Fill/text colours: root -> ``ROOT_FILL`` / ``ROOT_TEXT``; sectioned ->
    section palette; unsctioned fallback -> ``#ECECFF`` / ``#333333``.
    Underline fires for every non-root, non-circle node (a 3 px line in the
    node's own fill colour, sitting 5 px below the box).
    """
    if node.is_root:
        fill = _ROOT_FILL
        text_color = _ROOT_TEXT
    elif node.section is not None:
        fill = _section_fill(node.section)
        text_color = _section_text(node.section)
    else:
        fill = _DEFAULT_NODE_FILL
        text_color = _DEFAULT_NODE_TEXT

    cx = node.x
    cy = node.y
    nt = node.node_type

    if nt == _NodeType.CIRCLE:
        r = node.width / 2.0
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
            f'fill="{fill}" stroke="none" />'
        )
    elif nt == _NodeType.RECT:
        x = cx - node.width / 2.0
        y = cy - node.height / 2.0
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="0" ry="0" fill="{fill}" stroke="none" />'
        )
    elif nt in (_NodeType.ROUNDED_RECT, _NodeType.DEFAULT):
        x = cx - node.width / 2.0
        y = cy - node.height / 2.0
        # Corner radius 5 matches Mermaid's reference SVG path data (grok L590).
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="5" ry="5" fill="{fill}" stroke="none" />'
        )
    elif nt == _NodeType.HEXAGON:
        x = cx - node.width / 2.0
        y = cy - node.height / 2.0
        inset = node.height / 4.0
        points = (
            f"{x + inset:.1f},{y:.1f} "
            f"{x + node.width - inset:.1f},{y:.1f} "
            f"{x + node.width:.1f},{cy:.1f} "
            f"{x + node.width - inset:.1f},{y + node.height:.1f} "
            f"{x + inset:.1f},{y + node.height:.1f} "
            f"{x:.1f},{cy:.1f}"
        )
        parts.append(f'<polygon points="{points}" fill="{fill}" stroke="none" />')
    else:  # Cloud | Bang -> ellipse (Cloud is dead in practice).
        rx = node.width / 2.0
        ry = node.height / 2.0
        parts.append(
            f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
            f'fill="{fill}" stroke="none" />'
        )

    # Underline decoration: non-root, non-circle nodes get a coloured line
    # below the box (grok L632-L649). The stroke is the node's own fill colour
    # (grok's "hue+180" comment is aspirational; the code emits ``fill``).
    if not node.is_root and nt != _NodeType.CIRCLE:
        x1 = cx - node.width / 2.0
        x2 = cx + node.width / 2.0
        line_y = cy + node.height / 2.0 + 5.0
        parts.append(
            f'<line x1="{x1:.1f}" y1="{line_y:.1f}" x2="{x2:.1f}" y2="{line_y:.1f}" '
            f'stroke="{fill}" stroke-width="3" />'
        )

    # Label (grok L651-L662). ``font-size`` uses the Rust Display bridge so the
    # 16.0 constant renders as ``"16"`` (no trailing ``.0``).
    parts.append(
        f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
        'dominant-baseline="central" '
        "font-family=\"'trebuchet ms', verdana, arial, sans-serif\" "
        f'font-size="{_fmt(FONT_SIZE)}" fill="{text_color}">'
        f"{_escape_xml(node.label)}</text>"
    )


__all__ = ["render_mindmap_diagram_to_svg"]
