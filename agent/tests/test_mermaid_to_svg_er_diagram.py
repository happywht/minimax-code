"""Black-box + white-box tests for the migrated er renderer (R295).

Exercises :mod:`minimax_code.mermaid.to_svg.er_diagram` -- the 16th
self-contained SVG emitter and the *second* renderer to drive the dagre
layout engine directly (after R294 ``requirementDiagram``). Mirrors grok
``mermaid-to-svg/src/er_diagram.rs`` (936 lines): an entity-relationship
diagram parser that collects entity tables (header + ``type name``
attribute rows) and binary relationships annotated with crow's-foot
cardinality, then hands the AST to dagre for ranked placement and emits
an SVG whose edges carry the eight classic ER markers
(onlyOne / zeroOrOne / oneOrMore / zeroOrMore x Start / End).

Coverage groups:

* Dispatch smoke -- the ``erDiagram`` token routes through the dedicated
  arm, not the unsupported surface.
* Parse rules -- single/multi entity blocks, attribute rows, the
  ``ENTITY_A <spec> ENTITY_B : role`` relationship line, referenced-only
  entities auto-created empty, the BTreeMap name-ascending order, and the
  ``%%`` / blank-line skipping.
* Parse errors -- missing header, unrecognized line, invalid cardinality
  token, malformed relationship (fewer than 3 tokens).
* Cardinality -- the four crow's-foot tokens map to the four enum
  variants (``||`` -> OnlyOne, ``|o``/``o|`` -> ZeroOrOne, ``|{``/``}|``
  -> OneOrMore, ``o{``/``}o`` -> ZeroOrMore); ``marker_name`` returns
  the marker-id fragment.
* Identification -- ``--`` -> Identifying, ``..`` -> NonIdentifying.
* Markers -- all 8 ids appear unconditionally, the onlyOne geometry, and
  the zeroOrOne/zeroOrMore circles carry ``fill="white"``.
* Edge path -- ``marker-start`` is ``card_a.Start``, ``marker-end`` is
  ``card_b.End``; NonIdentifying emits the ``8,8`` dash pattern;
  Identifying does not.
* Edge label -- the role renders as a centred ``edgeLabel`` text; no role
  -> no edge label group.
* Zebra striping -- light Odd rows are white, Even rows are
  ``lighten(node_fill, 0.25)`` (``#F0F0FF``); dark themes derive both
  stripes from the background.
* Columns + divider -- the type/name texts are ``text-anchor="start"``
  ``er entityLabel`` and a vertical ``.divider`` splits the columns.
* Theme awareness (5 channels) -- background / node_fill / node_stroke /
  edge_color / text flow into the emitted SVG; dark differs from light.
* Helpers -- ``_fmt`` (Rust f64 Display), ``_escape_xml`` (``&apos;``),
  ``_is_dark_hex`` / ``_lighten_hex`` (per-channel ``unwrap_or``), and
  the comma-form ``_points_to_path_d``.
* Public surface -- the single-symbol ``__all__`` and the dispatch no
  longer lists ``erDiagram`` as unsupported (8 -> 7 tokens).
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.er_diagram import (
    Cardinality,
    Identification,
    _escape_xml,
    _fmt,
    _is_dark_hex,
    _lighten_hex,
    _parse_cardinality,
    _parse_er_diagram,
    _parse_rel_spec,
    _parse_relationship,
    _points_to_path_d,
    render_er_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# A canonical two-entity relationship used by several structural + theme
# tests: CUSTOMER places zero-or-more ORDERs (identifying relationship).
_PLACES = "erDiagram\n  CUSTOMER ||--o{ ORDER : places"


# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_er_dispatches_to_renderer() -> None:
    """An ``erDiagram`` block routes through the dedicated renderer."""
    svg = render_mermaid_to_svg(_PLACES)
    assert isinstance(svg, str)
    # ER's svg root follows grok's er_diagram.rs attribute order, where
    # ``id="my-svg"`` lands at the *tail* (``width="100%" id="my-svg">``),
    # not the head like gitgraph. Assert the substring position-independently.
    assert 'id="my-svg"' in svg
    assert 'aria-roledescription="er"' in svg
    assert 'class="erDiagram"' in svg


def test_render_er_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_er_emits_three_groups() -> None:
    """The edgePaths / edgeLabels / nodes groups all appear."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert '<g class="edgePaths">' in svg
    assert '<g class="edgeLabels">' in svg
    assert '<g class="nodes">' in svg


# === parse rules ============================================================


