"""Black-box + white-box tests for the migrated gitgraph renderer (R291).

Exercises :mod:`minimax_code.mermaid.to_svg.gitgraph_diagram` -- the 13th
per-diagram leaf and 12th self-contained SVG emitter of the R269--R290 stack
(direction (1) brick 22). Mirrors grok ``gitgraph_diagram.rs`` (576 lines):
a four-command state machine (``commit`` / ``branch`` / ``checkout`` /
``merge``) that builds a branch-lane commit graph, then emits an SVG with
branch labels, three-state arrows, commit bullets, and rotated commit labels.

Coverage groups:

* Dispatch smoke -- the ``gitGraph`` token routes through the dedicated arm,
  not the unsupported surface.
* Parse rules -- the four commands, head-pointer advancement, merge-commit
  parent list, and the ``%%`` / blank-line skipping.
* Parse errors -- missing header, unknown command, missing branch argument.
* Geometry -- ``_commit_x`` formula + view-box presence.
* Commit hash -- the ``{seq}-{hash:07x}`` synthetic id and the Rust
  ``wrapping_mul`` -> Python masked-mul bridge.
* Palette -- the 8-branch HSL table and the branch-label text fill.
* Theme awareness -- the 4 channels (background / text_color / edge_color /
  node_fill) flow into the emitted SVG; dark differs from light.
* Arrow three-state -- straight (same lane), branch-down arc, merge-up arc.
* Commit bullets -- Normal single circle (r=10) vs. Merge double circle
  (r=9 outer + r=6 inner).
* Helpers -- ``_fmt`` (Rust f64 Display) + ``_escape_xml`` (``&apos;`` entity).
* Public surface -- the single-symbol ``__all__`` and the dispatch no longer
  lists ``gitGraph`` as unsupported.
"""

from __future__ import annotations

