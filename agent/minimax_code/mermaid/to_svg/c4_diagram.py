"""Migrated C4 diagram renderer (direction (1), brick 28 -- R297).

Exposes :func:`render_c4_diagram_to_svg` -- the public entry that drives all
five C4 variants (``C4Context`` / ``C4Container`` / ``C4Component`` /
``C4Dynamic`` / ``C4Deployment``) through one shared renderer. Mirrors grok
``mermaid-to-svg/src/c4_diagram.rs`` (1201 lines) -- a port of Mermaid's
``c4Renderer.js`` (the Structurizr C4 model diagrams).

This is the **18th self-contained SVG emitter** in the bespoke-geometry
family (after R279 info, R280 stateDiagram, R281 radar, R282 pie, R283
packet, R284 sankey, R285 gantt, R286 kanban, R287 timeline, R288 quadrant,
R289 block, R290 journey, R291 gitGraph, R292 mindmap, R293 xychart, R294
requirement, R295 er, R296 class). It does **not** consume the dagre layout
engine -- C4 uses bespoke grid geometry (a port of Mermaid's ``Bounds`` class)
that self-computes shape coordinates, so it shares nothing with the dagre
consumers (R294 requirement / R295 er / R296 class).

Functional contract (zero-semantic clone of grok):

1. **5 variants, 1 renderer** -- all five C4 diagram-type tokens route to
   this module's single entry (grok L786). ``C4Dynamic`` is the only variant
   with divergent render behaviour: relationship labels carry the 1-based
   ``{index}: {label}`` prefix (grok L1154-L1158); the other four emit the
   label verbatim.
2. **Bespoke grid layout** -- shapes are placed on a flowing grid (4 per row,
   wrap at ``width_limit=800``) via the :class:`_Bounds` tracker, a 9-field
   port of Mermaid's ``Bounds`` class (grok L127-L210). No dagre, no rank
   solver.
3. **18 colour mappings x 2** -- each of the 9 C4 shape types has a base and
   an ``external_*`` twin; ``_BG_COLOR_FOR`` / ``_BORDER_COLOR_FOR`` carry the
   fill / stroke hex per type (grok L23-L73), with a single ``default``
   fallback.
4. **3 shape geometries** -- rectangle (default), database cylinder (``*_db``),
   queue (``*_queue``). ``person`` / ``external_person`` additionally carry a
   48x48 PNG icon (grok L1005-L1132).
5. **Boundaries** -- dashed ``#444444`` rectangles wrapping child shapes,
   with a bold label + optional ``[type]`` subtitle (grok L968-L1003).
6. **Relationships** -- first rel is a straight ``<line>``, subsequent rels
   are quadratic Bézier ``<path>`` curves; all carry the ``arrowhead`` marker
   and an optional ``[techn]`` subtitle (grok L1134-L1201).
7. **Static defs** -- 3 icon ``<symbol>`` (computer/database/clock) + 4 arrow
   ``<marker>`` (arrowhead/arrowend/crosshead/filled-head) emitted unconditionally
   (grok L950-L966).
8. **Theme channels** -- 3 of the 5 channels flow in: ``background`` (svg
   ``background-color``, normalised ``#ffffff`` -> ``white``), ``text_color``
   (style ``fill``, normalised ``#333333`` -> ``#333``), ``edge_color`` (the
   ``.marker`` class fill/stroke). The shape/boundary palette is fixed C4.

Pythonic conversions (mechanical, no behaviour change):

* ``estimate_text_width`` uses ``len(text.encode('utf-8'))`` to mirror Rust's
  ``str::len()`` (byte length) -- C4 labels carry ASCII-heavy text, but the
  byte-accurate count keeps the heuristic identical for any non-ASCII edge.
* ``Bounds`` is a plain ``@dataclass`` with explicit ``set_data`` /
  ``insert`` / ``bump_last_margin`` methods; the ``insert`` algorithm is
  ported verbatim including the ``next_cnt > C4_SHAPE_IN_ROW`` row-wrap reset.
* ``PERSON_IMG`` / ``EXTERNAL_PERSON_IMG`` are kept as raw ``data:image/png``
  URIs verbatim (grok emits them without decoding); the ``EXTERNAL`` variant
  carries a legacy non-ASCII byte sequence that is preserved byte-for-byte.
* The parser is split into pure functions (``_parse_c4_diagram`` /
  ``_parse_c4_statement`` / ``_find_matching_paren`` / ``_split_c4_args``)
  matching grok's line-oriented state machine.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.theme import MermaidTheme

__all__ = ["render_c4_diagram_to_svg"]

# === constants (grok L6-L15) =================================================

#: Outer horizontal margin of the whole diagram (grok ``DIAGRAM_MARGIN_X``).
_DIAGRAM_MARGIN_X: float = 50.0
#: Outer vertical margin of the whole diagram (grok ``DIAGRAM_MARGIN_Y``).
_DIAGRAM_MARGIN_Y: float = 10.0
#: Inter-shape spacing inside a boundary (grok ``C4_SHAPE_MARGIN``).
_C4_SHAPE_MARGIN: float = 50.0
#: Inner text padding of a shape rect (grok ``C4_SHAPE_PADDING``).
_C4_SHAPE_PADDING: float = 20.0
#: Minimum shape width before clamping (grok ``DEFAULT_WIDTH``).
_DEFAULT_WIDTH: float = 216.0
#: Minimum shape height before clamping (grok ``DEFAULT_HEIGHT``).
_DEFAULT_HEIGHT: float = 60.0
#: Shapes per row before the grid wraps (grok ``C4_SHAPE_IN_ROW``).
_C4_SHAPE_IN_ROW: int = 4
#: Base C4 font size in px (grok ``FONT_SIZE``).
_FONT_SIZE: float = 14.0
#: C4 CSS font-family stack (grok ``FONT_FAMILY``).
_FONT_FAMILY: str = "'Open Sans', sans-serif"
#: Relationship label font size in px (grok ``MESSAGE_FONT_SIZE``).
_MESSAGE_FONT_SIZE: float = 12.0

# === person icons (grok L17/L19 -- verbatim data URIs) =======================
# NOTE: EXTERNAL_PERSON_IMG carries a legacy non-ASCII byte sequence in its
# base64 body (grok source bug). Kept byte-for-byte: grok emits the URI into
# the SVG without decoding, so a decode failure is irrelevant to output parity.

_PERSON_IMG: str = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAIAAADYYG7QAAACD0lEQVR4Xu2YoU4EMRCG"
    "T+4j8Ai8AhaH4QHgAUjQuFMECUgMIUgwJAgMhgQsAYUiJCiQIBBY+EITsjfTdme6V24v4c8vyGbb+ZjOtN0bNcvjQ"
    "XmkH83WvYBWto6PLm6v7p7uH1/w2fXD+PBycX1Pv2l3IdDm/vn7x+dXQiAubRzoURa7gRZWd0iGRIiJbOnhnfYBQZ"
    "NJjNbuyY2eJG8fkDE3bbG4ep6MHUAsgYxmE3nVs6VsBWJSGccsOlFPmLIViMzLOB7pCVO2AtHJMohH7Fh6zqitQK7"
    "m0rJvAVYgGcEpe//PLdDz65sM4pF9N7ICcXDKIB5Nv6j7tD0NoSdM2QrU9Gg0ewE1LqBhHR3BBdvj2vapnidjHxD/"
    "q6vd7Pvhr31AwcY8eXMTXAKECZZJFXuEq27aLgQK5uLMohCenGGuGewOxSjBvYBqeG6B+Nqiblggdjnc+ZXDy+FNF"
    "pFzw76O3UBAROuXh6FoiAcf5g9eTvUgzy0nWg6I8cXHRUpg5bOVBCo+KDpFajOf23GgPme7RSQ+lacIENUgJ6gg1k"
    "6HjgOlqnLqip4tEuhv0hNEMXUD0clyXE3p6pZA0S2nnvTlXwLJEZWlb7cTQH1+USgTN4VhAenm/wea1OCAOmqo6fE"
    "1WCb9WSKBah+rbUWPWAmE2Rvk0ApiB45eOyNAzU8xcTvj8KvkKEoOaIYeHNA3ZuygAvFMUO0AAAAASUVORK5CYII="
)

_EXTERNAL_PERSON_IMG: str = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAIAAADYYG7QAAAB6ElEQVR4Xu2YLY+ЕМБ"
    "CG9+dWr0aj0Wg0Go1Go0+j8Xdv2uTCvv1gpt0ebHKPuhDaeW4605Z9mJvx4AdXUyTUdd08z+u6flmWZRnHsWkafk9"
    "DptAwDPu+f0eAYtu2PEaGWuj5fCIZrBAC2eLBAnRCsEkkxmeaJp7iDJ2QMDdHsLg8SxKFEJaAo8lAXnmuOFIhTMpx"
    "xKATebo4UiFknuNo4OniSIXQyRxEA3YsnjGCVEjVXD7yLUAqxBGUyPv/Y4W2beMgGuS7kVQIBycH0fD+oi5pezQET"
    "xdHKmQKGk1eQEYldK+jw5GxPfZ9z7Mk0Qnhf1W1m3w//EUn5BDmSZsbR44QQLBEqrBHqOrmSKaQAxdnLArCrxZcM7"
    "A7ZKs4ioRq8LFC+NpC3WCBJsvpVw5edm9iEXFuyNfxXAgSwfrFQ1c0iNda8AdejvUgnktOtJQQxmcfFzGglc5WVCj"
    "7oDgFqU18boeFSs52CUh8LE8BIVQDT1ABrB0HtgSEYlX5doJnCwv9TXocKCaKbnwhdDKPq4lf3SwU3HLq4V/+WYhH"
    "VMa/3b4IlfyikAduCkcBc7mQ3/z/Qq/cTuikhkzB12Ae/mcJC9U+Vo8Ej1gWAtgbeGgFsAMHr50BIWOLCbezvhpBF"
    "UdY6EJuJ/QDW0XoMX60zZ0AAAAASUVORK5CYII="
)

# === colour mappings (grok L23-L73) ==========================================
# 18 entries each (9 base types x 2 for the external_* twin) + a default.

_BG_COLOR_FOR: dict = {
    "person": "#08427B",
    "external_person": "#686868",
    "system": "#1168BD",
    "external_system": "#999999",
    "system_db": "#1168BD",
    "external_system_db": "#999999",
    "system_queue": "#1168BD",
    "external_system_queue": "#999999",
    "container": "#438DD5",
    "external_container": "#B3B3B3",
    "container_db": "#438DD5",
    "external_container_db": "#B3B3B3",
    "container_queue": "#438DD5",
    "external_container_queue": "#B3B3B3",
    "component": "#85BBF0",
    "external_component": "#CCCCCC",
    "component_db": "#85BBF0",
    "external_component_db": "#CCCCCC",
    "component_queue": "#85BBF0",
    "external_component_queue": "#CCCCCC",
}

_BORDER_COLOR_FOR: dict = {
    "person": "#073B6F",
    "external_person": "#8A8A8A",
    "system": "#3C7FC0",
    "external_system": "#8A8A8A",
    "system_db": "#3C7FC0",
    "external_system_db": "#8A8A8A",
    "system_queue": "#3C7FC0",
    "external_system_queue": "#8A8A8A",
    "container": "#3C7FC0",
    "external_container": "#A6A6A6",
    "container_db": "#3C7FC0",
    "external_container_db": "#A6A6A6",
    "container_queue": "#3C7FC0",
    "external_container_queue": "#A6A6A6",
    "component": "#78A8D8",
    "external_component": "#BFBFBF",
    "component_db": "#78A8D8",
    "external_component_db": "#BFBFBF",
    "component_queue": "#78A8D8",
    "external_component_queue": "#BFBFBF",
}

_DEFAULT_BG_COLOR = "#1168BD"
_DEFAULT_BORDER_COLOR = "#3C7FC0"


def _bg_color_for(type_c4: str) -> str:
    """Return the fill hex for a C4 shape type (grok ``bg_color_for`` L23)."""
    return _BG_COLOR_FOR.get(type_c4, _DEFAULT_BG_COLOR)


def _border_color_for(type_c4: str) -> str:
    """Return the stroke hex for a C4 shape type (grok ``border_color_for`` L49)."""
    return _BORDER_COLOR_FOR.get(type_c4, _DEFAULT_BORDER_COLOR)


# === data model (grok L77-L123) ==============================================


@dataclass
class C4Shape:
    """A single C4 shape (person / system / container / component / external_*).

    ``parent_boundary`` tracks which boundary (by alias) owns this shape;
    ``"global"`` means the top-level scope. The ``x`` / ``y`` / ``width`` /
    ``height`` quadruple is populated by the layout pass.
    """

    alias: str
    type_c4: str
    label: str
    techn: str
    descr: str
    parent_boundary: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class C4Boundary:
    """A dashed boundary box wrapping child shapes."""

    alias: str
    label: str
    type_text: str
    parent_boundary: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class C4Rel:
    """A relationship arrow between two shapes (by alias)."""

    rel_type: str  # "rel" / "birel" -- both render identically
    from_: str
    to: str
    label: str
    techn: str


@dataclass
class C4Diagram:
    """The full parsed C4 AST: variant + title + shapes + boundaries + rels."""

    c4_type: str
    title: str
    shapes: list[C4Shape] = field(default_factory=list)
    boundaries: list[C4Boundary] = field(default_factory=list)
    rels: list[C4Rel] = field(default_factory=list)


# === Bounds (grok L127-L210 -- port of Mermaid's Bounds class) ===============


class _Bounds:
    """9-field grid-layout tracker (port of Mermaid's ``Bounds`` class).

    Tracks the running extent (``startx``/``stopx``/``starty``/``stopy``) of
    laid-out shapes plus the "next row" cursor (``next_*``) and an in-row
    counter (``next_cnt``). :meth:`insert` places one shape, wrapping to a new
    row when the running width exceeds ``width_limit`` or the per-row cap is
    hit (grok L168-L204).
    """

    __slots__ = (
        "startx",
        "stopx",
        "starty",
        "stopy",
        "width_limit",
        "next_startx",
        "next_stopx",
        "next_starty",
        "next_stopy",
        "next_cnt",
    )

    def __init__(self) -> None:
        self.startx: float = 0.0
        self.stopx: float = 0.0
        self.starty: float = 0.0
        self.stopy: float = 0.0
        self.width_limit: float = math.inf
        self.next_startx: float = 0.0
        self.next_stopx: float = 0.0
        self.next_starty: float = 0.0
        self.next_stopy: float = 0.0
        self.next_cnt: int = 0

    def set_data(self, startx: float, stopx: float, starty: float, stopy: float) -> None:
        """Seed the extent and the next-row cursor to the same rectangle."""
        self.startx = startx
        self.stopx = stopx
        self.starty = starty
        self.stopy = stopy
        self.next_startx = startx
        self.next_stopx = stopx
        self.next_starty = starty
        self.next_stopy = stopy

    def insert(self, shape: C4Shape) -> None:
        """Place ``shape`` on the grid, wrapping to a new row if needed.

        Faithful port of grok L168-L204: the next-shape x-offset doubles the
        margin when the row already has a shape; the wrap condition is
        ``sx >= width_limit`` OR ``ex >= width_limit`` OR the per-row cap is
        exceeded (``next_cnt > C4_SHAPE_IN_ROW``); on wrap the cursor drops to
        a fresh row and the counter resets to 1.
        """
        self.next_cnt += 1
        margin = _C4_SHAPE_MARGIN

        # First shape in a row takes a single margin; subsequent ones double it.
        if abs(self.next_startx - self.next_stopx) < 0.001:
            sx = self.next_stopx + margin
        else:
            sx = self.next_stopx + margin * 2.0
        ex = sx + shape.width
        sy = self.next_starty + margin * 2.0
        ey = sy + shape.height

        # Row wrap: width limit breached or per-row cap exceeded.
        if sx >= self.width_limit or ex >= self.width_limit or self.next_cnt > _C4_SHAPE_IN_ROW:
            sx = self.next_startx + margin
            sy = self.next_stopy + margin * 2.0
            self.next_stopx = sx + shape.width
            ex = self.next_stopx
            self.next_starty = self.next_stopy
            self.next_stopy = sy + shape.height
            ey = self.next_stopy
            self.next_cnt = 1

        shape.x = sx
        shape.y = sy

        # updateVal: min for start, max for stop -- both for the running extent
        # and the next-row cursor.
        self.startx = min(self.startx, sx)
        self.starty = min(self.starty, sy)
        self.stopx = max(self.stopx, ex)
        self.stopy = max(self.stopy, ey)
        self.next_startx = min(self.next_startx, sx)
        self.next_starty = min(self.next_starty, sy)
        self.next_stopx = max(self.next_stopx, ex)
        self.next_stopy = max(self.next_stopy, ey)

    def bump_last_margin(self) -> None:
        """Pad the running extent by one margin (grok L206-L209)."""
        self.stopx += _C4_SHAPE_MARGIN
        self.stopy += _C4_SHAPE_MARGIN


# === text measurement (grok L214-L221) =======================================


def _estimate_text_width(text: str, font_size: float) -> float:
    """Rough Open-Sans width heuristic: byte length * font_size * 0.6.

    Rust ``str::len()`` returns the UTF-8 byte count, so the Python port uses
    ``len(text.encode('utf-8'))`` to stay byte-accurate for any non-ASCII label.
    """
    return len(text.encode("utf-8")) * font_size * 0.6


def _estimate_text_height(font_size: float) -> float:
    """Line-height heuristic: font_size + 2.0 (grok L219-L221)."""
    return font_size + 2.0


# === layout computation (grok L225-L279) =====================================


def _compute_shape_dimensions(shape: C4Shape) -> None:
    """Size ``shape.width`` / ``shape.height`` from its text content.

    Walks the vertical stack (type label -> person image -> label -> techn ->
    description), accumulating the height; the width is the max text width
    plus padding. Both clamp to the defaults (grok L225-L271).
    """
    type_font_size = _FONT_SIZE - 2.0

    y = _C4_SHAPE_PADDING
    # <<type>> line.
    y += type_font_size + 2.0 - 4.0

    # Person image height.
    if shape.type_c4 == "person" or shape.type_c4 == "external_person":
        y += 48.0

    # Label.
    label_font_size = _FONT_SIZE + 2.0
    label_width = _estimate_text_width(shape.label, label_font_size)
    y += 8.0
    y += _estimate_text_height(label_font_size)

    rect_width = label_width

    # Techn / type text.
    if shape.techn:
        techn_display = f"[{shape.techn}]"
        techn_width = _estimate_text_width(techn_display, _FONT_SIZE)
        rect_width = max(rect_width, techn_width)
        y += 5.0
        y += _estimate_text_height(_FONT_SIZE)

    # Description.
    if shape.descr:
        descr_width = _estimate_text_width(shape.descr, _FONT_SIZE)
        rect_width = max(rect_width, descr_width)
        y += 20.0
        y += _estimate_text_height(_FONT_SIZE)

    rect_height = y
    rect_width += _C4_SHAPE_PADDING

    shape.width = max(shape.width, rect_width, _DEFAULT_WIDTH)
    shape.height = max(shape.height, rect_height, _DEFAULT_HEIGHT)


def _layout_shapes_in_bounds(bounds: _Bounds, shapes: list[C4Shape]) -> None:
    """Size then grid-place every shape; pad the extent at the end."""
    for shape in shapes:
        _compute_shape_dimensions(shape)
        bounds.insert(shape)
    bounds.bump_last_margin()


# === intersection geometry (grok L281-L367) ==================================


def _get_intersect_point(
    from_x: float,
    from_y: float,
    from_w: float,
    from_h: float,
    end_x: float,
    end_y: float,
) -> tuple[float, float]:
    """Border intersection of the ``from`` rect with the line toward ``end``.

    Faithful port of grok L281-L343: handles the degenerate same-point /
    axis-aligned cases first, then the four diagonal quadrants split by the
    ``from_dyx >= tan_dyx`` test (rect aspect vs. line slope).
    """
    cx = from_x + from_w / 2.0
    cy = from_y + from_h / 2.0
    dx = abs(from_x - end_x)
    dy = abs(from_y - end_y)

    if dx < 0.001 and dy < 0.001:
        return (cx, cy)

    from_dyx = from_h / from_w

    # Horizontally aligned -> exit left/right edge.
    if abs(from_y - end_y) < 0.001:
        if from_x < end_x:
            return (from_x + from_w, cy)
        return (from_x, cy)
    # Vertically aligned -> exit top/bottom edge.
    if abs(from_x - end_x) < 0.001:
        if from_y < end_y:
            return (cx, from_y + from_h)
        return (cx, from_y)

    tan_dyx = dy / dx

    if from_x > end_x and from_y < end_y:
        if from_dyx >= tan_dyx:
            return (from_x, cy + tan_dyx * from_w / 2.0)
        return (cx - dx / dy * from_h / 2.0, from_y + from_h)
    elif from_x < end_x and from_y < end_y:
        if from_dyx >= tan_dyx:
            return (from_x + from_w, cy + tan_dyx * from_w / 2.0)
        return (cx + dx / dy * from_h / 2.0, from_y + from_h)
    elif from_x < end_x and from_y > end_y:
        if from_dyx >= tan_dyx:
            return (from_x + from_w, cy - tan_dyx * from_w / 2.0)
        return (cx + from_h / 2.0 * dx / dy, from_y)
    else:  # from_x > end_x and from_y > end_y
        if from_dyx >= tan_dyx:
            return (from_x, cy - from_w / 2.0 * tan_dyx)
        return (cx - from_h / 2.0 * dx / dy, from_y)


def _get_intersect_points(from_: C4Shape, to: C4Shape) -> tuple[tuple[float, float], tuple[float, float]]:
    """Border intersection points on the ``from`` and ``to`` rects (grok L345)."""
    end_center = (to.x + to.width / 2.0, to.y + to.height / 2.0)
    start_point = _get_intersect_point(
        from_.x, from_.y, from_.width, from_.height, end_center[0], end_center[1]
    )

    from_center = (from_.x + from_.width / 2.0, from_.y + from_.height / 2.0)
    end_point = _get_intersect_point(
        to.x, to.y, to.width, to.height, from_center[0], from_center[1]
    )

    return (start_point, end_point)


# === parser (grok L369-L775) =================================================

_C4_HEADERS = ("C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment")

#: Function name -> (type_c4, has_techn). ``has_techn`` flags whether the
#: statement carries a technology field between label and description (grok
#: treats Container/Component families as 4-arg; Person/System as 3-arg).
_SHAPE_FUNCS: dict = {
    "Person": ("person", False),
    "Person_Ext": ("external_person", False),
    "System": ("system", False),
    "System_Ext": ("external_system", False),
    "SystemDb": ("system_db", False),
    "SystemDb_Ext": ("external_system_db", False),
    "SystemQueue": ("system_queue", False),
    "SystemQueue_Ext": ("external_system_queue", False),
    "Container": ("container", True),
    "Container_Ext": ("external_container", True),
    "ContainerDb": ("container_db", True),
    "ContainerDb_Ext": ("external_container_db", True),
    "ContainerQueue": ("container_queue", True),
    "ContainerQueue_Ext": ("external_container_queue", True),
    "Component": ("component", True),
    "Component_Ext": ("external_component", True),
    "ComponentDb": ("component_db", True),
    "ComponentDb_Ext": ("external_component_db", True),
    "ComponentQueue": ("component_queue", True),
    "ComponentQueue_Ext": ("external_component_queue", True),
}

_REL_FUNCS = frozenset(
    {"Rel", "Rel_U", "Rel_D", "Rel_L", "Rel_R", "Rel_Back", "Rel_Neighbor"}
)
_BIREL_FUNCS = frozenset(
    {"BiRel", "BiRel_U", "BiRel_D", "BiRel_L", "BiRel_R", "BiRel_Neighbor"}
)
_BOUNDARY_FUNCS = frozenset(
    {
        "Boundary",
        "Enterprise_Boundary",
        "System_Boundary",
        "Container_Boundary",
        "Deployment_Node",
        "Deployment_Node_L",
        "Deployment_Node_R",
    }
)
_IGNORED_STYLE_FUNCS = frozenset({"UpdateElementStyle", "UpdateRelStyle", "UpdateLayoutConfig"})


@dataclass
class _ParsedStatement:
    """One ``Func(arg, arg, ...)`` statement split into name + arg list."""

    func: str
    args: list[str] = field(default_factory=list)


def _find_matching_paren(s: str) -> int | None:
    """Index of the ``)`` that closes the first top-level ``(`` (grok L722).

    Tracks nested parens and skips over double-quoted spans. Returns ``None``
    when no matching close is found.
    """
    depth = 0
    in_quote = False
    for i, ch in enumerate(s):
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    return i
                depth -= 1
    return None


def _split_c4_args(s: str) -> list[str]:
    """Split a call's argument span on top-level commas (grok L741).

    Double quotes toggle a literal span (and are dropped from the output);
    nested parens are preserved verbatim; leading/trailing whitespace on each
    argument is trimmed.
    """
    args: list[str] = []
    current: list[str] = []
    in_quote = False
    depth = 0
    for ch in s:
        if ch == '"':
            in_quote = not in_quote  # quotes delimit literals; not emitted.
        elif not in_quote:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        else:
            current.append(ch)
    last = "".join(current).strip()
    if last:
        args.append(last)
    return args


def _parse_c4_statement(line: str) -> _ParsedStatement | None:
    """Parse one ``Func(args)`` statement; ``None`` if it has no call (grok L702)."""
    line = line.strip()

    paren_pos = line.find("(")
    if paren_pos == -1:
        return None
    func = line[:paren_pos].strip()
    if not func:
        return None

    rest = line[paren_pos + 1:]
    close_paren = _find_matching_paren(rest)
    if close_paren is None:
        return None
    args_str = rest[:close_paren]

    args = _split_c4_args(args_str)
    return _ParsedStatement(func=func, args=args)


def _arg(parsed: _ParsedStatement, idx: int) -> str:
    """Return the i-th argument, defaulting to empty string (grok ``unwrap_or_default``)."""
    if idx < len(parsed.args):
        return parsed.args[idx]
    return ""


def _make_shape(
    parsed: _ParsedStatement,
    type_c4: str,
    has_techn: bool,
    parent_boundary: str,
) -> C4Shape:
    """Build a :class:`C4Shape` from a parsed call (grok L444-L627 family).

    Person/System families are 3-arg (alias, label, descr); Container/Component
    families are 4-arg (alias, label, techn, descr).
    """
    if has_techn:
        label = _arg(parsed, 1)
        techn = _arg(parsed, 2)
        descr = _arg(parsed, 3)
    else:
        label = _arg(parsed, 1)
        techn = ""
        descr = _arg(parsed, 2)
    return C4Shape(
        alias=_arg(parsed, 0),
        type_c4=type_c4,
        label=label,
        techn=techn,
        descr=descr,
        parent_boundary=parent_boundary,
    )


def _parse_c4_diagram(input_source: str) -> C4Diagram:
    """Parse C4 mermaid source into a :class:`C4Diagram` (grok L371-L693).

    Line-oriented state machine: the first non-blank / non-``%%`` line must be
    a C4 variant header; ``title`` sets the title; ``}`` / ``end`` pop the
    boundary stack; function-call statements dispatch to shape / rel /
    boundary collectors. Unknown statements and styling updates are skipped.
    """
    c4_type = ""
    title = ""
    shapes: list[C4Shape] = []
    boundaries: list[C4Boundary] = []
    rels: list[C4Rel] = []
    boundary_stack: list[str] = ["global"]

    # The implicit top-level boundary owns shapes declared outside any block.
    boundaries.append(
        C4Boundary(
            alias="global",
            label="global",
            type_text="global",
            parent_boundary="",
        )
    )

    found_header = False

    for idx, raw_line in enumerate(input_source.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        if not found_header:
            token = line.split()[0] if line.split() else ""
            if token in _C4_HEADERS:
                c4_type = token
                found_header = True
                continue
            raise ParseError(
                line=idx + 1,
                message=f"Expected C4 diagram type, got '{token}'",
            )

        # Title.
        if line.startswith("title "):
            title = line[len("title "):].strip()
            continue
        if line == "title":
            continue

        # Boundary end.
        if line == "}" or line == "end":
            if len(boundary_stack) > 1:
                boundary_stack.pop()
            continue

        # Function-call statements.
        parsed = _parse_c4_statement(line)
        if parsed is None:
            continue

        current_boundary = boundary_stack[-1]

        if parsed.func in _SHAPE_FUNCS:
            type_c4, has_techn = _SHAPE_FUNCS[parsed.func]
            shapes.append(_make_shape(parsed, type_c4, has_techn, current_boundary))
        elif parsed.func in _REL_FUNCS:
            rels.append(
                C4Rel(
                    rel_type="rel",
                    from_=_arg(parsed, 0),
                    to=_arg(parsed, 1),
                    label=_arg(parsed, 2),
                    techn=_arg(parsed, 3),
                )
            )
        elif parsed.func in _BIREL_FUNCS:
            rels.append(
                C4Rel(
                    rel_type="birel",
                    from_=_arg(parsed, 0),
                    to=_arg(parsed, 1),
                    label=_arg(parsed, 2),
                    techn=_arg(parsed, 3),
                )
            )
        elif parsed.func in _BOUNDARY_FUNCS:
            alias = _arg(parsed, 0)
            boundaries.append(
                C4Boundary(
                    alias=alias,
                    label=_arg(parsed, 1),
                    type_text=_arg(parsed, 2),
                    parent_boundary=current_boundary,
                )
            )
            boundary_stack.append(alias)
        elif parsed.func in _IGNORED_STYLE_FUNCS:
            # Styling updates are ignored (grok L669-L671).
            pass
        # Unknown statements are skipped (grok L672-L674).

    if not found_header:
        raise ParseError(line=1, message="Expected C4 diagram type declaration")

    return C4Diagram(
        c4_type=c4_type,
        title=title,
        shapes=shapes,
        boundaries=boundaries,
        rels=rels,
    )


# === SVG rendering (grok L779-L1201) =========================================

_C4_FONT_CSS = '"trebuchet ms",verdana,arial,sans-serif'


def _escape_xml(s: str) -> str:
    """Escape the five XML-special chars (grok L779-L784).

    Uses the ``&quot;`` named entity for the double quote (distinct from the
    ``&apos;`` variant in er / the ``&#39;`` variant in class).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(value: float) -> str:
    """Format an f64 for SVG: drop the trailing ``.0`` on integers (grok ``{}``)."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def _render_icon_defs(svg: list[str]) -> None:
    """Emit the 3 static icon ``<symbol>`` defs (grok L950-L959)."""
    svg.append(
        '<defs><symbol id="computer" width="24" height="24">'
        '<path transform="scale(.5)" d="M2 2v13h20v-13h-20zm18 11h-16v-9h16v9zm-10.228 6'
        'l.466-1h3.524l.467 1h-4.457zm14.228 3h-24l2-6h2.104l-1.33 4h18.45l-1.297-4h2.073l2 6'
        'zm-5-10h-14v-7h14v7z"/></symbol></defs>'
    )
    svg.append(
        '<defs><symbol id="database" fill-rule="evenodd" clip-rule="evenodd">'
        '<path transform="scale(.5)" d="M12.258.001l.256.004.255.005.253.008.251.01.249.012'
        '.247.015.246.016.242.019.241.02.239.023.236.024.233.027.231.028.229.031.225.032.223.034'
        '.22.036.217.038.214.04.211.041.208.043.205.045.201.046.198.048.194.05.191.051.187.053'
        '.183.054.18.056.175.057.172.059.168.06.163.061.16.063.155.064.15.066.074.033.073.033'
        '.071.034.07.034.069.035.068.035.067.035.066.035.064.036.064.036.062.036.06.036.06.037'
        '.058.037.058.037.055.038.055.038.053.038.052.038.051.039.05.039.048.039.047.039.045.04'
        '.044.04.043.04.041.04.04.041.039.041.037.041.036.041.034.041.033.042.032.042.03.042'
        '.029.042.027.042.026.043.024.043.023.043.021.043.02.043.018.044.017.043.015.044.013.044'
        '.012.044.011.045.009.044.007.045.006.045.004.045.002.045.001.045v17l-.001.045-.002.045'
        '-.004.045-.006.045-.007.045-.009.044-.011.045-.012.044-.013.044-.015.044-.017.043-.018'
        '.044-.02.043-.021.043-.023.043-.024.043-.026.043-.027.042-.029.042-.03.042-.032.042'
        '-.033.042-.034.041-.036.041-.037.041-.039.041-.04.041-.041.04-.043.04-.044.04-.045.04'
        '-.047.039-.048.039-.05.039-.051.039-.052.038-.053.038-.055.038-.055.038-.058.037-.058'
        '.037-.06.037-.06.036-.062.036-.064.036-.064.036-.066.035-.067.035-.068.035-.069.035'
        '-.07.034-.071.034-.073.033-.074.033-.15.066-.155.064-.16.063-.163.061-.168.06-.172.059'
        '-.175.057-.18.056-.183.054-.187.053-.191.051-.194.05-.198.048-.201.046-.205.045-.208'
        '.043-.211.041-.214.04-.217.038-.22.036-.223.034-.225.032-.229.031-.231.028-.233.027'
        '-.236.024-.239.023-.241.02-.242.019-.246.016-.247.015-.249.012-.251.01-.253.008-.255'
        '.005-.256.004-.258.001-.258-.001-.256-.004-.255-.005-.253-.008-.251-.01-.249-.012-.247'
        '-.015-.245-.016-.243-.019-.241-.02-.238-.023-.236-.024-.234-.027-.231-.028-.228-.031'
        '-.226-.032-.223-.034-.22-.036-.217-.038-.214-.04-.211-.041-.208-.043-.204-.045-.201-.046'
        '-.198-.048-.195-.05-.19-.051-.187-.053-.184-.054-.179-.056-.176-.057-.172-.059-.167-.06'
        '-.164-.061-.159-.063-.155-.064-.151-.066-.074-.033-.072-.033-.072-.034-.07-.034-.069-.035'
        '-.068-.035-.067-.035-.066-.035-.064-.036-.063-.036-.062-.036-.061-.036-.06-.037-.058-.037'
        '-.057-.037-.056-.038-.055-.038-.053-.038-.052-.038-.051-.039-.049-.039-.049-.039-.046-.039'
        '-.046-.04-.044-.04-.043-.04-.041-.04-.04-.041-.039-.041-.037-.041-.036-.041-.034-.041'
        '-.033-.042-.032-.042-.03-.042-.029-.042-.027-.042-.026-.043-.024-.043-.023-.043-.021-.043'
        '-.02-.043-.018-.044-.017-.043-.015-.044-.013-.044-.012-.044-.011-.045-.009-.044-.007-.045'
        '-.006-.045-.004-.045-.002-.045-.001-.045v-17l.001-.045.002-.045.004-.045.006-.045.007-.045'
        '.009-.044.011-.045.012-.044.013-.044.015-.044.017-.043.018-.044.02-.043.021-.043.023-.043'
        '.024-.043.026-.043.027-.042.029-.042.03-.042.032-.042.033-.042.034-.041.036-.041.037-.041'
        '.039-.041.04-.041.041-.04.043-.04.044-.04.046-.04.046-.039.049-.039.049-.039.051-.039.052'
        '-.038.053-.038.055-.038.056-.038.057-.037.058-.037.06-.037.061-.036.062-.036.063-.036.064'
        '-.036.066-.035.067-.035.068-.035.069-.035.07-.034.072-.034.072-.033.074-.033.151-.066.155'
        '-.064.159-.063.164-.061.167-.06.172-.059.176-.057.179-.056.184-.054.187-.053.19-.051.195'
        '-.05.198-.048.201-.046.204-.045.208-.043.211-.041.214-.04.217-.038.22-.036.223-.034.226'
        '-.032.228-.031.231-.028.234-.027.236-.024.238-.023.241-.02.243-.019.245-.016.247-.015.249'
        '-.012.251-.01.253-.008.255-.005.256-.004.258-.001.258.001z"/></symbol></defs>'
    )
    svg.append(
        '<defs><symbol id="clock" width="24" height="24">'
        '<path transform="scale(.5)" d="M12 2c5.514 0 10 4.486 10 10s-4.486 10-10 10-10-4.486'
        '-10-10 4.486-10 10-10zm0-2c-6.627 0-12 5.373-12 12s5.373 12 12 12 12-5.373 12-12-5.373'
        '-12-12-12zm5.848 12.459c.202.038.202.333.001.372-1.907.361-6.045 1.111-6.547 1.111-.719'
        ' 0-1.301-.582-1.301-1.301 0-.512.77-5.447 1.125-7.445.034-.192.312-.181.343.014l.985'
        ' 6.238 5.394 1.011z"/></symbol></defs>'
    )


def _render_arrow_defs(svg: list[str]) -> None:
    """Emit the 4 static arrow ``<marker>`` defs (grok L961-L966)."""
    svg.append(
        '<defs><marker id="arrowhead" refX="9" refY="5" markerUnits="userSpaceOnUse" '
        'markerWidth="12" markerHeight="12" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'
    )
    svg.append(
        '<defs><marker id="arrowend" refX="1" refY="5" markerUnits="userSpaceOnUse" '
        'markerWidth="12" markerHeight="12" orient="auto">'
        '<path d="M 10 0 L 0 5 L 10 10 z"/></marker></defs>'
    )
    svg.append(
        '<defs><marker id="crosshead" markerWidth="15" markerHeight="8" orient="auto" '
        'refX="16" refY="4">'
        '<path fill="black" stroke="#000000" stroke-width="1px" d="M 9,2 V 6 L16,4 Z" '
        'style="stroke-dasharray: 0, 0;"/>'
        '<path fill="none" stroke="#000000" stroke-width="1px" '
        'd="M 0,1 L 6,7 M 6,1 L 0,7" style="stroke-dasharray: 0, 0;"/></marker></defs>'
    )
    svg.append(
        '<defs><marker id="filled-head" refX="18" refY="7" markerWidth="20" markerHeight="28" '
        'orient="auto"><path d="M 18,7 L9,13 L14,7 L9,1 Z"/></marker></defs>'
    )


def _render_boundary(svg: list[str], boundary: C4Boundary) -> None:
    """Emit one dashed boundary box with its bold label + optional type (grok L968)."""
    stroke_color = "#444444"
    font_color = "black"

    svg.append(
        f'<g><rect x="{_fmt(boundary.x)}" y="{_fmt(boundary.y)}" fill="none" '
        f'stroke="{stroke_color}" width="{_fmt(boundary.width)}" '
        f'height="{_fmt(boundary.height)}" rx="2.5" ry="2.5" stroke-width="1" '
        f'stroke-dasharray="7.0,7.0"/>'
    )

    # Boundary label (bold, +2 font size), centred above the box.
    label_y = boundary.y + _C4_SHAPE_MARGIN - 35.0
    svg.append(
        f'<text x="{_fmt(boundary.x + boundary.width / 2.0)}" y="{_fmt(label_y)}" '
        f'dominant-baseline="middle" fill="{font_color}" style="text-anchor: middle; '
        f'font-size: {_fmt(_FONT_SIZE + 2.0)}px; font-weight: bold; '
        f'font-family: {_FONT_FAMILY};">{_escape_xml(boundary.label)}</text>'
    )

    # Optional [type] subtitle below the label.
    if boundary.type_text:
        type_y = label_y + _FONT_SIZE + 5.0
        svg.append(
            f'<text x="{_fmt(boundary.x + boundary.width / 2.0)}" y="{_fmt(type_y)}" '
            f'dominant-baseline="middle" fill="{font_color}" style="text-anchor: middle; '
            f'font-size: {_fmt(_FONT_SIZE)}px; font-weight: normal; '
            f'font-family: {_FONT_FAMILY};">[{_escape_xml(boundary.type_text)}]</text>'
        )

    svg.append("</g>")


_DB_TYPES = frozenset(
    {
        "system_db",
        "external_system_db",
        "container_db",
        "external_container_db",
        "component_db",
        "external_component_db",
    }
)
_QUEUE_TYPES = frozenset(
    {
        "system_queue",
        "external_system_queue",
        "container_queue",
        "external_container_queue",
        "component_queue",
        "external_component_queue",
    }
)


def _render_c4_shape(svg: list[str], shape: C4Shape) -> None:
    """Emit one C4 shape: background geometry + type label + text stack (grok L1005)."""
    fill = _bg_color_for(shape.type_c4)
    stroke = _border_color_for(shape.type_c4)
    font_color = "#FFFFFF"

    svg.append('<g class="person-man">')

    # --- Shape background (3 geometries) -----------------------------------
    if shape.type_c4 in _DB_TYPES:
        # Database cylinder: two arcs cap the top/bottom of a rounded body.
        half = shape.width / 2.0
        svg.append(
            f'<path fill="{fill}" stroke-width="0.5" stroke="{stroke}" '
            f'd="M{_fmt(shape.x)},{_fmt(shape.y)}c0,-10 {_fmt(half)},-10 {_fmt(half)},-10'
            f'c0,0 {_fmt(half)},0 {_fmt(half)},10l0,{_fmt(shape.height)}'
            f'c0,10 -{_fmt(half)},10 -{_fmt(half)},10c0,0 -{_fmt(half)},0 -{_fmt(half)},-10'
            f'l0,-{_fmt(shape.height)}"/>'
        )
        svg.append(
            f'<path fill="none" stroke-width="0.5" stroke="{stroke}" '
            f'd="M{_fmt(shape.x)},{_fmt(shape.y)}c0,10 {_fmt(half)},10 {_fmt(half)},10'
            f'c0,0 {_fmt(half)},0 {_fmt(half)},-10"/>'
        )
    elif shape.type_c4 in _QUEUE_TYPES:
        # Queue: rounded right edge with two bulge arcs.
        half = shape.height / 2.0
        svg.append(
            f'<path fill="{fill}" stroke-width="0.5" stroke="{stroke}" '
            f'd="M{_fmt(shape.x)},{_fmt(shape.y)}l{_fmt(shape.width)},0'
            f'c5,0 5,{_fmt(half)} 5,{_fmt(half)}c0,0 0,{_fmt(half)} -5,{_fmt(half)}'
            f'l-{_fmt(shape.width)},0c-5,0 -5,-{_fmt(half)} -5,-{_fmt(half)}'
            f'c0,0 0,-{_fmt(half)} 5,-{_fmt(half)}"/>'
        )
        svg.append(
            f'<path fill="none" stroke-width="0.5" stroke="{stroke}" '
            f'd="M{_fmt(shape.x + shape.width)},{_fmt(shape.y)}'
            f'c-5,0 -5,{_fmt(half)} -5,{_fmt(half)}c0,{_fmt(half)} 5,{_fmt(half)} 5,{_fmt(half)}"/>'
        )
    else:
        # Normal rounded rectangle.
        svg.append(
            f'<rect x="{_fmt(shape.x)}" y="{_fmt(shape.y)}" fill="{fill}" '
            f'stroke="{stroke}" width="{_fmt(shape.width)}" height="{_fmt(shape.height)}" '
            f'rx="2.5" ry="2.5" stroke-width="0.5"/>'
        )

    # --- Type label (<<type>>, italic, centred) ----------------------------
    type_font_size = _FONT_SIZE - 2.0
    type_text = f"<<{shape.type_c4}>>"
    type_text_width = _estimate_text_width(type_text, type_font_size)
    type_y = shape.y + _C4_SHAPE_PADDING
    svg.append(
        f'<text fill="{font_color}" font-family="{_FONT_FAMILY}" '
        f'font-size="{_fmt(type_font_size)}" font-style="italic" lengthAdjust="spacing" '
        f'textLength="{_fmt(type_text_width)}" x="{_fmt(shape.x + shape.width / 2.0 - type_text_width / 2.0)}" '
        f'y="{_fmt(type_y)}">{_escape_xml(type_text)}</text>'
    )

    current_y = type_y + type_font_size + 2.0 - 4.0

    # --- Person image (48x48 PNG, centred) ---------------------------------
    if shape.type_c4 == "person" or shape.type_c4 == "external_person":
        img_src = _EXTERNAL_PERSON_IMG if shape.type_c4 == "external_person" else _PERSON_IMG
        img_x = shape.x + shape.width / 2.0 - 24.0
        svg.append(
            f'<image width="48" height="48" x="{_fmt(img_x)}" y="{_fmt(current_y)}" '
            f'xlink:href="{img_src}"/>'
        )
        current_y += 48.0

    # --- Label (bold, +2 font size) ----------------------------------------
    current_y += 8.0
    label_font_size = _FONT_SIZE + 2.0
    svg.append(
        f'<text x="{_fmt(shape.x + shape.width / 2.0)}" y="{_fmt(current_y)}" '
        f'dominant-baseline="middle" fill="{font_color}" style="text-anchor: middle; '
        f'font-size: {_fmt(label_font_size)}px; font-weight: bold; '
        f'font-family: {_FONT_FAMILY};"><tspan dy="0" alignment-baseline="mathematical">'
        f'{_escape_xml(shape.label)}</tspan></text>'
    )
    current_y += _estimate_text_height(label_font_size)

    # --- Techn ([techn], italic) -------------------------------------------
    if shape.techn:
        current_y += 5.0
        techn_display = f"[{shape.techn}]"
        svg.append(
            f'<text x="{_fmt(shape.x + shape.width / 2.0)}" y="{_fmt(current_y)}" '
            f'dominant-baseline="middle" fill="{font_color}" style="text-anchor: middle; '
            f'font-size: {_fmt(_FONT_SIZE)}px; font-weight: normal; font-style: italic; '
            f'font-family: {_FONT_FAMILY};"><tspan dy="0" alignment-baseline="mathematical">'
            f'{_escape_xml(techn_display)}</tspan></text>'
        )
        current_y += _estimate_text_height(_FONT_SIZE)

    # --- Description -------------------------------------------------------
    if shape.descr:
        current_y += 20.0
        svg.append(
            f'<text x="{_fmt(shape.x + shape.width / 2.0)}" y="{_fmt(current_y)}" '
            f'dominant-baseline="middle" fill="{font_color}" style="text-anchor: middle; '
            f'font-size: {_fmt(_FONT_SIZE)}px; font-weight: normal; '
            f'font-family: {_FONT_FAMILY};"><tspan dy="0" alignment-baseline="mathematical">'
            f'{_escape_xml(shape.descr)}</tspan></text>'
        )

    svg.append("</g>")


def _render_rels(svg: list[str], rels: list[C4Rel], shapes: list[C4Shape], c4_type: str) -> None:
    """Emit relationship arrows: line for the first, Bézier for the rest (grok L1134)."""
    if not rels:
        return

    # Alias -> shape lookup for border-intersection math.
    by_alias: OrderedDict[str, C4Shape] = OrderedDict()
    for shape in shapes:
        by_alias[shape.alias] = shape

    svg.append("<g>")
    text_color = "#444444"
    stroke_color = "#444444"

    for i, rel in enumerate(rels):
        from_shape = by_alias.get(rel.from_)
        to_shape = by_alias.get(rel.to)
        if from_shape is None or to_shape is None:
            continue

        start, end = _get_intersect_points(from_shape, to_shape)

        # C4Dynamic prefixes the 1-based index to the label.
        if c4_type == "C4Dynamic":
            label = f"{i + 1}: {rel.label}"
        else:
            label = rel.label

        # First rel is a straight line; subsequent rels curve via a quadratic Bézier.
        if i == 0:
            svg.append(
                f'<line x1="{_fmt(start[0])}" y1="{_fmt(start[1])}" '
                f'x2="{_fmt(end[0])}" y2="{_fmt(end[1])}" stroke-width="1" '
                f'stroke="{stroke_color}" marker-end="url(#arrowhead)" style="fill: none;"/>'
            )
        else:
            ctrl_x = start[0] + (end[0] - start[0]) / 2.0 - (end[0] - start[0]) / 4.0
            ctrl_y = start[1] + (end[1] - start[1]) / 2.0
            svg.append(
                f'<path fill="none" stroke-width="1" stroke="{stroke_color}" '
                f'd="M{_fmt(start[0])},{_fmt(start[1])} Q{_fmt(ctrl_x)},{_fmt(ctrl_y)} '
                f'{_fmt(end[0])},{_fmt(end[1])}" marker-end="url(#arrowhead)"/>'
            )

        # Centred label at the midpoint.
        mid_x = min(start[0], end[0]) + abs(end[0] - start[0]) / 2.0
        mid_y = min(start[1], end[1]) + abs(end[1] - start[1]) / 2.0
        svg.append(
            f'<text x="{_fmt(mid_x)}" y="{_fmt(mid_y)}" dominant-baseline="middle" '
            f'fill="{text_color}" style="text-anchor: middle; '
            f'font-size: {_fmt(_MESSAGE_FONT_SIZE)}px; font-weight: normal; '
            f'font-family: {_FONT_FAMILY};"><tspan dy="0" alignment-baseline="mathematical">'
            f'{_escape_xml(label)}</tspan></text>'
        )

        # Optional [techn] subtitle below the label.
        if rel.techn:
            techn_display = f"[{rel.techn}]"
            techn_y = mid_y + _MESSAGE_FONT_SIZE + 5.0
            svg.append(
                f'<text x="{_fmt(mid_x)}" y="{_fmt(techn_y)}" dominant-baseline="middle" '
                f'fill="{text_color}" style="text-anchor: middle; '
                f'font-size: {_fmt(_MESSAGE_FONT_SIZE)}px; font-weight: normal; font-style: italic; '
                f'font-family: {_FONT_FAMILY};"><tspan dy="0" alignment-baseline="mathematical">'
                f'{_escape_xml(techn_display)}</tspan></text>'
            )

    svg.append("</g>")


def render_c4_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render any of the 5 C4 variants to an SVG string (grok L786-L948).

    The five diagram-type tokens (``C4Context`` / ``C4Container`` /
    ``C4Component`` / ``C4Dynamic`` / ``C4Deployment``) share one renderer;
    only ``C4Dynamic`` diverges in output (its relationship labels carry the
    1-based index prefix).
    """
    diagram = _parse_c4_diagram(mermaid_source)

    # --- Layout ---------------------------------------------------------
    screen_bounds = _Bounds()
    screen_bounds.set_data(
        _DIAGRAM_MARGIN_X,
        _DIAGRAM_MARGIN_X,
        _DIAGRAM_MARGIN_Y,
        _DIAGRAM_MARGIN_Y,
    )
    # Puppeteer default viewport width (matches mermaid-cli).
    screen_bounds.width_limit = 800.0

    # Split shapes by owning boundary.
    global_indices: list[int] = []
    boundary_shapes: OrderedDict[str, list[int]] = OrderedDict()
    for i, shape in enumerate(diagram.shapes):
        if shape.parent_boundary == "global":
            global_indices.append(i)
        else:
            boundary_shapes.setdefault(shape.parent_boundary, []).append(i)

    # Layout global (top-level) shapes first.
    if global_indices:
        to_layout = [diagram.shapes[i] for i in global_indices]
        _layout_shapes_in_bounds(screen_bounds, to_layout)
        for j, orig_idx in enumerate(global_indices):
            diagram.shapes[orig_idx] = to_layout[j]

    # Layout each non-global boundary's children, then size the boundary box.
    for boundary in diagram.boundaries:
        if boundary.alias == "global":
            continue
        indices = boundary_shapes.get(boundary.alias)
        if indices is None:
            continue

        inner_bounds = _Bounds()
        parent_y = screen_bounds.stopy
        inner_bounds.set_data(
            screen_bounds.startx + _DIAGRAM_MARGIN_X,
            screen_bounds.startx + _DIAGRAM_MARGIN_X,
            parent_y + _DIAGRAM_MARGIN_Y + 30.0,  # leave room for boundary header
            parent_y + _DIAGRAM_MARGIN_Y + 30.0,
        )
        inner_bounds.width_limit = screen_bounds.width_limit

        to_layout = [diagram.shapes[i] for i in indices]
        _layout_shapes_in_bounds(inner_bounds, to_layout)
        for j, orig_idx in enumerate(indices):
            diagram.shapes[orig_idx] = to_layout[j]

        boundary.x = inner_bounds.startx
        boundary.y = inner_bounds.starty - 30.0
        boundary.width = inner_bounds.stopx - inner_bounds.startx
        boundary.height = inner_bounds.stopy - inner_bounds.starty + 30.0

        screen_bounds.stopy = max(screen_bounds.stopy, inner_bounds.stopy + _C4_SHAPE_MARGIN)
        screen_bounds.stopx = max(screen_bounds.stopx, inner_bounds.stopx + _C4_SHAPE_MARGIN)

    # --- Canvas dimensions ---------------------------------------------
    global_max_x = screen_bounds.stopx
    global_max_y = screen_bounds.stopy
    for shape in diagram.shapes:
        global_max_x = max(global_max_x, shape.x + shape.width + _C4_SHAPE_MARGIN)
        global_max_y = max(global_max_y, shape.y + shape.height + _C4_SHAPE_MARGIN)

    box_width = global_max_x - screen_bounds.startx
    box_height = global_max_y - screen_bounds.starty
    svg_width = box_width + 2.0 * _DIAGRAM_MARGIN_X
    svg_height = box_height + 2.0 * _DIAGRAM_MARGIN_Y

    extra_vert_for_title = 60.0 if diagram.title else 0.0

    vb_x = screen_bounds.startx - _DIAGRAM_MARGIN_X
    vb_y = -(_DIAGRAM_MARGIN_Y + extra_vert_for_title)
    vb_w = svg_width
    vb_h = svg_height + extra_vert_for_title

    # Theme channels (3 of 5): normalise the mermaid-default sentinels.
    background_color = "white" if theme.background == "#ffffff" else theme.background
    text_color = "#333" if theme.text_color == "#333333" else theme.text_color
    edge = theme.edge_color

    # --- Emit SVG ------------------------------------------------------
    svg: list[str] = []

    max_w = math.ceil(svg_width)
    svg.append(
        f'<svg id="my-svg" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" style="max-width: {max_w}px; '
        f'background-color: {background_color};" viewBox="{_fmt(vb_x)} {_fmt(vb_y)} '
        f'{_fmt(vb_w)} {_fmt(vb_h)}" role="graphics-document document" '
        f'aria-roledescription="c4">'
    )

    # Style block (grok L906-L909): font + edge animations + marker fill.
    svg.append(
        f'<style>#my-svg{{font-family:{_C4_FONT_CSS};font-size:16px;fill:{text_color};}}'
        "@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}"
        "@keyframes dash{to{stroke-dashoffset:0;}}"
        "#my-svg .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;"
        "animation:dash 50s linear infinite;stroke-linecap:round;}"
        "#my-svg .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;"
        "animation:dash 20s linear infinite;stroke-linecap:round;}"
        "#my-svg .error-icon{fill:#552222;}"
        "#my-svg .error-text{fill:#552222;stroke:#552222;}"
        "#my-svg .edge-thickness-normal{stroke-width:1px;}"
        "#my-svg .edge-thickness-thick{stroke-width:3.5px;}"
        "#my-svg .edge-pattern-solid{stroke-dasharray:0;}"
        "#my-svg .edge-thickness-invisible{stroke-width:0;fill:none;}"
        "#my-svg .edge-pattern-dashed{stroke-dasharray:3;}"
        "#my-svg .edge-pattern-dotted{stroke-dasharray:2;}"
        f"#my-svg .marker{{fill:{edge};stroke:{edge};}}"
        f"#my-svg .marker.cross{{stroke:{edge};}}"
        f"#my-svg svg{{font-family:{_C4_FONT_CSS};font-size:16px;}}"
        "#my-svg p{margin:0;}"
        "#my-svg .person{stroke:hsl(240, 60%, 86.2745098039%);fill:#ECECFF;}"
        f'#my-svg :root{{--mermaid-font-family:{_C4_FONT_CSS};}}</style>'
    )

    svg.append("<g/>")

    _render_icon_defs(svg)

    # Boundaries (skip the implicit global + empty boxes).
    for boundary in diagram.boundaries:
        if boundary.alias == "global" or boundary.width < 1.0:
            continue
        _render_boundary(svg, boundary)

    # Shapes.
    for shape in diagram.shapes:
        _render_c4_shape(svg, shape)

    _render_arrow_defs(svg)

    _render_rels(svg, diagram.rels, diagram.shapes, diagram.c4_type)

    # Title (centred at the top of the canvas).
    if diagram.title:
        title_x = (global_max_x - screen_bounds.startx) / 2.0 - 4.0 * _DIAGRAM_MARGIN_X
        title_y = screen_bounds.starty + _DIAGRAM_MARGIN_Y
        svg.append(
            f'<text x="{_fmt(title_x)}" y="{_fmt(title_y)}">'
            f"{_escape_xml(diagram.title)}</text>"
        )

    svg.append("</svg>")

    return "".join(svg)