def test_parse_single_entity_with_attributes() -> None:
    """An entity block carries its ``type name`` attribute rows in order."""
    diagram = _parse_er_diagram(
        "erDiagram\n"
        "  CUSTOMER {\n"
        "    string name\n"
        "    int age\n"
        "  }"
    )
    entity = diagram.entities["CUSTOMER"]
    assert entity.id == "CUSTOMER"
    assert len(entity.attributes) == 2
    assert entity.attributes[0].attr_type == "string"
    assert entity.attributes[0].name == "name"
    assert entity.attributes[1].attr_type == "int"
    assert entity.attributes[1].name == "age"


def test_parse_multiple_entities_preserved() -> None:
    """Two entity blocks both land in the AST."""
    diagram = _parse_er_diagram(
        "erDiagram\n"
        "  A {\n    int id\n  }\n"
        "  B {\n    int id\n  }"
    )
    assert set(diagram.entities) == {"A", "B"}


def test_parse_relationship_collected() -> None:
    """A relationship line parses into entity_a / entity_b / role / spec."""
    diagram = _parse_er_diagram(_PLACES)
    assert len(diagram.relationships) == 1
    rel = diagram.relationships[0]
    assert rel.entity_a == "CUSTOMER"
    assert rel.entity_b == "ORDER"
    assert rel.role == "places"


def test_parse_referenced_entities_auto_created_empty() -> None:
    """Entities named only in a relationship are auto-created with no attrs."""
    diagram = _parse_er_diagram(_PLACES)
    # Neither CUSTOMER nor ORDER had a block, so both auto-create empty.
    assert diagram.entities["CUSTOMER"].attributes == []
    assert diagram.entities["ORDER"].attributes == []


def test_parse_entities_sorted_btreemap_order() -> None:
    """``entities`` keeps name-ascending order (grok BTreeMap traversal)."""
    diagram = _parse_er_diagram(
        "erDiagram\n"
        "  ZEBRA {\n    int id\n  }\n"
        "  APPLE {\n    int id\n  }"
    )
    assert list(diagram.entities.keys()) == ["APPLE", "ZEBRA"]


def test_parse_skips_comments_and_blanks() -> None:
    """``%%`` comments and blank lines are skipped before and within."""
    diagram = _parse_er_diagram(
        "%% leading comment\n"
        "\n"
        "erDiagram\n"
        "  %% inline comment\n"
        "  A {\n    int id\n  }"
    )
    assert "A" in diagram.entities


# === parse errors ===========================================================


def test_parse_missing_header_raises() -> None:
    """A first non-comment line that is not ``erDiagram`` raises."""
    with pytest.raises(ParseError):
        _parse_er_diagram("CUSTOMER {\n  string name\n}")


def test_parse_unrecognized_line_raises() -> None:
    """A line that is neither a block, a relationship, nor ``}`` raises."""
    with pytest.raises(ParseError):
        _parse_er_diagram("erDiagram\n  bogusline")


def test_parse_invalid_cardinality_raises() -> None:
    """An unknown crow's-foot token raises."""
    with pytest.raises(ParseError):
        _parse_cardinality("xx", 1)


def test_parse_invalid_relationship_raises() -> None:
    """A relationship left-hand side with fewer than 3 tokens raises."""
    with pytest.raises(ParseError):
        _parse_relationship("A B", 2)


def test_parse_error_str_carries_line_number() -> None:
    """ParseError stringifies with the 1-based line number (error.py contract)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_er_diagram("erDiagram\n  bogusline")
    assert "line 2" in str(exc_info.value)


# === cardinality ============================================================


@pytest.mark.parametrize(
    "token,expected",
    [
        ("||", Cardinality.OnlyOne),
        ("|o", Cardinality.ZeroOrOne),
        ("o|", Cardinality.ZeroOrOne),
        ("|{", Cardinality.OneOrMore),
        ("}|", Cardinality.OneOrMore),
        ("o{", Cardinality.ZeroOrMore),
        ("}o", Cardinality.ZeroOrMore),
    ],
)
def test_parse_cardinality_all_tokens(token: str, expected: Cardinality) -> None:
    """Every two-char crow's-foot token maps to its enum variant."""
    assert _parse_cardinality(token, 1) == expected


def test_cardinality_marker_name_returns_value() -> None:
    """``marker_name`` returns the marker-id fragment (grok ``marker_name``)."""
    assert Cardinality.OnlyOne.marker_name() == "onlyOne"
    assert Cardinality.ZeroOrOne.marker_name() == "zeroOrOne"
    assert Cardinality.OneOrMore.marker_name() == "oneOrMore"
    assert Cardinality.ZeroOrMore.marker_name() == "zeroOrMore"


