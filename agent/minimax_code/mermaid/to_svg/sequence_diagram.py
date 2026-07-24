"""Migrated sequence diagram renderer (direction (1), brick 29 -- R298).

Exposes :func:`render_sequence_diagram_to_svg` -- the public entry that drives
the ``sequenceDiagram`` token. Mirrors grok
``mermaid-to-svg/src/sequence_diagram.rs`` (1326 lines) -- a port of Mermaid's
``sequenceRenderer.js`` / ``sequenceDb.js`` (the UML sequence / interaction
diagram).

This is the **19th self-contained SVG emitter** in the bespoke-geometry
family (after R279 info, R280 stateDiagram, R281 radar, R282 pie, R283 packet,
R284 sankey, R285 gantt, R286 kanban, R287 timeline, R288 quadrant, R289
block, R290 journey, R291 gitGraph, R292 mindmap, R293 xychart, R294
requirement, R295 er, R296 class, R297 C4). It does **not** consume the dagre
layout engine -- sequence diagrams use bespoke temporal geometry (fixed
participant columns + a 37px event-row grid + an activation-bar stack + a
fragment nesting-depth inset), so it shares nothing with the dagre consumers
(R294 requirement / R295 er / R296 class). Migrating this leaf empties
:data:`render._UNSUPPORTED_DIAGRAM_TYPES` (1 -> 0 tokens) -- the last
per-diagram renderer ships, closing the direction-(1) leaf migration.

Functional contract (zero-semantic clone of grok):

1. **Bespoke temporal layout** -- participants are placed on fixed x columns
   whose pair-spacings grow with the widest message text spanning them
   (grok ``compute_sequence_pair_spacings`` L1172); events stack on a 37px
   row grid (``SEQUENCE_EVENT_ROW_HEIGHT``); the canvas width is the max of
   the participant span and the per-message / per-note requirements
   (``required_sequence_width`` L1198). No dagre, no rank solver.
2. **Header / lifeline / footer** -- each participant emits a header rect at
   the top, a footer rect at the bottom, and a 0.5-stroke lifeline line
   joining them (grok L112-L147). The header height grows to 44px when any
   participant label carries a ``<br/>`` multi-line wrap (else 32px).
3. **10 arrow operators** -- ``parse_message_line`` (L736) recognises the 10
   mermaid sequence arrows in fixity order (``<<-->>`` / ``<<->>`` /
   ``-->>`` / ``->>`` / ``--x`` / ``-x`` / ``--)`` / ``-)`` / ``-->`` /
   ``->``), split into dashed/solid x filled/cross/open/none x
   bidirectional/unidirectional. A ``+`` prefix on the target activates it,
   ``-`` deactivates the source (grok L764-L770).
4. **Self-message loop** -- when ``from == to`` the message renders as a
   cubic-Bezier self-loop (26px wide, 42% of row height) instead of a line
   (grok L210-L227).
5. **Activation bars** -- ``activate``/``deactivate`` events drive a stack
   matcher (``compute_activation_layouts`` L1033) that pairs the most recent
   open activate per participant, nesting by depth (4px x-offset per level);
   unclosed bars extend to the content bottom.
6. **Fragments** -- ``alt`` / ``loop`` / ``opt`` / ``par`` / ``critical`` /
   ``break`` / ``rect`` open a dashed-border ``#d7c8f8`` rectangle inset by
   depth; ``else`` / ``and`` / ``option`` insert dashed separator lines;
   ``end`` closes. Each fragment carries a tab (``rect`` has none) with the
   keyword + optional ``[label]`` (grok ``render_fragment`` L1080).
7. **Notes** -- ``note over A,B``, ``note left of A``, ``note right of A``
   emit a ``#fff2b0`` sticky-note rectangle with centred text (grok
   ``render_note`` L1148); ``over`` widens to span the participant range,
   beside-notes cap at 380px width.
8. **Participants** -- ``participant`` / ``actor`` / ``create`` declarations
   parse ``A as B`` aliases (``choose_participant_id_and_label`` L863 picks
   the whitespace-bearing side as the label); message/note references
   auto-create missing participants. ``autonumber`` prefixes message text
   with ``N.`` / ``N. `` via the :class:`_AutoNumber` state machine.
9. **Static defs** -- 4 ``<marker>`` arrowheads emitted unconditionally
   (``seq_arrow`` / ``seq_arrow_rev`` / ``seq_cross`` / ``seq_open``, grok
   L102-L110).
10. **Theme channels** -- 4 of the 5 channels flow in: ``background``
    (full-canvas rect), ``node_fill`` / ``node_stroke`` (participant header
    / footer / activation bars / fragment tab), ``edge_color`` (lifelines /
    message strokes / arrowhead fills / note borders), ``text_color``
    (labels / message text). Note interiors stay hard-coded
    (``#fff2b0`` fill / ``#333333`` text) as in grok.

Pythonic conversions (mechanical, no behaviour change):

* :class:`_ArrowHead` uses an :class:`~enum.Enum` (Rust ``#[derive(Clone,
  Copy, PartialEq, Eq)]``); the ``None`` variant becomes ``NONE`` (``None``
  is a Python keyword).
* ``SequenceEvent`` becomes a ``Union`` of light ``@dataclass`` variants
  (``_Message`` / ``_Note`` / ``_Activate`` / ``_Deactivate`` /
  ``_FragmentStart`` / ``_FragmentElse`` / ``_FragmentEnd``); the ``from``
  field is spelled ``from_`` (``from`` is a Python keyword).
* ``BTreeMap<&str, f64>`` -> plain :class:`dict` -- grok only ever does point
  lookups (``x_for.get(id)``), never an ordered traversal that feeds output,
  so the sorted-key semantics are unobservable.
* ``eq_ignore_ascii_case`` -> :py:meth:`str.lower` (ASCII keywords only);
  ``trim_start`` -> :py:meth:`str.lstrip` (the preceding char is already a
  space/tab).
* ``saturating_add`` / ``saturating_sub`` -> plain ``+`` / ``-`` (u64 cannot
  overflow under Python's arbitrary-precision int for any realistic
  autonumber count).
* ``f64::total_cmp`` sort keys -> plain :class:`float` comparison (sequence
  coordinates are never NaN).
* Rust ``{x:.3}`` (fixed 3-decimal precision) -> Python ``{x:.3f}``
  (Rust ``{:.3}`` is *not* Python ``{:.3}`` which means 3 significant
  digits).
* ``decode_sequence_text`` maps mermaid's ``#59;`` entity to ``;`` before
  escape_xml (grok L1316-L1318); ``escape_xml`` emits the ``&apos;`` named
  entity for ``'`` (shared with er / gitgraph / journey).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.text_wrap import display_width_units
from minimax_code.mermaid.to_svg.theme import MermaidTheme

__all__ = ["render_sequence_diagram_to_svg"]

# === constants (grok L6-L15) =================================================

#: Vertical height of a single message / note event row (grok
#: ``SEQUENCE_EVENT_ROW_HEIGHT``).
_SEQ_EVENT_ROW_H: float = 37.0
#: Height of a fragment's header band (where the tab + label sit) (grok
#: ``SEQUENCE_FRAGMENT_HEADER_HEIGHT``).
_SEQ_FRAG_HEADER_H: float = 28.0
#: Height added when a fragment closes (grok ``SEQUENCE_FRAGMENT_FOOTER_HEIGHT``).
_SEQ_FRAG_FOOTER_H: float = 12.0
#: Per-depth horizontal inset of nested fragment borders (grok
#: ``SEQUENCE_FRAGMENT_INSET_X``).
_SEQ_FRAG_INSET_X: float = 18.0
#: Horizontal margin between the outermost fragment border and the participant
#: span (grok ``SEQUENCE_FRAGMENT_MARGIN_X``).
_SEQ_FRAG_MARGIN_X: float = 20.0
#: Inner padding of a fragment tab label (grok
#: ``SEQUENCE_FRAGMENT_TAB_PADDING_X``).
_SEQ_FRAG_TAB_PAD_X: float = 10.0
#: Stroke colour of fragment borders / separators (grok
#: ``SEQUENCE_FRAGMENT_STROKE``).
_SEQ_FRAG_STROKE: str = "#d7c8f8"
#: Text colour of fragment tab labels (grok ``SEQUENCE_FRAGMENT_TEXT``).
_SEQ_FRAG_TEXT: str = "#4c3f6f"
#: Width of an activation bar (grok ``SEQUENCE_ACTIVATION_W``).
_SEQ_ACTIVATION_W: float = 8.0
#: Per-depth x-offset of nested activation bars (grok
#: ``SEQUENCE_ACTIVATION_NEST_OFFSET_X``).
_SEQ_ACTIVATION_NEST_OFFSET_X: float = 4.0


# === data types (grok L282-L463) =============================================


class _ArrowHead(Enum):
    """The 4 arrowhead styles a sequence message can carry (grok L347-L353)."""

    FILLED = "filled"
    CROSS = "cross"
    OPEN = "open"
    NONE = "none"


class _FragmentKind(Enum):
    """The 7 fragment-opening keywords (grok L304-L313)."""

    ALT = "alt"
    LOOP = "loop"
    OPT = "opt"
    PAR = "par"
    CRITICAL = "critical"
    BREAK = "break"
    RECT = "rect"

    def tab_label(self) -> str | None:
        """Return the keyword rendered in the fragment tab (``rect`` -> None)."""
        if self is _FragmentKind.RECT:
            return None
        return self.value


@dataclass
class _NoteSide:
    """Tag for whether a beside-note sits left or right of its participant.

    A trivial dataclass mirrors grok's unit struct pair (``Left`` / ``Right``)
    so ``is`` / ``==`` comparisons stay identity-based.
    """

    left: bool


_LEFT = _NoteSide(left=True)
_RIGHT = _NoteSide(left=False)


@dataclass
class _NotePlacement:
    """Where a note attaches (grok ``SequenceNotePlacement`` L335-L345).

    ``over`` notes span a participant pair (from == to for a single
    participant); ``beside`` notes sit to the left/right of one participant.
    """

    over: bool
    participant_a: str
    participant_b: str
    side: _NoteSide


@dataclass
class _Participant:
    """A declared or auto-created participant (grok L282-L287)."""

    id: str
    label: str
    aliases: list[str] = field(default_factory=list)

    def matches(self, reference: str) -> bool:
        """Whether ``reference`` names this participant (grok L297-L302)."""
        return (
            self.id == reference
            or self.label == reference
            or any(alias == reference for alias in self.aliases)
        )


@dataclass
class _Message:
    """A ``from -> to`` message event (grok ``SequenceEvent::Message``)."""

    from_: str
    to: str
    text: str
    dashed: bool
    head: _ArrowHead
    bidirectional: bool


@dataclass
class _Note:
    """A note event (grok ``SequenceEvent::Note``)."""

    placement: _NotePlacement
    text: str


@dataclass
class _Activate:
    """An ``activate P`` event (grok ``SequenceEvent::Activate``)."""

    participant: str


@dataclass
class _Deactivate:
    """A ``deactivate P`` event (grok ``SequenceEvent::Deactivate``)."""

    participant: str


@dataclass
class _FragmentStart:
    """An ``alt``/``loop``/.../``rect`` opener (grok L375-L378)."""

    kind: _FragmentKind
    label: str


@dataclass
class _FragmentElse:
    """An ``else``/``and``/``option`` separator (grok L379-L381)."""

    label: str


@dataclass
class _FragmentEnd:
    """An ``end`` closer (grok ``SequenceEvent::FragmentEnd``)."""

    # Singleton-style sentinel: grok's ``FragmentEnd`` carries no fields; a
    # shared instance keeps ``isinstance`` dispatch cheap.
    pass


# Tagged union over the 7 sequence event variants (grok ``SequenceEvent``).
_SequenceEvent = (
    _Message | _Note | _Activate | _Deactivate | _FragmentStart | _FragmentElse | _FragmentEnd
)

_FRAGMENT_END = _FragmentEnd()


@dataclass
class _ElseMarker:
    """A recorded ``else`` separator inside a fragment (grok L395-L399)."""

    separator_y: float
    label: str


@dataclass
class _FragmentLayout:
    """A fully laid-out fragment box (grok L385-L393)."""

    kind: _FragmentKind
    label: str
    depth: int
    start_y: float
    end_y: float
    else_markers: list[_ElseMarker] = field(default_factory=list)


@dataclass
class _OpenFragment:
    """A fragment that has opened but not yet closed (grok L401-L407)."""

    kind: _FragmentKind
    label: str
    depth: int
    start_y: float
    else_markers: list[_ElseMarker] = field(default_factory=list)


@dataclass
class _ActivationLayout:
    """A fully laid-out activation bar (grok L409-L415)."""

    participant: str
    depth: int
    start_y: float
    end_y: float


@dataclass
class _Diagram:
    """The parsed sequence AST (grok ``SequenceDiagram`` L289-L294)."""

    participants: list[_Participant]
    events: list[_SequenceEvent]
    title: str | None = None


@dataclass
class _ParsedMessage:
    """The dissected fields of a message line (grok ``ParsedMessage`` L725-L734)."""

    from_: str
    to: str
    text: str
    dashed: bool
    head: _ArrowHead
    bidirectional: bool
    activate_target: bool
    deactivate_source: bool


class _AutoNumber:
    """State machine for the ``autonumber`` directive (grok L417-L463).

    ``apply`` parses the ``autonumber`` argument list (``off`` / ``<start>``
    / ``<start> <step>``); ``number`` prefixes a message label with the
    1-based counter when active.
    """

    def __init__(self) -> None:
        self.active: bool = False
        self.next: int = 1
        self.step: int = 1

    def apply(self, rest: str) -> None:
        """Parse an ``autonumber`` argument tail (grok L434-L449)."""
        tokens = rest.split()
        if not tokens:
            self.active = True
            return
        head = tokens[0]
        if head.lower() == "off":  # eq_ignore_ascii_case
            self.active = False
            return
        self.active = True
        if head.isdigit():
            self.next = int(head)
        if len(tokens) >= 2 and tokens[1].isdigit():
            self.step = int(tokens[1])

    def number(self, text: str) -> str:
        """Prefix ``text`` with the next counter value when active (grok L451-L462)."""
        if not self.active:
            return text
        current = self.next
        self.next += self.step  # u64 saturating_add -- no overflow under Python int
        if not text:
            return f"{current}."
        return f"{current}. {text}"


# === public entry (grok L17-L280) ============================================


def render_sequence_diagram_to_svg(mermaid_source: str, theme: MermaidTheme) -> str:
    """Render a ``sequenceDiagram`` source into an SVG string.

    Mirrors grok ``render_sequence_diagram_to_svg`` (L17-L280): parse the
    source into a :class:`_Diagram`, compute the participant x columns and
    event y grid, then emit the SVG (background -> title -> arrowhead defs
    -> participant headers / lifelines / footers -> activation bars ->
    fragment boxes -> message / note events).
    """
    diagram = _parse_sequence_diagram(mermaid_source)

    title_h = 26.0 if diagram.title is not None else 0.0
    header_y = 16.0 + title_h
    box_margin = 10.0
    edge_pad = 10.0

    box_w = max(
        [_estimate_label_box_width(p.label) for p in diagram.participants] + [100.0]
    )
    left_margin = box_w / 2.0 + edge_pad

    pair_spacings = _compute_sequence_pair_spacings(diagram, box_w)
    participant_xs: list[float] = []
    next_x = left_margin
    participant_xs.append(next_x)
    for spacing in pair_spacings:
        next_x += spacing
        participant_xs.append(next_x)
    width = max(
        participant_xs[-1] if participant_xs else left_margin,
        left_margin + box_w / 2.0 + edge_pad,
    )
    width = max(width, 360.0)
    width = max(width, _required_sequence_width(diagram, participant_xs, box_w, edge_pad))
    if diagram.title is not None:
        width = max(width, _estimate_sequence_text_width(diagram.title) * 1.25 + edge_pad * 2.0)

    max_label_lines = max(
        (p.label.count("<br/>") + 1 for p in diagram.participants), default=1
    )
    header_h = 44.0 if max_label_lines > 1 else 32.0

    events_top = header_y + header_h + 28.0
    event_y_positions, fragment_layouts, activation_layouts, content_bottom = (
        _layout_sequence_events(diagram.events, events_top)
    )
    footer_h = header_h
    footer_y = content_bottom + box_margin
    height = max(footer_y + footer_h + header_y, 220.0)

    x_for: dict[str, float] = {}
    for participant, x in zip(diagram.participants, participant_xs, strict=False):
        x_for[participant.id] = x

    svg: list[str] = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.3f} {height:.3f}">'
    )
    svg.append(
        f'<rect x="0" y="0" width="{width:.3f}" height="{height:.3f}" fill="{theme.background}"/>'
    )

    if diagram.title is not None:
        tx = width / 2.0
        svg.append(
            f'<text x="{tx:.3f}" y="20" text-anchor="middle" '
            f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="13" '
            f'font-weight="bold" fill="{theme.text_color}">{_escape_xml(diagram.title)}</text>'
        )

    ec = theme.edge_color
    svg.append(
        "<defs>"
        f'<marker id="seq_arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" '
        f'orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="{ec}"/></marker>'
        f'<marker id="seq_arrow_rev" markerWidth="8" markerHeight="8" refX="1" refY="4" '
        f'orient="auto"><path d="M8,0 L0,4 L8,8 Z" fill="{ec}"/></marker>'
        f'<marker id="seq_cross" markerWidth="11" markerHeight="11" refX="5" refY="5" '
        f'orient="auto"><path d="M1,1 L9,9 M9,1 L1,9" stroke="{ec}" '
        f'stroke-width="1.5" fill="none"/></marker>'
        f'<marker id="seq_open" markerWidth="10" markerHeight="10" refX="7" refY="4" '
        f'orient="auto"><path d="M0,0 L8,4 L0,8" stroke="{ec}" '
        f'stroke-width="1.2" fill="none"/></marker>'
        "</defs>"
    )

    for participant in diagram.participants:
        x = x_for.get(participant.id, left_margin)
        box_x = x - box_w / 2.0

        svg.append(
            f'<rect x="{box_x:.3f}" y="{header_y:.3f}" width="{box_w:.3f}" '
            f'height="{header_h:.3f}" rx="3" ry="3" fill="{theme.node_fill}" '
            f'stroke="{theme.node_stroke}" stroke-width="1"/>'
        )
        svg.append(
            _render_participant_label(
                x, header_y + header_h / 2.0, participant.label, theme.text_color
            )
        )

        y0 = header_y + header_h
        svg.append(
            f'<line x1="{x:.3f}" y1="{y0:.3f}" x2="{x:.3f}" y2="{footer_y:.3f}" '
            f'stroke="{theme.edge_color}" stroke-width="0.5"/>'
        )

        svg.append(
            f'<rect x="{box_x:.3f}" y="{footer_y:.3f}" width="{box_w:.3f}" '
            f'height="{footer_h:.3f}" rx="3" ry="3" fill="{theme.node_fill}" '
            f'stroke="{theme.node_stroke}" stroke-width="1"/>'
        )
        svg.append(
            _render_participant_label(
                x, footer_y + footer_h / 2.0, participant.label, theme.text_color
            )
        )

    for bar in activation_layouts:
        x = x_for.get(bar.participant)
        if x is None:
            continue
        bx = x - _SEQ_ACTIVATION_W / 2.0 + bar.depth * _SEQ_ACTIVATION_NEST_OFFSET_X
        h = bar.end_y - bar.start_y
        svg.append(
            f'<rect x="{bx:.3f}" y="{bar.start_y:.3f}" width="{_SEQ_ACTIVATION_W:.3f}" '
            f'height="{h:.3f}" fill="{theme.node_fill}" stroke="{theme.node_stroke}" '
            f'stroke-width="0.8"/>'
        )

    span = _participant_span(diagram, x_for)
    if span is not None:
        min_participant_x, max_participant_x = span
        for fragment in fragment_layouts:
            _render_fragment(
                svg, fragment, min_participant_x, max_participant_x, theme
            )

    for idx, ev in enumerate(diagram.events):
        y_opt = event_y_positions[idx]
        if y_opt is None:
            continue
        y = y_opt

        if isinstance(ev, _Message):
            x1 = x_for.get(ev.from_, left_margin)
            x2 = x_for.get(ev.to, left_margin)

            dash = ' stroke-dasharray="5,4"' if ev.dashed else ""
            if ev.head is _ArrowHead.FILLED:
                marker_end = ' marker-end="url(#seq_arrow)"'
            elif ev.head is _ArrowHead.CROSS:
                marker_end = ' marker-end="url(#seq_cross)"'
            elif ev.head is _ArrowHead.OPEN:
                marker_end = ' marker-end="url(#seq_open)"'
            else:  # _ArrowHead.NONE
                marker_end = ""
            marker_start = ' marker-start="url(#seq_arrow_rev)"' if ev.bidirectional else ""

            if abs(x1 - x2) < 1.0:
                loop_w = 26.0
                loop_h = _SEQ_EVENT_ROW_H * 0.42
                xr = x1 + loop_w
                ye = y + loop_h
                svg.append(
                    f'<path d="M {x1:.3f},{y:.3f} C {xr:.3f},{y:.3f} '
                    f'{xr:.3f},{ye:.3f} {x1:.3f},{ye:.3f}" stroke="{theme.edge_color}" '
                    f'stroke-width="1.5" fill="none"{dash}{marker_end}/>'
                )
                if ev.text:
                    tx = x1 + 4.0
                    ty = y - 6.0
                    svg.append(
                        f'<text x="{tx:.3f}" y="{ty:.3f}" text-anchor="start" '
                        f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
                        f'fill="{theme.text_color}">{_escape_xml(ev.text)}</text>'
                    )
            else:
                svg.append(
                    f'<line x1="{x1:.3f}" y1="{y:.3f}" x2="{x2:.3f}" y2="{y:.3f}" '
                    f'stroke="{theme.edge_color}" stroke-width="1.5"'
                    f"{marker_end}{marker_start}{dash}/>"
                )
                if ev.text:
                    mx = (x1 + x2) / 2.0
                    ty = y - 6.0
                    svg.append(
                        f'<text x="{mx:.3f}" y="{ty:.3f}" text-anchor="middle" '
                        f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
                        f'fill="{theme.text_color}">{_escape_xml(ev.text)}</text>'
                    )
        elif isinstance(ev, _Note):
            placement = ev.placement
            if placement.over:
                x1 = x_for.get(placement.participant_a, left_margin)
                x2 = x_for.get(placement.participant_b, left_margin)
                lx, rx = (x1, x2) if x1 <= x2 else (x2, x1)
                pad = 50.0
                note_x = max(lx - pad, 8.0)
                note_w = max(rx - lx + pad * 2.0, 120.0)
                _render_note(svg, note_x, y, note_w, ev.text, theme)
            else:
                participant_x = x_for.get(placement.participant_a, left_margin)
                note_w = min(_estimate_note_width(ev.text), 380.0)
                if placement.side is _LEFT:
                    note_x = max(participant_x - note_w - 12.0, 8.0)
                else:  # _RIGHT
                    note_x = min(participant_x + 12.0, max(width - note_w - 8.0, 8.0))
                _render_note(svg, note_x, y, note_w, ev.text, theme)
        # _Activate / _Deactivate / _FragmentStart / _FragmentElse / _FragmentEnd
        # carry no direct SVG emission here -- their geometry was resolved
        # above (activation bars + fragment boxes).

    svg.append("</svg>")
    return "".join(svg)


# === parser (grok L465-L933) =================================================


def _strip_keyword_ci(line: str, kw: str) -> str | None:
    """Case-insensitively strip a leading ASCII keyword (grok L465-L478).

    Returns the trimmed remainder when ``line`` starts with ``kw`` (ASCII
    case-insensitive) followed by end-of-string or a space/tab separator;
    otherwise ``None``. Mirrors Rust ``eq_ignore_ascii_case`` + the
    space/tab separator gate.
    """
    if len(line) < len(kw):
        return None
    if line[: len(kw)].lower() != kw.lower():
        return None
    rest = line[len(kw) :]
    if not rest:
        return rest
    if not rest.startswith((" ", "\t")):
        return None
    return rest.lstrip()


def _parse_sequence_diagram(input_source: str) -> _Diagram:
    """Parse a sequence-diagram body into a :class:`_Diagram` (grok L480-L669)."""
    lines = input_source.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("%%"):
            i += 1
            continue
        if line.split() and line.split()[0] == "sequenceDiagram":
            i += 1
            break
        raise ParseError(i + 1, "Expected 'sequenceDiagram' declaration")

    participants: list[_Participant] = []
    events: list[_SequenceEvent] = []
    title: str | None = None
    autonumber = _AutoNumber()
    in_box = False
    open_fragment_depth = 0
    in_acc_block = False

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        line_no = i + 1
        i += 1

        if not line or line.startswith("%%"):
            continue

        if in_acc_block:
            if "}" in line:
                in_acc_block = False
            continue

        rest = _strip_keyword_ci(line, "participant")
        if rest is None:
            rest = _strip_keyword_ci(line, "actor")
        if rest is not None:
            participant = _parse_participant_declaration(rest, line_no)
            _register_participant(participants, participant)
            continue

        rest = _strip_keyword_ci(line, "create")
        if rest is not None:
            decl = _strip_keyword_ci(rest, "participant")
            if decl is None:
                decl = _strip_keyword_ci(rest, "actor")
            if decl is None:
                decl = rest
            participant = _parse_participant_declaration(decl, line_no)
            _register_participant(participants, participant)
            continue

        rest = _strip_keyword_ci(line, "destroy")
        if rest is not None and rest:
            _resolve_participant_ref(participants, rest)
            continue

        if _strip_keyword_ci(line, "box") is not None:
            in_box = True
            continue
        if in_box and open_fragment_depth == 0 and line.lower() == "end":
            in_box = False
            continue

        rest = _strip_keyword_ci(line, "note")
        if rest is not None:
            events.append(_parse_note_line(rest, line, line_no, participants))
            continue

        rest = _strip_keyword_ci(line, "activate")
        if rest is not None and rest:
            participant = _resolve_participant_ref(participants, rest)
            events.append(_Activate(participant=participant))
            continue
        rest = _strip_keyword_ci(line, "deactivate")
        if rest is not None and rest:
            participant = _resolve_participant_ref(participants, rest)
            events.append(_Deactivate(participant=participant))
            continue

        rest = _strip_keyword_ci(line, "autonumber")
        if rest is not None:
            autonumber.apply(rest)
            continue

        rest = _strip_keyword_ci(line, "title")
        if rest is not None:
            cleaned = rest.lstrip(":").strip()
            if cleaned:
                title = _decode_sequence_text(cleaned)
            continue

        lower = line.lower()
        if lower.startswith("acctitle") or lower.startswith("accdescr"):
            if "{" in lower and "}" not in lower:
                in_acc_block = True
            continue
        if lower.startswith("links ") or lower.startswith("link ") or lower.startswith(
            "properties "
        ):
            continue

        msg = _parse_message_line(line)
        if msg is not None:
            from_id = _resolve_participant_ref(participants, msg.from_)
            to_id = _resolve_participant_ref(participants, msg.to)

            activate = to_id if msg.activate_target else None
            deactivate = from_id if msg.deactivate_source else None

            events.append(
                _Message(
                    from_=from_id,
                    to=to_id,
                    text=autonumber.number(msg.text),
                    dashed=msg.dashed,
                    head=msg.head,
                    bidirectional=msg.bidirectional,
                )
            )
            if activate is not None:
                events.append(_Activate(participant=activate))
            if deactivate is not None:
                events.append(_Deactivate(participant=deactivate))
            continue

        fragment = _parse_fragment_line(line)
        if fragment is not None:
            if isinstance(fragment, _FragmentStart):
                open_fragment_depth += 1
            elif isinstance(fragment, _FragmentEnd):
                open_fragment_depth = max(0, open_fragment_depth - 1)
            events.append(fragment)
            continue

        raise ParseError(line_no, f"Unrecognized sequenceDiagram line: {line}")

    if not participants:
        participants.append(_Participant(id="Participant", label="Participant"))

    return _Diagram(participants=participants, events=events, title=title)


def _parse_note_line(
    rest: str, line: str, line_no: int, participants: list[_Participant]
) -> _Note:
    """Parse the tail of a ``note ...`` line into a :class:`_Note` (grok L671-L723)."""

    def invalid() -> ParseError:
        return ParseError(line_no, f"Invalid Note syntax: {line}")

    stripped = _strip_keyword_ci(rest, "over")
    side: _NoteSide | None
    if stripped is not None:
        side = None
        rest = stripped
    else:
        stripped = _strip_keyword_ci(rest, "right")
        if stripped is not None:
            side = _RIGHT
            of = _strip_keyword_ci(stripped, "of")
            if of is None:
                raise invalid()
            rest = of
        else:
            stripped = _strip_keyword_ci(rest, "left")
            if stripped is not None:
                side = _LEFT
                of = _strip_keyword_ci(stripped, "of")
                if of is None:
                    raise invalid()
                rest = of
            else:
                raise invalid()

    if ":" not in rest:
        raise invalid()
    who_raw, text_raw = rest.split(":", 1)
    who = who_raw.strip()
    text = _decode_sequence_text(text_raw.strip())

    if side is not None:
        participant = _resolve_participant_ref(participants, who)
        placement = _NotePlacement(
            over=False, participant_a=participant, participant_b=participant, side=side
        )
    else:
        if "," in who:
            a_raw, b_raw = who.split(",", 1)
            participant_a = _resolve_participant_ref(participants, a_raw.strip())
            participant_b = _resolve_participant_ref(participants, b_raw.strip())
        else:
            participant = _resolve_participant_ref(participants, who)
            participant_a = participant
            participant_b = participant
        placement = _NotePlacement(
            over=True,
            participant_a=participant_a,
            participant_b=participant_b,
            side=_LEFT,  # side unused for over-notes
        )

    return _Note(placement=placement, text=text)


# The 10 mermaid sequence arrow operators, in fixity order (longest / most
# specific first so a substring search does not mis-match). Tuple fields:
# (operator, dashed, head, bidirectional). Mirrors grok L742-L753.
_ARROWS: tuple[tuple[str, bool, _ArrowHead, bool], ...] = (
    ("<<-->>", True, _ArrowHead.FILLED, True),
    ("<<->>", False, _ArrowHead.FILLED, True),
    ("-->>", True, _ArrowHead.FILLED, False),
    ("->>", False, _ArrowHead.FILLED, False),
    ("--x", True, _ArrowHead.CROSS, False),
    ("-x", False, _ArrowHead.CROSS, False),
    ("--)", True, _ArrowHead.OPEN, False),
    ("-)", False, _ArrowHead.OPEN, False),
    ("-->", True, _ArrowHead.NONE, False),
    ("->", False, _ArrowHead.NONE, False),
)


def _parse_message_line(line: str) -> _ParsedMessage | None:
    """Dissect a ``from <op> to: text`` message line (grok L736-L782)."""
    if ":" in line:
        head_raw, text = line.split(":", 1)
        text = text.strip()
    else:
        head_raw, text = line, ""
    head_raw = head_raw.strip()

    found = None
    for op, dashed, head, bidirectional in _ARROWS:
        if op in head_raw:
            found = (op, dashed, head, bidirectional)
            break
    if found is None:
        return None
    op, dashed, head, bidirectional = found

    if op not in head_raw:
        return None
    from_raw, to_raw = head_raw.split(op, 1)
    from_id = from_raw.strip()
    to_raw = to_raw.strip()
    activate_target = False
    deactivate_source = False
    if to_raw.startswith("+"):
        activate_target = True
        to_raw = to_raw[1:]
    elif to_raw.startswith("-"):
        deactivate_source = True
        to_raw = to_raw[1:]

    return _ParsedMessage(
        from_=from_id,
        to=to_raw.strip(),
        text=text,
        dashed=dashed,
        head=head,
        bidirectional=bidirectional,
        activate_target=activate_target,
        deactivate_source=deactivate_source,
    )


# The fragment-opening keywords (grok L787-L795). Order matters only for
# documentation; strip_keyword_ci is prefix-exact.
_FRAGMENT_STARTS: tuple[tuple[str, _FragmentKind], ...] = (
    ("alt", _FragmentKind.ALT),
    ("loop", _FragmentKind.LOOP),
    ("opt", _FragmentKind.OPT),
    ("par", _FragmentKind.PAR),
    ("critical", _FragmentKind.CRITICAL),
    ("break", _FragmentKind.BREAK),
    ("rect", _FragmentKind.RECT),
)
_FRAGMENT_ELSES: tuple[str, ...] = ("else", "and", "option")


def _parse_fragment_line(line: str) -> _SequenceEvent | None:
    """Parse an ``alt``/``else``/``end`` fragment control line (grok L784-L818)."""
    trimmed = line.strip()

    for kw, kind in _FRAGMENT_STARTS:
        label = _strip_keyword_ci(trimmed, kw)
        if label is not None:
            return _FragmentStart(kind=kind, label=label)

    for kw in _FRAGMENT_ELSES:
        label = _strip_keyword_ci(trimmed, kw)
        if label is not None:
            return _FragmentElse(label=label)

    if trimmed.lower() == "end":
        return _FRAGMENT_END

    return None


def _parse_participant_declaration(raw: str, line_no: int) -> _Participant:
    """Parse a ``participant``/``actor`` declaration tail (grok L820-L861)."""
    raw = raw.strip()
    if not raw:
        raise ParseError(line_no, "Expected participant name")

    if " as " in raw:
        lhs_raw, rhs_raw = raw.split(" as ", 1)
        lhs = _normalize_participant_token(lhs_raw)
        rhs = _normalize_participant_token(rhs_raw)
        if not lhs or not rhs:
            raise ParseError(line_no, "Expected participant name")
        pid, label = _choose_participant_id_and_label(lhs, rhs)
        aliases: list[str] = []
        _push_unique_alias(aliases, lhs)
        _push_unique_alias(aliases, rhs)
        return _Participant(id=pid, label=label, aliases=aliases)

    name = _normalize_participant_token(raw)
    if not name:
        raise ParseError(line_no, "Expected participant name")
    return _Participant(id=name, label=name, aliases=[])


def _choose_participant_id_and_label(lhs: str, rhs: str) -> tuple[str, str]:
    """Pick which side of ``A as B`` is the id vs the label (grok L863-L873).

    The side bearing whitespace becomes the (display) label; the other the
    (reference) id. If neither or both bear whitespace, the shorter is the id.
    """
    lhs_ws = any(ch.isspace() for ch in lhs)
    rhs_ws = any(ch.isspace() for ch in rhs)

    if lhs_ws and not rhs_ws:
        return rhs, lhs
    if not lhs_ws and rhs_ws:
        return lhs, rhs
    if len(lhs) <= len(rhs):
        return lhs, rhs
    return rhs, lhs


def _normalize_participant_token(token: str) -> str:
    """Trim + strip surrounding quotes from a participant token (grok L875-L881)."""
    return token.strip().strip('"').strip("'").strip()


def _register_participant(
    list_: list[_Participant], participant: _Participant
) -> None:
    """Insert or merge a participant declaration (grok L883-L907)."""
    match_index = None
    for index, existing in enumerate(list_):
        if (
            existing.matches(participant.id)
            or existing.matches(participant.label)
            or any(existing.matches(alias) for alias in participant.aliases)
        ):
            match_index = index
            break

    if match_index is not None:
        existing = list_[match_index]
        if existing.label == existing.id and participant.label != participant.id:
            existing.label = participant.label
        _push_unique_alias(existing.aliases, participant.id)
        _push_unique_alias(existing.aliases, participant.label)
        for alias in participant.aliases:
            _push_unique_alias(existing.aliases, alias)
    else:
        list_.append(participant)


def _resolve_participant_ref(
    list_: list[_Participant], reference: str
) -> str:
    """Resolve a participant reference, auto-creating if absent (grok L909-L927)."""
    reference = _normalize_participant_token(reference)

    for participant in list_:
        if participant.matches(reference):
            return participant.id

    participant = _Participant(id=reference, label=reference)
    list_.append(participant)
    return participant.id


def _push_unique_alias(aliases: list[str], value: str) -> None:
    """Append ``value`` to ``aliases`` unless empty or already present (grok L929-L933)."""
    if value and value not in aliases:
        aliases.append(value)


# === layout (grok L935-L1078) ================================================


def _participant_span(
    diagram: _Diagram, x_for: dict[str, float]
) -> tuple[float, float] | None:
    """Return the (min, max) x of all participants, or None (grok L935-L948)."""
    xs = [
        x_for[p.id]
        for p in diagram.participants
        if p.id in x_for
    ]
    if not xs:
        return None
    return min(xs), max(xs)


def _layout_sequence_events(
    events: list[_SequenceEvent], events_top: float
) -> tuple[
    list[float | None],
    list[_FragmentLayout],
    list[_ActivationLayout],
    float,
]:
    """Assign a y to each event and resolve fragment boxes (grok L950-L1031).

    Returns the per-event y positions (``None`` for events with no direct
    row), the closed fragment layouts, the activation-bar layouts, and the
    cursor y at the bottom of the last event (``content_bottom``).
    """
    event_y_positions: list[float | None] = [None] * len(events)
    fragment_layouts: list[_FragmentLayout] = []
    open_fragments: list[_OpenFragment] = []
    cursor_y = events_top
    last_row_center: float | None = None

    for idx, event in enumerate(events):
        if isinstance(event, (_Message, _Note)):
            center = cursor_y + _SEQ_EVENT_ROW_H / 2.0
            event_y_positions[idx] = center
            last_row_center = center
            cursor_y += _SEQ_EVENT_ROW_H
        elif isinstance(event, (_Activate, _Deactivate)):
            event_y_positions[idx] = last_row_center if last_row_center is not None else cursor_y
        elif isinstance(event, _FragmentStart):
            open_fragments.append(
                _OpenFragment(
                    kind=event.kind,
                    label=event.label,
                    depth=len(open_fragments),
                    start_y=cursor_y,
                )
            )
            cursor_y += _SEQ_FRAG_HEADER_H
        elif isinstance(event, _FragmentElse):
            if open_fragments:
                open_fragments[-1].else_markers.append(
                    _ElseMarker(separator_y=cursor_y, label=event.label)
                )
            cursor_y += _SEQ_FRAG_HEADER_H
        elif isinstance(event, _FragmentEnd):
            if open_fragments:
                fragment = open_fragments.pop()
                fragment_layouts.append(
                    _FragmentLayout(
                        kind=fragment.kind,
                        label=fragment.label,
                        depth=fragment.depth,
                        start_y=fragment.start_y,
                        end_y=cursor_y + _SEQ_FRAG_FOOTER_H,
                        else_markers=fragment.else_markers,
                    )
                )
                cursor_y += _SEQ_FRAG_FOOTER_H

    while open_fragments:
        fragment = open_fragments.pop()
        fragment_layouts.append(
            _FragmentLayout(
                kind=fragment.kind,
                label=fragment.label,
                depth=fragment.depth,
                start_y=fragment.start_y,
                end_y=cursor_y + _SEQ_FRAG_FOOTER_H,
                else_markers=fragment.else_markers,
            )
        )
        cursor_y += _SEQ_FRAG_FOOTER_H

    fragment_layouts.sort(key=lambda f: (f.depth, f.start_y))

    activations = _compute_activation_layouts(events, event_y_positions, cursor_y)
    return event_y_positions, fragment_layouts, activations, cursor_y


def _compute_activation_layouts(
    events: list[_SequenceEvent],
    event_y_positions: list[float | None],
    content_bottom: float,
) -> list[_ActivationLayout]:
    """Pair activate/deactivate events into bars (grok L1033-L1078).

    A stack per participant: ``activate`` pushes ``(participant, y, depth)``;
    ``deactivate`` pops the most recent open bar for that participant.
    Unclosed bars extend to ``content_bottom``.
    """
    min_bar_h = 6.0

    open_stack: list[tuple[str, float, int]] = []
    bars: list[_ActivationLayout] = []
    for idx, event in enumerate(events):
        y = event_y_positions[idx]
        if isinstance(event, _Activate):
            depth = sum(1 for (p, _y, _d) in open_stack if p == event.participant)
            open_stack.append((event.participant, y if y is not None else content_bottom, depth))
        elif isinstance(event, _Deactivate):
            pos = None
            for j in range(len(open_stack) - 1, -1, -1):
                if open_stack[j][0] == event.participant:
                    pos = j
                    break
            if pos is not None:
                participant, start_y, depth = open_stack.pop(pos)
                end_y = y if y is not None else content_bottom
                bars.append(
                    _ActivationLayout(
                        participant=participant,
                        depth=depth,
                        start_y=start_y,
                        end_y=max(end_y, start_y + min_bar_h),
                    )
                )
    for participant, start_y, depth in open_stack:
        bars.append(
            _ActivationLayout(
                participant=participant,
                depth=depth,
                start_y=start_y,
                end_y=max(content_bottom, start_y + min_bar_h),
            )
        )
    bars.sort(key=lambda b: (b.depth, b.start_y))
    return bars


# === render helpers (grok L1080-L1314) =======================================


def _render_fragment(
    svg: list[str],
    fragment: _FragmentLayout,
    min_participant_x: float,
    max_participant_x: float,
    theme: MermaidTheme,
) -> None:
    """Emit a fragment border + tab + else separators (grok L1080-L1146)."""
    inset = fragment.depth * _SEQ_FRAG_INSET_X
    x = min_participant_x - _SEQ_FRAG_MARGIN_X + inset
    width = (
        (max_participant_x - min_participant_x)
        + _SEQ_FRAG_MARGIN_X * 2.0
        - inset * 2.0
    )
    height = max(fragment.end_y - fragment.start_y, _SEQ_FRAG_HEADER_H)
    y = fragment.start_y

    svg.append(
        f'<rect x="{x:.3f}" y="{y:.3f}" width="{width:.3f}" height="{height:.3f}" '
        f'fill="none" stroke="{_SEQ_FRAG_STROKE}" stroke-width="1" '
        f'stroke-dasharray="3,3"/>'
    )

    tab_text = fragment.kind.tab_label()
    if tab_text is not None:
        tab_text_width = display_width_units(tab_text) * 6.5
        tab_width = tab_text_width + _SEQ_FRAG_TAB_PAD_X * 2.0 + 10.0
        tab_height = 18.0
        tab_x = x + 8.0
        tab_y = y + 4.0
        tab_body_width = max(tab_width - 10.0, 10.0)

        svg.append(
            f'<path d="M {tab_x:.3f},{tab_y:.3f} h {tab_body_width:.3f} l 10,0 '
            f'l 0,10 l -10,8 h -{tab_body_width:.3f} z" fill="{theme.node_fill}" '
            f'stroke="{_SEQ_FRAG_STROKE}" stroke-width="1"/>'
        )
        text_x = tab_x + tab_body_width / 2.0
        text_y = tab_y + tab_height / 2.0
        svg.append(
            f'<text x="{text_x:.3f}" y="{text_y:.3f}" text-anchor="middle" '
            f'dominant-baseline="central" '
            f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
            f'fill="{_SEQ_FRAG_TEXT}">{_escape_xml(tab_text)}</text>'
        )

        if fragment.label:
            label_x = min(tab_x + tab_body_width + 18.0, x + width - 8.0)
            label_text_y = y + _SEQ_FRAG_HEADER_H / 2.0
            svg.append(
                f'<text x="{label_x:.3f}" y="{label_text_y:.3f}" text-anchor="start" '
                f'dominant-baseline="central" '
                f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
                f'fill="{theme.text_color}">[{_escape_xml(fragment.label)}]</text>'
            )

    for else_marker in fragment.else_markers:
        x2 = x + width
        svg.append(
            f'<line x1="{x:.3f}" y1="{else_marker.separator_y:.3f}" '
            f'x2="{x2:.3f}" y2="{else_marker.separator_y:.3f}" '
            f'stroke="{_SEQ_FRAG_STROKE}" stroke-width="1" stroke-dasharray="3,3"/>'
        )
        if else_marker.label:
            text_x = x + width / 2.0
            text_y = else_marker.separator_y + _SEQ_FRAG_HEADER_H / 2.0
            svg.append(
                f'<text x="{text_x:.3f}" y="{text_y:.3f}" text-anchor="middle" '
                f'dominant-baseline="central" '
                f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
                f'fill="{theme.text_color}">[{_escape_xml(else_marker.label)}]</text>'
            )


def _render_note(
    svg: list[str],
    note_x: float,
    center_y: float,
    note_w: float,
    text: str,
    theme: MermaidTheme,
) -> None:
    """Emit a sticky-note rectangle + centred text (grok L1148-L1167)."""
    note_h = 26.0
    ny = center_y - note_h / 2.0
    x = note_x + note_w / 2.0
    svg.append(
        f'<rect x="{note_x:.3f}" y="{ny:.3f}" width="{note_w:.3f}" '
        f'height="{note_h:.3f}" rx="4" ry="4" fill="#fff2b0" '
        f'stroke="{theme.edge_color}" stroke-width="1"/>'
    )
    svg.append(
        f'<text x="{x:.3f}" y="{center_y:.3f}" text-anchor="middle" '
        f'dominant-baseline="middle" '
        f'font-family="Trebuchet MS,Verdana,Arial,sans-serif" font-size="11" '
        f'fill="#333333">{_escape_xml(text)}</text>'
    )


# === text helpers (grok L1169-L1326) =========================================


def _estimate_note_width(text: str) -> float:
    """Width heuristic for a note from its text (grok L1169-L1171)."""
    return max(display_width_units(text) * 6.5 + 18.0, 120.0)


def _compute_sequence_pair_spacings(diagram: _Diagram, box_w: float) -> list[float]:
    """Per-pair participant x spacing driven by message text (grok L1172-L1196)."""
    pair_spacings = [box_w + 30.0] * max(len(diagram.participants) - 1, 0)
    for event in diagram.events:
        if not isinstance(event, _Message):
            continue
        from_idx = _participant_index(diagram, event.from_)
        to_idx = _participant_index(diagram, event.to)
        if from_idx is None or to_idx is None:
            continue
        a = min(from_idx, to_idx)
        b = max(from_idx, to_idx)
        if a == b:
            continue
        total_gap = max(_estimate_sequence_text_width(event.text) * 0.95, box_w + 30.0)
        per_gap = total_gap / (b - a)
        for k in range(a, b):
            if per_gap > pair_spacings[k]:
                pair_spacings[k] = per_gap
    return pair_spacings


def _required_sequence_width(
    diagram: _Diagram,
    participant_xs: list[float],
    box_w: float,
    edge_pad: float,
) -> float:
    """The minimum canvas width to fit every message / note (grok L1198-L1257)."""
    width = (participant_xs[-1] if participant_xs else 0.0) + box_w / 2.0 + edge_pad
    for event in diagram.events:
        if isinstance(event, _Message):
            from_idx = _participant_index(diagram, event.from_)
            to_idx = _participant_index(diagram, event.to)
            if from_idx is None or to_idx is None:
                continue
            x1 = participant_xs[from_idx] if from_idx < len(participant_xs) else 0.0
            x2 = participant_xs[to_idx] if to_idx < len(participant_xs) else 0.0
            if from_idx == to_idx:
                width = max(width, x1 + 50.0 + edge_pad)
                continue
            mx = (x1 + x2) / 2.0
            width = max(width, mx + _estimate_sequence_text_width(event.text) / 2.0 + edge_pad)
        elif isinstance(event, _Note):
            placement = event.placement
            if placement.over:
                from_idx = _participant_index(diagram, placement.participant_a)
                to_idx = _participant_index(diagram, placement.participant_b)
                if from_idx is None or to_idx is None:
                    continue
                x1 = participant_xs[from_idx] if from_idx < len(participant_xs) else 0.0
                x2 = participant_xs[to_idx] if to_idx < len(participant_xs) else 0.0
                lx, rx = (x1, x2) if x1 <= x2 else (x2, x1)
                note_x = max(lx - 50.0, 8.0)
                note_w = max(rx - lx + 100.0, 120.0)
                width = max(width, note_x + note_w + edge_pad)
            else:
                if placement.side is not _RIGHT:
                    continue
                idx = _participant_index(diagram, placement.participant_a)
                if idx is None:
                    continue
                x = participant_xs[idx] if idx < len(participant_xs) else 0.0
                width = max(
                    width, x + 12.0 + min(_estimate_note_width(event.text), 380.0) + edge_pad
                )
    return width


def _participant_index(diagram: _Diagram, id_: str) -> int | None:
    """Position of the participant whose id == ``id_`` (grok L1259-L1264)."""
    for index, participant in enumerate(diagram.participants):
        if participant.id == id_:
            return index
    return None


def _estimate_sequence_text_width(text: str) -> float:
    """Width heuristic for sequence message / title text (grok L1266-L1268)."""
    return display_width_units(text) * 6.5


def _estimate_label_box_width(label: str) -> float:
    """Width heuristic for a participant header box (grok L1270-L1278)."""
    char_w = 7.2
    padding = 16.0
    text_w = max(display_width_units(line) * char_w for line in label.split("<br/>"))
    return max(text_w + padding, 100.0)


def _render_participant_label(x: float, cy: float, label: str, color: str) -> str:
    """Emit a participant label, multi-line via ``<tspan>`` (grok L1280-L1314)."""
    font = "Trebuchet MS,Verdana,Arial,sans-serif"
    size = 12
    lines = label.split("<br/>")

    if len(lines) == 1:
        return (
            f'<text x="{x:.3f}" y="{cy:.3f}" text-anchor="middle" '
            f'dominant-baseline="middle" font-family="{font}" font-size="{size}" '
            f'fill="{color}">{_escape_xml(lines[0])}</text>'
        )

    line_h = 15.0
    total_h = line_h * len(lines)
    y0 = cy - total_h / 2.0 + line_h / 2.0

    out = [
        f'<text x="{x:.3f}" text-anchor="middle" font-family="{font}" '
        f'font-size="{size}" fill="{color}">'
    ]
    for i, line_text in enumerate(lines):
        if i == 0:
            out.append(
                f'<tspan x="{x:.3f}" y="{y0:.3f}">{_escape_xml(line_text)}</tspan>'
            )
        else:
            out.append(
                f'<tspan x="{x:.3f}" dy="{line_h:.3f}">{_escape_xml(line_text)}</tspan>'
            )
    out.append("</text>")
    return "".join(out)


def _decode_sequence_text(s: str) -> str:
    """Map mermaid's ``#59;`` entity to ``;`` (grok L1316-L1318)."""
    return s.replace("#59;", ";")


def _escape_xml(s: str) -> str:
    """Escape the 5 XML special chars (grok L1320-L1326).

    Emits the ``&apos;`` named entity for ``'`` (the variant shared with the
    er / gitgraph / journey renderers).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
