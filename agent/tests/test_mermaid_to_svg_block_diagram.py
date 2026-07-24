"""Tests for the migrated ``block-beta`` renderer (R289 -- direction (1) brick 20).

Exercises :mod:`minimax_code.mermaid.to_svg.block_diagram` -- the functional
clone of grok ``mermaid-to-svg/src/block_diagram.rs`` (the 11th per-diagram
leaf and the 10th self-contained SVG emitter, same lineage as R279 info /
R281 radar / R282 pie / R283 packet / R284 sankey / R285 gantt / R286 kanban /
R287 timeline / R288 quadrant). ``block-beta`` is mermaid's grid-laid block
diagram: labelled rectangular nodes on a ``columns``-driven grid connected by
D3 ``curveBasis`` edges. Block is the **most theme-aware** renderer in the
family -- all five ``MermaidTheme`` channels flow into the SVG.

Coverage matrix (zero-semantic clone contract):

* **grok smoke** (lib.rs L1013-L1024) -- the canonical ``A --> B`` fixture
  asserts the SVG carries ``aria-roledescription="block"``, ``id="A"``, and
  ``id="B"``.
* **SVG root** -- ``aria-roledescription="block"``, ``role="graphics-document
  document"``, a 4-number ``viewBox``, the themed ``max-width`` /
  ``background-color`` style, ``id="my-svg"``.
* **Markers** -- the six block-family arrow markers (point / circle / cross,
  End / Start) emit with their contract ``refX`` values.
* **Nodes** -- the ``node default default flowchart-label`` group, the
  ``basic label-container`` rect, the centred ``nodeLabel`` text, XML-escaped
  labels, and ``id[label]`` bracket syntax.
* **Edges** -- the D3 ``curveBasis`` ``<path>`` (``M..L..C..``), the
  ``marker-end`` arrowhead link, the ``LS-/LE-`` flowchart-link classes, and
  the ``{n}-{from}-{to}`` id format.
* **Theme awareness** -- light ``#ffffff`` -> ``white`` and ``#333333`` ->
  ``#333`` normalization; the dark palette's five channels all reach the SVG.
* **Parsing** -- ``columns N`` / ``columns auto`` grid sizing; the skipped
  directives (``style`` / ``classDef`` / ``class`` / ``linkStyle`` / ``space``
  / ``space:N`` / ``block:`` / ``end``); the silent-swallow vs. propagate
  asymmetry (a bad standalone node is skipped; a bad edge endpoint raises).
* **Errors** -- a missing or wrong header raises :class:`ParseError`.
* **Helpers** -- ``_round_half_away`` (half away from zero, not banker's),
  ``_fmt`` (Rust Display bridge), ``_fmt_num`` (D3 3-decimal bridge),
  ``_calculate_block_position`` (3-branch grid cell), ``_rect_intersect``
  (ray/rect boundary), ``_curve_basis_path`` (D3 curveBasis spline).

The renderer is reached only via the ``render.py`` dispatch arm; the barrel
does NOT re-export it (grok never re-exports per-diagram renderers from the
crate root), so tests import it directly from the leaf module.
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg.block_diagram import (
    ARROW_POINT_OFFSET,
    BLOCK_CHAR_WIDTH,
    BLOCK_PADDING,
    BLOCK_TEXT_HEIGHT,
    VB_MARGIN,
    _calculate_block_position,
    _curve_basis_path,
    _fmt,
    _fmt_num,
    _parse_block_beta,
    _parse_block_node,
    _rect_intersect,
    _round_half_away,
    render_block_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# === grok smoke (lib.rs L1013-L1024) =======================================


def test_block_grok_smoke_two_nodes_edge() -> None:
    """The canonical ``A --> B`` fixture mirrors grok's crate-root smoke.

    grok lib.rs L1013-L1024 asserts the emitted SVG contains
    ``aria-roledescription="block"``, ``id="A"``, and ``id="B"`` -- the bare
    minimum that the block renderer is wired and emitting node groups for the
    two declared nodes. The edge between them is rendered as a curveBasis
    path with an arrowhead marker.
    """
    svg = render_block_diagram_to_svg("block-beta\nA --> B", MermaidTheme.light())
    assert 'aria-roledescription="block"' in svg
    assert 'id="A"' in svg
    assert 'id="B"' in svg
    # The A->B edge carries the pointEnd arrowhead marker link.
    assert 'marker-end="url(#my-svg_block-pointEnd)"' in svg


# === SVG root structure (grok L134-L145) ===================================


def test_block_root_svg_has_block_aria_roledescription() -> None:
    """The root ``<svg>`` carries ``aria-roledescription="block"``."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert '<svg aria-roledescription="block"' in svg


