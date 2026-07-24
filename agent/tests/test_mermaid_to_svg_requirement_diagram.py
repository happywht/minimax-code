"""Black-box tests for the migrated requirement-diagram renderer (R294).

Exercises :mod:`minimax_code.mermaid.to_svg.requirement_diagram` -- the
zero-semantic clone of grok ``mermaid-to-svg/src/requirement_diagram.rs``
(direction (1), leaf 25). This is the **first per-diagram-type renderer that
consumes the dagre layout engine** (R246--R268); the preceding R279--R293
renderers (info / radar / pie / packet / sankey / quadrant / block / journey /
gitGraph / mindmap / xychart) all hand-place their geometry.

The renderer fuses grok's parse -> dagre-layout -> SVG-emit pipeline behind a
single :func:`render_requirement_diagram_to_svg` call -- mirroring grok
``render`` L1-L50. Covers the public entry, the module-private parser/layout
helpers, and the theme-aware SVG surface.

Coverage matrix (grok behaviour parity asserted):

1. **grok smoke** -- a full multi-node diagram with relations renders every
   structural contract (root id, markers, edge label, nodes, body lines).
2. **SVG root** -- ``id="my-svg"`` is the LAST attribute (grok L711-L714);
   ``aria-roledescription`` / ``role`` / ``viewBox`` / ``class`` present.
3. **Markers** -- ``containsStart`` (circle + crosshair, refX=0) and
   ``arrowEnd`` (open chevron, refX=20), both stroked with ``edge_color``.
4. **Style** -- the 7-channel ``<style>`` block (5 theme channels + 2 fixed).
5. **Nodes** -- sorted by id; ``<<type>>`` header + bold name + optional
   divider + body lines; the divider appears only when a body exists.
6. **Node-kind -> type-line** -- all 7 valid kinds map to their header text
   (grok ``REQ_TYPE_MAP`` L82-L90 + the ``Element`` / ``Requirement`` fallbacks).
7. **Edges & labels** -- forward ``-contains->`` carries a leading dash
   (``<<-contains>>``); the ``<-`` relation flips src/dst; backward
   ``contains`` is dash-free and renders SOLID (grok dash-position asymmetry).
8. **Theme** -- the 5 palette channels (background / text / edge / node_fill /
   node_stroke) flow verbatim into the SVG (no normalisation).
9. **Parsing** -- direction, element/requirement props, prop-key aliases
   (``docRef`` / ``docref``, ``verifyMethod`` / ``verifymethod``), and the
   risk / verifyMethod canonicalisation.
10. **Errors** -- every :class:`ParseError` path raises with the grok-faithful
    line number and message text.
11. **Helpers** -- the pure formatting primitives (``_fmt`` / ``_escape_xml``
    / ``_points_to_path_d`` / ``_strip_quotes`` / ``_split_once_ws`` /
    ``_normalize_risk`` / ``_normalize_verify_method`` / ``_Direction``).
"""

from __future__ import annotations

import re

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.requirement_diagram import (
    _Direction,
    _escape_xml,
    _fmt,
    _normalize_risk,
    _normalize_verify_method,
    _points_to_path_d,
    _split_once_ws,
    _strip_quotes,
    render_requirement_diagram_to_svg,
)

# === theme palette constants (smoke-verified, rendered VERBATIM -- no
# normalisation, unlike the block-diagram renderer's ``#333`` shortening) ===

# MermaidTheme.light() palette.
_LIGHT_BG = "#ffffff"
_LIGHT_TEXT = "#333333"
_LIGHT_EDGE = "#333333"
_LIGHT_NODE_FILL = "#ECECFF"
_LIGHT_NODE_STROKE = "#9370DB"
# MermaidTheme.dark() palette.
_DARK_BG = "#1e1e1e"
_DARK_TEXT = "#ffffff"
_DARK_EDGE = "#888888"
_DARK_NODE_FILL = "#2d2d2d"
_DARK_NODE_STROKE = "#888888"

# Edge-label rect fill (hard-coded ``#E8E8E8`` @ 0.8 opacity -- grok L766).
_EDGE_LABEL_FILL = "#E8E8E8"

