"""State diagram parser -- behavioral-equivalent port of grok's ``state_diagram.rs``.

Direction (1) brick 11 (R280). The second per-diagram leaf and the first
per-diagram *parser*. Unlike the ``info`` renderer (R279, a self-contained
SVG emitter with no geometry), ``stateDiagram`` / ``stateDiagram-v2`` parse
into the existing flowchart AST (:class:`FlowchartGraph`) and then ride the
already-migrated dagre stack (:func:`layout.compute_layout` +
:func:`svg_renderer.render`) -- exactly grok lib.rs L63-L68. That makes this
leaf a *parser* port, not a renderer port: the dedicated work is turning
mermaid state-machine syntax into nodes + edges, after which the generic
flowchart machinery takes over.

Behavioral-equivalence mapping (function-not-line, same framework as R279)
-------------------------------------------------------------------------

* grok ``pub fn parse_state_diagram(input) -> Result<FlowchartGraph,
  MermaidError>`` -> ``parse_state_diagram(input) -> FlowchartGraph``. The
  ``Result<...>`` (``Ok(graph)`` / ``Err(ParseError)``) maps to "return the
  graph / raise :class:`ParseError`" -- the Pythonic shape for a fallible
  parse, identical to how R279 maps the ``info`` renderer's ``Result``.
* grok ``BTreeMap<String, NodeShape>`` -> Python ``dict[str, NodeShape]``.
  grok keeps an ordered map keyed by node id plus a separate ``node_order``
  ``Vec<String>`` that records first-seen order; the final statement list
  iterates ``node_order`` (NOT the BTreeMap), so the map's sort-by-key
  property never reaches the output. A plain ``dict`` (insertion-ordered,
  used only for membership / shape lookup) is therefore behaviorally
  identical -- the Pythonic choice, dropping the sort that goes unused.
* grok ``Vec<(String, String, Option<String>)>`` for edges ->
  ``list[tuple[str, str, str | None]]``. The triple carries from / to /
  optional label before they are folded into :class:`Edge` statements.
* grok's manual index-driven ``while`` loops (one for the header scan, one
  for the body) share the line cursor ``i``. The port uses a single
  :meth:`str.splitlines` then enumerates with the original index, so the
  1-based ``line`` carried by every :class:`ParseError` matches grok's
  ``i + 1`` exactly (a line number *within the body*, since the dispatch
  passes the front-matter-stripped body -- grok lib.rs L47/L64).
* grok's two free helpers ``normalize_state_id`` / ``ensure_state_node``
  -> module-private ``_normalize_state_id`` / ``_ensure_state_node``. Their
  contracts are unchanged: ``[*]`` collapses to ``__start`` (as a source)
  or ``__end`` (as a target); an unseen node id is appended to the order
  vector and stamped ``StartState`` / ``EndState`` / ``RoundedRectangle``.
* grok's shape heuristics on ``state X <<choice>>`` / ``<<fork>>`` /
  ``<<join>>`` -> literal Python ``in`` checks, verbatim. The
  ``NodeShape`` variants (``Diamond`` / ``ForkJoin`` / ``RoundedRectangle``)
  are the same enum migrated in R271 (``ast.py``).

Why no inline ``_first_diagram_type_token`` (unlike R279)
---------------------------------------------------------

grok's ``info_diagram.rs`` inlines a copy of ``first_diagram_type_token``
because it defends its public entry against a non-``info`` first token.
``state_diagram.rs`` does NOT: it is a hand-written line scanner that finds
the ``stateDiagram`` / ``stateDiagram-v2`` header itself (skipping blanks
and ``%%`` comments), so it never calls the crate-root helper. The port
mirrors that -- no inline copy, no circular-import edge to sidestep. The
token helper stays a private detail of :mod:`.render`; this leaf is
reached only through the dispatch arm added in R280.

Public surface (1 symbol): :func:`parse_state_diagram` (grok ``pub fn``).
The two helpers stay module-private; grok's crate root never re-exports
them, and neither does the :mod:`.to_svg` barrel -- the parser is reached
only through the dispatch in :mod:`.render`, exactly like the ``info``
renderer in R279.
"""

from __future__ import annotations