def test_block_root_svg_has_graphics_document_role() -> None:
    """The root ``<svg>`` carries the ``graphics-document document`` role."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'role="graphics-document document"' in svg


def test_block_root_svg_has_my_svg_id() -> None:
    """The root ``<svg>`` carries ``id="my-svg"`` (the CSS anchor)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'id="my-svg">' in svg
    assert svg.endswith("</svg>")


def test_block_root_svg_has_four_number_viewbox() -> None:
    """The ``viewBox`` is four space-separated numbers (x y w h)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    # Pull the viewBox attribute value and confirm it is "n n n n".
    start = svg.index('viewBox="') + len('viewBox="')
    end = svg.index('"', start)
    parts = svg[start:end].split()
    assert len(parts) == 4
    for token in parts:
        # Each token is an integer or a decimal (optional leading "-").
        assert token.lstrip("-").replace(".", "", 1).isdigit()


def test_block_root_svg_max_width_matches_viewbox_width() -> None:
    """The ``max-width`` style knob mirrors the viewBox width (grok L142)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    vb_start = svg.index('viewBox="') + len('viewBox="')
    vb_end = svg.index('"', vb_start)
    vb_width = svg[vb_start:vb_end].split()[2]
    assert f"max-width: {vb_width}px;" in svg


# === six arrow markers (grok L150-L177) ====================================


@pytest.mark.parametrize(
    "marker_id, ref_x",
    [
        ("my-svg_block-pointEnd", "6"),
        ("my-svg_block-pointStart", "4.5"),
        ("my-svg_block-circleEnd", "11"),
        ("my-svg_block-circleStart", "-1"),
        ("my-svg_block-crossEnd", "12"),
        ("my-svg_block-crossStart", "-1"),
    ],
)
def test_block_emits_six_arrow_markers(marker_id: str, ref_x: str) -> None:
    """Each of the six block-family markers ships with its contract ``refX``.

    Mirrors grok L150-L177: point/circle/cross x End/Start = 6 markers, each
    carrying the ``marker block`` class and its own ``refX`` landing offset.
    """
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert f'id="{marker_id}"' in svg
    assert f'refX="{ref_x}"' in svg


def test_block_markers_carry_block_class() -> None:
    """Every marker carries the ``marker block`` (or ``marker cross block``) class."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'class="marker block"' in svg
    assert 'class="marker cross block"' in svg


# === block group + style (grok L134, L147, L179) ==========================


def test_block_emits_block_group_wrapper() -> None:
    """A single ``<g class="block">`` wraps every node and edge."""
    svg = render_block_diagram_to_svg("block-beta\nA\nB", MermaidTheme.light())
    assert svg.count('<g class="block">') == 1
    assert svg.endswith("</g></svg>")


def test_block_emits_style_element_with_css() -> None:
    """A ``<style>`` element carries the themed block CSS stylesheet."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "<style>" in svg
    assert "</style>" in svg
    # The CSS anchors on #my-svg (grok block_css L537).
    assert "#my-svg{" in svg or "#my-svg {" in svg


def test_block_emits_empty_placeholder_group() -> None:
    """An empty ``<g/>`` placeholder precedes the markers (grok L147)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "<g/>" in svg


# === node rendering (grok L172-L195) =======================================


def test_block_node_group_has_default_flowchart_label_classes() -> None:
    """Each node group carries the ``node default default flowchart-label`` classes."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'class="node default default flowchart-label"' in svg