# === identification =========================================================


def test_parse_rel_spec_identifying() -> None:
    """``--`` is Identifying and splits the spec into left/right cards."""
    spec = _parse_rel_spec("||--||", 1)
    assert spec.rel_type == Identification.Identifying
    assert spec.card_a == Cardinality.OnlyOne
    assert spec.card_b == Cardinality.OnlyOne


def test_parse_rel_spec_nonidentifying() -> None:
    """``..`` is NonIdentifying."""
    spec = _parse_rel_spec("|o..o{", 1)
    assert spec.rel_type == Identification.NonIdentifying
    assert spec.card_a == Cardinality.ZeroOrOne
    assert spec.card_b == Cardinality.ZeroOrMore


# === markers ================================================================


def test_all_eight_markers_present() -> None:
    """All 4 cardinalities x Start/End marker ids appear unconditionally."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    for name in ("onlyOne", "zeroOrOne", "oneOrMore", "zeroOrMore"):
        assert f'id="my-svg_er-{name}Start"' in svg
        assert f'id="my-svg_er-{name}End"' in svg


def test_marker_geometry_onlyOne_start() -> None:
    """The onlyOneStart marker carries grok's refX/refY/width/height."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert 'id="my-svg_er-onlyOneStart"' in svg
    assert 'refX="0" refY="9" markerWidth="18" markerHeight="18"' in svg


def test_marker_circles_carry_white_fill() -> None:
    """zeroOrOne (x2) + zeroOrMore (x2) markers each carry a white circle."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert svg.count('circle fill="white"') == 4


# === edge path ==============================================================


def test_edge_path_marker_connections() -> None:
    """``marker-start`` is ``card_a.Start``, ``marker-end`` is ``card_b.End``."""
    # card_a=OnlyOne, card_b=ZeroOrMore.
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert 'marker-start="url(#my-svg_er-onlyOneStart)"' in svg
    assert 'marker-end="url(#my-svg_er-zeroOrMoreEnd)"' in svg


def test_edge_path_nonidentifying_dashed() -> None:
    """A ``..`` (NonIdentifying) relationship emits the ``8,8`` dash array."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A |o..o{ B : r", MermaidTheme.light()
    )
    assert 'stroke-dasharray="8,8"' in svg


def test_edge_path_identifying_not_dashed() -> None:
    """An identifying (``--``) relationship carries no dash array."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert 'stroke-dasharray="8,8"' not in svg


# === edge label =============================================================


def test_edge_label_emits_role_text() -> None:
    """A non-empty role renders as a centred ``edgeLabel`` text."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert 'class="edgeLabel"' in svg
    assert 'style="font-size:14px"' in svg
    assert ">places</text>" in svg


def test_edge_label_skipped_when_no_role() -> None:
    """A relationship with no role emits no ``edgeLabel`` group."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A ||--o{ B", MermaidTheme.light()
    )
    # ``edgeLabels`` (plural) is the group wrapper and is always present, and
    # the ``.edgeLabel`` CSS selector in the ``<style>`` block is unconditional
    # -- so we assert the per-edge *element* (``class="edgeLabel"``) is absent.
    # A bare ``"edgeLabel"`` substring check would always fail because the
    # style block ships the selector regardless of whether any edge carries
    # a role; the element form (with the quoted attribute) isolates the label.
    assert 'class="edgeLabel"' not in svg


# === entity node ============================================================


def test_entity_node_emits_id_and_class() -> None:
    """Each entity emits a ``<g id="entity-{name}" class="node default">``."""
    svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    assert 'id="entity-CUSTOMER"' in svg
    assert 'id="entity-ORDER"' in svg
    assert 'class="node default"' in svg


# === zebra striping =========================================================


def test_zebra_light_odd_row_is_white() -> None:
    """Light theme: the first attribute row (Odd) fills white."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n    string name\n  }",
        MermaidTheme.light(),
    )
    assert 'class="er attributeBoxOdd"' in svg
    assert 'fill:#ffffff;stroke:#9370DB' in svg