from .ast import (
    Edge,
    EdgeStyle,
    FlowchartGraph,
    GraphDirection,
    Node,
    NodeShape,
    Statement,
)
from .error import ParseError

__all__ = ["parse_state_diagram"]

#: The two header tokens grok recognizes (``stateDiagram`` /
#: ``stateDiagram-v2``). The first non-blank / non-``%%`` line's leading
#: token must be one of these, else the parse raises (grok L19/L25-L28).
_STATE_HEADER_TOKENS: frozenset[str] = frozenset({"stateDiagram", "stateDiagram-v2"})


def _normalize_state_id(raw: str, is_from: bool) -> str:
    """Collapse mermaid's ``[*]`` pseudo-state into ``__start`` / ``__end``.

    Mirrors grok ``normalize_state_id`` (state_diagram.rs L143-L154). In a
    state transition ``[*] --> Idle`` the ``[*]`` source is the start state;
    in ``Working --> [*]`` the ``[*]`` target is the end state. grok rewrites
    those to the synthetic ids ``__start`` / ``__end`` so the rest of the
    pipeline (node creation, layout, rendering) treats them as the dedicated
    :class:`NodeShape.StartState` / :class:`NodeShape.EndState` shapes.

    Args:
        raw: the raw from/to token trimmed of whitespace.
        is_from: ``True`` when ``raw`` is the transition source (``[*]`` ->
            ``__start``), ``False`` when it is the target (``[*]`` ->
            ``__end``).

    Returns:
        The normalized id (``__start`` / ``__end`` for ``[*]``, else the
        trimmed token verbatim).
    """
    raw = raw.strip()
    if raw == "[*]":
        return "__start" if is_from else "__end"
    return raw


def _ensure_state_node(
    nodes: dict[str, NodeShape], node_order: list[str], node_id: str
) -> None:
    """Auto-create a state node the first time an edge references it.

    Mirrors grok ``ensure_state_node`` (state_diagram.rs L156-L172). A
    transition ``A --> B`` may name endpoints that have no preceding ``state
    A`` / ``state B`` declaration; grok creates them on the fly so the edge
    always has well-defined endpoints. ``__start`` / ``__end`` become the
    dedicated start / end shapes; anything else defaults to
    :class:`NodeShape.RoundedRectangle` (the mermaid state default).
    """
    if node_id in nodes:
        return
    node_order.append(node_id)
    if node_id == "__start":
        nodes[node_id] = NodeShape.StartState
    elif node_id == "__end":
        nodes[node_id] = NodeShape.EndState
    else:
        nodes[node_id] = NodeShape.RoundedRectangle