# Divider ``<line>`` signature: the node-body separator carries the
# ``stroke-width="1.3"`` ATTRIBUTE (the ``<style>`` block uses the CSS form
# ``stroke-width:1.3;`` -- colon/semicolon, not an attribute -- and the
# marker crosshair ``<line>``s have no width at all). So this signature
# uniquely identifies a divider line across the whole SVG.
_DIVIDER_SIG = 'stroke-width="1.3"'


def _node_group(svg: str, node_id: str) -> str:
    """Extract the ``class="node default"`` group for ``node_id`` from ``svg``."""
    pattern = rf'<g[^>]*id="{re.escape(node_id)}"[^>]*class="node default"[^>]*>.*?</g>'
    match = re.search(pattern, svg)
    assert match is not None, f"node group for {node_id!r} not found"
    return match.group(0)


# === fixtures ===============================================================
#
# Multi-line block syntax is REQUIRED: ``element Foo {}`` (single-line empty
# block) is NOT legal -- grok and Python both demand a brace on its own line
# (functional-parity, not a bug). Relations use the grok dash-position
# conventions that the dash-stripping tokeniser preserves verbatim.

_GROK_SMOKE = (
    "requirementDiagram\n"
    "direction TB\n"
    "requirement TestReq {\n"
    "  id: 1\n"
    "  text: The system shall do X\n"
    "  risk: Low\n"
    "  verifyMethod: Analysis\n"
    "}\n"
    "element System {\n"
    "  type: Model\n"
    "  docRef: RFC 1\n"
    "}\n"
    "System -contains-> TestReq"
)

_BARE_ELEMENT = "requirementDiagram\nelement System {\n}\n"

_TWO_NODES = (
    "requirementDiagram\n"
    "requirement A {\n"
    "  id: 1\n"
    "}\n"
    "requirement B {\n"
    "  id: 2\n"
    "}\n"
)


def _render(source: str, theme: MermaidTheme | None = None) -> str:
    """Render with the light theme by default (grok's default-light branch)."""
    return render_requirement_diagram_to_svg(
        source, theme if theme is not None else MermaidTheme.light()
    )


# === 1. grok smoke (full diagram) ===========================================


def test_grok_smoke_renders_every_structural_contract() -> None:
    """The canonical two-node diagram satisfies all 20 structural contracts.

    Mirrors the grok reference render: the ``<<-contains>>`` edge label carries
    its leading dash (grok L384-L385 tokeniser preserves ``-contains``), the
    edge is dashed with an ``arrowEnd`` marker, both nodes render their
    ``<<type>>`` header + body lines, and the root ``id="my-svg"`` closes the
    attribute list.
    """
    svg = _render(_GROK_SMOKE, MermaidTheme.dark())
    assert 'width="100%" id="my-svg">' in svg
    assert "&lt;&lt;-contains&gt;&gt;" in svg
    assert 'class="edgeLabel"' in svg
    assert "relationshipLine" in svg
    assert "my-svg_requirement-requirement_arrowEnd" in svg
    assert "my-svg_requirement-requirement_containsStart" in svg
    assert 'id="TestReq"' in svg
    assert 'id="System"' in svg
    assert "&lt;&lt;Requirement&gt;&gt;" in svg
    assert "&lt;&lt;Element&gt;&gt;" in svg
    assert "ID: 1" in svg
    assert "Text: The system shall do X" in svg
    assert "Risk: Low" in svg
    assert "Verification: Analysis" in svg
    assert "Type: Model" in svg
    assert "Doc Ref: RFC 1" in svg
    assert 'font-weight="bold"' in svg
    assert "<line" in svg
    assert "stroke-dasharray: 10,7" in svg
    assert svg.endswith("</svg>")


# === 2. SVG root structure (grok L711-L714) =================================


def test_svg_root_id_my_svg_is_last_attribute() -> None:
    """``id="my-svg"`` is the FINAL attribute of the root ``<svg>`` (grok L714)."""
    svg = _render(_GROK_SMOKE)
    assert 'width="100%" id="my-svg">' in svg
    # Nothing follows ``id="my-svg">`` except the ``<style>`` block.
    assert svg.index('id="my-svg">') < svg.index("<style>")


