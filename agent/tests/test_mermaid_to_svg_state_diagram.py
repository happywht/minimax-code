"""Black-box tests for the ``stateDiagram`` parser (R280) -- the behavioral-
equivalent port of grok's ``state_diagram.rs``.

Direction (1) brick 11 (R280). The second per-diagram leaf and the first
per-diagram *parser*. Unlike the ``info`` renderer (R279, a self-contained
SVG emitter), ``stateDiagram`` / ``stateDiagram-v2`` parse into the existing
flowchart AST (:class:`FlowchartGraph`) and ride the dagre stack
(:func:`layout.compute_layout` + :func:`svg_renderer.render`). This file
exercises the parser's contract surface directly and through the dispatch,
mirroring grok's own coverage:

* **Dispatch smoke** (grok ``lib.rs`` ``test_simple_state_diagram`` L635-L647):
  :func:`render_mermaid_to_svg` with the canonical state fixture succeeds and
  emits a well-formed SVG. This is the single integration assertion grok makes
  for the state arm.
* **Header recognition** (grok state_diagram.rs L9-L36): the first
  substantial line's leading token must be ``stateDiagram`` or
  ``stateDiagram-v2``; any other first token raises :class:`ParseError`, and
  an all-blank body raises pinning line 1.
* **``[*]`` pseudo-state normalization** (grok L143-L154): ``[*]`` as a
  source collapses to ``__start`` (StartState), as a target to ``__end``
  (EndState).
* **Auto node creation** (grok L156-L172): an edge naming an undeclared node
  appends it to the order and stamps the default shape (RoundedRectangle, or
  StartState/EndState for the synthetic ids).
* **Transition grammar** (grok L77-L103): ``A --> B`` is unlabeled;
  ``A --> B : label`` carries the label (spaces preserved); labels are
  stripped to ``None`` when empty after the colon.
* **State stereotypes** (grok L52-L75): ``<<choice>>`` -> Diamond,
  ``<<fork>>`` / ``<<join>>`` -> ForkJoin, plain -> RoundedRectangle.
* **Statement ordering** (grok L111-L135): all nodes (first-seen order)
  before all edges (``EdgeStyle.Arrow``); start/end/fork-join nodes carry
  ``None`` labels, others self-label with their id.
* **Module surface**: the single-symbol ``__all__``; the parser stays out of
  the :mod:`.to_svg` barrel (dispatch-only reach, mirrors R279's info leaf).
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import state_diagram as state_diagram_mod
from minimax_code.mermaid.to_svg.ast import (
    Edge,
    EdgeStyle,
    FlowchartGraph,
    GraphDirection,
    Node,
    NodeShape,
)
from minimax_code.mermaid.to_svg.error import ParseError, UnsupportedDiagramType
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.state_diagram import parse_state_diagram

# === dispatch smoke (grok lib.rs test_simple_state_diagram L635-L647) ======


def test_render_mermaid_to_svg_state_dispatch_emits_svg() -> None:
    """The canonical state fixture dispatches end-to-end to an SVG (grok L635-L647).

    Mirrors grok's sole integration assertion for the state arm: the v2 header
    plus the classic start/idle/working cycle renders through
    ``parse_state_diagram`` -> ``compute_layout`` -> ``render`` (no config)
    and yields a well-formed SVG with the pinned start/end nodes.
    """
    source = (
        "stateDiagram-v2\n"
        "  [*] --> Idle\n"
        "  Idle --> Working : start\n"
        "  Working --> Idle : done\n"
        "  Working --> [*]"
    )
    svg = render_mermaid_to_svg(source)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_mermaid_to_svg_state_v1_dispatch_emits_svg() -> None:
    """The legacy ``stateDiagram`` header dispatches through the same arm."""
    source = "stateDiagram\n  [*] --> Idle\n  Idle --> [*]"
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_mermaid_to_svg_state_is_not_unsupported() -> None:
    """State tokens no longer raise :class:`UnsupportedDiagramType` (R280).

    R277 shipped the state tokens in ``_UNSUPPORTED_DIAGRAM_TYPES``; R280
    lifts them into a dedicated dispatch arm (mirrors grok lib.rs L63-L68).
    Both headers now render instead of raising.
    """
    for header in ("stateDiagram", "stateDiagram-v2"):
        source = f"{header}\n  [*] --> Idle"
        try:
            svg = render_mermaid_to_svg(source)
        except UnsupportedDiagramType:
            pytest.fail(f"{header} must not raise UnsupportedDiagramType after R280")
        assert "<svg" in svg


# === header recognition (grok state_diagram.rs L9-L36) ====================


def test_parse_state_header_skips_blank_and_comment_lines() -> None:
    """Leading blank / ``%%`` comment lines are skipped before the header."""
    graph = parse_state_diagram("\n%% a comment\n\n  stateDiagram\n  [*] --> Idle")
    assert graph.direction is GraphDirection.TopToBottom


def test_parse_state_missing_header_raises_line_one() -> None:
    """A body whose first token is not a state header raises (grok L19/L25-L28)."""
    with pytest.raises(ParseError) as exc_info:
        parse_state_diagram("flowchart TD\n  A --> B")
    assert exc_info.value.line == 1
    assert exc_info.value.message == (
        "Expected 'stateDiagram' or 'stateDiagram-v2' declaration"
    )


def test_parse_state_all_blank_raises_line_one() -> None:
    """An all-blank / comment body raises :class:`ParseError` pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_state_diagram("\n%% only\n   \n")
    assert exc_info.value.line == 1


