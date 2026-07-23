"""Mermaid flowchart parser -- fusion of Grok Build's ``mermaid-to-svg``.

Turns mermaid flowchart source (``graph TD`` / ``flowchart LR``) into the
:class:`~minimax_code.mermaid.to_svg.ast.FlowchartGraph` AST built in R271.
This is the R272 parser leaf of direction (1) -- it consumes the R271
``ast`` + ``error`` types and is the input gate for the future ``layout`` /
``svg_renderer`` leaves (without a parser the render stack has no AST to lay
out, so this is the unblocking P0).

Mirrors grok's ``mermaid-to-svg/src/parser.rs`` as a zero-semantic clone:

* the public entry :func:`parse_mermaid` rejects known non-flowchart diagram
  types (``sequenceDiagram``, ``classDiagram``, ...) with
  :class:`UnsupportedDiagramType`, then drives the internal :class:`Parser`
  state machine,
* the :class:`Parser` walks the source line-by-line, parsing the
  ``graph``/``flowchart`` declaration (direction), then statements
  (subgraphs / style / edge chains / nodes),
* node shapes follow grok's precedence order (Circle ``((..))`` through a
  bare alphanumeric id); edge syntax covers 6 labeled + 6 unlabeled styles
  plus the 3 open-label forms (``-- x -->`` / ``== x ==>`` / ``-. x .->``).

Mapping decisions
-----------------

* grok's ``Result<T, MermaidError>`` -> ``T`` or ``raise`` a R271 subclass
  (``?`` propagation -> exception propagation); ``ok_or_else(...)?`` becomes
  an explicit ``None`` check + ``raise``.
* grok's byte-index scans (``as_bytes()`` / ``starts_with``) become
  character-index scans here -- Unicode-safe (a CJK label never byte-splits)
  and the returned indices feed character slices consistently.
* grok's ``Parser<'a>`` borrow-lifetime parameter is dropped (CPython GC owns
  the strings); ``&mut self`` / ``&self`` become ``self``.
* the ``from`` field on :class:`~minimax_code.mermaid.to_svg.ast.Edge` keeps
  its R271 ``from_`` rename (``from`` is a Python keyword).
* grok's ``edge_patterns`` / ``open_patterns`` / ``PATTERNS`` consts and the
  known-type ``matches!`` table hoist to module-level immutable tuples / a
  frozenset (grok rebuilds nothing per call either -- they are static).

Like grok's ``mod parser;`` (private) + the crate-internal
``parser::parse_mermaid`` call site (the crate root never ``pub use``s it --
see ``lib.rs`` line 150), this module is **internal**: it declares its own
``__all__`` (the entry function) but is NOT re-exported through the
``to_svg`` barrel. The future ``layout``/``renderer`` leaves consume it via
the deep path (``from .parser import parse_mermaid``).
"""

from __future__ import annotations

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
from minimax_code.mermaid.to_svg.error import (
    InvalidDirection,
    ParseError,
    UnsupportedDiagramType,
)

__all__ = ["parse_mermaid"]


# Edge-token scan order for :meth:`Parser._find_edge_start` (longest first so
# ``-.->`` wins over ``-.-``). Mirrors grok's 9-entry ``PATTERNS`` const.
_EDGE_START_PATTERNS: tuple[str, ...] = (
    "-.->",
    "-.-",
    "-->",
    "---",
    "==>",
    "===",
    "--",
    "==",
    "-.",
)

# 12 labeled/unlabeled edge syntaxes for :meth:`Parser._parse_edge_syntax`.
# Each tuple is (prefix, style, label-end-marker) -- the labeled forms carry a
# ``|`` end marker, the unlabeled forms carry an empty marker.
_LABELED_EDGE_PATTERNS: tuple[tuple[str, EdgeStyle, str], ...] = (
    ("-->|", EdgeStyle.Arrow, "|"),
    ("---|", EdgeStyle.Line, "|"),
    ("-.->|", EdgeStyle.DottedArrow, "|"),
    ("-.-|", EdgeStyle.DottedLine, "|"),
    ("==>|", EdgeStyle.ThickArrow, "|"),
    ("===|", EdgeStyle.ThickLine, "|"),
    ("-->", EdgeStyle.Arrow, ""),
    ("---", EdgeStyle.Line, ""),
    ("-.->", EdgeStyle.DottedArrow, ""),
    ("-.-", EdgeStyle.DottedLine, ""),
    ("==>", EdgeStyle.ThickArrow, ""),
    ("===", EdgeStyle.ThickLine, ""),
)