def test_svg_root_has_requirement_role_and_class() -> None:
    """``aria-roledescription`` / ``role`` / ``class`` match grok L711-L713."""
    svg = _render(_GROK_SMOKE)
    assert 'aria-roledescription="requirement"' in svg
    assert 'role="graphics-document document"' in svg
    assert 'class="requirementDiagram"' in svg


def test_svg_root_viewbox_is_four_numbers() -> None:
    """``viewBox`` carries four space-separated numbers (grok L712)."""
    svg = _render(_GROK_SMOKE)
    assert 'viewBox="0 0 ' in svg
    # The viewBox width/height are the canvas span -- non-zero for a real graph.
    head = svg.split('viewBox="', 1)[1].split('"', 1)[0]
    parts = head.split(" ")
    assert len(parts) == 4
    assert all(p.replace(".", "", 1).isdigit() for p in parts)


def test_svg_root_has_xmlns_namespaces() -> None:
    """Both ``xmlns`` and ``xmlns:xlink`` namespaces are declared (grok L713)."""
    svg = _render(_GROK_SMOKE)
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert 'xmlns:xlink="http://www.w3.org/1999/xlink"' in svg


# === 3. Markers (grok L726-L737) ============================================


def test_contains_start_marker_is_circle_plus_crosshair() -> None:
    """``containsStart`` is a circle with a crosshair line pair, refX=0."""
    svg = _render(_GROK_SMOKE)
    assert 'id="my-svg_requirement-requirement_containsStart"' in svg
    assert 'refX="0"' in svg
    assert "<circle" in svg
    # Crosshair = two <line> children inside the marker's <g>.
    marker_open = svg.index("containsStart")
    marker_close = svg.index("</marker>", marker_open)
    marker_body = svg[marker_open:marker_close]
    assert marker_body.count("<line") == 2


def test_arrow_end_marker_is_open_chevron() -> None:
    """``arrowEnd`` is an open chevron path ``M0,0 L20,10 M20,10 L0,20``, refX=20."""
    svg = _render(_GROK_SMOKE)
    assert 'id="my-svg_requirement-requirement_arrowEnd"' in svg
    assert 'refX="20"' in svg
    assert 'd="M0,0 L20,10 M20,10 L0,20"' in svg


def test_markers_stroked_with_edge_color() -> None:
    """Both marker groups stroke with the theme ``edge_color`` (grok L730/L737)."""
    svg = _render(_GROK_SMOKE, MermaidTheme.dark())
    assert f'stroke="{_DARK_EDGE}"' in svg


# === 4. Style block -- 7 channels (grok L716-L722) =========================


def test_style_block_emits_seven_channels() -> None:
    """The ``<style>`` block carries the 5 theme channels + 2 fixed rules.

    Channels: root fill (text_color), relationshipLine stroke (edge_color),
    node rect fill+stroke (node_fill/node_stroke), label color (text_color),
    label text fill (text_color), and the fixed ``labelBkg`` rgba.
    """
    svg = _render(_GROK_SMOKE, MermaidTheme.dark())
    assert f"fill:{_DARK_TEXT};}}" in svg  # root #my-svg fill
    assert f".relationshipLine{{stroke:{_DARK_EDGE};" in svg
    assert f".node rect{{fill:{_DARK_NODE_FILL};" in svg
    assert f"stroke:{_DARK_NODE_STROKE};stroke-width:1.3;" in svg
    assert ".label{font-family:" in svg and f"color:{_DARK_TEXT};" in svg
    assert ".labelBkg{background-color:rgba(232,232,232, 0.8);}" in svg


# === 5. Nodes (grok L769-L849) =============================================


def test_nodes_emitted_sorted_by_id() -> None:
    """Node ``<g>`` groups are emitted in ascending id order (grok L769).

    ``System`` sorts before ``TestReq`` regardless of source order.
    """
    svg = _render(_GROK_SMOKE)
    assert svg.index('id="System"') < svg.index('id="TestReq"')


def test_requirement_node_renders_header_name_and_body() -> None:
    """A requirement node emits ``<<Requirement>>`` + bold name + body lines."""
    svg = _render(_GROK_SMOKE)
    assert "&lt;&lt;Requirement&gt;&gt;" in svg
    assert ">TestReq<" in svg
    assert "ID: 1" in svg
    assert "Text: The system shall do X" in svg


