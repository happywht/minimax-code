"""Black-box + white-box tests for the migrated mindmap renderer (R292).

Exercises :mod:`minimax_code.mermaid.to_svg.mindmap_diagram` -- the 14th
per-diagram leaf and 13th self-contained SVG emitter of the R269--R291 stack
(direction (1) brick 23). Mirrors grok ``mindmap_diagram.rs`` (670 lines): an
indent-stack parser that builds a node tree, assigns each root branch a
section colour from the 8-hue palette, lays the tree out radially around a
central root, and emits an SVG with quadratic-bezier edges, six node shapes,
and underline decoration.

Coverage groups:

* Dispatch smoke -- the ``mindmap`` token routes through the dedicated arm;
  the SVG header carries ``aria-roledescription="mindmap"`` and NO
  ``id="my-svg"`` (mindmap's header diverges from flowchart / gitGraph).
* Parse rules -- indent-stack collapsing, root Default->Circle coercion,
  ``::icon(...)`` decoration skipping, ``%%`` / blank-line skipping.
* Parse errors -- missing header, empty body (line 0), comments-only.
* Label/shape extraction -- the six delimiter shapes + Default fallback
  (order-sensitive: ``((`` / ``{{`` / ``))`` tested before ``[`` / ``(``).
* Section assignment -- root carries ``None``; direct children carry their
  child index; deeper descendants inherit their ancestor's section.
* Node sizing -- the four measure branches (circle / rect / hexagon / bang)
  with exact pixel math (``display_width_units * 8.5`` text width).
* Radial layout -- subtree_weight (leaf=1, internal=max(sum,1)) + root at
  origin pre-normalize + normalize shift into the positive quadrant.
* Palette -- the 8-hue section table (section 0 lightness 73.53%, sections
  1-7 lightness 76.27%), wrap mod 8, section-2 white text, edge == fill.
* Six shapes -- circle / rect (rx=0) / rounded-rect+default (rx=5) /
  hexagon (polygon) / bang (ellipse).
* Underline decoration -- non-root, non-circle nodes carry a 3-px ``<line>``.
* Edges -- quadratic bezier (``M ... Q ... ...``), stroke fades with depth
  (``max(17 - 3*depth, 2)``), ``fill="none"``.
* html_escape -- the ``&quot;`` variant (``'`` is NOT escaped, unlike the
  gitGraph / journey ``&apos;`` variant).
* Theme awareness -- 0 channels (grok hard-codes the palette; light == dark).
* Helpers -- ``_fmt`` (Rust f64 Display bridge).
* Public surface -- the single-symbol ``__all__`` + dispatch removes mindmap
  from the unsupported set (11 -> 10 tokens).
"""

from __future__ import annotations