import re

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.gitgraph_diagram import (
    _branch_color,
    _branch_label_color,
    _commit_label_text,
    _commit_x,
    _CommitKind,
    _escape_xml,
    _fmt,
    _parse_gitgraph,
    render_gitgraph_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# A canonical merge graph used by several structural + theme tests: main has
# two commits with a develop branch forked after the first, then merged back.
_MERGE_GRAPH = (
    "gitGraph\n"
    "  commit\n"        # seq 0, main
    "  branch develop\n"
    "  checkout develop\n"
    "  commit\n"        # seq 1, develop
    "  checkout main\n"
    "  commit\n"        # seq 2, main
    "  merge develop\n"  # seq 3, main, MERGE (parents 2 + 1)
)


# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_gitgraph_dispatches_to_renderer() -> None:
    """A ``gitGraph`` block routes through the dedicated renderer."""
    svg = render_mermaid_to_svg("gitGraph\n  commit\n  commit")
    assert isinstance(svg, str)
    assert '<svg id="my-svg"' in svg
    assert 'aria-roledescription="gitGraph"' in svg


def test_render_gitgraph_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", MermaidTheme.light())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_gitgraph_emits_all_five_groups() -> None:
    """The branches, arrows, bullets, and labels groups all appear."""
    svg = render_gitgraph_diagram_to_svg(_MERGE_GRAPH, MermaidTheme.light())
    assert '<g class="commit-arrows">' in svg
    assert '<g class="commit-bullets">' in svg
    assert '<g class="commit-labels">' in svg
    assert 'class="branch branch0"' in svg


# === parse rules ============================================================


def test_parse_single_branch_linear_commits() -> None:
    """Three commits on ``main`` form a linear parent chain."""
    graph = _parse_gitgraph("gitGraph\n  commit\n  commit\n  commit")
    assert graph.main_branch == "main"
    assert graph.branch_order == ["main"]
    assert len(graph.commits) == 3
    assert all(c.kind == _CommitKind.NORMAL for c in graph.commits)
    # Each commit's synthetic id is its seq as a string; parent is the prior head.
    assert graph.commits[0].parents == []
    assert graph.commits[1].parents == ["0"]
    assert graph.commits[2].parents == ["1"]


def test_parse_branch_registers_without_switching() -> None:
    """``branch`` registers the lane but ``current_branch`` stays put."""
    graph = _parse_gitgraph(
        "gitGraph\n  commit\n  branch develop\n  commit"
    )
    assert graph.branch_order == ["main", "develop"]
    # The second commit still belongs to main (no checkout happened).
    assert graph.commits[1].branch == "main"


def test_parse_checkout_switches_current_branch() -> None:
    """``checkout`` moves the head to the target branch."""
    graph = _parse_gitgraph(
        "gitGraph\n  commit\n  branch develop\n  checkout develop\n  commit"
    )
    assert graph.branch_order == ["main", "develop"]
    assert graph.commits[0].branch == "main"
    assert graph.commits[1].branch == "develop"


def test_parse_merge_creates_merge_commit_with_two_parents() -> None:
    """``merge`` appends a Merge commit whose parents are head + target head."""
    graph = _parse_gitgraph(_MERGE_GRAPH)
    merge = graph.commits[3]
    assert merge.kind == _CommitKind.MERGE
    assert merge.parents == ["2", "1"]
    assert merge.branch == "main"


def test_parse_skips_comments_and_blank_lines() -> None:
    """``%%`` comments and blank lines are ignored before the header and within."""
    graph = _parse_gitgraph("%% leading comment\n\ngitGraph\n  %% inline\n  commit")
    assert len(graph.commits) == 1


def test_parse_commit_by_id_index() -> None:
    """``commit_by_id`` maps each synthetic id to its commit seq."""
    graph = _parse_gitgraph("gitGraph\n  commit\n  commit")
    assert graph.commit_by_id == {"0": 0, "1": 1}


# === parse errors ===========================================================


def test_parse_missing_header_raises() -> None:
    """A first command that is not ``gitGraph`` raises."""
    with pytest.raises(ParseError):
        _parse_gitgraph("commit\n  commit")


def test_parse_empty_input_raises() -> None:
    """No header at all raises."""
    with pytest.raises(ParseError):
        _parse_gitgraph("")


def test_parse_only_comments_raises() -> None:
    """Only ``%%`` / blank lines -> no header -> raises."""
    with pytest.raises(ParseError):
        _parse_gitgraph("%% a\n\n%% b")


def test_parse_unknown_command_raises() -> None:
    """An unrecognized command raises."""
    with pytest.raises(ParseError):
        _parse_gitgraph("gitGraph\n  rebase origin")


def test_parse_branch_missing_name_raises() -> None:
    with pytest.raises(ParseError):
        _parse_gitgraph("gitGraph\n  branch")


def test_parse_checkout_missing_name_raises() -> None:
    with pytest.raises(ParseError):
        _parse_gitgraph("gitGraph\n  checkout")


def test_parse_merge_missing_name_raises() -> None:
    with pytest.raises(ParseError):
        _parse_gitgraph("gitGraph\n  commit\n  merge")


def test_parse_error_str_carries_line_number() -> None:
    """ParseError stringifies with the 1-based line number (error.py contract)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_gitgraph("gitGraph\n  commit\n  bogus")
    assert "line 3" in str(exc_info.value)


# === geometry ===============================================================


def test_commit_x_formula() -> None:
    """``_commit_x(seq) = seq * (40 + 10) + 10 = seq * 50 + 10``."""
    assert _commit_x(0) == 10.0
    assert _commit_x(1) == 60.0
    assert _commit_x(5) == 260.0


def test_svg_carries_viewbox() -> None:
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit\n  commit", MermaidTheme.light())
    assert "viewBox=" in svg


def test_svg_carries_branch_lane_lines() -> None:
    """Each registered branch emits one horizontal ``<line>`` lane."""
    svg = render_gitgraph_diagram_to_svg(
        "gitGraph\n  commit\n  branch develop\n  checkout develop\n  commit",
        MermaidTheme.light(),
    )
    assert svg.count('class="branch branch0"') == 1
    assert svg.count('class="branch branch1"') == 1


# === commit hash (Rust wrapping_mul bridge) =================================


def test_commit_label_text_format_is_seq_dash_seven_hex() -> None:
    label = _commit_label_text(0)
    assert re.fullmatch(r"\d+-[0-9a-f]{7}", label)


def test_commit_label_text_seq_zero_anchor() -> None:
    """seq 0 -> ``0-79ad076`` (grok's wrapping_mul(0, K) ^ C = C, masked to 28 bits)."""
    assert _commit_label_text(0) == "0-79ad076"


def test_commit_label_text_unique_per_seq() -> None:
    labels = {_commit_label_text(seq) for seq in range(20)}
    assert len(labels) == 20


def test_commit_label_text_hash_bridge_matches_masked_mul() -> None:
    """The Python masked-mul bridge reproduces grok's ``wrapping_mul`` + XOR.

    Grok: ``((seq as u32).wrapping_mul(0x9E37_79B9) ^ 0x079A_D076) & 0x0FFF_FFFF``.
    Python has no fixed-width mul, so the mask to 32 bits lands *before* the XOR
    (mirroring ``wrapping_mul``'s overflow discard); the final ``& 0x0FFFFFFF``
    caps the value at 7 hex digits for ``:07x``.
    """
    for seq in range(16):
        expected = (((seq * 0x9E3779B9) & 0xFFFFFFFF) ^ 0x079AD076) & 0x0FFFFFFF
        assert _commit_label_text(seq) == f"{seq}-{expected:07x}"


# === palette ================================================================


def test_branch_color_eight_hues() -> None:
    assert _branch_color(0) == "hsl(240, 100%, 46.2745098039%)"
    assert _branch_color(1) == "hsl(60, 100%, 43.5294117647%)"
    assert _branch_color(7) == "hsl(0, 100%, 46.2745098039%)"


def test_branch_color_overflow_collapses_onto_default() -> None:
    """Index 8+ reuses the index-4 hue (grok's fallthrough arm)."""
    assert _branch_color(8) == "hsl(180, 100%, 46.2745098039%)"
    assert _branch_color(99) == _branch_color(4)


def test_branch_label_color_white_on_zero_and_three() -> None:
    assert _branch_label_color(0) == "#ffffff"
    assert _branch_label_color(3) == "#ffffff"
    assert _branch_label_color(1) == "black"
    assert _branch_label_color(7) == "black"


def test_style_block_expands_all_eight_palette_classes() -> None:
    """The ``<style>`` block emits per-branch classes for all 8 hues."""
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", MermaidTheme.light())
    for i in range(8):
        assert f".branch-label{i}{{" in svg
        assert f".commit{i}{{" in svg
        assert f".arrow{i}{{" in svg


# === theme awareness (4 channels) ===========================================


def test_theme_background_flows_into_svg_style() -> None:
    dark = MermaidTheme.dark()
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", dark)
    assert f"background-color: {dark.background};" in svg


def test_theme_text_color_in_style_block() -> None:
    light = MermaidTheme.light()
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", light)
    assert f"font-size:16px;fill:{light.text_color};" in svg
    assert f".gitTitleText{{text-anchor:middle;font-size:18px;fill:{light.text_color};" in svg


def test_theme_edge_color_in_branch_stroke() -> None:
    light = MermaidTheme.light()
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", light)
    assert f".branch{{stroke-width:1;stroke:{light.edge_color};stroke-dasharray:2;}}" in svg


def test_theme_node_fill_in_merge_and_tag_classes() -> None:
    light = MermaidTheme.light()
    svg = render_gitgraph_diagram_to_svg(_MERGE_GRAPH, light)
    assert f".commit-merge{{stroke:{light.node_fill};fill:{light.node_fill};" in svg
    assert f".tag-label-bkg{{fill:{light.node_fill};" in svg


def test_dark_theme_svg_differs_from_light() -> None:
    light_svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", MermaidTheme.light())
    dark_svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", MermaidTheme.dark())
    assert light_svg != dark_svg


# === arrow three-state ======================================================


def test_arrow_same_branch_is_straight_line() -> None:
    """Two commits on one lane -> a straight ``M ... L ...`` arrow (no arc)."""
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit\n  commit", MermaidTheme.light())
    assert re.search(r'<path d="M \d+ \d+ L \d+ \d+" class="arrow', svg)
    # No arc segment on a flat single-lane graph.
    assert "A 20 20" not in svg


def test_arrow_branch_down_emits_arc() -> None:
    """A commit on a lower lane emits the branch-down quarter-arc."""
    source = "gitGraph\n  commit\n  branch develop\n  checkout develop\n  commit"
    svg = render_gitgraph_diagram_to_svg(source, MermaidTheme.light())
    assert "A 20 20" in svg


def test_arrow_merge_emits_arc() -> None:
    """A merge from a sibling lane emits an arc (up-merge in this layout)."""
    svg = render_gitgraph_diagram_to_svg(_MERGE_GRAPH, MermaidTheme.light())
    assert "A 20 20" in svg


def test_arrow_stroke_width_is_eight() -> None:
    """``.arrow`` carries the grok ``stroke-width:8`` (ARROW_STROKE_WIDTH)."""
    svg = render_gitgraph_diagram_to_svg(_MERGE_GRAPH, MermaidTheme.light())
    assert ".arrow{stroke-width:8;stroke-linecap:round;fill:none;}" in svg


# === commit bullets =========================================================


def test_normal_commit_emits_single_radius_ten_circle() -> None:
    """A Normal commit renders one bullet circle with ``r=10``."""
    svg = render_gitgraph_diagram_to_svg("gitGraph\n  commit", MermaidTheme.light())
    assert 'r="10"' in svg
    # No merge double-circle on a single normal commit.
    assert 'r="9"' not in svg
    assert 'r="6"' not in svg


def test_merge_commit_emits_double_circle() -> None:
    """A Merge commit renders an outer (r=9) + inner (r=6) bullet pair."""
    svg = render_gitgraph_diagram_to_svg(_MERGE_GRAPH, MermaidTheme.light())
    assert 'r="9"' in svg
    assert 'r="6"' in svg


# === helpers ================================================================


def test_fmt_drops_trailing_zero_for_integers() -> None:
    assert _fmt(10.0) == "10"
    assert _fmt(0.0) == "0"
    assert _fmt(-19.0) == "-19"


def test_fmt_keeps_fraction_for_non_integers() -> None:
    assert _fmt(-1.5) == "-1.5"
    assert _fmt(0.625) == "0.625"


def test_escape_xml_uses_apos_named_entity() -> None:
    """``'`` -> ``&apos;`` (the named-entity variant shared with journey/block)."""
    assert _escape_xml("it's") == "it&apos;s"
    assert _escape_xml("a&b<c>d") == "a&amp;b&lt;c&gt;d"


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    from minimax_code.mermaid.to_svg import gitgraph_diagram

    assert gitgraph_diagram.__all__ == ["render_gitgraph_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_gitgraph_unsupported() -> None:
    """R291 removed ``gitGraph`` (12 -> 11 tokens); R292 removed ``mindmap``
    (11 -> 10); R293 removed ``xychart-beta`` (10 -> 9); R294 removed
    ``requirementDiagram`` (9 -> 8), so the set keeps shrinking as renderers
    ship."""
    assert "gitGraph" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 8
