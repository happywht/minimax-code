"""Black-box tests for the migrated sankey-diagram renderer (R284).

Exercises :mod:`minimax_code.mermaid.to_svg.sankey_diagram` -- the sixth
per-diagram leaf and fifth self-contained SVG emitter, fused from grok
``mermaid-to-svg/src/sankey_diagram.rs`` (direction (1), leaf 17). The
``sankey-beta`` diagram is mermaid's flow / Sankey visualizer: a set of
weighted directed links laid out left-to-right by longest-path depth, with
throughput-proportional node bars and flow-proportional gradient-stroked
cubic-Bezier ribbons. Pure iterative geometry -- no dagre, no AST -- so the
leaf emits SVG directly from the parsed model.

Mirrors the test paradigm established by R281 (radar) / R282 (pie) / R283
(packet): English docstrings, === section separators, strict asserts, and
white-box access to the private helpers (``_fmt`` / ``_escape_xml`` /
``_parse_f64`` / ``_format_node_label`` / ``_compute_layout``) via a direct
module import.

Coverage map:

* dispatch smoke -- the ``sankey-beta`` arm fires and emits an SVG (the
  ``sankey-beta`` token left ``_UNSUPPORTED_DIAGRAM_TYPES`` in R284).
* header recognition -- ``sankey-beta`` declaration, blank / ``%%`` skipping,
  missing / wrong first token.
* link grammar -- single / multiple links, ``node_order`` first-seen order,
  decimal value acceptance.
* link errors -- non-3-part lines, non-numeric / underscore values, zero links.
* geometry -- viewBox / canvas / background rect, node bars, node labels
  (start vs. end anchor), per-link gradients, cubic-Bezier path, blend group.
* compute_layout algorithm -- longest-path depth, x-column distribution.
* theme wiring (R284 contract lift) -- sankey IS theme-aware (unlike pie /
  packet): ``theme.background`` flows into the root style and background rect.
* escape_xml -- all five significant chars; ampersand not double-escaped;
  ``'`` -> ``&#39;`` (radar variant, not pie/packet's ``&apos;``).
* format_node_label -- the EPSILON tolerance arm picks ``int(value)``
  (truncation) for integer-valued throughputs, ``repr`` otherwise.
* float formatting bridge -- ``_fmt`` drops ``.0`` on integer-valued floats.
* f64 parse bridge -- ``_parse_f64`` accepts decimal / exponent grammar and
  rejects underscores / ``inf`` / ``nan`` (stricter than Python ``float()``).
* geometry constants -- the six canvas constants + the 10-entry palette.
* node palette fallback -- a node whose ``node_order`` index >= 10 falls back
  to ``NODE_COLORS[0]`` (a fallback, not a modulo wrap).
* module surface -- the single public symbol, barrel non-export, frozen
  dataclasses.
* front-matter dispatch -- the ``---`` fence is stripped before the sankey arm.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import sankey_diagram as sankey_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.sankey_diagram import (
    HEIGHT,
    LABEL_OFFSET,
    NODE_COLORS,
    NODE_PADDING,
    NODE_WIDTH,
    WIDTH,
    SankeyLayout,
    SankeyLink,
    SankeyNodeLayout,
    _compute_layout,
    _escape_xml,
    _fmt,
    _format_node_label,
    _parse_f64,
    parse_sankey_diagram,
    render_sankey_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# === dispatch smoke (grok lib.rs L98-L100 sankey arm) ======================


def test_render_mermaid_to_svg_sankey_dispatch_emits_svg() -> None:
    """The ``sankey-beta`` dispatch arm fires and returns an SVG string.

    Mirrors grok's integration coverage of the sankey arm (lib.rs L98-L100):
    the token left ``_UNSUPPORTED_DIAGRAM_TYPES`` in R284 (19 -> 18 tokens),
    so dispatch routes to :func:`render_sankey_diagram_to_svg` rather than
    raising.
    """
    svg = render_mermaid_to_svg("sankey-beta\nA,B,5")
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg
    assert 'aria-roledescription="sankey"' in svg


def test_render_mermaid_to_svg_sankey_is_not_unsupported() -> None:
    """``sankey-beta`` no longer raises :class:`UnsupportedDiagramType`.

    The dedicated arm added in R284 sits above the unsupported-type check, so
    a ``sankey-beta`` body never reaches the ``_UNSUPPORTED_DIAGRAM_TYPES``
    frozenset.
    """
    svg = render_mermaid_to_svg("sankey-beta\nA,B,5\nB,C,3")
    assert "</svg>" in svg


def test_render_sankey_diagram_to_svg_returns_svg_directly() -> None:
    """The renderer emits an SVG when called directly (not via dispatch)."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


