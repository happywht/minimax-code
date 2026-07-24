"""Black-box + white-box tests for the migrated class-diagram renderer (R296).

Exercises :mod:`minimax_code.mermaid.to_svg.class_diagram` -- the 17th
self-contained SVG emitter and the *third* renderer to drive the dagre
layout engine directly (after R294 ``requirementDiagram`` and R295
``erDiagram``). Mirrors grok ``mermaid-to-svg/src/class_diagram.rs``
(1144 lines): a UML class-diagram parser that collects class boxes
(name + optional ``<<stereotype>>`` + ``attributes`` / ``methods`` member
partitions) and binary relationships annotated with one of eight UML
relation operators, then hands the AST to dagre for ranked placement and
emits an SVG whose nodes carry the title / attribute-divider /
method-divider three-band layout and whose edges carry the classic UML
markers (extension / composition / aggregation / dependency x Start/End).

Coverage groups:

* Dispatch smoke -- the ``classDiagram`` token routes through the
  dedicated arm, not the unsupported surface.
* Parse rules -- ``class Name { }`` blocks (members, ``<<x>>``
  stereotype, attribute vs method split on ``(``), the ``direction``
  directive, the ``A op B : label`` relationship line, referenced-only
  classes auto-created empty, the BTreeMap name-ascending order, the
  ``Class : member`` single-line append, and the ``%%`` / blank skipping.
* Parse errors -- missing ``classDiagram`` header, unrecognized line,
  empty class name before ``{``.
* Direction -- ``TD``/``TB``/``BT``/``LR``/``RL`` (case-insensitive) map
  to the four ``Direction`` variants; ``as_rankdir`` returns the dagre
  rankdir string; an unknown token raises :class:`InvalidDirection`.
* Relation type -- the eight :class:`RelationType` variants classify via
  ``_classify_relationship``; ``is_dashed`` / ``start_marker`` /
  ``end_marker`` match grok's match arms.
* Operators -- all 17 relation operators recognised by
  ``_is_relationship_operator``.
* Markers -- all 9 UML marker ids appear unconditionally, and the
  extensionStart geometry carries grok's refX/refY/width/height.
* Edge path -- ``marker-start`` / ``marker-end`` route to the right
  marker ids; ``is_dashed`` edges carry ``stroke-dasharray="3"``; the
  edge ``d`` uses the one-decimal ``M{x:.1}`` form.
* Edge label -- a non-empty label renders a 0.75-opacity rect backdrop
  plus centred text; no label -> no label group.
* Class node -- the node group carries ``id="classId-{name}"`` + the
  ``classTitle`` / ``classMember`` / ``classAnnotation`` classes and the
  attribute + method divider lines.
* Stereotype -- ``<<interface>>`` renders as ``«interface»`` (guillemets).
* Helpers -- ``_fmt`` (Rust f64 Display, integers drop ``.0``), ``_fmt1``
  (one-decimal), ``_xml_escape`` (numeric ``&#39;`` variant),
  ``_stereotype_to_guillemet``, and ``_intersect_rect``.
* Theme awareness (4 channels) -- node_fill / node_stroke / edge_color /
  text flow into the emitted SVG; dark differs from light.
* Public surface -- the single-symbol ``__all__`` and the dispatch no
  longer lists ``classDiagram`` as unsupported (7 -> 6 tokens).
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.class_diagram import (
    Direction,
    RelationType,
    _classify_members,
    _classify_relationship,
    _compute_class_box_size,
    _fmt,
    _fmt1,
    _intersect_rect,
    _is_relationship_operator,
    _parse_class_diagram,
    _parse_direction,
    _stereotype_to_guillemet,
    _xml_escape,
    render_class_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.error import InvalidDirection, ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# A canonical two-class inheritance hierarchy used by several structural
# + theme tests: Animal is an abstract parent with one attribute and one
# method, Dog extends it and adds a method.
_ANIMALS = (
    "classDiagram\n"
    "  Animal <|-- Dog\n"
    "  class Animal {\n"
    "    +name: String\n"
    "    +makeSound()\n"
    "  }\n"
    "  class Dog {\n"
    "    +bark()\n"
    "  }"
)


# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_class_dispatches_to_renderer() -> None:
    """A ``classDiagram`` block routes through the dedicated renderer."""
    svg = render_mermaid_to_svg("classDiagram\n  Animal <|-- Dog")
    assert isinstance(svg, str)
    # classDiagram's svg root carries NO ``id="my-svg"`` (the fourth root
    # variant) -- it opens with class= then viewBox= then role=.
    assert 'id="my-svg"' not in svg
    assert 'class="classDiagram"' in svg
    assert 'aria-roledescription="class"' in svg


def test_render_class_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_class_viewbox_uses_integer_form() -> None:
    """The viewBox dimensions are formatted with ``{:.0f}`` (no decimals)."""
    svg = render_class_diagram_to_svg("classDiagram\n  A <|-- B")
    # ``viewBox="0 0 {w:.0f} {h:.0f}"`` -> integer dimensions.
    assert 'viewBox="0 0 ' in svg
    # No decimal point inside the viewBox attribute (e.g. ``100.0`` is ``100``).
    viewBox = svg.split('viewBox="', 1)[1].split('"', 1)[0]
    for token in viewBox.split():
        assert "." not in token


# === parse rules ============================================================


def test_parse_class_block_with_attributes_and_methods() -> None:
    """A class block partitions members by ``(`` into attrs vs methods."""
    diagram = _parse_class_diagram(
        "classDiagram\n"
        "  class Animal {\n"
        "    +name: String\n"
        "    +makeSound()\n"
        "  }"
    )
    info = diagram.classes["Animal"]
    assert info.name == "Animal"
    assert info.attributes == ["+name: String"]
    assert info.methods == ["+makeSound()"]


def test_parse_stereotype_inside_class_block() -> None:
    """A ``<<x>>`` line inside a class block sets the stereotype."""
    diagram = _parse_class_diagram(
        "classDiagram\n"
        "  class Animal {\n"
        "    <<interface>>\n"
        "    +makeSound()\n"
        "  }"
    )
    assert diagram.classes["Animal"].stereotype == "<<interface>>"


def test_parse_direction_directive() -> None:
    """A ``direction LR`` directive sets the layout direction."""
    diagram = _parse_class_diagram("classDiagram\n  direction LR\n  A <|-- B")
    assert diagram.direction is Direction.LR


def test_parse_relationship_collected() -> None:
    """A relationship line ``A op B : label`` parses into from / to / label."""
    diagram = _parse_class_diagram(_ANIMALS)
    assert len(diagram.relationships) == 1
    rel = diagram.relationships[0]
    assert rel.from_ == "Animal"
    assert rel.to == "Dog"
    assert rel.rel_type is RelationType.EXTENSION


def test_parse_relationship_quoted_cardinality_becomes_label() -> None:
    """Quoted cardinalities ``"1"`` splice into the relationship label."""
    diagram = _parse_class_diagram(
        'classDiagram\n  Customer "1" --> "many" Order : places'
    )
    rel = diagram.relationships[0]
    assert rel.from_ == "Customer"
    assert rel.to == "Order"
    assert rel.rel_type is RelationType.DEPENDENCY
    assert rel.label == "1 places many"


def test_parse_referenced_classes_auto_created_empty() -> None:
    """Classes named only in a relationship auto-create with no members."""
    diagram = _parse_class_diagram("classDiagram\n  A <|-- B")
    assert set(diagram.classes) == {"A", "B"}
    assert diagram.classes["A"].attributes == []
    assert diagram.classes["A"].methods == []


def test_parse_classes_sorted_btreemap_order() -> None:
    """``classes`` keeps name-ascending order (grok BTreeMap traversal)."""
    diagram = _parse_class_diagram(
        "classDiagram\n"
        "  class Zebra {\n"
        "    +x: int\n"
        "  }\n"
        "  class Apple {\n"
        "    +y: int\n"
        "  }"
    )
    assert list(diagram.classes.keys()) == ["Apple", "Zebra"]


def test_parse_single_line_member_append() -> None:
    """``Class : member`` appends to the named class's method list.

    grok splits the line on the first ``:`` (``split_once``): ``Class`` is the
    head (a single whitespace token) and ``member`` is the label. The
    single-line branch fires when ``parts.len() == 1`` and ``label`` is
    present, routing the member through :func:`_classify_members` (``(`` ->
    method, stored verbatim). Mirrors grok ``class_diagram.rs`` L365-L382.
    """
    diagram = _parse_class_diagram(
        "classDiagram\n  class Dog\n  Dog: +bark() String"
    )
    assert diagram.classes["Dog"].methods == ["+bark() String"]


def test_parse_skips_comment_and_blank_lines() -> None:
    """``%%`` comments and blank lines are skipped before and within."""
    diagram = _parse_class_diagram(
        "%% leading\n"
        "\n"
        "classDiagram\n"
        "  %% inline\n"
        "  class A {\n"
        "    +x: int\n"
        "  }"
    )
    assert diagram.classes["A"].attributes == ["+x: int"]


# === parse errors ===========================================================


def test_parse_missing_header_raises() -> None:
    """A first non-comment line that is not ``classDiagram`` raises."""
    with pytest.raises(ParseError):
        _parse_class_diagram("class A {\n  +x: int\n}")


def test_parse_unrecognized_line_raises() -> None:
    """A line that is neither a class block, relationship, nor member raises."""
    with pytest.raises(ParseError):
        _parse_class_diagram("classDiagram\n  bogus line here")


def test_parse_empty_class_name_before_brace_raises() -> None:
    """``class {`` (empty name before ``{``) raises."""
    with pytest.raises(ParseError):
        _parse_class_diagram("classDiagram\n  class {")


def test_parse_error_str_carries_line_number() -> None:
    """ParseError stringifies with the 1-based line number (error.py contract)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_class_diagram("classDiagram\n  bogus")
    assert "line 2" in str(exc_info.value)


# === direction ==============================================================


@pytest.mark.parametrize(
    "token,expected",
    [
        ("TD", Direction.TB),
        ("TB", Direction.TB),
        ("BT", Direction.BT),
        ("LR", Direction.LR),
        ("RL", Direction.RL),
        ("td", Direction.TB),  # case-insensitive
        ("lr", Direction.LR),
    ],
)
def test_parse_direction_all_tokens(token: str, expected: Direction) -> None:
    """Every direction token (case-insensitive) maps to its enum variant."""
    assert _parse_direction(token) is expected


def test_parse_direction_unknown_raises_value_error() -> None:
    """An unknown direction token raises ValueError (caller wraps as InvalidDirection)."""
    with pytest.raises(ValueError):
        _parse_direction("XY")


def test_parse_direction_unknown_in_diagram_raises_invalid_direction() -> None:
    """A ``direction XY`` line inside a diagram raises InvalidDirection."""
    with pytest.raises(InvalidDirection):
        _parse_class_diagram("classDiagram\n  direction XY\n  A <|-- B")


def test_direction_as_rankdir_returns_value() -> None:
    """``as_rankdir`` returns the lowercase dagre rankdir string."""
    assert Direction.TB.as_rankdir() == "tb"
    assert Direction.BT.as_rankdir() == "bt"
    assert Direction.LR.as_rankdir() == "lr"
    assert Direction.RL.as_rankdir() == "rl"


# === relation type ==========================================================


@pytest.mark.parametrize(
    "op,expected",
    [
        ("<|--", RelationType.EXTENSION),
        ("--|>", RelationType.EXTENSION),
        ("<|..", RelationType.REALIZATION),
        ("..|>", RelationType.REALIZATION),
        ("*--", RelationType.COMPOSITION),
        ("--*", RelationType.COMPOSITION),
        ("*..", RelationType.COMPOSITION),
        ("..*", RelationType.COMPOSITION),
        ("o--", RelationType.AGGREGATION),
        ("--o", RelationType.AGGREGATION),
        ("o..", RelationType.AGGREGATION),
        ("..o", RelationType.AGGREGATION),
        ("-->", RelationType.DEPENDENCY),
        ("<--", RelationType.DEPENDENCY),
        ("..>", RelationType.DASHED_DEP),
        ("<..", RelationType.DASHED_DEP),
        ("..", RelationType.DASHED_ASSOC),
        ("--", RelationType.ASSOCIATION),
    ],
)
def test_classify_relationship_all_operators(
    op: str, expected: RelationType
) -> None:
    """Every relation operator maps to its :class:`RelationType` variant."""
    assert _classify_relationship(op) is expected


def test_relation_type_is_dashed() -> None:
    """Realization / dashed-dep / dashed-assoc render dashed; the rest solid."""
    dashed = {RelationType.REALIZATION, RelationType.DASHED_DEP, RelationType.DASHED_ASSOC}
    for rt in RelationType:
        assert rt.is_dashed() is (rt in dashed)


def test_relation_type_start_marker() -> None:
    """``start_marker`` returns the marker id or None (grok ``start_marker``)."""
    assert RelationType.EXTENSION.start_marker() == "extensionStart"
    assert RelationType.REALIZATION.start_marker() == "extensionStart"
    assert RelationType.COMPOSITION.start_marker() == "compositionStart"
    assert RelationType.AGGREGATION.start_marker() == "aggregationStart"
    for rt in (
        RelationType.DEPENDENCY,
        RelationType.DASHED_DEP,
        RelationType.ASSOCIATION,
        RelationType.DASHED_ASSOC,
    ):
        assert rt.start_marker() is None


def test_relation_type_end_marker() -> None:
    """``end_marker`` returns ``dependencyEnd`` for dependency/dashed-dep, else None."""
    assert RelationType.DEPENDENCY.end_marker() == "dependencyEnd"
    assert RelationType.DASHED_DEP.end_marker() == "dependencyEnd"
    for rt in (
        RelationType.EXTENSION,
        RelationType.REALIZATION,
        RelationType.COMPOSITION,
        RelationType.AGGREGATION,
        RelationType.ASSOCIATION,
        RelationType.DASHED_ASSOC,
    ):
        assert rt.end_marker() is None


# === operators ==============================================================


def test_is_relationship_operator_all_seventeen() -> None:
    """All 17 UML relation operators are recognised."""
    ops = (
        "<|--", "<|..", "..|>", "--|>", "--", "-->", "<--",
        "..", "..>", "<..", "*--", "o--", "--*", "--o",
        "*..", "o..", "..*", "..o",
    )
    for op in ops:
        assert _is_relationship_operator(op) is True


def test_is_relationship_operator_rejects_non_operator() -> None:
    """A non-operator token is rejected."""
    assert _is_relationship_operator("Animal") is False
    assert _is_relationship_operator(":") is False
    assert _is_relationship_operator("-->") is True  # sanity


# === markers ================================================================


def test_all_nine_markers_present() -> None:
    """All 9 UML marker ids appear unconditionally in ``<defs>``."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    for name in (
        "extensionStart", "extensionEnd",
        "dependencyStart", "dependencyEnd",
        "compositionStart", "compositionEnd",
        "aggregationStart", "aggregationEnd",
        "lollipopStart",
    ):
        assert f'id="{name}"' in svg


def test_marker_extension_start_geometry() -> None:
    """extensionStart carries grok's refX/refY/width/height (190x240)."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert 'id="extensionStart"' in svg
    assert 'refX="18" refY="7" markerWidth="190" markerHeight="240"' in svg


def test_marker_dependency_end_is_filled_arrowhead() -> None:
    """dependencyEnd's path is a filled arrowhead (not transparent)."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    # The dependencyEnd marker block.
    start = svg.index('id="dependencyEnd"')
    block = svg[start:start + 200]
    assert 'path d="M 18,7 L9,13 L14,7 L9,1 Z"' in block


# === edge path ==============================================================


def test_edge_path_extension_carries_start_marker() -> None:
    """An ``<|--`` (extension) edge carries ``marker-start=extensionStart``."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert 'marker-start="url(#extensionStart)"' in svg


def test_edge_path_dependency_carries_end_marker() -> None:
    """A ``-->`` (dependency) edge carries ``marker-end=dependencyEnd``."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  A --> B", MermaidTheme.light()
    )
    assert 'marker-end="url(#dependencyEnd)"' in svg


def test_edge_path_dashed_for_realization() -> None:
    """A realization (``<|..``) edge emits ``stroke-dasharray="3"``."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  A <|.. B", MermaidTheme.light()
    )
    assert 'stroke-dasharray="3"' in svg


def test_edge_path_solid_for_extension() -> None:
    """An extension (``<|--``) edge carries no dash array."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert 'stroke-dasharray="3"' not in svg


def test_edge_path_uses_one_decimal_form() -> None:
    """The edge ``d`` attribute uses the ``M{x:.1},{y:.1}`` one-decimal form."""
    svg = render_class_diagram_to_svg("classDiagram\n  A <|-- B", MermaidTheme.light())
    # The first ``d="M`` in the SVG belongs to the arrow-head marker definition
    # (integer coords, e.g. ``M 1,7 L18,13 V 1 Z``). The edge relation path is
    # the ``<path d="..." class="relation" .../>`` element -- locate it via the
    # ``class="relation"`` attribute and walk back to its opening ``d="``.
    idx = svg.index('class="relation"')
    d_open = svg.rindex('d="', 0, idx)
    d_attr = svg[d_open:idx]
    # ``M12.0,`` or ``M12.5,`` style -- one digit after the dot.
    assert d_attr.startswith('d="M')
    assert "." in d_attr


# === edge label =============================================================


def test_edge_label_emits_rect_and_text() -> None:
    """A non-empty relationship label renders a 0.75-opacity rect + centred text."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  Animal <|-- Dog : extends", MermaidTheme.light()
    )
    assert 'opacity="0.75"' in svg
    assert ">extends</text>" in svg


def test_edge_label_skipped_when_no_label() -> None:
    """A relationship with no label emits no opacity rect."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  A <|-- B", MermaidTheme.light()
    )
    assert 'opacity="0.75"' not in svg


# === class node =============================================================


def test_class_node_emits_id_and_title_class() -> None:
    """Each class emits ``<g id="classId-{name}" class="node default">``."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert 'id="classId-Animal"' in svg
    assert 'id="classId-Dog"' in svg
    assert 'class="node default"' in svg


def test_class_node_title_and_member_classes() -> None:
    """The class name renders as ``classTitle``; members as ``classMember``."""
    svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    assert 'class="classTitle"' in svg
    assert 'class="classMember"' in svg


def test_class_node_emits_two_divider_lines() -> None:
    """An attribute-bearing class emits attribute + method divider lines."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  class A {\n    +x: int\n    +foo()\n  }",
        MermaidTheme.light(),
    )
    # Two divider lines inside the node.
    assert svg.count("<line ") >= 2


# === stereotype =============================================================


def test_stereotype_renders_as_guillemets() -> None:
    """``<<interface>>`` renders as ``«interface»`` (U+00AB/U+00BB guillemets)."""
    svg = render_class_diagram_to_svg(
        "classDiagram\n  class Animal {\n    <<interface>>\n    +foo()\n  }",
        MermaidTheme.light(),
    )
    assert "«interface»" in svg
    assert 'class="classAnnotation"' in svg


# === helpers ================================================================


def test_fmt_drops_trailing_zero_for_integers() -> None:
    """``_fmt`` formats integer-valued floats without ``.0`` (Rust Display)."""
    assert _fmt(10.0) == "10"
    assert _fmt(0.0) == "0"
    assert _fmt(-19.0) == "-19"
    assert _fmt(48.0) == "48"


def test_fmt_keeps_fraction_for_non_integers() -> None:
    """``_fmt`` keeps the fractional part for non-integer floats."""
    assert _fmt(57.5) == "57.5"
    assert _fmt(-13.5) == "-13.5"


def test_fmt1_always_one_decimal() -> None:
    """``_fmt1`` formats with exactly one decimal place (edge-path ``{:.1}``)."""
    assert _fmt1(10.0) == "10.0"
    assert _fmt1(10.5) == "10.5"
    assert _fmt1(-3.25) == "-3.2"


def test_xml_escape_uses_numeric_apos_entity() -> None:
    """``'`` -> ``&#39;`` (numeric-entity variant, distinct from er's ``&apos;``)."""
    assert _xml_escape("it's") == "it&#39;s"
    assert _xml_escape("a&b<c>d\"e") == "a&amp;b&lt;c&gt;d&quot;e"


def test_stereotype_to_guillemet_well_formed() -> None:
    """``<<x>>`` -> ``«x»``; a bare token is also wrapped."""
    assert _stereotype_to_guillemet("<<interface>>") == "«interface»"
    assert _stereotype_to_guillemet("interface") == "«interface»"
    # Prefix present but suffix missing -> grok ``unwrap_or(s)`` returns the
    # original full string (not the prefix-stripped intermediate).
    assert _stereotype_to_guillemet("<<interface") == "«<<interface»"


def test_intersect_rect_right_edge() -> None:
    """A rightward ray hits the right edge at (hw, cy)."""
    assert _intersect_rect(0, 0, 10, 10, 100, 0) == (10, 0)


def test_intersect_rect_bottom_edge() -> None:
    """A downward ray hits the bottom edge at (cx, hh)."""
    assert _intersect_rect(0, 0, 10, 10, 0, 100) == (0, 10)


def test_intersect_rect_coincident_returns_top() -> None:
    """A coincident centre+target returns the top edge (cx, cy-hh)."""
    assert _intersect_rect(0, 0, 10, 10, 0, 0) == (0, -10)


def test_intersect_rect_diagonal_hits_corner() -> None:
    """A diagonal ray hits the corner (hw, hh)."""
    assert _intersect_rect(0, 0, 10, 10, 50, 50) == (10, 10)


# === box-size helper ========================================================


def test_compute_class_box_size_height_grows_with_members() -> None:
    """Adding members grows the computed height (smoke on the layout core)."""
    from minimax_code.mermaid.to_svg.class_diagram import ClassInfo

    empty = _compute_class_box_size(ClassInfo(name="A"))
    full = _compute_class_box_size(
        ClassInfo(name="A", attributes=["+x: int"], methods=["+foo()"])
    )
    assert full[1] > empty[1]  # height index 1


# === classify_members =======================================================


def test_classify_members_splits_by_paren() -> None:
    """Members containing ``(`` are methods; others are attributes."""
    attrs: list[str] = []
    methods: list[str] = []
    _classify_members(attrs, methods, ["+x: int", "+foo()", "+y: float", "+bar()"])
    assert attrs == ["+x: int", "+y: float"]
    assert methods == ["+foo()", "+bar()"]


# === theme awareness (4 channels) ===========================================


def test_theme_node_fill_flows_into_background_rect() -> None:
    """The light ``node_fill`` hex appears in the node background fill rect."""
    light = MermaidTheme.light()
    svg = render_class_diagram_to_svg(_ANIMALS, light)
    assert f'fill="{light.node_fill}"' in svg


def test_theme_node_stroke_flows_into_divider_lines() -> None:
    """The light ``node_stroke`` hex appears in the divider-line strokes."""
    light = MermaidTheme.light()
    svg = render_class_diagram_to_svg(_ANIMALS, light)
    assert f'stroke="{light.node_stroke}"' in svg


def test_theme_edge_color_flows_into_relationship_path() -> None:
    """The light ``edge_color`` hex appears in the ``class="relation"`` path."""
    light = MermaidTheme.light()
    svg = render_class_diagram_to_svg(_ANIMALS, light)
    assert f'class="relation" stroke="{light.edge_color}"' in svg


def test_dark_theme_svg_differs_from_light() -> None:
    """The dark palette produces a different SVG than the light palette."""
    light_svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    dark_svg = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.dark())
    assert light_svg != dark_svg


def test_default_theme_matches_light() -> None:
    """``render_class_diagram_to_svg`` with no theme resolves to light."""
    explicit = render_class_diagram_to_svg(_ANIMALS, MermaidTheme.light())
    default = render_class_diagram_to_svg(_ANIMALS)
    assert explicit == default


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    """The leaf exports exactly one public symbol."""
    from minimax_code.mermaid.to_svg import class_diagram

    assert class_diagram.__all__ == ["render_class_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_class_unsupported() -> None:
    """R296 removed ``classDiagram`` (7 -> 6 tokens); R297 removed the 5 C4
    tokens (6 -> 1); the set keeps shrinking."""
    assert "classDiagram" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 1