def test_element_node_renders_element_header_and_body() -> None:
    """An element node emits ``<<Element>>`` + bold name + Type/Doc Ref lines."""
    svg = _render(_GROK_SMOKE)
    assert "&lt;&lt;Element&gt;&gt;" in svg
    assert ">System<" in svg
    assert "Type: Model" in svg
    assert "Doc Ref: RFC 1" in svg


def test_node_name_label_is_bold_others_are_not() -> None:
    """Only the name line carries ``font-weight="bold"`` (grok L692)."""
    svg = _render(_GROK_SMOKE)
    # The name is bold; the ``<<type>>`` header and body lines are not.
    assert 'font-weight="bold">TestReq<' in svg
    assert 'font-weight="bold">&lt;&lt;Requirement&gt;&gt;' not in svg


def test_divider_line_present_when_node_has_body() -> None:
    """A node with body lines renders a divider ``<line>`` (grok L699-L705).

    The divider is the ONLY ``<line>`` carrying the ``stroke-width="1.3"``
    attribute -- the marker crosshair ``<line>``s have no width, and the
    ``<style>`` block uses the CSS colon form. So we assert against the
    TestReq node group (which has 4 body lines) rather than the whole SVG.
    """
    svg = _render(_GROK_SMOKE)
    node = _node_group(svg, "TestReq")
    assert "<line" in node
    assert _DIVIDER_SIG in node


def test_no_divider_for_bare_element_node() -> None:
    """A body-less element node emits NO divider (grok L699 guard).

    The bare ``element System {}`` has no body lines -> the node group carries
    only ``<rect>`` + 2 ``<text>`` (header + bold name), no ``<line>``.
    """
    svg = _render(_BARE_ELEMENT)
    node = _node_group(svg, "System")
    assert "<line" not in node
    assert _DIVIDER_SIG not in svg  # no divider anywhere
    assert "&lt;&lt;Element&gt;&gt;" in svg


def test_bare_element_still_renders_header_and_name() -> None:
    """A body-less element still shows ``<<Element>>`` + its bold name."""
    svg = _render(_BARE_ELEMENT)
    assert "&lt;&lt;Element&gt;&gt;" in svg
    assert 'font-weight="bold">System<' in svg


# === 6. Node-kind -> type-line mapping (grok L82-L90 + fallbacks) ==========


@pytest.mark.parametrize(
    "kind,expected_header",
    [
        ("requirement", "Requirement"),
        ("functionalRequirement", "Functional Requirement"),
        ("interfaceRequirement", "Interface Requirement"),
        ("performanceRequirement", "Performance Requirement"),
        ("physicalRequirement", "Physical Requirement"),
        ("designConstraint", "Design Constraint"),
        ("element", "Element"),
    ],
)
def test_node_kind_maps_to_type_header(kind: str, expected_header: str) -> None:
    """Each of the 7 valid kinds maps to its ``<<type>>`` header text.

    The 5 typed requirements use ``_REQ_TYPE_MAP``; bare ``requirement`` falls
    back to ``"Requirement"``; ``element`` uses the hard-coded ``<<Element>>``
    header. Kind matching is case-insensitive (``kind.lower()``).
    """
    if kind == "element":
        source = f"requirementDiagram\n{kind} Foo {{\n}}\n"
    else:
        source = f"requirementDiagram\n{kind} Foo {{\n  id: 1\n}}\n"
    svg = _render(source)
    assert f"&lt;&lt;{expected_header}&gt;&gt;" in svg


def test_node_kind_matching_is_case_insensitive() -> None:
    """``Requirement`` (mixed case) parses like ``requirement`` (grok lower)."""
    source = "requirementDiagram\nRequirement Foo {\n  id: 1\n}\n"
    svg = _render(source)
    assert "&lt;&lt;Requirement&gt;&gt;" in svg
    assert 'id="Foo"' in svg


# === 7. Edges & labels (grok L741-L766) ====================================


def test_forward_relation_edge_is_dashed_with_arrow_end() -> None:
    """``A -contains-> B`` renders a dashed path with the ``arrowEnd`` marker.

    The rel_type is ``-contains`` (leading dash preserved), so ``is_contains``
    is False -> dashed + marker. Edge id is ``{src}-{dst}-{idx}``.
    """
    svg = _render(_TWO_NODES + "A -contains-> B")
    assert 'id="A-B-0"' in svg
    assert "stroke-dasharray: 10,7" in svg
    assert 'marker-end="url(#my-svg_requirement-requirement_arrowEnd)"' in svg