# === header recognition (grok L175-L188) ====================================


def test_parse_sankey_header_skips_blank_and_comment_lines() -> None:
    """Blank and ``%%`` lines before the header are skipped."""
    diagram = parse_sankey_diagram("  \n%% a comment\nsankey-beta\nA,B,5")
    assert diagram.node_order == ["A", "B"]


def test_parse_sankey_missing_header_raises_line_one() -> None:
    """A body whose first substantial token is not ``sankey-beta`` raises."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("A,B,5")
    assert exc_info.value.line == 1
    assert str(exc_info.value) == "Parse error at line 1: Expected 'sankey-beta' declaration"


def test_parse_sankey_wrong_first_token_carries_that_line_number() -> None:
    """A wrong first token reports its own line number (not pinned to 1)."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("\nflowchart\nA,B,5")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Expected 'sankey-beta' declaration"


def test_parse_sankey_all_blank_raises_line_one() -> None:
    """A body of only blank / comment lines raises the missing-header error."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("%% a\n\n  \n")
    assert exc_info.value.line == 1


# === link grammar (grok L189-L214) ==========================================


def test_parse_sankey_single_link() -> None:
    """A single ``source,target,value`` link parses with the value as float."""
    diagram = parse_sankey_diagram("sankey-beta\nA,B,5")
    assert diagram.links == [SankeyLink(source="A", target="B", value=5.0)]
    assert diagram.node_order == ["A", "B"]


def test_parse_sankey_multiple_links_preserve_order() -> None:
    """Multiple links are kept in source order."""
    diagram = parse_sankey_diagram("sankey-beta\nA,B,5\nC,D,3")
    assert len(diagram.links) == 2
    assert diagram.links[0].source == "A"
    assert diagram.links[1].source == "C"


def test_parse_sankey_node_order_dedupes_endpoints() -> None:
    """Endpoints enter ``node_order`` once each, in first-seen order."""
    diagram = parse_sankey_diagram("sankey-beta\nA,B,5\nB,C,3")
    assert diagram.node_order == ["A", "B", "C"]


def test_parse_sankey_decimal_value_accepted() -> None:
    """A decimal value parses as a float (grok ``parse::<f64>``)."""
    diagram = parse_sankey_diagram("sankey-beta\nA,B,3.5")
    assert diagram.links[0].value == 3.5


# === link errors (grok L189-L214) ===========================================


def test_parse_sankey_two_part_link_raises() -> None:
    """A link line with fewer than three comma-parts raises on its line."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("sankey-beta\nA,B")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid sankey link: A,B"


def test_parse_sankey_four_part_link_raises() -> None:
    """A link line with more than three comma-parts raises on its line."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("sankey-beta\nA,B,C,D")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid sankey link: A,B,C,D"


def test_parse_sankey_non_numeric_value_raises() -> None:
    """A non-numeric value raises ``Invalid sankey value`` on its line."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("sankey-beta\nA,B,abc")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid sankey value: abc"


def test_parse_sankey_underscore_value_raises() -> None:
    """A digit-group underscore (``1_000``) raises (PEP 515 rejection).

    Python ``float()`` accepts ``1_000``; Rust ``parse::<f64>()`` does not.
    :func:`_parse_f64` gates on a strict full-match so the two agree.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("sankey-beta\nA,B,1_000")
    assert exc_info.value.line == 2
    assert str(exc_info.value) == "Parse error at line 2: Invalid sankey value: 1_000"


def test_parse_sankey_no_links_raises_line_one() -> None:
    """A diagram with the header but no links raises on line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_sankey_diagram("sankey-beta")
    assert exc_info.value.line == 1
    assert str(exc_info.value) == "Parse error at line 1: sankey diagram requires at least one link"


# === geometry: viewBox + canvas (grok L24-L35) ==============================


def test_render_sankey_viewbox_dims_drop_trailing_dot_zero() -> None:
    """The viewBox width / height render without a trailing ``.0`` (R281 bridge).

    ``_fmt`` collapses ``600.0`` -> ``"600"`` (Rust ``Display``); Python ``str``
    would keep the ``.0``.
    """
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert 'viewBox="0 0 600 400"' in svg


def test_render_sankey_emits_sankey_roledescription() -> None:
    """The SVG root carries the sankey aria-roledescription + document role."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert 'aria-roledescription="sankey"' in svg
    assert 'role="graphics-document document"' in svg
    assert 'width="100%"' in svg
    assert 'id="my-svg"' in svg


def test_render_sankey_empty_g_self_closing() -> None:
    """The empty ``<g/>`` placeholder is emitted self-closed (grok L36)."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert "<g/>" in svg