def test_block_node_group_carries_id_and_transform() -> None:
    """A node group carries its id and a ``translate(cx, cy)`` transform."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'id="A" transform="translate(' in svg


def test_block_node_has_basic_label_container_rect() -> None:
    """Each node emits a ``basic label-container`` rect with x/y/width/height."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert '<rect class="basic label-container"' in svg
    assert 'rx="0" ry="0"' in svg
    assert "x=" in svg and "y=" in svg
    assert "width=" in svg and "height=" in svg


def test_block_node_has_centred_nodelabel_text() -> None:
    """Each node label is a centred ``nodeLabel`` text."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert 'text-anchor="middle" dominant-baseline="central"' in svg
    assert 'class="nodeLabel" dy="0">A</text>' in svg


def test_block_node_count_matches_declarations() -> None:
    """Three declared nodes emit exactly three node groups."""
    svg = render_block_diagram_to_svg(
        "block-beta\nA\nB\nC", MermaidTheme.light()
    )
    assert svg.count('<g class="node default default flowchart-label"') == 3


def test_block_bare_node_label_equals_id() -> None:
    """A bare token (no brackets) doubles as its own label (grok L498)."""
    svg = render_block_diagram_to_svg("block-beta\nAlpha", MermaidTheme.light())
    assert 'id="Alpha"' in svg
    assert 'class="nodeLabel" dy="0">Alpha</text>' in svg


def test_block_bracket_label_uses_explicit_label() -> None:
    """``id[Label]`` keeps the id but renders the explicit label text."""
    svg = render_block_diagram_to_svg(
        "block-beta\nA[Alpha]", MermaidTheme.light()
    )
    assert 'id="A"' in svg
    assert 'class="nodeLabel" dy="0">Alpha</text>' in svg
    # The bare id is NOT the rendered label when an explicit label is given.
    assert 'class="nodeLabel" dy="0">A</text>' not in svg


def test_block_label_xml_escapes_special_characters() -> None:
    """Label text escapes the five XML-significant characters (grok L541)."""
    svg = render_block_diagram_to_svg(
        'block-beta\nA[A & <B> "C"]', MermaidTheme.light()
    )
    assert "A &amp; &lt;B&gt; &quot;C&quot;</text>" in svg


def test_block_id_xml_escapes_special_characters() -> None:
    """The node id is XML-escaped too (it flows into the id attribute)."""
    svg = render_block_diagram_to_svg(
        'block-beta\nA&B["x"]', MermaidTheme.light()
    )
    assert 'id="A&amp;B"' in svg


# === edge rendering (grok L200-L243) =======================================


def test_block_edge_emits_curvebasis_path() -> None:
    """An edge renders as a D3 curveBasis path (``M..L..C..``)."""
    svg = render_block_diagram_to_svg("block-beta\nA --> B", MermaidTheme.light())
    # The path carries the pointEnd marker and a curveBasis ``d`` data string.
    assert 'marker-end="url(#my-svg_block-pointEnd)"' in svg
    # curveBasis for >=3 points opens with M..L (leading third) then C.
    assert 'd="M' in svg


def test_block_edge_has_flowchart_link_classes() -> None:
    """An edge carries the edge-thickness/pattern + flowchart-link classes.

    The ``LS-{from}1`` / ``LE-{to}1`` link classes are lowercased ids with a
    trailing ``1`` (grok L235-L237).
    """
    svg = render_block_diagram_to_svg("block-beta\nAlpha --> Beta", MermaidTheme.light())
    assert "LS-alpha1" in svg
    assert "LE-beta1" in svg
    assert "edge-thickness-normal edge-pattern-solid flowchart-link" in svg


def test_block_edge_id_format_is_index_from_to() -> None:
    """The edge path id is ``{n}-{from}-{to}`` (1-based index, grok L233)."""
    svg = render_block_diagram_to_svg("block-beta\nA --> B", MermaidTheme.light())
    assert 'id="1-A-B"' in svg


def test_block_multiple_edges_have_increasing_indices() -> None:
    """A second edge gets index ``2`` in its path id."""
    svg = render_block_diagram_to_svg(
        "block-beta\nA --> B\nB --> C", MermaidTheme.light()
    )
    assert 'id="1-A-B"' in svg
    assert 'id="2-B-C"' in svg


def test_block_edge_count_matches_declarations() -> None:
    """Three declared edges emit exactly three edge paths."""
    svg = render_block_diagram_to_svg(
        "block-beta\nA --> B\nB --> C\nC --> A", MermaidTheme.light()
    )
    assert svg.count('marker-end="url(#my-svg_block-pointEnd)"') == 3


# === theme awareness (grok L123-L132 + block_css L535-L539) ================


def test_block_light_background_normalizes_to_white() -> None:
    """Light ``#ffffff`` background normalizes to the CSS keyword ``white``.

    Mirrors grok L123: ``background_color = if bg == "#ffffff" { "white" }``.
    The normalized value lands in the root svg ``background-color`` style.
    """
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "background-color: white;" in svg


def test_block_light_text_color_normalizes_to_333() -> None:
    """Light ``#333333`` text color normalizes to ``#333`` in the CSS fill.

    Mirrors grok L128: ``text_color = if tc == "#333333" { "#333" }``. Lands
    in the ``#my-svg`` font fill and every label text fill.
    """
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "fill:#333;" in svg


def test_block_light_node_fill_flows_into_node_rect_css() -> None:
    """Light ``#ECECFF`` node_fill reaches the ``.node rect`` fill (grok block_css)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "fill:#ECECFF;" in svg


