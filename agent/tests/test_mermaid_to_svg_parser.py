"""Black-box tests for the migrated mermaid-to-svg flowchart parser (R272).

Exercises :mod:`minimax_code.mermaid.to_svg.parser` -- the flowchart parser
fused from grok's ``mermaid-to-svg/src/parser.rs`` (direction (1), leaf 4).
Consumes the R271 ``ast`` + ``error`` leaves; produces
:class:`~minimax_code.mermaid.to_svg.ast.FlowchartGraph` trees.

Like grok's ``mod parser;`` (private) and the crate-internal
``parser::parse_mermaid`` call site (the crate root never ``pub use``s it --
see ``lib.rs`` line 150), this module is **internal**: it declares its own
``__all__`` (the entry function) but is NOT re-exported through the
``to_svg`` barrel. The future ``layout``/``renderer`` leaves consume it via
the deep path. Covers:

* the public entry :func:`parse_mermaid` -- ``graph``/``flowchart``
  declarations with all 5 direction tokens (case-insensitive TD/TB/BT/LR/RL),
  refusal of known non-flowchart diagram types
  (:class:`UnsupportedDiagramType`), and ``InvalidDirection`` for unknown
  direction tokens,
* the 10 node shapes (Circle ``((..))`` through a bare alphanumeric id) with
  grok's exact precedence, plus id-derivation-from-label for empty ids,
* the 6 edge styles (Arrow / Line / DottedArrow / DottedLine / ThickArrow /
  ThickLine) in labeled (``-->|x|``), unlabeled (``-->``), and open-label
  (``-- x -->``) forms, plus multi-hop edge chains,
* :class:`Subgraph` parsing (bracket title, synthesized ``subGraph{N}`` id,
  single-word id, recursion, optional ``end``) and :class:`StyleStatement`,
* label normalization (wrapping quotes, the 6 HTML entities, the ``<br>``
  variants, ``\\n`` escapes) and ``%%`` comment skipping,
* the barrel contract: ``parser`` is internal -- reachable by deep path, NOT
  re-exported through the ``to_svg`` barrel; the ``mermaid`` root stays at 24.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import parser as parser_mod
from minimax_code.mermaid.to_svg.ast import (
    Edge,
    EdgeStyle,
    GraphDirection,
    Node,
    NodeShape,
    StyleStatement,
    Subgraph,
)
from minimax_code.mermaid.to_svg.error import (
    InvalidDirection,
    ParseError,
    UnsupportedDiagramType,
)
from minimax_code.mermaid.to_svg.parser import (
    decode_html_entities,
    first_non_empty_non_comment_line,
    is_known_mermaid_type,
    normalize_label,
    parse_mermaid,
    strip_wrapping_quotes,
)

# === parse_mermaid: graph/flowchart declaration + direction ================


@pytest.mark.parametrize(
    "token,expected",
    [
        ("TD", GraphDirection.TopToBottom),
        ("TB", GraphDirection.TopToBottom),
        ("BT", GraphDirection.BottomToTop),
        ("LR", GraphDirection.LeftToRight),
        ("RL", GraphDirection.RightToLeft),
    ],
)
def test_graph_declaration_directions(token: str, expected: GraphDirection) -> None:
    """``graph <DIR>`` maps each direction token (case-insensitive) correctly."""
    graph = parse_mermaid(f"graph {token}\nA")
    assert graph.direction is expected


def test_flowchart_keyword_is_alias_of_graph() -> None:
    """``flowchart`` declares a flowchart just like ``graph``."""
    graph = parse_mermaid("flowchart LR\nA-->B")
    assert graph.direction is GraphDirection.LeftToRight


def test_direction_is_case_insensitive() -> None:
    """Direction tokens match case-insensitively (mirrors grok ``to_uppercase``)."""
    assert parse_mermaid("graph lr\nA").direction is GraphDirection.LeftToRight
    assert parse_mermaid("graph Tb\nA").direction is GraphDirection.TopToBottom


def test_invalid_direction_raises() -> None:
    """An unknown direction token raises :class:`InvalidDirection`."""
    with pytest.raises(InvalidDirection) as exc_info:
        parse_mermaid("graph sideways\nA")
    assert exc_info.value.direction == "sideways"


# === parse_mermaid: unsupported diagram type refusal =======================


@pytest.mark.parametrize(
    "diagram_type",
    ["sequenceDiagram", "classDiagram", "stateDiagram", "erDiagram", "pie", "gantt"],
)
def test_known_non_flowchart_type_is_refused(diagram_type: str) -> None:
    """A known non-flowchart diagram type raises :class:`UnsupportedDiagramType`.

    The parser refuses to mis-parse ``sequenceDiagram``/``pie``/etc. as a
    flowchart -- those belong to the render-stack dispatcher, not this parser.
    """
    with pytest.raises(UnsupportedDiagramType) as exc_info:
        parse_mermaid(f"{diagram_type}\nA-->B")
    assert exc_info.value.diagram_type == diagram_type


def test_unknown_first_token_is_not_refused() -> None:
    """A first token that is NOT a known diagram type is left to the parser
    (which will raise ``ParseError`` for a missing ``graph``/``flowchart``)."""
    with pytest.raises(ParseError):
        parse_mermaid("nonsense\nA-->B")


def test_leading_comments_and_blanks_skipped_before_type_check() -> None:
    """``%%`` comments and blank lines before the declaration are skipped
    before the first-token diagram-type check fires."""
    graph = parse_mermaid("%% a comment\n\n   \ngraph TD\nA")
    assert graph.direction is GraphDirection.TopToBottom


# === node shapes: the 10-shape precedence ==================================


def test_rectangle_shape() -> None:
    """``A[label]`` -> Rectangle."""
    graph = parse_mermaid("graph TD\nA[Hello]")
    node = graph.statements[0]
    assert isinstance(node, Node)
    assert node.id == "A"
    assert node.label == "Hello"
    assert node.shape is NodeShape.Rectangle


def test_rounded_rectangle_shape() -> None:
    """``A(label)`` -> RoundedRectangle."""
    node = parse_mermaid("graph TD\nA(Hello)").statements[0]
    assert isinstance(node, Node)
    assert node.shape is NodeShape.RoundedRectangle


def test_circle_shape() -> None:
    """``A((label))`` -> Circle."""
    node = parse_mermaid("graph TD\nA((Hello))").statements[0]
    assert isinstance(node, Node)
    assert node.shape is NodeShape.Circle


def test_stadium_shape() -> None:
    """``A([label])`` -> Stadium."""
    assert parse_mermaid("graph TD\nA([Hi])").statements[0].shape is NodeShape.Stadium  # type: ignore[union-attr]


def test_cylinder_shape() -> None:
    """``A[(label)]`` -> Cylinder."""
    assert parse_mermaid("graph TD\nA[(DB)]").statements[0].shape is NodeShape.Cylinder  # type: ignore[union-attr]


def test_subroutine_shape() -> None:
    """``A[[label]]`` -> Subroutine."""
    assert parse_mermaid("graph TD\nA[[Sub]]").statements[0].shape is NodeShape.Subroutine  # type: ignore[union-attr]


def test_hexagon_shape() -> None:
    """``A{{label}}`` -> Hexagon."""
    assert parse_mermaid("graph TD\nA{{Hex}}").statements[0].shape is NodeShape.Hexagon  # type: ignore[union-attr]


def test_diamond_shape() -> None:
    """``A{label}`` -> Diamond."""
    assert parse_mermaid("graph TD\nA{Choice}").statements[0].shape is NodeShape.Diamond  # type: ignore[union-attr]


def test_asymmetric_shape() -> None:
    """``A>label]`` -> Asymmetric (no id-derivation when id is non-empty)."""
    node = parse_mermaid("graph TD\nA>Asym]").statements[0]
    assert isinstance(node, Node)
    assert node.shape is NodeShape.Asymmetric
    assert node.id == "A"
    assert node.label == "Asym"


def test_bare_alphanumeric_id_has_no_label() -> None:
    """A bare alphanumeric id -> Rectangle with ``label=None``."""
    node = parse_mermaid("graph TD\nABC").statements[0]
    assert isinstance(node, Node)
    assert node.id == "ABC"
    assert node.label is None
    assert node.shape is NodeShape.Rectangle


def test_circle_with_empty_id_derives_id_from_label() -> None:
    """``((label))`` with no id derives the id from the label's alphanumeric chars."""
    node = parse_mermaid("graph TD\n((Hello World))").statements[0]
    assert isinstance(node, Node)
    assert node.id == "HelloWorld"
    assert node.label == "Hello World"
    assert node.shape is NodeShape.Circle


