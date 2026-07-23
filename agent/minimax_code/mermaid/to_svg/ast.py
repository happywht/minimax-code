"""Mermaid-to-svg flowchart AST -- fusion of Grok Build's ``mermaid-to-svg/src/ast.rs``.

Direction (1) leaf 3 (type-foundation pair with ``error.rs``). Mirrors grok's
``ast.rs`` -- the flowchart abstract syntax tree that ``parser.rs`` produces
from mermaid source. Pure data layer, zero non-stdlib dependency.

Type mapping (grok struct/enum -> Python)
-----------------------------------------

* ``#[derive(Debug, Clone, PartialEq, Eq)]`` struct -> ``@dataclass``
* ``#[derive(..., Copy)]`` fieldless enum -> ``@unique enum.Enum`` (singleton
  semantics; grok fieldless variants carry no value, so each Python variant
  stamps its own name as the value for readable ``repr`` + round-trip via
  ``Cls("VariantName")``)
* ``Option<String>`` -> ``str | None`` (UP007)
* ``String`` -> ``str``
* ``Vec<T>`` -> ``list[T]``

Statement dispatch
------------------

grok's ``enum Statement { Node(Node), Edge(Edge), Subgraph(Subgraph),
Style(StyleStatement) }`` maps to a marker base class :class:`Statement` with
four dataclass subclasses. grok dispatches via
``match stmt { Statement::Node(n) => ... }``; Python dispatches via
``isinstance(stmt, Node)`` -- semantically identical -- and ``list[Statement]``
type-checks on :attr:`FlowchartGraph.statements` / :attr:`Subgraph.statements`.

:class:`Subgraph` is recursive: its ``statements`` field can hold further
``Subgraph`` instances (mirrors grok's self-referential enum, where
``Statement::Subgraph`` wraps a struct carrying ``Vec<Statement>``).
``from __future__ import annotations`` keeps every annotation a string at
class-definition time, so the forward reference resolves cleanly.

Internal module
---------------

grok declares ``mod ast;`` (private) -- the AST is consumed by ``parser.rs``,
not re-exported at the crate root (only ``config`` / ``error::MermaidError`` /
``theme`` are ``pub use``-d). This module mirrors that: it has its own
``__all__`` but is NOT re-exported through the ``to_svg`` barrel (the barrel
tracks grok's crate-root public surface, which omits ``ast``). Future
``parser.py`` consumes it via ``from .ast import ...``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique


@unique
class GraphDirection(Enum):
    """Flowchart layout direction (grok ``enum GraphDirection``, Copy, 4 variants).

    grok's enum is fieldless; Python ``Enum`` requires a value, so each variant
    carries its own name (readable + round-trippable via
    ``GraphDirection("TopToBottom")``). The mermaid wire abbreviation
    (``TB`` / ``BT`` / ``LR`` / ``RL``) is NOT baked in here -- that mapping is
    the parser's job (YAGNI until ``parser.rs`` migrates).
    """

    TopToBottom = "TopToBottom"
    BottomToTop = "BottomToTop"
    LeftToRight = "LeftToRight"
    RightToLeft = "RightToLeft"


@unique
class NodeShape(Enum):
    """Node geometry (grok ``enum NodeShape``, Copy, 12 variants)."""

    Rectangle = "Rectangle"
    RoundedRectangle = "RoundedRectangle"
    Stadium = "Stadium"
    Diamond = "Diamond"
    Hexagon = "Hexagon"
    Asymmetric = "Asymmetric"
    Subroutine = "Subroutine"
    Cylinder = "Cylinder"
    Circle = "Circle"
    StartState = "StartState"
    EndState = "EndState"
    ForkJoin = "ForkJoin"


@unique
class EdgeStyle(Enum):
    """Edge line style (grok ``enum EdgeStyle``, Copy, 6 variants)."""

    Arrow = "Arrow"
    Line = "Line"
    DottedArrow = "DottedArrow"
    DottedLine = "DottedLine"
    ThickArrow = "ThickArrow"
    ThickLine = "ThickLine"


@dataclass
class Statement:
    """Marker base for flowchart statements (mirrors grok ``enum Statement``).

    The four subclasses (:class:`Node` / :class:`Edge` / :class:`Subgraph` /
    :class:`StyleStatement`) are the variant arms; ``isinstance(stmt, Node)``
    dispatches just like grok's ``match stmt { Statement::Node(n) => ... }``.
    """


@dataclass
class Node(Statement):
    """A flowchart node (grok ``struct Node``, variant ``Statement::Node``)."""

    id: str
    label: str | None
    shape: NodeShape


@dataclass
class Edge(Statement):
    """A flowchart edge (grok ``struct Edge``, variant ``Statement::Edge``).

    grok's ``from`` field maps to ``from_`` -- ``from`` is a Python keyword and
    cannot be an attribute name. The trailing underscore is PEP 8's idiom for
    a keyword collision.
    """

    from_: str
    to: str
    label: str | None
    style: EdgeStyle


@dataclass
class StyleStatement(Statement):
    """A ``style`` directive (grok ``struct StyleStatement``, variant ``Statement::Style``)."""

    node_id: str
    properties: list[tuple[str, str]]


@dataclass
class Subgraph(Statement):
    """A ``subgraph`` block (grok ``struct Subgraph``, variant ``Statement::Subgraph``).

    Recursive: ``statements`` can hold further :class:`Subgraph` instances.
    """

    id: str
    title: str | None
    statements: list[Statement]


@dataclass
class FlowchartGraph:
    """The parsed flowchart (grok ``struct FlowchartGraph``)."""

    direction: GraphDirection
    statements: list[Statement]


__all__ = [
    "Edge",
    "EdgeStyle",
    "FlowchartGraph",
    "GraphDirection",
    "Node",
    "NodeShape",
    "Statement",
    "StyleStatement",
    "Subgraph",
]