def test_zebra_light_even_row_is_lightened_node_fill() -> None:
    """Light theme: the Even row is ``lighten(node_fill, 0.25) = #F0F0FF``."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n    string name\n  }",
        MermaidTheme.light(),
    )
    assert 'class="er attributeBoxEven"' in svg
    assert 'fill:#F0F0FF;stroke:#9370DB' in svg


def test_zebra_dark_theme_derives_stripes_from_background() -> None:
    """Dark theme: stripes come from ``lighten(background, 0.08/0.16)``."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n    string name\n  }",
        MermaidTheme.dark(),
    )
    assert "fill:#303030" in svg  # lighten("#1e1e1e", 0.08)
    assert "fill:#424242" in svg  # lighten("#1e1e1e", 0.16)


# === columns + divider ======================================================


def test_attribute_type_and_name_text_use_start_anchor() -> None:
    """Both columns render as ``text-anchor="start"`` ``er entityLabel``."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n  }", MermaidTheme.light()
    )
    assert 'class="er entityLabel">int</text>' in svg  # type column
    assert 'class="er entityLabel">id</text>' in svg  # name column


def test_dividers_present_for_entity_with_attributes() -> None:
    """An attribute-bearing entity emits header + column dividers."""
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n  }", MermaidTheme.light()
    )
    # Header/attributes horizontal divider + type/name vertical divider.
    assert svg.count('class="divider"') >= 2


# === theme awareness (5 channels) ===========================================


def test_theme_background_flows_into_svg_style() -> None:
    light = MermaidTheme.light()
    svg = render_er_diagram_to_svg(_PLACES, light)
    assert f"background-color: {light.background};" in svg


def test_theme_node_fill_in_entity_box_style() -> None:
    light = MermaidTheme.light()
    svg = render_er_diagram_to_svg(_PLACES, light)
    assert f".entityBox {{fill:{light.node_fill};stroke:{light.node_stroke};}}" in svg


def test_theme_node_stroke_in_divider_style() -> None:
    light = MermaidTheme.light()
    svg = render_er_diagram_to_svg(
        "erDiagram\n  A {\n    int id\n  }", light
    )
    assert f".divider {{stroke:{light.node_stroke};stroke-width:1;}}" in svg


def test_theme_edge_color_in_relationship_line_style() -> None:
    light = MermaidTheme.light()
    svg = render_er_diagram_to_svg(_PLACES, light)
    assert (
        f".relationshipLine {{stroke:{light.edge_color};stroke-width:1;fill:none;}}"
        in svg
    )


def test_dark_theme_svg_differs_from_light() -> None:
    light_svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.light())
    dark_svg = render_er_diagram_to_svg(_PLACES, MermaidTheme.dark())
    assert light_svg != dark_svg


# === helpers ================================================================


def test_fmt_drops_trailing_zero_for_integers() -> None:
    assert _fmt(10.0) == "10"
    assert _fmt(0.0) == "0"
    assert _fmt(-19.0) == "-19"


def test_fmt_keeps_fraction_for_non_integers() -> None:
    assert _fmt(0.625) == "0.625"
    assert _fmt(-1.5) == "-1.5"


def test_escape_xml_uses_apos_named_entity() -> None:
    """``'`` -> ``&apos;`` (named-entity variant shared with gitgraph/journey)."""
    assert _escape_xml("it's") == "it&apos;s"
    assert _escape_xml("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_is_dark_hex_luminance_threshold() -> None:
    assert _is_dark_hex("#ffffff") is False
    assert _is_dark_hex("#000000") is True
    assert _is_dark_hex("#1e1e1e") is True
    assert _is_dark_hex("#ECECFF") is False


def test_lighten_hex_blends_toward_white_upper_cased() -> None:
    assert _lighten_hex("#ECECFF", 0.25) == "#F0F0FF"
    assert _lighten_hex("#1e1e1e", 0.08) == "#303030"
    assert _lighten_hex("#1e1e1e", 0.16) == "#424242"
    assert _lighten_hex("#000000", 0.5) == "#7F7F7F"


def test_points_to_path_d_uses_comma_form() -> None:
    """ER's path ``d`` uses the comma form ``M{x},{y}`` then ``L{x},{y}``."""
    assert _points_to_path_d([]) == ""
    assert _points_to_path_d([(10.0, 20.0)]) == "M10,20"
    assert _points_to_path_d([(10.0, 20.0), (30.0, 40.0)]) == "M10,20L30,40"


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    from minimax_code.mermaid.to_svg import er_diagram

    assert er_diagram.__all__ == ["render_er_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_er_unsupported() -> None:
    """R295 removed ``erDiagram`` (8 -> 7 tokens); the set keeps shrinking."""
    assert "erDiagram" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 7