# === edge styles: 6 variants x labeled / unlabeled / open-label ============


@pytest.mark.parametrize(
    "syntax,expected_style",
    [
        ("-->", EdgeStyle.Arrow),
        ("---", EdgeStyle.Line),
        ("-.->", EdgeStyle.DottedArrow),
        ("-.-", EdgeStyle.DottedLine),
        ("==>", EdgeStyle.ThickArrow),
        ("===", EdgeStyle.ThickLine),
    ],
)
def test_unlabeled_edge_styles(syntax: str, expected_style: EdgeStyle) -> None:
    """Each edge token maps to its :class:`EdgeStyle` with no label."""
    graph = parse_mermaid(f"graph TD\nA {syntax} B")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 1
    assert edges[0].style is expected_style
    assert edges[0].label is None
    assert edges[0].from_ == "A"
    assert edges[0].to == "B"


@pytest.mark.parametrize(
    "syntax,expected_style,expected_label",
    [
        ("-->|yes|", EdgeStyle.Arrow, "yes"),
        ("---|no|", EdgeStyle.Line, "no"),
        ("-.->|maybe|", EdgeStyle.DottedArrow, "maybe"),
        ("-.-|skip|", EdgeStyle.DottedLine, "skip"),
        ("==>|bold|", EdgeStyle.ThickArrow, "bold"),
        ("===|heavy|", EdgeStyle.ThickLine, "heavy"),
    ],
)
def test_labeled_edge_styles_pipe_form(
    syntax: str, expected_style: EdgeStyle, expected_label: str
) -> None:
    """The ``-->|label|`` pipe-delimited form carries a label per style."""
    graph = parse_mermaid(f"graph TD\nA {syntax} B")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert len(edges) == 1
    edge = edges[0]
    assert edge.style is expected_style
    assert edge.label == expected_label
    assert edge.from_ == "A"
    assert edge.to == "B"