def test_render_sankey_background_rect_uses_theme() -> None:
    """The full-canvas background rect carries the theme background fill."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert '<rect x="0" y="0" width="600" height="400" fill="#ffffff"/>' in svg


# === geometry: node bars (grok L58-L76) =====================================


def test_render_sankey_node_a_bar_geometry() -> None:
    """Node A (depth 0) sits at x=0 with its palette color and dom_id."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    # global_idx 0 -> dom_id node-1, color NODE_COLORS[0] = #4e79a7.
    assert '<g class="node" id="node-1" transform="translate(0,0)" x="0" y="0">' in svg
    assert '<rect height="400" width="10" fill="#4e79a7"/>' in svg


def test_render_sankey_node_b_bar_geometry() -> None:
    """Node B (depth 1) sits at x=590 with its palette color and dom_id."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert (
        '<g class="node" id="node-2" transform="translate(590,0)" x="590" y="0">' in svg
    )
    assert '<rect height="400" width="10" fill="#f28e2c"/>' in svg


# === geometry: node labels (grok L78-L95) ===================================


def test_render_sankey_left_column_label_anchored_start() -> None:
    """A non-rightmost-column label is anchored ``start`` (label right of bar).

    Node A at x=0 -> label x = 0 + NODE_WIDTH(10) + LABEL_OFFSET(6) = 16.
    """
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert '<text x="16" y="200" dy="0em" text-anchor="start">A 5</text>' in svg


def test_render_sankey_rightmost_column_label_anchored_end() -> None:
    """The rightmost-column label is anchored ``end`` (label left of bar).

    Node B at x=590 -> label x = 590 - LABEL_OFFSET(6) = 584.
    """
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert '<text x="584" y="200" dy="0em" text-anchor="end">B 5</text>' in svg


def test_render_sankey_integer_value_label_no_decimal() -> None:
    """An integer-valued throughput renders without a decimal (int arm)."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert ">A 5</text>" in svg
    assert ">B 5</text>" in svg


def test_render_sankey_decimal_value_label_keeps_decimal() -> None:
    """A non-integer throughput keeps its decimal (repr arm via _fmt)."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,3.5", MermaidTheme.default())
    assert ">A 3.5</text>" in svg
    assert ">B 3.5</text>" in svg


# === geometry: gradients + links (grok L38-L56, L97-L116) ===================


def test_render_sankey_emits_one_linear_gradient_per_link() -> None:
    """Each link gets a ``userSpaceOnUse`` gradient spanning its x-extent."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert (
        '<linearGradient id="linearGradient-0" gradientUnits="userSpaceOnUse" '
        'x1="10" x2="590">'
    ) in svg
    assert '<stop offset="0%" stop-color="#4e79a7"/>' in svg
    assert '<stop offset="100%" stop-color="#f28e2c"/>' in svg


def test_render_sankey_link_path_is_cubic_bezier() -> None:
    """The link path is a cubic Bezier with the control point at column mid.

    mx = (x0 + x1) / 2 = (10 + 590) / 2 = 300; the path anchors at (10,200)
    and (590,200) with both control points at (300,200).
    """
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert 'd="M10,200C300,200,300,200,590,200"' in svg


def test_render_sankey_link_wrapped_in_multiply_blend_group() -> None:
    """Each link path sits in a ``mix-blend-mode: multiply`` group (grok L113)."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert '<g class="links" fill="none" stroke-opacity="0.5">' in svg
    assert '<g class="link" style="mix-blend-mode: multiply;">' in svg
    assert (
        '<path d="M10,200C300,200,300,200,590,200" '
        'stroke="url(#linearGradient-0)" stroke-width="400"/>'
    ) in svg


# === compute_layout algorithm (grok L235-L420) ==============================


def test_compute_layout_longest_path_depth_three_node_chain() -> None:
    """A three-node chain A->B->C resolves to depths 0/1/2 (max_depth 2).

    The iterative relaxation (at most ``2 * node_count`` passes with in-place
    updates) propagates B's depth to C within the same pass.
    """
    diagram = parse_sankey_diagram("sankey-beta\nA,B,2\nB,C,3")
    layout = _compute_layout(diagram)
    assert layout.max_depth == 2
    depths = {n.name: n.depth for n in layout.nodes}
    assert depths == {"A": 0, "B": 1, "C": 2}


def test_compute_layout_x_columns_distribute_by_depth() -> None:
    """Depth columns spread across the canvas: x = (WIDTH-NODE_WIDTH)*d/(layers-1).

    For a three-layer layout (layers=3): x = 590 * d / 2 -> 0 / 295 / 590.
    """
    diagram = parse_sankey_diagram("sankey-beta\nA,B,2\nB,C,3")
    layout = _compute_layout(diagram)
    xs = {n.name: n.x for n in layout.nodes}
    assert xs == {"A": 0.0, "B": 295.0, "C": 590.0}


# === theme wiring (R284 contract lift -- sankey IS theme-aware) ============


def test_render_sankey_default_theme_background_is_white() -> None:
    """The default theme's ``#ffffff`` background flows into root style + rect.

    Unlike R282 pie / R283 packet (which hard-code their palette and take an
    unused ``_theme``), sankey reads ``theme.background`` in two emission sites.
    """
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.default())
    assert "background-color: #ffffff;" in svg
    assert 'fill="#ffffff"' in svg


def test_render_sankey_dark_theme_background_tints_canvas() -> None:
    """The dark theme's ``#1e1e1e`` background tints both the style and the rect."""
    svg = render_sankey_diagram_to_svg("sankey-beta\nA,B,5", MermaidTheme.dark())
    assert "background-color: #1e1e1e;" in svg
    assert 'fill="#1e1e1e"' in svg