import re

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.mindmap_diagram import (
    _assign_sections,
    _escape_xml,
    _extract_label_and_type,
    _fmt,
    _layout_mindmap,
    _measure_node,
    _MindmapNode,
    _NodeType,
    _parse_mindmap_tree,
    _section_edge,
    _section_fill,
    _section_text,
    _subtree_weight,
    render_mindmap_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_mindmap_dispatches_to_renderer() -> None:
    """A ``mindmap`` block routes through the dedicated renderer arm."""
    svg = render_mermaid_to_svg("mindmap\n  root\n    child")
    assert isinstance(svg, str)
    assert 'aria-roledescription="mindmap"' in svg
    # mindmap's SVG header carries NO ``id="my-svg"`` (unlike flowchart/gitGraph).
    assert 'id="my-svg"' not in svg


def test_render_mindmap_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_mindmap_carries_role_and_maxwidth_style() -> None:
    """The header carries the graphics-document role + max-width style."""
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    assert 'role="graphics-document document"' in svg
    assert 'style="max-width: 100%;"' in svg


def test_render_mindmap_carries_viewbox() -> None:
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    assert "viewBox=" in svg


# === parse rules ============================================================


def test_parse_root_default_is_coerced_to_circle() -> None:
    """A root whose shape is Default is forced to Circle (grok L188-L192)."""
    root = _parse_mindmap_tree("mindmap\n  root")
    assert root.node_type == _NodeType.CIRCLE
    assert root.label == "root"


def test_parse_indented_children_attach_to_parent() -> None:
    root = _parse_mindmap_tree("mindmap\n  root\n    a\n    b")
    assert root.label == "root"
    assert [c.label for c in root.children] == ["a", "b"]


def test_parse_nested_indent_builds_tree() -> None:
    """Deeper indent nests under the shallower parent (stack-pop semantics)."""
    root = _parse_mindmap_tree("mindmap\n  root\n    a\n      a1\n    b")
    assert [c.label for c in root.children] == ["a", "b"]
    assert [c.label for c in root.children[0].children] == ["a1"]


def test_parse_icon_decoration_lines_skipped() -> None:
    """``::icon(...)`` decoration lines are skipped (grok L159-L161)."""
    root = _parse_mindmap_tree(
        "mindmap\n  root\n    ::icon(fa fa-star)\n    child"
    )
    assert [c.label for c in root.children] == ["child"]


def test_parse_skips_comments_and_blank_lines() -> None:
    """``%%`` comments and blank lines are ignored before and within the body."""
    root = _parse_mindmap_tree(
        "%% header comment\n\nmindmap\n  %% inline\n  root\n    child"
    )
    assert root.label == "root"
    assert [c.label for c in root.children] == ["child"]


# === parse errors ===========================================================


def test_parse_missing_header_raises() -> None:
    """A first token that is not ``mindmap`` raises."""
    with pytest.raises(ParseError):
        _parse_mindmap_tree("flowchart TD\n  A --> B")


def test_parse_empty_input_raises() -> None:
    with pytest.raises(ParseError):
        _parse_mindmap_tree("")


def test_parse_only_comments_raises() -> None:
    """Only ``%%`` / blank lines -> no header -> raises."""
    with pytest.raises(ParseError):
        _parse_mindmap_tree("%% a\n\n%% b")


def test_parse_header_but_no_nodes_raises_line_zero() -> None:
    """Header present, body empty -> ``No nodes found`` reported at line 0."""
    with pytest.raises(ParseError) as exc_info:
        _parse_mindmap_tree("mindmap")
    assert "line 0" in str(exc_info.value)


def test_parse_error_str_carries_line_number() -> None:
    """A non-mindmap first content line reports its 1-based line number."""
    with pytest.raises(ParseError) as exc_info:
        _parse_mindmap_tree("\n\nflowchart TD")
    assert "line 3" in str(exc_info.value)


# === label/shape extraction (grok extract_label_and_type L214-L259) ========


def test_extract_circle_double_parens() -> None:
    assert _extract_label_and_type("((hello))") == ("hello", _NodeType.CIRCLE)


def test_extract_hexagon_double_braces() -> None:
    assert _extract_label_and_type("{{hex}}") == ("hex", _NodeType.HEXAGON)


def test_extract_bang_double_close() -> None:
    assert _extract_label_and_type("))bang((") == ("bang", _NodeType.BANG)


def test_extract_rect_brackets() -> None:
    assert _extract_label_and_type("[box]") == ("box", _NodeType.RECT)


def test_extract_rounded_rect_parens() -> None:
    assert _extract_label_and_type("(round)") == ("round", _NodeType.ROUNDED_RECT)


def test_extract_default_no_delimiter() -> None:
    assert _extract_label_and_type("plain") == ("plain", _NodeType.DEFAULT)


def test_extract_empty_brackets_falls_through_to_default() -> None:
    """``[]`` (empty interior) fails the non-empty guard -> Default fallback."""
    assert _extract_label_and_type("[]") == ("[]", _NodeType.DEFAULT)


# === section assignment (grok assign_sections L263) =========================


def test_assign_sections_root_none_children_indexed() -> None:
    """Root carries ``None``; direct children carry their child index; deeper
    descendants inherit their ancestor's section."""
    root = _parse_mindmap_tree("mindmap\n  root\n    a\n      a1\n    b")
    _assign_sections(root, None)
    assert root.section is None
    assert root.children[0].section == 0
    assert root.children[1].section == 1
    assert root.children[0].children[0].section == 0


# === node sizing (grok measure_node L291-L316) =============================


def test_measure_circle_is_square_with_diameter_floor() -> None:
    """Circle returns ``(d, d)``; ``ab`` (tw=17) -> diameter floored at 60."""
    node = _MindmapNode(id="n", label="ab", node_type=_NodeType.CIRCLE)
    w, h = _measure_node(node)
    assert w == h
    # max(max(17, 16) + 40, 60) = max(57, 60) = 60
    assert (w, h) == (60.0, 60.0)


def test_measure_rect_uses_padding_thirty() -> None:
    node = _MindmapNode(id="n", label="ab", node_type=_NodeType.RECT)
    w, h = _measure_node(node)
    # tw(ab)=17 -> w = max(17+30, 40) = 47; h = max(16+30, 36) = 46
    assert (w, h) == (47.0, 46.0)


def test_measure_hexagon_uses_padding_forty_five() -> None:
    node = _MindmapNode(id="n", label="ab", node_type=_NodeType.HEXAGON)
    w, h = _measure_node(node)
    # tw(ab)=17 -> w = max(17+45, 50) = 62; h = max(16+30, 40) = 46
    assert (w, h) == (62.0, 46.0)


def test_measure_bang_uses_padding_thirty_seven_half() -> None:
    node = _MindmapNode(id="n", label="ab", node_type=_NodeType.BANG)
    w, h = _measure_node(node)
    # tw(ab)=17 -> w = max(17+37.5, 50) = 54.5; h = max(16+37.5, 40) = 53.5
    assert (w, h) == (54.5, 53.5)


def test_measure_default_equals_rounded_rect() -> None:
    """Default and RoundedRect share the same box (grok's rect branch)."""
    default_node = _MindmapNode(id="n", label="ab", node_type=_NodeType.DEFAULT)
    rounded_node = _MindmapNode(id="n", label="ab", node_type=_NodeType.ROUNDED_RECT)
    assert _measure_node(default_node) == _measure_node(rounded_node)


# === radial layout (grok layout_mindmap + subtree_weight L323-L482) ========


def test_subtree_weight_leaf_one() -> None:
    leaf = _MindmapNode(id="n", label="x", node_type=_NodeType.DEFAULT)
    assert _subtree_weight(leaf) == 1.0


def test_subtree_weight_internal_sums_children() -> None:
    leaf = _MindmapNode(id="n", label="x", node_type=_NodeType.DEFAULT)
    parent = _MindmapNode(
        id="n", label="x", node_type=_NodeType.DEFAULT, children=[leaf, leaf]
    )
    assert _subtree_weight(parent) == 2.0


def test_subtree_weight_internal_floors_at_one() -> None:
    """An internal node with zero children still weighs 1.0 (max(sum, 1.0))."""
    empty_internal = _MindmapNode(
        id="n", label="x", node_type=_NodeType.DEFAULT, children=[]
    )
    assert _subtree_weight(empty_internal) == 1.0


def test_layout_root_alone_stays_at_origin_without_normalize() -> None:
    """A lone root returns at the origin -- grok's early return skips normalize.

    Grok ``mindmap_diagram.rs`` L342-L345: when ``root.children`` is empty the
    function returns immediately after placing the root at ``(0, 0)``, never
    reaching the L376-L400 normalize pass. A single root node is therefore
    *not* shifted into the positive quadrant -- there is nothing to fan out,
    hence nothing to normalize. Mirrored verbatim in ``_layout_mindmap``.
    """
    root = _MindmapNode(id="r", label="x", node_type=_NodeType.CIRCLE)
    placed_nodes, placed_edges = _layout_mindmap(root)
    assert len(placed_nodes) == 1
    assert len(placed_edges) == 0
    assert placed_nodes[0].is_root is True
    # diameter = max(max(8.5, 16) + 40, 60) = 60, but normalize is skipped, so
    # the root remains at the origin (grok L343-L345 early return).
    assert placed_nodes[0].x == 0.0
    assert placed_nodes[0].y == 0.0


def test_layout_with_children_emits_one_edge_per_child() -> None:
    root = _MindmapNode(
        id="r",
        label="root",
        node_type=_NodeType.CIRCLE,
        children=[
            _MindmapNode(id="a", label="a", node_type=_NodeType.DEFAULT),
            _MindmapNode(id="b", label="b", node_type=_NodeType.DEFAULT),
        ],
    )
    placed_nodes, placed_edges = _layout_mindmap(root)
    assert len(placed_nodes) == 3  # root + 2 children
    assert len(placed_edges) == 2  # one edge per child


# === palette (grok SECTION_COLORS L40-L89) =================================


def test_section_fill_eight_hue_table() -> None:
    assert _section_fill(0) == "hsl(60, 100%, 73.53%)"
    assert _section_fill(1) == "hsl(80, 100%, 76.27%)"
    assert _section_fill(2) == "hsl(270, 100%, 76.27%)"
    assert _section_fill(7) == "hsl(90, 100%, 76.27%)"


def test_section_fill_wraps_modulo_eight() -> None:
    """Index 8 wraps back to section 0 (yellow, 73.53% lightness)."""
    assert _section_fill(8) == _section_fill(0)
    assert _section_fill(16) == _section_fill(0)
    assert _section_fill(99) == _section_fill(3)


def test_section_text_white_only_on_section_two() -> None:
    """Section 2 (purple) carries white text; the rest carry black."""
    for i in range(8):
        if i == 2:
            assert _section_text(i) == "#ffffff"
        else:
            assert _section_text(i) == "black"


def test_section_edge_equals_fill() -> None:
    """Edge colour == fill colour for every section (grok SectionColors.edge)."""
    for i in range(8):
        assert _section_edge(i) == _section_fill(i)


def test_root_fill_is_navy_regardless_of_theme() -> None:
    """ROOT_FILL navy (hsl(240, 100%, 46.27%)) appears even under a dark theme."""
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.dark())
    assert "hsl(240, 100%, 46.27%)" in svg