def test_open_label_arrow_form() -> None:
    """The ``-- text -->`` open-label form (no pipe) carries a label."""
    graph = parse_mermaid("graph TD\nA -- yes --> B")
    edge = next(s for s in graph.statements if isinstance(s, Edge))
    assert edge.style is EdgeStyle.Arrow
    assert edge.label == "yes"


def test_open_label_thick_form() -> None:
    """The ``== text ==>`` open-label thick form carries a label."""
    edge = next(
        s for s in parse_mermaid("graph TD\nA == bold ==> B").statements if isinstance(s, Edge)
    )
    assert edge.style is EdgeStyle.ThickArrow
    assert edge.label == "bold"


def test_open_label_dotted_form() -> None:
    """The ``-. text .->`` open-label dotted form carries a label."""
    edge = next(
        s for s in parse_mermaid("graph TD\nA -. maybe .-> B").statements if isinstance(s, Edge)
    )
    assert edge.style is EdgeStyle.DottedArrow
    assert edge.label == "maybe"


# === edge chains ===========================================================


def test_edge_chain_three_nodes() -> None:
    """``A --> B --> C`` produces 3 nodes + 2 edges (nodes lead, edges follow)."""
    graph = parse_mermaid("graph TD\nA-->B-->C")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert [n.id for n in nodes] == ["A", "B", "C"]
    assert len(edges) == 2
    assert (edges[0].from_, edges[0].to) == ("A", "B")
    assert (edges[1].from_, edges[1].to) == ("B", "C")