def parse_state_diagram(input: str) -> FlowchartGraph:
    """Parse a ``stateDiagram`` / ``stateDiagram-v2`` body into a flowchart graph.

    Direction (1) brick 11 (R280). Mirrors grok ``state_diagram.rs``
    ``parse_state_diagram``: scan the body for the ``stateDiagram``[-v2]
    header, then walk the remaining lines building nodes + edges. The result
    is a :class:`FlowchartGraph` whose ``direction`` is pinned
    :class:`GraphDirection.TopToBottom` (state diagrams always render
    top-to-bottom in grok) and whose ``statements`` list all nodes (in
    first-seen order) followed by all edges (``EdgeStyle.Arrow``).

    Line grammar recognized (grok L42-L109):

    * ``state <name> <<choice>>``  -> node ``<name>`` shaped
      :class:`NodeShape.Diamond`.
    * ``state <name> <<fork>>`` / ``<<join>>`` -> node ``<name>`` shaped
      :class:`NodeShape.ForkJoin`.
    * ``state <name>`` (no stereotype) -> :class:`NodeShape.RoundedRectangle`.
    * ``<from> --> <to>`` -> an unlabeled arrow edge.
    * ``<from> --> <to> : <label>`` -> a labeled arrow edge.
    * ``[*]`` as a source / target -> the synthetic start / end node.

    Blank lines and ``%%`` comments are skipped. The header line itself is
    consumed and not emitted as a statement. Any other non-empty line raises
    :class:`ParseError`.

    Args:
        input: the mermaid body (front-matter already stripped by the
            dispatch -- grok lib.rs L47/L64 passes the body, not the raw
            source). The first non-blank / non-``%%`` line must carry the
            ``stateDiagram`` or ``stateDiagram-v2`` header token.

    Returns:
        The parsed :class:`FlowchartGraph` (``TopToBottom`` direction, nodes
        then edges).

    Raises:
        ParseError: when the header token is missing / wrong (``line == 1``
            when no header is found, else the offending header line), when
            a ``state`` line carries no name, or when a body line matches
            none of the recognized forms.
    """
    lines = input.splitlines()

    # Header scan (grok L9-L36): skip blanks / ``%%`` comments, demand the
    # first substantial line's leading token be a state header. Anything
    # else is an immediate ParseError carrying that line's 1-based number;
    # if every line is blank / comment, ParseError pins line 1.
    header_idx: int | None = None
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        token = line.split()[0]
        if token in _STATE_HEADER_TOKENS:
            header_idx = idx
            break
        raise ParseError(
            idx + 1,
            "Expected 'stateDiagram' or 'stateDiagram-v2' declaration",
        )
    if header_idx is None:
        raise ParseError(
            1, "Expected 'stateDiagram' or 'stateDiagram-v2' declaration"
        )

    # Body scan (grok L42-L109): nodes keyed by id (insertion-ordered dict,
    # used for membership + shape lookup -- see module docstring for why a
    # plain dict replaces grok's BTreeMap), node_order preserves first-seen
    # order for deterministic statement emission, edges collect triples
    # before they are folded into Edge statements.
    nodes: dict[str, NodeShape] = {}
    node_order: list[str] = []
    edges: list[tuple[str, str, str | None]] = []

    for idx in range(header_idx + 1, len(lines)):
        line = lines[idx].strip()
        line_no = idx + 1

        if not line or line.startswith("%%"):
            continue

        # ``state <name> [<stereotype>]`` declaration (grok L52-L75).
        if line.startswith("state "):
            rest = line.removeprefix("state ").strip()
            if not rest:
                raise ParseError(line_no, "Expected state name after 'state'")
            name = rest.split()[0]
            if "<<choice>>" in rest:
                shape = NodeShape.Diamond
            elif "<<fork>>" in rest or "<<join>>" in rest:
                shape = NodeShape.ForkJoin
            else:
                shape = NodeShape.RoundedRectangle
            if name not in nodes:
                node_order.append(name)
            nodes[name] = shape
            continue

        # ``<from> --> <to> [: <label>]`` transition (grok L77-L103). The
        # first ``-->`` splits source from the right-hand side; the first
        # ``:`` on the right-hand side splits target from the optional label.
        if "-->" in line:
            from_raw, rhs = line.split("-->", 1)
            from_raw = from_raw.strip()
            rhs = rhs.strip()
            if ":" in rhs:
                to_raw, label_raw = rhs.split(":", 1)
                to_raw = to_raw.strip()
                label_text = label_raw.strip()
                label: str | None = label_text or None
            else:
                to_raw = rhs
                label = None

            from_id = _normalize_state_id(from_raw, is_from=True)
            to_id = _normalize_state_id(to_raw, is_from=False)
            _ensure_state_node(nodes, node_order, from_id)
            _ensure_state_node(nodes, node_order, to_id)
            edges.append((from_id, to_id, label))
            continue

        raise ParseError(line_no, f"Unrecognized stateDiagram line: {line}")

    # Statement emission (grok L111-L140): nodes first, in first-seen order.
    # The start / end / fork-join shapes carry ``None`` labels (mermaid draws
    # them as pure geometry -- a filled circle for start / end, a bar for
    # fork-join); every other shape labels itself with its id.
    statements: list[Statement] = []
    for node_id in node_order:
        shape = nodes.get(node_id)
        if shape is None:
            continue
        if shape in (NodeShape.StartState, NodeShape.EndState, NodeShape.ForkJoin):
            label = None
        else:
            label = node_id
        statements.append(Node(id=node_id, label=label, shape=shape))

    for from_id, to_id, label in edges:
        statements.append(
            Edge(from_=from_id, to=to_id, label=label, style=EdgeStyle.Arrow)
        )

    return FlowchartGraph(
        direction=GraphDirection.TopToBottom, statements=statements
    )