def test_block_light_node_stroke_flows_into_node_rect_css() -> None:
    """Light ``#9370DB`` node_stroke reaches the ``.node rect`` stroke."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    assert "stroke:#9370DB;" in svg


def test_block_light_edge_color_flows_into_marker_css() -> None:
    """Light ``#333333`` edge_color reaches the ``.marker`` fill/stroke (un-normalized)."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.light())
    # edge_color is NOT subject to the #333333 -> #333 normalization (only
    # text_color is); the marker CSS carries the raw #333333.
    assert ".marker{fill:#333333;stroke:#333333;}" in svg


def test_block_dark_theme_flows_all_channels() -> None:
    """The dark palette's five channels all reach the SVG unchanged.

    Dark has no ``#ffffff`` / ``#333333`` values, so no normalization fires;
    every channel lands verbatim. This is the superset theme test -- block is
    the only renderer that reads all five ``MermaidTheme`` channels.
    """
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.dark())
    assert "background-color: #1e1e1e;" in svg  # background
    assert "fill:#ffffff;" in svg  # text_color
    assert "fill:#2d2d2d;" in svg  # node_fill
    assert "stroke:#888888;" in svg  # node_stroke
    assert ".marker{fill:#888888;stroke:#888888;}" in svg  # edge_color


def test_block_dark_background_not_normalized() -> None:
    """A non-``#ffffff`` background is NOT normalized to ``white``."""
    svg = render_block_diagram_to_svg("block-beta\nA", MermaidTheme.dark())
    assert "background-color: white;" not in svg


# === parsing: columns directive (grok L435-L443) ==========================


def test_block_columns_directive_sets_grid() -> None:
    """``columns 2`` lays four nodes on a 2x2 grid (all four still render)."""
    svg = render_block_diagram_to_svg(
        "block-beta\ncolumns 2\nA\nB\nC\nD", MermaidTheme.light()
    )
    assert svg.count('<g class="node default default flowchart-label"') == 4


def test_block_columns_auto_is_single_row() -> None:
    """``columns auto`` (-1) lays nodes on a single row (still renders all)."""
    svg = render_block_diagram_to_svg(
        "block-beta\ncolumns auto\nA\nB\nC", MermaidTheme.light()
    )
    assert svg.count('<g class="node default default flowchart-label"') == 3