def test_forward_relation_label_carries_leading_dash() -> None:
    """``A -contains-> B`` edge label is ``<<-contains>>`` (dash preserved).

    grok L384-L385 tokeniser: ``src=tokens[0]``, ``rel=tokens[1]`` -- no dash
    stripping, so ``-contains`` (one token, not ``-``) survives verbatim.
    """
    svg = _render(_TWO_NODES + "A -contains-> B")
    assert "&lt;&lt;-contains&gt;&gt;" in svg


def test_backward_relation_flips_src_and_dst() -> None:
    """``A <- contains - B`` means dst=A, src=B -- edge id is ``B-A-0``.

    grok ``<-`` branch: ``dst=lhs`` (A), ``src=tokens[1]`` (B). The edge id
    is therefore ``{src}-{dst}-{idx}`` = ``B-A-0``.
    """
    svg = _render(_TWO_NODES + "A <- contains - B")
    assert 'id="B-A-0"' in svg


def test_backward_contains_is_solid_without_marker() -> None:
    """Backward ``contains`` (dash-free) renders SOLID with NO marker.

    grok dash-position asymmetry: forward ``-contains`` (leading dash) has
    ``rel_type="-contains"`` -> ``is_contains=False`` -> dashed + arrowEnd.
    Backward ``contains`` (the ``-`` is an isolated token, filtered out) has
    ``rel_type="contains"`` -> ``is_contains=True`` -> solid, no marker.
    """
    svg = _render(_TWO_NODES + "A <- contains - B")
    assert "&lt;&lt;contains&gt;&gt;" in svg  # no leading dash
    # This edge is solid: no stroke-dasharray, no marker-end on its <path>.
    assert "stroke-dasharray: 10,7" not in svg


def test_backward_non_contains_is_dashed() -> None:
    """Backward ``copies`` (not ``contains``) still renders dashed + marker."""
    svg = _render(_TWO_NODES + "A <- copies - B")
    assert "&lt;&lt;copies&gt;&gt;" in svg
    assert "stroke-dasharray: 10,7" in svg


def test_edge_label_is_xml_escaped() -> None:
    """The ``<<rel>>`` label is XML-escaped to ``&lt;&lt;rel&gt;&gt;``."""
    svg = _render(_TWO_NODES + "A -contains-> B")
    assert "&lt;&lt;-contains&gt;&gt;" in svg
    assert "<<-contains>>" not in svg  # raw form never reaches the output


def test_edge_label_has_gray_background_rect() -> None:
    """The edge label rect is ``#E8E8E8`` at 0.8 opacity (grok L766)."""
    svg = _render(_TWO_NODES + "A -contains-> B")
    assert f'fill="{_EDGE_LABEL_FILL}"' in svg
    assert 'fill-opacity="0.8"' in svg


def test_edge_label_group_has_edgeLabel_class() -> None:
    """The edge-label ``<g>`` carries ``class="edgeLabel"`` (grok L760)."""
    svg = _render(_TWO_NODES + "A -contains-> B")
    assert 'class="edgeLabel"' in svg


def test_edge_label_text_uses_theme_text_color() -> None:
    """The edge-label ``<text>`` fill is the theme ``text_color`` (grok L765)."""
    svg = _render(_TWO_NODES + "A -contains-> B", MermaidTheme.dark())
    # The edge-label <text> is the only text inside an edgeLabel <g>.
    g_start = svg.index('class="edgeLabel"')
    text_seg = svg[g_start : svg.index("</g>", g_start)]
    assert f'fill="{_DARK_TEXT}"' in text_seg


# === 8. Theme -- 5 palette channels flow verbatim (grok L716-L722) ========


def test_theme_light_background_flows_into_root_style() -> None:
    """Light ``background`` appears in the root ``style`` attribute."""
    svg = _render(_GROK_SMOKE, MermaidTheme.light())
    assert f"background-color: {_LIGHT_BG};" in svg


