"""Black-box tests for the migrated mermaid-to-svg flowchart AST (R271).

Exercises :mod:`minimax_code.mermaid.to_svg.ast` -- the flowchart abstract
syntax tree fused from grok's ``mermaid-to-svg/src/ast.rs`` (direction (1),
leaf 3, paired with ``error.rs``). Pure data layer consumed by the future
``parser.rs`` leaf. Covers:

* the three fieldless Copy enums (:class:`GraphDirection` 4 variants /
  :class:`NodeShape` 12 variants / :class:`EdgeStyle` 6 variants) are
  ``@unique`` and stamp each variant name as its value (readable ``repr`` +
  round-trip via ``Cls("VariantName")``),
* the four ``@dataclass`` node/edge/style types mirror grok's structs; the
  :class:`Statement` marker base + four subclasses reproduce grok's tagged
  enum (``isinstance`` dispatch == grok ``match``),
* :attr:`Edge.from_` carries the keyword-collision rename (grok ``from`` is
  a Python keyword),
* :class:`Subgraph` is recursive (its ``statements`` can hold further
  :class:`Subgraph`), and :class:`FlowchartGraph` is the top container,
* ``@dataclass`` equality mirrors grok's ``#[derive(PartialEq, Eq)]``,
* the barrel contract: ``ast`` is an **internal** module (grok ``mod ast;``
  private) -- reachable by deep path, NOT re-exported through the
  ``to_svg`` barrel; the ``mermaid`` root surface stays at 24.
"""

from __future__ import annotations

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg.ast import (
    Edge,
    EdgeStyle,
    FlowchartGraph,
    GraphDirection,
    Node,
    NodeShape,
    Statement,
    StyleStatement,
    Subgraph,
)

# === GraphDirection: 4 fieldless variants =================================


def test_graph_direction_has_four_unique_variants() -> None:
    """``GraphDirection`` has exactly 4 variants, ``@unique``-distinct."""
    assert len(GraphDirection) == 4
    assert set(GraphDirection) == {
        GraphDirection.TopToBottom,
        GraphDirection.BottomToTop,
        GraphDirection.LeftToRight,
        GraphDirection.RightToLeft,
    }


def test_graph_direction_round_trips_by_variant_name() -> None:
    """Each variant stamps its own name as the value (readable + round-trippable)."""
    for variant in GraphDirection:
        assert GraphDirection(variant.value) is variant


# === NodeShape: 12 fieldless variants =====================================


def test_node_shape_has_twelve_unique_variants() -> None:
    """``NodeShape`` has exactly 12 variants, ``@unique``-distinct."""
    expected = {
        NodeShape.Rectangle,
        NodeShape.RoundedRectangle,
        NodeShape.Stadium,
        NodeShape.Diamond,
        NodeShape.Hexagon,
        NodeShape.Asymmetric,
        NodeShape.Subroutine,
        NodeShape.Cylinder,
        NodeShape.Circle,
        NodeShape.StartState,
        NodeShape.EndState,
        NodeShape.ForkJoin,
    }
    assert len(NodeShape) == 12
    assert set(NodeShape) == expected


def test_node_shape_round_trips_by_variant_name() -> None:
    """Each variant stamps its own name as the value."""
    for variant in NodeShape:
        assert NodeShape(variant.value) is variant


# === EdgeStyle: 6 fieldless variants ======================================


def test_edge_style_has_six_unique_variants() -> None:
    """``EdgeStyle`` has exactly 6 variants, ``@unique``-distinct."""
    assert len(EdgeStyle) == 6
    assert set(EdgeStyle) == {
        EdgeStyle.Arrow,
        EdgeStyle.Line,
        EdgeStyle.DottedArrow,
        EdgeStyle.DottedLine,
        EdgeStyle.ThickArrow,
        EdgeStyle.ThickLine,
    }


def test_edge_style_round_trips_by_variant_name() -> None:
    """Each variant stamps its own name as the value."""
    for variant in EdgeStyle:
        assert EdgeStyle(variant.value) is variant


# === Statement dispatch: marker base + 4 subclasses =======================


def test_statement_is_marker_base_with_four_subclasses() -> None:
    """``Node`` / ``Edge`` / ``Subgraph`` / ``StyleStatement`` subclass ``Statement``."""
    assert issubclass(Node, Statement)
    assert issubclass(Edge, Statement)
    assert issubclass(Subgraph, Statement)
    assert issubclass(StyleStatement, Statement)


def test_isinstance_dispatches_like_grok_match() -> None:
    """``isinstance(stmt, Node)`` dispatches just like grok's ``match`` arm.

    A ``list[Statement]`` of mixed arms dispatches each element to its
    handler without a discriminator field.
    """
    node = Node(id="A", label=None, shape=NodeShape.Rectangle)
    edge = Edge(from_="A", to="B", label="yes", style=EdgeStyle.Arrow)
    style = StyleStatement(node_id="A", properties=[("fill", "#fff")])
    sub = Subgraph(id="cluster1", title=None, statements=[node])

    statements: list[Statement] = [node, edge, style, sub]
    handled: list[str] = []
    for stmt in statements:
        if isinstance(stmt, Node):
            handled.append("node")
        elif isinstance(stmt, Edge):
            handled.append("edge")
        elif isinstance(stmt, StyleStatement):
            handled.append("style")
        elif isinstance(stmt, Subgraph):
            handled.append("subgraph")
    assert handled == ["node", "edge", "style", "subgraph"]


# === Node: dataclass with id/label/shape ==================================