# === [*] pseudo-state normalization (grok L143-L154) ======================


def test_parse_state_start_pseudo_as_source_becomes_start() -> None:
    """``[*] --> Idle`` names the synthetic start node as the source."""
    graph = parse_state_diagram("stateDiagram\n  [*] --> Idle")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    assert any(
        n.id == "__start" and n.shape is NodeShape.StartState for n in nodes
    )


def test_parse_state_end_pseudo_as_target_becomes_end() -> None:
    """``Idle --> [*]`` names the synthetic end node as the target."""
    graph = parse_state_diagram("stateDiagram\n  Idle --> [*]")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    assert any(n.id == "__end" and n.shape is NodeShape.EndState for n in nodes)


# === auto node creation (grok L156-L172) ==================================


def test_parse_state_edge_creates_undeclared_endpoint() -> None:
    """An edge naming an undeclared node auto-creates it as RoundedRectangle."""
    graph = parse_state_diagram("stateDiagram\n  A --> B")
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert set(nodes) == {"A", "B"}
    assert nodes["A"].shape is NodeShape.RoundedRectangle
    assert nodes["B"].shape is NodeShape.RoundedRectangle
    # Undeclared nodes self-label with their id (grok L116-L118).
    assert nodes["A"].label == "A"
    assert nodes["B"].label == "B"


# === transition grammar (grok L77-L103) ===================================


def test_parse_state_transition_unlabeled() -> None:
    """``A --> B`` produces an unlabeled arrow edge."""
    graph = parse_state_diagram("stateDiagram\n  A --> B")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 1
    assert edges[0].from_ == "A"
    assert edges[0].to == "B"
    assert edges[0].label is None
    assert edges[0].style is EdgeStyle.Arrow


def test_parse_state_transition_labeled() -> None:
    """``A --> B : label`` carries the label with spaces preserved."""
    graph = parse_state_diagram("stateDiagram\n  A --> B : start working")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 1
    assert edges[0].label == "start working"


def test_parse_state_transition_empty_label_collapses_to_none() -> None:
    """``A --> B :`` (label whitespace-only) collapses to ``None``."""
    graph = parse_state_diagram("stateDiagram\n  A --> B :   ")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 1
    assert edges[0].label is None


# === state stereotypes (grok L52-L75) =====================================


def test_parse_state_choice_stereotype_is_diamond() -> None:
    """``state X <<choice>>`` shapes X as :class:`NodeShape.Diamond`."""
    graph = parse_state_diagram("stateDiagram\n  state X <<choice>>")
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert nodes["X"].shape is NodeShape.Diamond


def test_parse_state_fork_stereotype_is_forkjoin() -> None:
    """``state X <<fork>>`` shapes X as :class:`NodeShape.ForkJoin`."""
    graph = parse_state_diagram("stateDiagram\n  state X <<fork>>")
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert nodes["X"].shape is NodeShape.ForkJoin


def test_parse_state_join_stereotype_is_forkjoin() -> None:
    """``state X <<join>>`` also shapes X as :class:`NodeShape.ForkJoin`."""
    graph = parse_state_diagram("stateDiagram\n  state X <<join>>")
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert nodes["X"].shape is NodeShape.ForkJoin


def test_parse_state_plain_declaration_is_rounded_rectangle() -> None:
    """``state X`` (no stereotype) defaults to :class:`NodeShape.RoundedRectangle`."""
    graph = parse_state_diagram("stateDiagram\n  state X")
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert nodes["X"].shape is NodeShape.RoundedRectangle