def test_two_children_carry_section_zero_and_one_fills() -> None:
    svg = render_mindmap_diagram_to_svg("mindmap\n  root\n    a\n    b", MermaidTheme.light())
    assert _section_fill(0) in svg
    assert _section_fill(1) in svg


# === six shapes (grok render_node L553-L663) ===============================


def test_shape_circle_emits_circle_element() -> None:
    """The coerced-circle root renders as a ``<circle>``."""
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    assert "<circle " in svg


def test_shape_rect_brackets_emit_sharp_corners() -> None:
    """``[box]`` -> RECT with ``rx="0"`` (sharp corners)."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    [box]", MermaidTheme.light()
    )
    assert 'rx="0"' in svg


def test_shape_rounded_rect_parens_emit_rounded_corners() -> None:
    """``(round)`` -> RoundedRect with ``rx="5"``."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    (round)", MermaidTheme.light()
    )
    assert 'rx="5"' in svg


def test_shape_default_child_is_rounded_rect() -> None:
    """A Default-shape child renders as a rounded rect (rx=5)."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    child", MermaidTheme.light()
    )
    assert 'rx="5"' in svg


def test_shape_hexagon_braces_emit_polygon() -> None:
    """``{{hex}}`` -> Hexagon renders as a ``<polygon>``."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    {{hex}}", MermaidTheme.light()
    )
    assert "<polygon " in svg