def test_theme_light_text_color_flows_into_style_and_labels() -> None:
    """Light ``text_color`` appears in the root fill + label color rules."""
    svg = _render(_GROK_SMOKE, MermaidTheme.light())
    assert f"fill:{_LIGHT_TEXT};" in svg
    assert f"color:{_LIGHT_TEXT};" in svg


def test_theme_light_edge_color_flows_into_markers_and_relationships() -> None:
    """Light ``edge_color`` strokes both markers + the relationshipLine rule."""
    svg = _render(_GROK_SMOKE, MermaidTheme.light())
    assert f".relationshipLine{{stroke:{_LIGHT_EDGE};" in svg
    assert f'stroke="{_LIGHT_EDGE}"' in svg  # marker strokes


def test_theme_light_node_fill_flows_into_node_rect() -> None:
    """Light ``node_fill`` fills the ``.node rect`` rule."""
    svg = _render(_GROK_SMOKE, MermaidTheme.light())
    assert f".node rect{{fill:{_LIGHT_NODE_FILL};" in svg


def test_theme_light_node_stroke_flows_into_rect_and_divider() -> None:
    """Light ``node_stroke`` strokes the rect rule + divider ``<line>``."""
    svg = _render(_GROK_SMOKE, MermaidTheme.light())
    assert f"stroke:{_LIGHT_NODE_STROKE};stroke-width:1.3;" in svg


def test_theme_dark_all_five_channels_flow() -> None:
    """The dark palette flows all 5 channels into the SVG (no normalisation)."""
    svg = _render(_GROK_SMOKE, MermaidTheme.dark())
    assert f"background-color: {_DARK_BG};" in svg
    assert f"fill:{_DARK_TEXT};" in svg
    assert f".relationshipLine{{stroke:{_DARK_EDGE};" in svg
    assert f".node rect{{fill:{_DARK_NODE_FILL};" in svg
    assert f"stroke:{_DARK_NODE_STROKE};stroke-width:1.3;" in svg


# === 9. Parsing -- direction, props, aliases, normalisation ===============


@pytest.mark.parametrize("direction", ["TB", "TD", "BT", "LR", "RL"])
def test_valid_direction_renders_without_error(direction: str) -> None:
    """Each of the 5 direction tokens parses (``TB``/``TD`` alias to ``Tb``)."""
    source = f"requirementDiagram\ndirection {direction}\nrequirement A {{\n  id: 1\n}}\n"
    svg = _render(source)
    assert "<svg" in svg and "</svg>" in svg


def test_direction_defaults_to_tb_when_omitted() -> None:
    """No ``direction`` clause -> default ``_Direction.Tb`` (grok L130)."""
    svg = _render("requirementDiagram\nrequirement A {\n  id: 1\n}\n")
    assert "<svg" in svg  # parsed and rendered with the default rankdir


def test_element_props_type_and_docref() -> None:
    """An element reads ``type`` + ``docRef`` props into its body lines."""
    svg = _render("requirementDiagram\nelement Doc {\n  type: Spec\n  docRef: ABC\n}\n")
    assert "Type: Spec" in svg
    assert "Doc Ref: ABC" in svg


def test_element_docref_lowercase_alias() -> None:
    """``docref`` (lowercase) is accepted alongside ``docRef`` (grok L300)."""
    svg = _render("requirementDiagram\nelement Doc {\n  type: Spec\n  docref: ABC\n}\n")
    assert "Doc Ref: ABC" in svg


def test_requirement_props_full_set() -> None:
    """A requirement reads id/text/risk/verifyMethod props into body lines."""
    svg = _render(
        "requirementDiagram\nrequirement Q {\n"
        "  id: 9\n  text: hello\n  risk: high\n  verifyMethod: test\n}\n"
    )
    assert "ID: 9" in svg
    assert "Text: hello" in svg
    assert "Risk: High" in svg  # normalised
    assert "Verification: Test" in svg  # normalised


def test_requirement_verifymethod_lowercase_alias() -> None:
    """``verifymethod`` (lowercase) is accepted alongside ``verifyMethod``."""
    svg = _render(
        "requirementDiagram\nrequirement Q {\n"
        "  id: 9\n  verifymethod: analysis\n}\n"
    )
    assert "Verification: Analysis" in svg