def test_edge_chain_with_labeled_middle() -> None:
    """A labeled edge inside a chain preserves its label."""
    graph = parse_mermaid("graph TD\nA-->|yes|B-->C")
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert edges[0].label == "yes"
    assert edges[1].label is None


def test_edge_with_shaped_endpoints() -> None:
    """Shaped nodes as edge endpoints emit Node statements in the chain."""
    graph = parse_mermaid("graph TD\nA[Start]-->B[End]")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    edges = [s for s in graph.statements if isinstance(s, Edge)]
    assert nodes[0].label == "Start"
    assert nodes[1].label == "End"
    assert len(edges) == 1


# === subgraph ==============================================================


def test_subgraph_with_bracket_title() -> None:
    """``subgraph id [Title]`` -> id + bracket-delimited title."""
    graph = parse_mermaid("graph TD\nsubgraph cluster1 [My Cluster]\nA\nend")
    sub = graph.statements[0]
    assert isinstance(sub, Subgraph)
    assert sub.id == "cluster1"
    assert sub.title == "My Cluster"
    assert len(sub.statements) == 1


def test_subgraph_multi_word_title_synthesizes_id() -> None:
    """A multi-word ``subgraph`` header with no bracket id synthesizes ``subGraph{N}``."""
    graph = parse_mermaid("graph TD\nsubgraph Some Title Here\nA")
    sub = graph.statements[0]
    assert isinstance(sub, Subgraph)
    assert sub.id == "subGraph0"
    assert sub.title == "Some Title Here"


def test_subgraph_single_word_id_has_no_title() -> None:
    """A single-word ``subgraph`` header is an id with no title."""
    sub = parse_mermaid("graph TD\nsubgraph group1\nA").statements[0]
    assert isinstance(sub, Subgraph)
    assert sub.id == "group1"
    assert sub.title is None


def test_subgraph_recursive() -> None:
    """Nested ``subgraph`` blocks nest their statements recursively."""
    graph = parse_mermaid(
        "graph TD\nsubgraph outer\nsubgraph inner\nA\nend\nend"
    )
    outer = graph.statements[0]
    assert isinstance(outer, Subgraph)
    inner = outer.statements[0]
    assert isinstance(inner, Subgraph)
    assert inner.id == "inner"


def test_subgraph_optional_end() -> None:
    """A ``subgraph`` without a closing ``end`` (EOF) still parses its body."""
    sub = parse_mermaid("graph TD\nsubgraph g\nA").statements[0]
    assert isinstance(sub, Subgraph)
    assert len(sub.statements) == 1


# === style statement =======================================================


def test_style_statement_with_properties() -> None:
    """``style nodeId fill:#fff,stroke:#333`` parses node_id + property pairs."""
    graph = parse_mermaid("graph TD\nA\nstyle A fill:#fff,stroke:#333,stroke-width:2px")
    style = next(s for s in graph.statements if isinstance(s, StyleStatement))
    assert style.node_id == "A"
    assert style.properties == [
        ("fill", "#fff"),
        ("stroke", "#333"),
        ("stroke-width", "2px"),
    ]


def test_style_statement_without_properties() -> None:
    """A ``style`` with no properties yields an empty list."""
    style = next(
        s
        for s in parse_mermaid("graph TD\nA\nstyle A").statements
        if isinstance(s, StyleStatement)
    )
    assert style.node_id == "A"
    assert style.properties == []


# === label normalization ===================================================


def test_normalize_label_strips_wrapping_quotes() -> None:
    """``normalize_label`` strips one layer of matching wrapping quotes."""
    assert normalize_label('"Hello"') == "Hello"
    assert normalize_label("'Hello'") == "Hello"


def test_normalize_label_decodes_html_entities() -> None:
    """``normalize_label`` decodes the 6 HTML entities (``&amp;`` last)."""
    assert normalize_label("a &lt; b &gt; c") == "a < b > c"
    assert normalize_label("&quot;hi&quot;") == '"hi"'
    assert normalize_label("it&#39;s &apos;ok&apos;") == "it's 'ok'"
    assert normalize_label("&amp;lt;") == "&lt;"  # amp decoded last -> no double-decode