def test_block_columns_default_is_auto_when_unset() -> None:
    """With no ``columns`` directive the grid defaults to auto (single row)."""
    svg = render_block_diagram_to_svg(
        "block-beta\nA\nB\nC", MermaidTheme.light()
    )
    assert svg.count('<g class="node default default flowchart-label"') == 3


def test_block_columns_directive_changes_layout_width() -> None:
    """``columns 1`` (single column) yields a taller, narrower viewBox than auto.

    The same three nodes laid out in a single column stack vertically, so the
    viewBox height grows and width shrinks relative to the single-row auto
    layout. Asserted via the viewBox geometry, not pixel-exact coordinates.
    """
    auto_svg = render_block_diagram_to_svg(
        "block-beta\nLongAlpha\nLongBeta\nLongGamma", MermaidTheme.light()
    )
    col_svg = render_block_diagram_to_svg(
        "block-beta\ncolumns 1\nLongAlpha\nLongBeta\nLongGamma",
        MermaidTheme.light(),
    )

    def _viewbox(s: str) -> tuple[float, float]:
        start = s.index('viewBox="') + len('viewBox="')
        end = s.index('"', start)
        parts = s[start:end].split()
        return float(parts[2]), float(parts[3])

    auto_w, auto_h = _viewbox(auto_svg)
    col_w, col_h = _viewbox(col_svg)
    # Single-column stacks vertically: narrower width, taller height.
    assert col_w < auto_w
    assert col_h > auto_h


# === parsing: skipped directives (grok L447-L464) =========================


def test_block_skips_style_classdef_class_linkstyle_directives() -> None:
    """``style``/``classDef``/``class``/``linkStyle`` lines are skipped cleanly.

    None of these directives declare nodes; they must not break the renderer
    nor emit spurious node groups. The single declared node ``A`` still ships.
    """
    source = (
        "block-beta\n"
        "style A fill:#ff0000\n"
        "classDef cls fill:#00ff00\n"
        "class A cls\n"
        "linkStyle 0 stroke:#0000ff\n"
        "A"
    )
    svg = render_block_diagram_to_svg(source, MermaidTheme.light())
    assert svg.count('<g class="node default default flowchart-label"') == 1
    assert 'id="A"' in svg


def test_block_skips_space_directives() -> None:
    """``space`` and ``space:N`` placeholders are skipped (invisible)."""
    source = "block-beta\nspace\nspace:2\nA\nspace\nB"
    svg = render_block_diagram_to_svg(source, MermaidTheme.light())
    assert svg.count('<g class="node default default flowchart-label"') == 2


def test_block_skips_block_group_markers() -> None:
    """``block:`` and ``end`` group markers are skipped (nested groups tolerated)."""
    source = "block-beta\nblock: grp\nA\nend\nB"
    svg = render_block_diagram_to_svg(source, MermaidTheme.light())
    assert svg.count('<g class="node default default flowchart-label"') == 2


def test_block_skips_comment_lines() -> None:
    """``%%`` comment lines are skipped before parsing (grok L420)."""
    source = "block-beta\n%% a comment\n%% another\nA"
    svg = render_block_diagram_to_svg(source, MermaidTheme.light())
    assert 'id="A"' in svg


# === parsing: silent-swallow vs. propagate (grok L467-L480) ===============


def test_block_swallows_bad_standalone_node() -> None:
    """A bad standalone node (bracket but no id) is silently skipped.

    Mirrors grok L477-L480: ``if let Ok(node) = parse_block_node(line)`` --
    a parse failure on a standalone declaration is swallowed (the offending
    line is just dropped), so the rest of the diagram still renders.
    """
    source = "block-beta\nA\n[orphan label no id]\nB"
    svg = render_block_diagram_to_svg(source, MermaidTheme.light())
    # A and B render; the bad middle line is dropped.
    assert 'id="A"' in svg
    assert 'id="B"' in svg
    assert svg.count('<g class="node default default flowchart-label"') == 2