def test_shape_bang_double_close_emit_ellipse() -> None:
    """``))bang((`` -> Bang renders as an ``<ellipse>`` (Cloud | Bang branch)."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    ))bang((", MermaidTheme.light()
    )
    assert "<ellipse " in svg


# === underline decoration (grok render_node L632-L649) =====================


def test_underline_absent_on_root_circle() -> None:
    """Root is a Circle -> no underline ``<line>`` decoration."""
    svg = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    assert "<line " not in svg


def test_underline_present_on_non_circle_child() -> None:
    """A non-root, non-circle node carries a 3-px underline ``<line>``."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    [box]", MermaidTheme.light()
    )
    assert "<line " in svg
    # The underline stroke-width is the literal ``"3"`` (not ``:.1f`` formatted).
    assert 'stroke-width="3"' in svg


# === edges (grok render_edge L532-L551) ====================================


def test_edge_is_quadratic_bezier() -> None:
    """Edges render as ``M ... Q ... ...`` quadratic beziers."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    child", MermaidTheme.light()
    )
    assert re.search(
        r'<path d="M [\d.]+,[\d.]+ Q [\d.]+,[\d.]+ [\d.]+,[\d.]+"', svg
    )


def test_edge_stroke_fades_with_depth() -> None:
    """``stroke_width = max(17 - 3*depth, 2)``: a depth-1 edge is 14.0 px."""
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    child", MermaidTheme.light()
    )
    assert 'stroke-width="14.0"' in svg


def test_edge_fill_is_none() -> None:
    svg = render_mindmap_diagram_to_svg(
        "mindmap\n  root\n    child", MermaidTheme.light()
    )
    assert 'fill="none"' in svg


# === html_escape (grok html_escape L665-L669) ==============================


def test_escape_xml_uses_quot_named_entity() -> None:
    """``"`` -> ``&quot;`` (mindmap variant; ``'`` is NOT escaped)."""
    assert _escape_xml('say "hi"') == "say &quot;hi&quot;"
    assert _escape_xml("it's") == "it's"  # apostrophe NOT escaped
    assert _escape_xml("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_quote_in_label_escaped_in_svg() -> None:
    svg = render_mindmap_diagram_to_svg(
        'mindmap\n  root\n    [say "hi"]', MermaidTheme.light()
    )
    assert "&quot;hi&quot;" in svg


# === theme awareness (0 channels) ==========================================


def test_theme_is_zero_channel_light_equals_dark() -> None:
    """mindmap hard-codes its palette; light == dark (grok ignores ``_theme``)."""
    light = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.light())
    dark = render_mindmap_diagram_to_svg("mindmap\n  root", MermaidTheme.dark())
    assert light == dark


# === helpers ================================================================


def test_fmt_drops_trailing_zero_for_integers() -> None:
    assert _fmt(16.0) == "16"
    assert _fmt(0.0) == "0"


def test_fmt_keeps_fraction_for_non_integers() -> None:
    assert _fmt(8.5) == "8.5"
    assert _fmt(-1.5) == "-1.5"


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    from minimax_code.mermaid.to_svg import mindmap_diagram

    assert mindmap_diagram.__all__ == ["render_mindmap_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_mindmap_unsupported() -> None:
    """R292 removed ``mindmap`` (11 -> 10); R293 removed ``xychart-beta``
    (10 -> 9); R294 removed ``requirementDiagram`` (9 -> 8) -- the set keeps
    shrinking as renderers ship."""
    assert "mindmap" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 8