# 3 open-label edge forms (``-- x -->`` etc.). Each entry pairs an opener with
# its candidate closers; the earliest closer wins, ties broken by longer length.
_OPEN_EDGE_PATTERNS: tuple[tuple[str, tuple[tuple[str, EdgeStyle], ...]], ...] = (
    ("--", (("-->", EdgeStyle.Arrow), ("---", EdgeStyle.Line))),
    ("==", (("==>", EdgeStyle.ThickArrow), ("===", EdgeStyle.ThickLine))),
    ("-.", ((".->", EdgeStyle.DottedArrow), (".-", EdgeStyle.DottedLine))),
)

# Known non-flowchart mermaid diagram types. When the first source token is one
# of these (and not ``graph``/``flowchart``), the parser refuses with
# ``UnsupportedDiagramType`` instead of mis-parsing it as a flowchart.
_KNOWN_MERMAID_TYPES: frozenset[str] = frozenset({
    "sequenceDiagram",
    "classDiagram",
    "classDiagram-v2",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "info",
    "kanban",
    "gitGraph",
    "requirementDiagram",
    "C4Context",
    "C4Container",
    "C4Component",
    "C4Dynamic",
    "C4Deployment",
    "sankey-beta",
    "packet-beta",
    "xychart-beta",
    "radar-beta",
    "block-beta",
    "flowchart-elk",
    "quadrantChart",
})


def parse_mermaid(input: str) -> FlowchartGraph:
    """Parse mermaid flowchart source into a :class:`FlowchartGraph` AST.

    Refuses known non-flowchart diagram types (raising
    :class:`UnsupportedDiagramType`) so the render stack's dispatcher -- not
    this parser -- owns those diagram types. Leading blank/``%%`` comment
    lines are skipped before the first-token check.
    """
    first_line = first_non_empty_non_comment_line(input)
    if first_line is not None:
        parts = first_line.split()
        first_token = parts[0] if parts else ""
        if (
            first_token != "graph"
            and first_token != "flowchart"
            and is_known_mermaid_type(first_token)
        ):
            raise UnsupportedDiagramType(first_token)
    parser = Parser(input)
    return parser.parse()