def test_block_propagates_bad_edge_endpoint() -> None:
    """A bad edge endpoint propagates :class:`ParseError` (grok L467-L474 ``?``).

    Edge endpoints use the propagating ``?`` operator (not ``if let Ok``), so
    a bracket-without-id endpoint aborts the whole render.
    """
    source = "block-beta\nA --> [bad no id]"
    with pytest.raises(ParseError):
        render_block_diagram_to_svg(source, MermaidTheme.light())


def test_block_propagates_empty_edge_endpoint() -> None:
    """An empty edge endpoint propagates :class:`ParseError`` (``Empty node``)."""
    source = "block-beta\nA --> "
    with pytest.raises(ParseError):
        render_block_diagram_to_svg(source, MermaidTheme.light())


# === errors (grok L415-L424, L494) =========================================


def test_block_missing_header_raises_parse_error() -> None:
    """A body with no ``block-beta`` header raises :class:`ParseError`."""
    with pytest.raises(ParseError):
        render_block_diagram_to_svg("A --> B", MermaidTheme.light())


def test_block_wrong_header_raises_parse_error() -> None:
    """A ``flowchart`` header (not ``block-beta``) raises :class:`ParseError`."""
    with pytest.raises(ParseError):
        render_block_diagram_to_svg("flowchart TD\nA --> B", MermaidTheme.light())


def test_block_empty_body_raises_parse_error() -> None:
    """An empty body raises :class:`ParseError` (no header found)."""
    with pytest.raises(ParseError):
        render_block_diagram_to_svg("", MermaidTheme.light())


def test_block_header_must_be_first_non_blank_line() -> None:
    """A header on a later line (after a non-comment line) still parses.

    Blank/comment lines may precede the header; once a non-blank non-comment
    line is seen it must be the ``block-beta`` token.
    """
    svg = render_block_diagram_to_svg(
        "\n%% leading comment\nblock-beta\nA", MermaidTheme.light()
    )
    assert 'id="A"' in svg


# === helpers: float-formatting bridges ====================================


def test_round_half_away_rounds_half_up_for_positive() -> None:
    """``_round_half_away(2.5) == 3`` (half away from zero, NOT banker's 2)."""
    assert _round_half_away(2.5) == 3
    assert _round_half_away(0.5) == 1
    assert _round_half_away(1.5) == 2


def test_round_half_away_rounds_half_down_for_negative() -> None:
    """``_round_half_away(-0.5) == -1`` (half away from zero for negatives)."""
    assert _round_half_away(-0.5) == -1
    assert _round_half_away(-2.5) == -3


def test_round_half_away_non_half_boundaries() -> None:
    """Non-half values round to the nearest integer either way."""
    assert _round_half_away(2.4) == 2
    assert _round_half_away(2.6) == 3
    assert _round_half_away(-2.4) == -2


def test_fmt_drops_trailing_zero_for_integer_floats() -> None:
    """``_fmt`` mirrors Rust ``f64::Display``: integer floats drop ``.0``."""
    assert _fmt(250.0) == "250"
    assert _fmt(-5.0) == "-5"
    assert _fmt(0.0) == "0"


def test_fmt_keeps_short_repr_for_non_integers() -> None:
    """Non-integer floats use Python's shortest ``repr`` (matches Rust Display)."""
    assert _fmt(9.5) == "9.5"
    assert _fmt(-29.5) == "-29.5"


def test_fmt_num_strips_trailing_zeros() -> None:
    """``_fmt_num`` rounds to 3 decimals then strips trailing zeros / dot."""
    assert _fmt_num(250.0) == "250"
    assert _fmt_num(9.485) == "9.485"
    assert _fmt_num(-29.5) == "-29.5"


def test_fmt_num_rounds_to_three_decimals() -> None:
    """``_fmt_num`` rounds the 4th decimal away (half away from zero)."""
    assert _fmt_num(9.4856) == "9.486"
    assert _fmt_num(1.0005) == "1.001"  # half away from zero, not banker's