# === statement ordering (grok L111-L135) ==================================


def test_parse_state_nodes_precede_edges_in_first_seen_order() -> None:
    """Statements emit all nodes (first-seen order) before all edges."""
    graph = parse_state_diagram(
        "stateDiagram\n  [*] --> Idle\n  Idle --> Working : start\n  Working --> [*]"
    )
    # Four nodes (__start, Idle, Working, __end) precede the three edges.
    # grok iterates ``node_order`` then the edges vec (state_diagram.rs
    # L112/L128). The fixture has three transitions: [*] -> Idle,
    # Idle -> Working, Working -> [*].
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert [n.id for n in nodes] == ["__start", "Idle", "Working", "__end"]
    assert len(edges) == 3
    assert edges[0].from_ == "__start" and edges[0].to == "Idle"
    assert edges[1].from_ == "Idle" and edges[1].to == "Working"
    assert edges[2].from_ == "Working" and edges[2].to == "__end"


def test_parse_state_start_end_forkjoin_nodes_carry_no_label() -> None:
    """Start / end / fork-join shapes carry ``None`` labels (grok L116-L117)."""
    graph = parse_state_diagram(
        "stateDiagram\n  [*] --> Idle\n  Idle --> [*]\n  state F <<fork>>"
    )
    nodes = {s.id: s for s in graph.statements if isinstance(s, Node)}
    assert nodes["__start"].label is None
    assert nodes["__end"].label is None
    assert nodes["F"].label is None
    # A declared rounded-rectangle node self-labels with its id.
    assert nodes["Idle"].label == "Idle"


def test_parse_state_direction_is_top_to_bottom() -> None:
    """State diagrams always render top-to-bottom (grok state_diagram.rs L138)."""
    graph = parse_state_diagram("stateDiagram\n  A --> B")
    assert graph.direction is GraphDirection.TopToBottom


def test_parse_state_declared_node_not_duplicated_by_edge() -> None:
    """A ``state X`` declaration is not re-created when an edge names X."""
    graph = parse_state_diagram("stateDiagram\n  state X\n  X --> Y")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    # X declared once; the ``X --> Y`` edge creates Y but does not duplicate X.
    assert [n.id for n in nodes] == ["X", "Y"]


def test_parse_state_returns_flowchart_graph() -> None:
    """The parser returns a :class:`FlowchartGraph` typed value."""
    graph = parse_state_diagram("stateDiagram\n  A --> B")
    assert isinstance(graph, FlowchartGraph)


# === body-line errors (grok state_diagram.rs L52/L77/L109) ================


def test_parse_state_bare_keyword_is_unrecognized() -> None:
    """A bare ``state`` token (trim strips the trailing space) is unrecognized.

    grok L44 trims the line, so ``"  state   "`` becomes ``"state"``;
    ``strip_prefix("state ")`` (L52) then fails because there is no trailing
    space, and the line falls through to the ``Unrecognized`` arm (L105-L108).
    The ``Expected state name`` branch (L54-L58) is effectively dead: trim
    always strips the separator that ``strip_prefix("state ")`` requires.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_state_diagram("stateDiagram\n  state   ")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Unrecognized stateDiagram line: state"


def test_parse_state_unrecognized_line_raises() -> None:
    """A body line matching none of the recognized forms raises (grok L109)."""
    with pytest.raises(ParseError) as exc_info:
        parse_state_diagram("stateDiagram\n  foo bar baz")
    assert exc_info.value.line == 2
    assert "Unrecognized stateDiagram line: foo bar baz" in exc_info.value.message


def test_parse_state_skips_blank_and_comment_body_lines() -> None:
    """Blank / ``%%`` lines interspersed in the body are skipped, not errors."""
    graph = parse_state_diagram(
        "stateDiagram\n\n  %% a note\n  A --> B\n  \n  B --> A"
    )
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 2


# === module surface =======================================================


def test_state_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``)."""
    assert state_diagram_mod.__all__ == ["parse_state_diagram"]


def test_state_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The parser stays out of the barrel -- dispatch-only reach (R280).

    grok's ``lib.rs`` never re-exports ``state_diagram``'s symbols at the
    crate root; the parser is invoked only via the state dispatch arm. The
    barrel ``__all__`` does not grow for R280.
    """
    assert "parse_state_diagram" not in to_svg.__all__
    assert not hasattr(to_svg, "parse_state_diagram")