def normalize_label(label: str) -> str:
    """Normalize a node/edge label: strip wrapping quotes, decode HTML entities,
    then map ``\\n`` and ``<br>`` variants to a real newline.
    """
    label = strip_wrapping_quotes(label.strip())
    return (
        decode_html_entities(label)
        .replace("\\n", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
        .replace("<br>", "\n")
        .replace("<BR/>", "\n")
        .replace("<BR />", "\n")
        .replace("<BR>", "\n")
    )


def decode_html_entities(label: str) -> str:
    """Decode the six HTML entities mermaid labels commonly carry.

    ``&amp;`` is decoded last so an entity like ``&amp;lt;`` does not
    double-decode into ``<``.
    """
    return (
        label.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def strip_wrapping_quotes(label: str) -> str:
    """Strip one layer of matching wrapping ``"`` or ``'`` quotes.

    Mirrors grok's byte-level check (``len >= 2`` + matching first/last byte)
    at the character level.
    """
    if len(label) >= 2 and (
        (label[0] == '"' and label[-1] == '"')
        or (label[0] == "'" and label[-1] == "'")
    ):
        return label[1:-1]
    return label


def first_non_empty_non_comment_line(input: str) -> str | None:
    """Return the first trimmed non-empty line that is not a ``%%`` comment."""
    for line in input.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped
    return None


def is_known_mermaid_type(token: str) -> bool:
    """True when ``token`` names a (non-flowchart) mermaid diagram type."""
    return token in _KNOWN_MERMAID_TYPES


class Parser:
    """Line-by-line mermaid flowchart parser state machine.

    Mirrors grok's ``Parser<'a>`` struct. The lifetime parameter is dropped
    (Python GC owns the strings); grok's byte-index scans become character-
    index scans here (Unicode-safe, consistent with the character slicing the
    returned indices feed).
    """

    def __init__(self, input: str) -> None:
        self._lines: list[str] = input.splitlines()
        self._current_line: int = 0
        self._next_subgraph_index: int = 0

    def parse(self) -> FlowchartGraph:
        """Parse the full graph: declaration first, then statements."""
        direction = self._parse_graph_declaration()
        statements = self._parse_statements()
        return FlowchartGraph(direction=direction, statements=statements)

    def _current_line_content(self) -> str | None:
        """Current line trimmed, or ``None`` past the end of input."""
        if self._current_line < len(self._lines):
            return self._lines[self._current_line].strip()
        return None

    def _advance(self) -> None:
        self._current_line += 1

    def _skip_empty_lines(self) -> None:
        """Advance past blank lines and ``%%`` comments."""
        while True:
            line = self._current_line_content()
            if line is None:
                break
            if line == "" or line.startswith("%%"):
                self._advance()
            else:
                break

    def _parse_graph_declaration(self) -> GraphDirection:
        """Parse the leading ``graph TD`` / ``flowchart LR`` line."""
        self._skip_empty_lines()
        line = self._current_line_content()
        if line is None:
            raise ParseError(self._current_line + 1, "Expected graph declaration")
        if line.startswith("graph ") or line.startswith("flowchart "):
            parts = line.split()
            if len(parts) < 2:
                raise ParseError(
                    self._current_line + 1,
                    "Expected direction after 'graph' or 'flowchart'",
                )
            direction = self._parse_direction(parts[1])
        else:
            raise ParseError(
                self._current_line + 1,
                "Expected 'graph' or 'flowchart' declaration",
            )
        self._advance()
        return direction

    def _parse_direction(self, direction: str) -> GraphDirection:
        """Map a direction token (case-insensitive) to a :class:`GraphDirection`."""
        match direction.upper():
            case "TD" | "TB":
                return GraphDirection.TopToBottom
            case "BT":
                return GraphDirection.BottomToTop
            case "LR":
                return GraphDirection.LeftToRight
            case "RL":
                return GraphDirection.RightToLeft
        raise InvalidDirection(direction)

    def _parse_statements(self) -> list[Statement]:
        """Parse statements until end-of-input or a closing ``end``."""
        statements: list[Statement] = []
        while self._current_line_content() is not None:
            self._skip_empty_lines()
            line = self._current_line_content()
            if line is None:
                break
            if line == "":
                self._advance()
                continue
            if line == "end":
                break
            if line.startswith("subgraph "):
                statements.append(self._parse_subgraph())
            elif line.startswith("style "):
                statements.append(self._parse_style())
            elif self._line_contains_edge(line):
                statements.extend(self._parse_edge_chain(line))
                self._advance()
            else:
                node = self._try_parse_node(line)
                if node is not None:
                    statements.append(node)
                self._advance()
        return statements

    def _line_contains_edge(self, line: str) -> bool:
        return self._find_edge_start(line) is not None

    def _parse_edge_chain(self, line: str) -> list[Statement]:
        """Parse an edge chain (``A --> B --> C``) into node + edge statements.

        Nodes lead the returned list (mirrors grok's
        ``filter_map(...).append(statements)``), edges follow, each edge links
        the previous node id to the next.
        """
        statements: list[Statement] = []
        remaining = line.strip()
        collected_nodes: list[tuple[str, Node | None]] = []

        first_node_end = self._find_edge_start(remaining)
        if first_node_end is None:
            first_node_end = len(remaining)
        first_node_str = remaining[:first_node_end].strip()
        first_node = self._try_parse_node(first_node_str)
        if first_node is not None:
            collected_nodes.append((first_node.id, first_node))
        else:
            collected_nodes.append((self._extract_node_id(first_node_str), None))
        remaining = remaining[first_node_end:]

        while remaining != "":
            edge_style, label, edge_len = self._parse_edge_syntax(remaining)
            remaining = remaining[edge_len:].lstrip()

            next_node_end = self._find_edge_start(remaining)
            if next_node_end is None:
                next_node_end = len(remaining)
            next_node_str = remaining[:next_node_end].strip()
            if next_node_str == "":
                break

            next_node = self._try_parse_node(next_node_str)
            if next_node is not None:
                next_id = next_node.id
                next_node_value: Node | None = next_node
            else:
                next_id = self._extract_node_id(next_node_str)
                next_node_value = None

            if collected_nodes:
                from_id = collected_nodes[-1][0]
                statements.append(
                    Edge(
                        from_=from_id,
                        to=next_id,
                        label=label,
                        style=edge_style,
                    )
                )
            collected_nodes.append((next_id, next_node_value))
            remaining = remaining[next_node_end:]

        node_statements = [node for _, node in collected_nodes if node is not None]
        return node_statements + statements

    def _find_edge_start(self, s: str) -> int | None:
        """Character index of the first edge token, ignoring tokens inside
        bracket/quote-delimited labels (``[..]`` / ``(..)`` / ``{..}`` / ``".."``).

        ``depth`` tracks nesting; ``in_quote`` skips quoted spans. Returns
        ``None`` when no edge token sits at depth 0.
        """
        depth = 0
        in_quote = False
        for i, ch in enumerate(s):
            if in_quote:
                if ch == '"':
                    in_quote = False
                continue
            if ch == '"':
                in_quote = True
            elif ch in "[({":
                depth += 1
            elif ch in "])}":
                if depth > 0:
                    depth -= 1
            elif depth == 0:
                for pattern in _EDGE_START_PATTERNS:
                    if s[i:].startswith(pattern):
                        return i
        return None

    def _parse_edge_syntax(self, s: str) -> tuple[EdgeStyle, str | None, int]:
        """Parse one edge token at the start of ``s``.

        Returns ``(style, optional label, consumed length)``. Tries the 12
        labeled/unlabeled prefixes first, then the 3 open-label forms.
        """
        s = s.lstrip()

        for pattern, style, label_end in _LABELED_EDGE_PATTERNS:
            if not s.startswith(pattern):
                continue
            if label_end:
                end_idx = s.find(label_end, len(pattern))
                if end_idx != -1:
                    label = normalize_label(s[len(pattern):end_idx])
                    return (style, label, end_idx + len(label_end))
            else:
                return (style, None, len(pattern))

        for opener, closers in _OPEN_EDGE_PATTERNS:
            if not s.startswith(opener):
                continue
            after = s[len(opener):]
            best: tuple[int, str, EdgeStyle] | None = None
            for closer, style in closers:
                idx = after.find(closer)
                if idx == -1:
                    continue
                if best is None or idx < best[0] or (
                    idx == best[0] and len(closer) > len(best[1])
                ):
                    best = (idx, closer, style)
            if best is not None:
                idx, closer, style = best
                label = normalize_label(after[:idx])
                return (style, label, len(opener) + idx + len(closer))

        raise ParseError(self._current_line + 1, f"Invalid edge syntax: {s}")

    def _extract_node_id(self, s: str) -> str:
        """Extract a node id: text before the first bracket char."""
        s = s.strip()
        for open_ch in ("[", "(", "{", "<"):
            idx = s.find(open_ch)
            if idx != -1:
                return s[:idx].strip()
        return s

    def _try_parse_node(self, s: str) -> Node | None:
        """Try to parse ``s`` as a node literal (one of 10 shapes), else ``None``.

        Shape precedence mirrors grok exactly: Circle ``((..))``, Stadium
        ``([..])``, Cylinder ``[(..)]``, Subroutine ``[[..]]``, Hexagon
        ``{{..}}``, Rectangle ``[..]``, RoundedRectangle ``(..)`` (excluding
        ``))``), Diamond ``{..}`` (excluding ``}}``), Asymmetric ``>..]``,
        then a bare alphanumeric id. When the id is empty, it is derived from
        the label's alphanumeric characters (grok behavior).
        """
        s = s.strip()
        if s == "":
            return None

        # Six exact bracket-pair shapes (opener/closer known precisely).
        for opener, closer, shape in (
            ("((", "))", NodeShape.Circle),
            ("([", "])", NodeShape.Stadium),
            ("[(", ")]", NodeShape.Cylinder),
            ("[[", "]]", NodeShape.Subroutine),
            ("{{", "}}", NodeShape.Hexagon),
            ("[", "]", NodeShape.Rectangle),
        ):
            start = s.find(opener)
            if start != -1 and s.endswith(closer):
                return self._make_node(s, start, len(opener), len(closer), shape)

        # Rounded rectangle: (x) but not the circle form ((x)).
        paren_start = s.find("(")
        if paren_start != -1 and s.endswith(")") and not s.endswith("))"):
            return self._make_node(s, paren_start, 1, 1, NodeShape.RoundedRectangle)

        # Diamond: {x} but not the hexagon form {{x}}.
        brace_start = s.find("{")
        if brace_start != -1 and s.endswith("}") and not s.endswith("}}"):
            return self._make_node(s, brace_start, 1, 1, NodeShape.Diamond)

        # Asymmetric: id>label] (no id-derivation, mirrors grok).
        if ">" in s and s.endswith("]"):
            gt_idx = s.find(">")
            id_ = s[:gt_idx].strip()
            label = normalize_label(s[gt_idx + 1:len(s) - 1])
            return Node(id=id_, label=label, shape=NodeShape.Asymmetric)

        # Bare alphanumeric + underscore id, no label.
        if all(c.isalnum() or c == "_" for c in s):
            return Node(id=s, label=None, shape=NodeShape.Rectangle)

        return None

    @staticmethod
    def _make_node(
        s: str,
        opener_start: int,
        opener_len: int,
        closer_len: int,
        shape: NodeShape,
    ) -> Node:
        """Build a node from ``s`` given the opener starts at ``opener_start``.

        ``id`` is the text before ``opener_start`` (trimmed); ``label`` sits
        between the opener and the trailing closer (``closer_len`` chars).
        When the id is empty, derive it from the label's alphanumeric chars.
        """
        id_ = s[:opener_start].strip()
        label = normalize_label(s[opener_start + opener_len:len(s) - closer_len])
        if id_ == "":
            id_ = "".join(c for c in label if c.isalnum())
        return Node(id=id_, label=label, shape=shape)

    def _parse_subgraph(self) -> Subgraph:
        """Parse a ``subgraph`` header, its nested statements, and optional ``end``."""
        line = self._current_line_content()
        if line is None:
            raise ParseError(self._current_line + 1, "Expected subgraph")

        after_keyword = line.removeprefix("subgraph ").strip()

        bracket_start = after_keyword.find("[")
        if bracket_start != -1:
            # A ``[`` is present: title-in-brackets form if it closes, else the
            # whole tail is the id with no title (grok's nested-if fall-through).
            if after_keyword.endswith("]"):
                id_ = after_keyword[:bracket_start].strip()
                title = normalize_label(
                    after_keyword[bracket_start + 1:len(after_keyword) - 1]
                )
                sub_id, sub_title = id_, title
            else:
                sub_id, sub_title = after_keyword, None
        elif len(after_keyword.split()) > 1:
            # Multi-word title with no explicit id: synthesize ``subGraph{N}``.
            sub_id = f"subGraph{self._next_subgraph_index}"
            self._next_subgraph_index += 1
            sub_title = normalize_label(after_keyword)
        else:
            # Single-word id, no title.
            sub_id = after_keyword
            sub_title = None

        self._advance()
        statements = self._parse_statements()
        if self._current_line_content() == "end":
            self._advance()
        return Subgraph(id=sub_id, title=sub_title, statements=statements)

    def _parse_style(self) -> StyleStatement:
        """Parse a ``style nodeId fill:#fff,stroke:#333`` line."""
        line = self._current_line_content()
        if line is None:
            raise ParseError(self._current_line + 1, "Expected style statement")

        after_keyword = line.removeprefix("style ").strip()
        parts = after_keyword.split(" ", 1)
        node_id = parts[0]
        if len(parts) > 1:
            properties: list[tuple[str, str]] = []
            for prop in parts[1].split(","):
                kv = prop.split(":", 1)
                if len(kv) == 2:
                    properties.append((kv[0].strip(), kv[1].strip()))
        else:
            properties = []

        self._advance()
        return StyleStatement(node_id=node_id, properties=properties)