def test_fmt_num_strips_dangling_dot_for_whole_result() -> None:
    """A value that rounds whole drops the dangling dot (``10.0`` -> ``10``)."""
    assert _fmt_num(10.0) == "10"
    assert _fmt_num(0.0) == "0"


# === helpers: _calculate_block_position (grok L252) =======================


def test_calculate_block_position_auto_is_single_row() -> None:
    """``columns < 0`` (auto) -> ``(position, 0)`` for every position."""
    assert _calculate_block_position(-1, 0) == (0, 0)
    assert _calculate_block_position(-1, 3) == (3, 0)


def test_calculate_block_position_single_column() -> None:
    """``columns == 1`` -> ``(0, position)`` (a vertical stack)."""
    assert _calculate_block_position(1, 0) == (0, 0)
    assert _calculate_block_position(1, 3) == (0, 3)


def test_calculate_block_position_grid_modulo() -> None:
    """``columns > 1`` -> ``(position % columns, position // columns)``."""
    assert _calculate_block_position(2, 0) == (0, 0)
    assert _calculate_block_position(2, 1) == (1, 0)
    assert _calculate_block_position(2, 2) == (0, 1)
    assert _calculate_block_position(2, 3) == (1, 1)
    assert _calculate_block_position(3, 5) == (2, 1)


# === helpers: _rect_intersect (grok L266-L299) ============================


def test_rect_intersect_right_edge() -> None:
    """A ray due east hits the right edge at ``(cx+hw, cy)``."""
    # Rect centred (0,0), 10x10 -> half-extents (5,5); ray toward (5,0).
    assert _rect_intersect(0, 0, 10, 10, 5, 0) == (5, 0)


def test_rect_intersect_bottom_edge() -> None:
    """A ray due south hits the bottom edge at ``(cx, cy+hh)``."""
    assert _rect_intersect(0, 0, 10, 10, 0, 5) == (0, 5)


def test_rect_intersect_degenerate_ray_hits_right_edge() -> None:
    """A zero-length ray (target == centre) defaults to the right edge."""
    assert _rect_intersect(0, 0, 10, 10, 0, 0) == (5, 0)


def test_rect_intersect_offset_rect() -> None:
    """A non-origin rect intersects correctly (centred at (10, 20))."""
    # Rect centred (10,20), 6x4 -> half-extents (3,2); ray toward (20,20) due east.
    assert _rect_intersect(10, 20, 6, 4, 20, 20) == (13, 20)


# === helpers: _curve_basis_path (grok L302-L373) ==========================


def test_curve_basis_path_empty_returns_empty() -> None:
    """An empty point list yields an empty path string."""
    assert _curve_basis_path([]) == ""


def test_curve_basis_path_single_point_is_move() -> None:
    """A single point yields a lone ``Mx,y`` move."""
    assert _curve_basis_path([(1.0, 2.0)]) == "M1,2"


def test_curve_basis_path_two_points_is_move_line() -> None:
    """Two points yield ``M..L..`` (move + line, no curve)."""
    assert _curve_basis_path([(0.0, 0.0), (10.0, 10.0)]) == "M0,0L10,10"


def test_curve_basis_path_three_points_emits_curve_command() -> None:
    """Three+ points emit a leading ``M..L..`` then at least one ``C`` curve."""
    d = _curve_basis_path([(0.0, 0.0), (5.0, 5.0), (10.0, 0.0)])
    assert d.startswith("M0,0")
    assert "L" in d
    assert "C" in d
    # The curve closes at the final point (10,0).
    assert d.endswith("L10,0")