# === escape_xml (grok escape_xml -- radar variant) ==========================


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """The five XML-significant chars are escaped (``'`` -> ``&#39;`` numeric).

    grok's ``sankey_diagram.rs`` uses the numeric character reference for the
    apostrophe (matching R281 radar), unlike R282 pie / R283 packet
    (``&apos;``) -- the Rust functions disagree.
    """
    assert _escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&#39;f"


def test_escape_xml_ampersand_not_double_escaped() -> None:
    """The ampersand is replaced once (the trailing ``;`` is not re-scanned)."""
    assert _escape_xml("&") == "&amp;"
    assert _escape_xml("<&>") == "&lt;&amp;&gt;"


def test_render_sankey_label_is_xml_escaped() -> None:
    """A node name with an ampersand is escaped in the emitted label text."""
    svg = render_sankey_diagram_to_svg(
        "sankey-beta\nA & B,C,5", MermaidTheme.default()
    )
    assert ">A &amp; B 5</text>" in svg
    # The raw ampersand never reaches the SVG verbatim.
    assert ">A & B 5</text>" not in svg


# === format_node_label (EPSILON bridge -- grok format_sankey_node_label) ====


def test_format_node_label_integer_value_uses_int_arm() -> None:
    """An integer-valued throughput renders as ``{name} {int(value)}`` (trunc).

    grok picks this arm when ``value.fract().abs() < f64::EPSILON``; the port
    mirrors the tolerance check (not Python's exact ``is_integer()``) and
    formats via ``int(value)`` (truncation, mirroring ``value as i64``).
    """
    assert _format_node_label("A", 5.0) == "A 5"
    assert _format_node_label("Node", 3.0) == "Node 3"


def test_format_node_label_decimal_value_uses_repr_arm() -> None:
    """A non-integer throughput renders as ``{name} {_fmt(value)}``."""
    assert _format_node_label("A", 3.5) == "A 3.5"


def test_format_node_label_zero_value() -> None:
    """Zero is integer-valued -> the int arm renders ``{name} 0``."""
    assert _format_node_label("A", 0.0) == "A 0"


# === float formatting bridge (R281/R282/R283 _fmt) ==========================


def test_fmt_integer_valued_float_drops_trailing_dot_zero() -> None:
    """Integer-valued floats drop the trailing ``.0`` (Rust ``Display``)."""
    assert _fmt(600.0) == "600"
    assert _fmt(0.0) == "0"


def test_fmt_non_integer_uses_repr() -> None:
    """Non-integers fall back to ``repr`` (shortest round-trippable decimal)."""
    assert _fmt(3.5) == "3.5"


# === f64 parse bridge (grok str::parse::<f64>()) ============================


def test_parse_f64_accepts_decimal_and_exponent() -> None:
    """The standard Rust ``f64`` grammar is accepted (decimal / exponent / sign)."""
    assert _parse_f64("5", 1) == 5.0
    assert _parse_f64("3.5", 1) == 3.5
    assert _parse_f64(".5", 1) == 0.5
    assert _parse_f64("5.", 1) == 5.0
    assert _parse_f64("1e3", 1) == 1000.0
    assert _parse_f64("+1.5", 1) == 1.5
    assert _parse_f64("-5", 1) == -5.0