@pytest.mark.parametrize(
    "raw,expected",
    [("low", "Low"), ("medium", "Medium"), ("high", "High")],
)
def test_risk_canonicalisation(raw: str, expected: str) -> None:
    """Known risk values are title-cased (grok ``normalize_risk`` L332-L339)."""
    svg = _render(f"requirementDiagram\nrequirement Q {{\n  risk: {raw}\n}}\n")
    assert f"Risk: {expected}" in svg


def test_risk_unknown_passthrough() -> None:
    """An unknown risk value passes through stripped, not title-cased."""
    svg = _render("requirementDiagram\nrequirement Q {\n  risk: critical\n}\n")
    assert "Risk: critical" in svg


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("analysis", "Analysis"),
        ("demonstration", "Demonstration"),
        ("inspection", "Inspection"),
        ("test", "Test"),
    ],
)
def test_verify_method_canonicalisation(raw: str, expected: str) -> None:
    """Known verifyMethod values are title-cased (grok L341-L349)."""
    svg = _render(
        f"requirementDiagram\nrequirement Q {{\n  verifyMethod: {raw}\n}}\n"
    )
    assert f"Verification: {expected}" in svg


def test_prop_value_strips_surrounding_quotes() -> None:
    """A quoted prop value has its surrounding quotes stripped (grok L351)."""
    svg = _render('requirementDiagram\nrequirement Q {\n  text: "hello world"\n}\n')
    assert "Text: hello world" in svg
    assert 'Text: "hello world"' not in svg


# === 10. Errors -- every ParseError path (grok L122-L366) =================


def test_error_missing_header_raises_parse_error() -> None:
    """A first line that is not ``requirementDiagram`` raises at line 1."""
    with pytest.raises(ParseError) as exc_info:
        _render("flowchart TD\n  A --> B")
    assert exc_info.value.line == 1
    assert "requirementDiagram" in str(exc_info.value)


def test_error_empty_source_raises_parse_error() -> None:
    """An empty body raises the missing-header error at line 1."""
    with pytest.raises(ParseError) as exc_info:
        _render("")
    assert exc_info.value.line == 1


def test_error_bad_direction_raises_parse_error() -> None:
    """An unknown direction token raises at the direction line."""
    with pytest.raises(ParseError) as exc_info:
        _render("requirementDiagram\ndirection XX\nrequirement A {\n  id: 1\n}\n")
    assert exc_info.value.line == 2
    assert "Invalid direction: XX" in str(exc_info.value)


def test_error_unnamed_node_raises_parse_error() -> None:
    """A ``kind`` with no name raises ``Expected name after '<kind>'``."""
    with pytest.raises(ParseError) as exc_info:
        _render("requirementDiagram\nrequirement {\n  id: 1\n}\n")
    assert exc_info.value.line == 2
    assert "Expected name after 'requirement'" in str(exc_info.value)


def test_error_malformed_property_line_raises_parse_error() -> None:
    """A body line without ``:`` raises ``Invalid property line``."""
    with pytest.raises(ParseError) as exc_info:
        _render("requirementDiagram\nrequirement Q {\n  garbage no colon\n}\n")
    assert exc_info.value.line == 3
    assert "Invalid property line" in str(exc_info.value)


def test_error_unrecognized_line_raises_parse_error() -> None:
    """A line that is neither a node nor a relation raises ``Unrecognized``."""
    with pytest.raises(ParseError) as exc_info:
        _render("requirementDiagram\nsome random line here\n")
    assert "Unrecognized requirementDiagram line" in str(exc_info.value)


def test_error_malformed_forward_relation_raises_parse_error() -> None:
    """``A ->`` (no rel token) raises ``Invalid relationship``."""
    with pytest.raises(ParseError) as exc_info:
        _render(_TWO_NODES + "A -> B")
    assert "Invalid relationship" in str(exc_info.value)


def test_error_malformed_backward_relation_raises_parse_error() -> None:
    """``A <- B`` (single rhs token, needs rel + src) raises ``Invalid relationship``."""
    with pytest.raises(ParseError) as exc_info:
        _render(_TWO_NODES + "A <- B")
    assert "Invalid relationship" in str(exc_info.value)