def test_curve_basis_path_uses_d3_number_format() -> None:
    """Path coordinates flow through ``_fmt_num`` (3-decimal, trailing-zero stripped)."""
    # (2*0+5)/3 = 1.6667 -> rounded 1.667 (trailing zero already absent).
    d = _curve_basis_path([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
    assert "1.667" in d


# === helpers: _parse_block_node + _parse_block_beta =======================


def test_parse_block_node_bare_token_doubles_as_label() -> None:
    """A bare token without brackets -> ``(token, token)``."""
    assert _parse_block_node("Alpha", 1) == ("Alpha", "Alpha")


def test_parse_block_node_bracket_keeps_explicit_label() -> None:
    """``id[Label]`` -> ``(id, Label)``."""
    assert _parse_block_node("A[Alpha]", 1) == ("A", "Alpha")


def test_parse_block_node_strips_surrounding_quotes() -> None:
    """A quoted label is unquoted (``id['Label']`` -> ``(id, Label)``)."""
    assert _parse_block_node("A['Alpha']", 1) == ("A", "Alpha")


def test_parse_block_node_empty_raises() -> None:
    """An empty input raises :class:`ParseError`."""
    with pytest.raises(ParseError):
        _parse_block_node("   ", 1)


def test_parse_block_node_bracket_without_id_raises() -> None:
    """``[label]`` with no id raises :class:`ParseError`."""
    with pytest.raises(ParseError):
        _parse_block_node("[orphan]", 1)


def test_parse_block_beta_returns_columns_and_nodes() -> None:
    """The parser exposes the parsed columns + node order + edges."""
    diagram = _parse_block_beta("block-beta\ncolumns 2\nA --> B\nC")
    assert diagram.columns == 2
    assert diagram.node_order == ["A", "B", "C"]
    assert diagram.edges == [("A", "B")]
    assert diagram.nodes["A"] == "A"
    assert diagram.nodes["B"] == "B"


def test_parse_block_beta_columns_auto_is_minus_one() -> None:
    """``columns auto`` sets columns to ``-1`` (the auto sentinel)."""
    diagram = _parse_block_beta("block-beta\ncolumns auto\nA")
    assert diagram.columns == -1


def test_parse_block_beta_columns_default_is_minus_one() -> None:
    """No ``columns`` directive leaves columns at ``-1`` (auto default)."""
    diagram = _parse_block_beta("block-beta\nA")
    assert diagram.columns == -1


def test_parse_block_beta_columns_non_numeric_left_unchanged() -> None:
    """A non-numeric, non-``auto`` ``columns`` rest leaves columns unchanged (grok ``if let Ok``)."""
    diagram = _parse_block_beta("block-beta\ncolumns bogus\nA")
    # The bogus value does not parse, so columns stays at the -1 default.
    assert diagram.columns == -1


def test_parse_block_beta_edge_inserts_both_nodes_in_order() -> None:
    """An edge registers both endpoints in first-seen order (grok L402)."""
    diagram = _parse_block_beta("block-beta\nA --> B")
    assert diagram.node_order == ["A", "B"]


def test_parse_block_beta_redeclared_node_keeps_first_order() -> None:
    """A later bare reference to an existing id keeps its first-seen order slot."""
    diagram = _parse_block_beta("block-beta\nA[Alpha]\nA --> B")
    # A was declared first; the bare ``A`` on the edge line does not reorder.
    assert diagram.node_order == ["A", "B"]
    # The explicit label survives the later bare reference (grok insert_node).
    assert diagram.nodes["A"] == "Alpha"


# === module surface ========================================================


def test_block_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly one public symbol (grok never re-exports per-diagram renderers)."""
    from minimax_code.mermaid.to_svg import block_diagram as block_mod

    assert block_mod.__all__ == ["render_block_diagram_to_svg"]


def test_block_constants_match_mermaid_defaults() -> None:
    """The four layout constants match mermaid 11.12.2 block defaults (grok L7-L27)."""
    assert BLOCK_PADDING == 8.0
    assert BLOCK_CHAR_WIDTH == pytest.approx(10.97)
    assert BLOCK_TEXT_HEIGHT == 19.0
    assert VB_MARGIN == 5.0
    assert ARROW_POINT_OFFSET == 4.0