def test_parse_f64_rejects_underscore_grouping() -> None:
    """Digit-group underscores reject (Rust ``parse::<f64>`` rejects; Python accepts)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_f64("1_000", 1)
    assert str(exc_info.value) == "Parse error at line 1: Invalid sankey value: 1_000"


def test_parse_f64_rejects_alpha_tokens() -> None:
    """A non-numeric token rejects with the verbatim raw value in the message."""
    with pytest.raises(ParseError) as exc_info:
        _parse_f64("abc", 7)
    assert str(exc_info.value) == "Parse error at line 7: Invalid sankey value: abc"


def test_parse_f64_rejects_inf_and_nan() -> None:
    """The ``inf`` / ``nan`` specials reject (mermaid flow values are finite)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_f64("inf", 1)
    assert str(exc_info.value) == "Parse error at line 1: Invalid sankey value: inf"
    with pytest.raises(ParseError):
        _parse_f64("nan", 1)


def test_parse_f64_rejects_empty() -> None:
    """An empty token rejects (the grammar requires at least one digit)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_f64("", 1)
    assert str(exc_info.value) == "Parse error at line 1: Invalid sankey value: "


# === geometry constants (grok L6-L15) =======================================


def test_sankey_constants_match_grok() -> None:
    """The six canvas constants + the 10-entry palette match grok verbatim."""
    assert WIDTH == 600.0
    assert HEIGHT == 400.0
    assert NODE_WIDTH == 10.0
    assert NODE_PADDING == 25.0
    assert LABEL_OFFSET == 6.0
    assert NODE_COLORS == (
        "#4e79a7",
        "#f28e2c",
        "#e15759",
        "#76b7b2",
        "#59a14f",
        "#edc948",
        "#b07aa1",
        "#9c755f",
        "#bab0ab",
        "#ff9da7",
    )


# === node palette fallback (grok L337-L341) =================================


def test_node_colors_fallback_to_first_past_palette_end() -> None:
    """A node whose ``node_order`` index >= 10 falls back to ``NODE_COLORS[0]``.

    grok's ``NODE_COLORS.get(idx).unwrap_or(NODE_COLORS[0])`` is a fallback, not
    a modulo wrap -- an 11th node reuses the first palette entry verbatim.
    """
    body = "sankey-beta\n" + "\n".join(f"N{i},N{i+1},1" for i in range(10))
    diagram = parse_sankey_diagram(body)
    assert len(diagram.node_order) == 11
    layout = _compute_layout(diagram)
    by_name = {n.name: n for n in layout.nodes}
    # The first ten nodes use their palette slot verbatim.
    assert by_name["N0"].color == NODE_COLORS[0]
    assert by_name["N9"].color == NODE_COLORS[9]
    # The 11th node (index 10) falls back to the first entry, not a wrap.
    assert by_name["N10"].color == NODE_COLORS[0]


# === module surface ========================================================


def test_sankey_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly the one renderer (grok ``pub fn``)."""
    assert sankey_mod.__all__ == ["render_sankey_diagram_to_svg"]


def test_sankey_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer is reached only via the dispatch arm, not the barrel.

    The barrel (:mod:`minimax_code.mermaid.to_svg`) re-exports the three
    crate-root symbols (R277); the per-diagram renderer stays out of its
    ``__all__`` -- callers reach it through :func:`render_mermaid_to_svg`.
    """
    assert "render_sankey_diagram_to_svg" not in to_svg.__all__


def test_sankey_diagram_dataclasses_are_frozen() -> None:
    """The five model dataclasses are frozen (construct-once, mirroring grok)."""
    with pytest.raises(FrozenInstanceError):
        link = SankeyLink(source="A", target="B", value=5.0)
        link.value = 3.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        node = SankeyNodeLayout(
            name="A",
            display_label="A 5",
            dom_id="node-1",
            depth=0,
            x=0.0,
            y=0.0,
            height=400.0,
            color="#4e79a7",
        )
        node.x = 5.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        layout = SankeyLayout(nodes=[], links=[], max_depth=1)
        layout.max_depth = 0  # type: ignore[misc]


# === front-matter dispatch (grok lib.rs L47 body shadow) ====================


def test_render_mermaid_to_svg_sankey_strips_frontmatter_before_dispatch() -> None:
    """A ``---`` front-matter block is stripped before the sankey arm fires.

    Mirrors grok lib.rs L47: ``mermaid_source`` is shadowed by the front-matter-
    stripped ``body`` before any per-diagram arm runs, so the renderer sees the
    clean body (not the raw source with its ``---`` fence).
    """
    source = "---\ntitle: Demo\n---\nsankey-beta\nA,B,5"
    svg = render_mermaid_to_svg(source)
    assert 'aria-roledescription="sankey"' in svg
    assert ">A 5</text>" in svg