# === 11. Helpers -- pure formatting primitives ============================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("low", "Low"),
        ("Low", "Low"),
        ("LOW", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "critical"),  # unknown -> stripped passthrough
        ("  High  ", "High"),  # whitespace stripped before match
    ],
)
def test_normalize_risk(raw: str, expected: str) -> None:
    """``_normalize_risk`` title-cases known values, passes unknown through."""
    assert _normalize_risk(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("analysis", "Analysis"),
        ("demonstration", "Demonstration"),
        ("inspection", "Inspection"),
        ("test", "Test"),
        ("Analysis", "Analysis"),  # case-insensitive match
        ("simulation", "simulation"),  # unknown passthrough
    ],
)
def test_normalize_verify_method(raw: str, expected: str) -> None:
    """``_normalize_verify_method`` title-cases the 4 known verify methods."""
    assert _normalize_verify_method(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"foo"', "foo"),
        ("'foo'", "foo"),
        ("foo", "foo"),  # no quotes -> unchanged
        ('"foo', '"foo'),  # unmatched -> unchanged
        ("foo'", "foo'"),  # unmatched -> unchanged
        ('""', ""),  # empty quoted -> empty
        ('  "foo"  ', "foo"),  # outer whitespace stripped first
    ],
)
def test_strip_quotes(raw: str, expected: str) -> None:
    """``_strip_quotes`` removes a MATCHED pair of surrounding quotes only."""
    assert _strip_quotes(raw) == expected


def test_split_once_ws_basic_split() -> None:
    """``_split_once_ws`` splits on the first whitespace, trims the rest."""
    assert _split_once_ws("a b c") == ("a", "b c")


def test_split_once_ws_collapses_inner_whitespace_in_rest() -> None:
    """The rest component is trimmed (grok ``b.trim()``)."""
    first, rest = _split_once_ws("kind   name {")
    assert first == "kind"
    assert rest == "name {"


def test_split_once_ws_no_whitespace_returns_empty_rest() -> None:
    """No whitespace -> ``(s, "")``."""
    assert _split_once_ws("solo") == ("solo", "")


def test_split_once_ws_empty_string() -> None:
    """Empty input -> ``("", "")``."""
    assert _split_once_ws("") == ("", "")


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, "0"),
        (1.0, "1"),
        (252.0, "252"),
        (-3.0, "-3"),
    ],
)
def test_fmt_integer_drops_fraction(value: float, expected: str) -> None:
    """``_fmt`` drops the fractional part of whole-valued floats."""
    assert _fmt(value) == expected


def test_fmt_fraction_uses_shortest_repr() -> None:
    """``_fmt`` uses Python's shortest round-trip repr for fractions."""
    assert _fmt(1.5) == "1.5"
    assert _fmt(10.25) == "10.25"


def test_escape_xml_replaces_five_special_chars() -> None:
    """``_escape_xml`` escapes ``&`` first, then ``<``/``>``/``"``/``'``."""
    assert _escape_xml("a<b>&'\"") == "a&lt;b&gt;&amp;&apos;&quot;"


def test_escape_xml_ampersand_first() -> None:
    """``&`` is replaced first so introduced entities are not re-escaped."""
    assert _escape_xml("<&>") == "&lt;&amp;&gt;"


def test_points_to_path_d_builds_move_then_lines() -> None:
    """``_points_to_path_d`` emits ``M`` then ``L`` segments."""
    d = _points_to_path_d([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)])
    assert d == "M1,2 L3,4 L5,6"


def test_points_to_path_d_empty_input() -> None:
    """An empty point list yields the empty string (``d=""``)."""
    assert _points_to_path_d([]) == ""


def test_points_to_path_d_single_point() -> None:
    """A single point yields a lone ``M`` move (no ``L``)."""
    assert _points_to_path_d([(7.0, 8.0)]) == "M7,8"


@pytest.mark.parametrize(
    "direction,rankdir",
    [
        (_Direction.Tb, "tb"),
        (_Direction.Bt, "bt"),
        (_Direction.Lr, "lr"),
        (_Direction.Rl, "rl"),
    ],
)
def test_direction_as_rankdir(direction: _Direction, rankdir: str) -> None:
    """``_Direction.as_rankdir`` returns the dagre rankdir string."""
    assert direction.as_rankdir() == rankdir