def test_normalize_label_br_variants() -> None:
    """``normalize_label`` maps ``<br>``/``<br/>``/``<BR>`` and ``\\n`` to newline."""
    assert normalize_label("line1<br>line2") == "line1\nline2"
    assert normalize_label("line1<br/>line2") == "line1\nline2"
    assert normalize_label("line1<br />line2") == "line1\nline2"
    assert normalize_label("line1<BR>line2") == "line1\nline2"
    assert normalize_label("line1\\nline2") == "line1\nline2"


def test_decode_html_entities_amp_last() -> None:
    """``&amp;`` is decoded last so ``&amp;lt;`` does not double-decode to ``<``."""
    assert decode_html_entities("&amp;lt;") == "&lt;"


def test_strip_wrapping_quotes_helper() -> None:
    """``strip_wrapping_quotes`` removes one matching layer; leaves unmatched alone."""
    assert strip_wrapping_quotes('"x"') == "x"
    assert strip_wrapping_quotes("'x'") == "x"
    assert strip_wrapping_quotes('"x') == '"x'  # unmatched -> unchanged
    assert strip_wrapping_quotes("x") == "x"


# === comment skipping ======================================================


def test_comment_lines_skipped_in_body() -> None:
    """``%%`` comment lines inside the body are skipped (not parsed as nodes)."""
    graph = parse_mermaid("graph TD\n%% a comment\nA\n%% another\nB")
    nodes = [s for s in graph.statements if isinstance(s, Node)]
    assert {n.id for n in nodes} == {"A", "B"}


# === module helper functions ===============================================


def test_is_known_mermaid_type() -> None:
    """``is_known_mermaid_type`` recognizes non-flowchart diagram types."""
    assert is_known_mermaid_type("sequenceDiagram") is True
    assert is_known_mermaid_type("pie") is True
    assert is_known_mermaid_type("graph") is False
    assert is_known_mermaid_type("flowchart") is False
    assert is_known_mermaid_type("notAType") is False


def test_first_non_empty_non_comment_line() -> None:
    """The helper skips blanks + ``%%`` comments and returns the first real line."""
    assert first_non_empty_non_comment_line("%% c\n\n  \ngraph TD") == "graph TD"
    assert first_non_empty_non_comment_line("%% only comments\n%% more") is None


# === barrel contract: parser is internal ===================================


def test_parser_module_all_is_just_the_entry() -> None:
    """``parser.py`` declares a 1-symbol ``__all__`` (its internal public surface).

    Mirrors grok's private ``mod parser;`` -- the parser is reachable by deep
    path but exposes only :func:`parse_mermaid` as its intended entry.
    """
    assert parser_mod.__all__ == ["parse_mermaid"]


def test_parse_mermaid_not_in_to_svg_barrel() -> None:
    """``parser`` is internal: :func:`parse_mermaid` is NOT re-exported via barrel.

    The ``to_svg`` barrel tracks grok's crate-root ``pub use`` surface, which
    omits ``parser`` (the crate calls ``parser::parse_mermaid`` internally,
    line 150 of ``lib.rs`` -- it is never ``pub use``-d at the crate root).
    """
    assert "parse_mermaid" not in to_svg.__all__


def test_parse_mermaid_importable_via_deep_path() -> None:
    """``parse_mermaid`` is importable as ``minimax_code.mermaid.to_svg.parser``.

    The deep path is how the future ``layout``/``renderer`` leaves will consume
    the parser (``from .parser import parse_mermaid``) -- no barrel re-export.
    """
    import importlib

    parser_deep = importlib.import_module("minimax_code.mermaid.to_svg.parser")
    assert parser_deep.parse_mermaid is parse_mermaid


def test_mermaid_root_barrel_unchanged_by_parser_leaf() -> None:
    """R272 adds an internal module; the R38 root surface stays at 24."""
    assert len(mermaid.__all__) == 24
    assert "to_svg" not in mermaid.__all__