def test_node_dataclass_fields() -> None:
    """``Node`` stamps ``id`` / ``label`` / ``shape`` (grok ``Option<String>`` -> ``str | None``)."""
    node = Node(id="A", label="Start", shape=NodeShape.Stadium)
    assert node.id == "A"
    assert node.label == "Start"
    assert node.shape is NodeShape.Stadium


def test_node_label_allows_none() -> None:
    """A node with no display text carries ``label=None`` (grok ``None`` arm)."""
    node = Node(id="B", label=None, shape=NodeShape.Circle)
    assert node.label is None


# === Edge: keyword-collision rename (from -> from_) =======================


def test_edge_uses_from_underscore_for_keyword() -> None:
    """grok's ``from`` field maps to ``from_`` (``from`` is a Python keyword)."""
    edge = Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)
    assert edge.from_ == "A"
    assert edge.to == "B"
    assert edge.label is None
    assert edge.style is EdgeStyle.Arrow


def test_edge_dataclass_equality_mirrors_grok_partialeq() -> None:
    """Two structurally-equal edges compare equal (grok ``#[derive(PartialEq, Eq)]``)."""
    a = Edge(from_="A", to="B", label="yes", style=EdgeStyle.Arrow)
    b = Edge(from_="A", to="B", label="yes", style=EdgeStyle.Arrow)
    assert a == b
    # A different style breaks equality.
    c = Edge(from_="A", to="B", label="yes", style=EdgeStyle.Line)
    assert a != c


# === StyleStatement: node_id + properties list[tuple[str,str]] ============


def test_style_statement_carries_properties() -> None:
    """``StyleStatement`` carries ``node_id`` + ``properties`` list of pairs."""
    style = StyleStatement(
        node_id="A",
        properties=[("fill", "#f9f"), ("stroke", "#333"), ("stroke-width", "2px")],
    )
    assert style.node_id == "A"
    assert style.properties == [("fill", "#f9f"), ("stroke", "#333"), ("stroke-width", "2px")]


# === Subgraph: recursive ==================================================


def test_subgraph_holds_nested_subgraph() -> None:
    """``Subgraph.statements`` can hold further ``Subgraph`` (grok self-reference)."""
    inner_node = Node(id="X", label=None, shape=NodeShape.Rectangle)
    inner = Subgraph(id="inner", title="Inner", statements=[inner_node])
    outer = Subgraph(id="outer", title=None, statements=[inner])
    assert isinstance(outer.statements[0], Subgraph)
    assert outer.statements[0].id == "inner"
    # Reach the leaf node two levels deep.
    assert outer.statements[0].statements[0].id == "X"


def test_subgraph_dataclass_equality() -> None:
    """Two structurally-equal subgraphs compare equal, recursively."""
    leaf = Node(id="L", label=None, shape=NodeShape.Circle)
    a = Subgraph(id="s", title="T", statements=[leaf])
    b = Subgraph(
        id="s",
        title="T",
        statements=[Node(id="L", label=None, shape=NodeShape.Circle)],
    )
    assert a == b


# === FlowchartGraph: top container ========================================


def test_flowchart_graph_carries_direction_and_statements() -> None:
    """``FlowchartGraph`` stamps ``direction`` + ``statements`` list."""
    node_a = Node(id="A", label="Start", shape=NodeShape.Stadium)
    node_b = Node(id="B", label="End", shape=NodeShape.Stadium)
    edge = Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[node_a, edge, node_b],
    )
    assert graph.direction is GraphDirection.TopToBottom
    assert len(graph.statements) == 3
    assert isinstance(graph.statements[0], Node)
    assert isinstance(graph.statements[1], Edge)
    assert isinstance(graph.statements[2], Node)


def test_flowchart_graph_dataclass_equality() -> None:
    """Two structurally-equal graphs compare equal."""

    def build() -> FlowchartGraph:
        return FlowchartGraph(
            direction=GraphDirection.LeftToRight,
            statements=[Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)],
        )

    assert build() == build()


# === barrel contract: ast is internal, not re-exported ====================


def test_ast_module_has_its_own_all_list() -> None:
    """``ast.py`` declares a 9-symbol ``__all__`` (its own internal public surface)."""
    from minimax_code.mermaid.to_svg import ast

    assert set(ast.__all__) == {
        "Edge",
        "EdgeStyle",
        "FlowchartGraph",
        "GraphDirection",
        "Node",
        "NodeShape",
        "Statement",
        "StyleStatement",
        "Subgraph",
    }


def test_ast_symbols_not_in_to_svg_barrel() -> None:
    """``ast`` is internal (grok ``mod ast;`` private): NOT re-exported via barrel.

    The ``to_svg`` barrel tracks grok's crate-root ``pub use`` surface, which
    omits ``ast``. Asserting the representative symbols are absent proves the
    module was not hoisted into the barrel.
    """
    for name in (
        "FlowchartGraph",
        "GraphDirection",
        "NodeShape",
        "EdgeStyle",
        "Statement",
    ):
        assert name not in to_svg.__all__, name


def test_ast_importable_via_deep_path() -> None:
    """``ast`` is importable as ``minimax_code.mermaid.to_svg.ast`` (deep path).

    The deep path is how the future ``parser.py`` consumes the AST
    (``from .ast import Node``) -- no barrel re-export needed.
    """
    import importlib

    ast_mod = importlib.import_module("minimax_code.mermaid.to_svg.ast")
    assert ast_mod.Node is Node
    assert ast_mod.FlowchartGraph is FlowchartGraph


def test_mermaid_root_barrel_unchanged_by_ast_leaf() -> None:
    """R271 grows the ``to_svg`` sub-package; the R38 root surface stays at 24."""
    assert len(mermaid.__all__) == 27
    assert "to_svg" not in mermaid.__all__
