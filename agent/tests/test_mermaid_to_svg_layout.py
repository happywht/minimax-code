"""Black-box tests for the migrated mermaid-to-svg layout type layer (R274a).

Exercises :mod:`minimax_code.mermaid.to_svg.layout` -- the type-foundation
sub-leaf fused from grok's ``mermaid-to-svg/src/layout.rs`` (direction (1),
leaf 6, sub-leaf a). This leaf ports the 18 layout constants, the 4 public
``pub struct`` (``LayoutNode`` / ``LayoutEdge`` / ``LayoutSubgraph`` /
``LayoutResult``), the 4 private internal structs, ``FlowchartLayoutOptions``,
4 stdlib type aliases, and the ``graph_contains_state_shapes`` helper.

The dead-code strip is the engineering win of this leaf: grok's ``compute()``
backup-layout chain (~1500 lines) + ``apply_flip`` + ``rotate_layout`` +
``shift_external_nodes`` + ``normalize_positions_and_edges`` + the two
``compute_layout_no_subgraph_centering*`` entries are all dropped (YAGNI) --
they are ``#[allow(dead_code)]`` or zero-call in grok. The live surface ports
across R274a--e; this test file covers R274a only.

Like grok's private ``mod layout;`` (consumed by ``lib.rs``, never ``pub use``-d
at the crate root), this module is **internal**: it declares its own ``__all__``
(the 4 ``pub struct``; the 2 ``pub fn`` entries join in R274e) but is NOT
re-exported through the ``to_svg`` barrel. Covers:

* the 18 layout constants (exact f64 values from grok),
* the 4 public + 4 private dataclasses (field names + types),
* :class:`FlowchartLayoutOptions` -- ``Default`` (field defaults) +
  ``from_render_config`` (int->float widening, ``None`` fallback, font-size
  delegation),
* :func:`graph_contains_state_shapes` -- state-shape detection + recursive
  subgraph scan,
* the 4 stdlib type aliases,
* the barrel contract: ``layout`` is internal -- reachable by deep path, NOT
  re-exported through the ``to_svg`` barrel; the ``mermaid`` root stays at 17.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import layout as layout_mod
from minimax_code.mermaid.to_svg.ast import (
    EdgeStyle,
    Node,
    NodeShape,
    Subgraph,
)
from minimax_code.mermaid.to_svg.config import FlowchartConfig, RenderConfig
from minimax_code.mermaid.to_svg.layout import (
    ClusterAnalysis,
    EdgeInfo,
    EdgeLabelPosMap,
    EdgeMap,
    EdgePointMap,
    FlowchartLayoutOptions,
    LayoutEdge,
    LayoutNode,
    LayoutResult,
    LayoutSubgraph,
    NodeInfo,
    PositionMap,
    SubgraphInfo,
    graph_contains_state_shapes,
)

# === layout constants (exact f64 values from grok) ==========================


def test_layout_constants_match_grok_values() -> None:
    """The 18 ``const f64`` values mirror grok exactly."""
    assert layout_mod.FLOWCHART_PADDING == 15.0
    assert layout_mod.EDGE_LABEL_PADDING == 2.0
    assert layout_mod.RANK_SEP == 50.0
    assert layout_mod.NODE_SEP == 50.0
    assert layout_mod.MARGIN == 8.0
    assert layout_mod.SUBGRAPH_PADDING == 8.0
    assert layout_mod.SUBGRAPH_TITLE_HEIGHT == 24.0
    assert layout_mod.SUBGRAPH_GAP == 25.0
    assert layout_mod.MIN_NODE_WIDTH == 0.0
    assert layout_mod.MIN_NODE_HEIGHT == 0.0
    assert layout_mod.EDGE_LABEL_GAP == 24.0  # grok #[allow(dead_code)]
    assert layout_mod.STATE_CHAR_WIDTH == 6.7
    assert layout_mod.STATE_NODE_WIDTH_PADDING == 6.0
    assert layout_mod.STATE_NODE_HEIGHT_PADDING == 16.0
    assert layout_mod.STATE_NODE_MIN_HEIGHT == 40.0
    assert layout_mod.STATE_DIAMOND_PADDING == 18.0
    assert layout_mod.STATE_FORK_WIDTH == 70.0
    assert layout_mod.STATE_FORK_HEIGHT == 7.0


def test_layout_constants_are_float_not_int() -> None:
    """Every layout constant is a Python ``float`` (grok ``f64``), not ``int``.

    Guards against an accidental ``15`` (int) migration -- dagre math is float.
    """
    for name in (
        "FLOWCHART_PADDING",
        "EDGE_LABEL_PADDING",
        "RANK_SEP",
        "NODE_SEP",
        "STATE_CHAR_WIDTH",
        "STATE_FORK_HEIGHT",
    ):
        assert type(getattr(layout_mod, name)) is float


# === public data types ======================================================


def test_layout_node_fields() -> None:
    """``LayoutNode`` carries the 9 grok fields with the right types."""
    node = LayoutNode(
        id="A",
        x=1.0,
        y=2.0,
        width=10.0,
        height=5.0,
        shape=NodeShape.Rectangle,
        label="A",
        fill_color="#fff",
        stroke_color="#000",
    )
    assert node.id == "A"
    assert node.x == 1.0
    assert node.y == 2.0
    assert node.width == 10.0
    assert node.height == 5.0
    assert node.shape is NodeShape.Rectangle
    assert node.label == "A"
    assert node.fill_color == "#fff"
    assert node.stroke_color == "#000"
    # grok Option<String> -> str | None: None accepted
    bare = LayoutNode(
        id="A",
        x=0.0,
        y=0.0,
        width=0.0,
        height=0.0,
        shape=NodeShape.Circle,
        label="",
        fill_color=None,
        stroke_color=None,
    )
    assert bare.fill_color is None
    assert bare.stroke_color is None


def test_layout_edge_fields_and_from_underscore() -> None:
    """``LayoutEdge`` carries 6 fields; ``from`` -> ``from_`` (keyword)."""
    edge = LayoutEdge(
        from_="A",
        to="B",
        label="yes",
        style=EdgeStyle.Arrow,
        points=[(0.0, 0.0), (1.0, 1.0)],
        label_pos=(0.5, 0.5),
    )
    assert edge.from_ == "A"  # not `from` (Python keyword)
    assert edge.to == "B"
    assert edge.label == "yes"
    assert edge.style is EdgeStyle.Arrow
    assert edge.points == [(0.0, 0.0), (1.0, 1.0)]
    assert edge.label_pos == (0.5, 0.5)
    # None variants
    bare = LayoutEdge(
        from_="A",
        to="B",
        label=None,
        style=EdgeStyle.Line,
        points=[],
        label_pos=None,
    )
    assert bare.label is None
    assert bare.label_pos is None


def test_layout_subgraph_fields() -> None:
    """``LayoutSubgraph`` carries the 6 grok fields."""
    sub = LayoutSubgraph(
        id="cluster1",
        title="My Cluster",
        x=0.0,
        y=0.0,
        width=100.0,
        height=50.0,
    )
    assert sub.id == "cluster1"
    assert sub.title == "My Cluster"
    assert sub.width == 100.0
    assert sub.height == 50.0
    bare = LayoutSubgraph(id="g", title=None, x=0.0, y=0.0, width=0.0, height=0.0)
    assert bare.title is None


def test_layout_result_fields() -> None:
    """``LayoutResult`` aggregates nodes (dict) + edges/subgraphs (lists) + bounds."""
    node = LayoutNode(
        id="A",
        x=0.0,
        y=0.0,
        width=1.0,
        height=1.0,
        shape=NodeShape.Rectangle,
        label="A",
        fill_color=None,
        stroke_color=None,
    )
    result = LayoutResult(
        nodes={"A": node},
        edges=[],
        subgraphs=[],
        width=10.0,
        height=5.0,
    )
    assert result.nodes == {"A": node}
    assert result.edges == []
    assert result.subgraphs == []
    assert result.width == 10.0
    assert result.height == 5.0


# === private internal data types ===========================================


def test_subgraph_info_fields() -> None:
    """``SubgraphInfo`` carries id + title + parent_subgraph_id (None at top)."""
    top = SubgraphInfo(id="outer", title="Outer", parent_subgraph_id=None)
    nested = SubgraphInfo(id="inner", title=None, parent_subgraph_id="outer")
    assert top.parent_subgraph_id is None
    assert nested.parent_subgraph_id == "outer"


def test_node_info_fields() -> None:
    """``NodeInfo`` carries the 7 grok fields (measurement + rank + order)."""
    info = NodeInfo(
        id="A",
        label="A",
        shape=NodeShape.Diamond,
        width=10.0,
        height=5.0,
        rank=2,
        order=3,
    )
    assert info.id == "A"
    assert info.shape is NodeShape.Diamond
    assert info.rank == 2
    assert info.order == 3
    assert type(info.rank) is int
    assert type(info.order) is int


def test_edge_info_fields() -> None:
    """``EdgeInfo`` carries from_/to/label/style."""
    info = EdgeInfo(from_="A", to="B", label=None, style=EdgeStyle.ThickArrow)
    assert info.from_ == "A"
    assert info.to == "B"
    assert info.label is None
    assert info.style is EdgeStyle.ThickArrow


def test_cluster_analysis_fields() -> None:
    """``ClusterAnalysis`` carries the subgraph_nodes + external_edges maps."""
    analysis = ClusterAnalysis(
        subgraph_nodes={"cluster1": {"A", "B"}},
        external_edges={"cluster1": True},
    )
    assert analysis.subgraph_nodes == {"cluster1": {"A", "B"}}
    assert analysis.external_edges == {"cluster1": True}


# === FlowchartLayoutOptions: Default ========================================


def test_flowchart_layout_options_default_matches_grok() -> None:
    """``Default`` maps to field defaults; ``()`` mirrors ``::default()``."""
    opts = FlowchartLayoutOptions()
    assert opts.node_spacing == layout_mod.NODE_SEP  # 50.0
    assert opts.rank_spacing == layout_mod.RANK_SEP  # 50.0
    assert opts.padding == layout_mod.FLOWCHART_PADDING  # 15.0
    assert opts.wrapping_width == 200.0  # DEFAULT_WRAP_WIDTH
    assert opts.font_size == 16.0  # DEFAULT_FONT_SIZE


def test_flowchart_layout_options_is_frozen() -> None:
    """grok ``Copy`` surfaces as ``frozen=True`` (immutability)."""
    opts = FlowchartLayoutOptions()
    with pytest.raises(AttributeError):
        opts.node_spacing = 99.0


def test_flowchart_layout_options_equality_is_value_based() -> None:
    """Two equal option sets compare equal (value semantics, grok ``Copy``)."""
    a = FlowchartLayoutOptions(node_spacing=42.0)
    b = FlowchartLayoutOptions(node_spacing=42.0)
    assert a == b
    assert a != FlowchartLayoutOptions(node_spacing=43.0)


# === FlowchartLayoutOptions: from_render_config =============================


def test_from_render_config_empty_config_uses_defaults() -> None:
    """An empty :class:`RenderConfig` yields the default options."""
    opts = FlowchartLayoutOptions.from_render_config(RenderConfig())
    assert opts == FlowchartLayoutOptions()


def test_from_render_config_widens_int_to_float() -> None:
    """The int flowchart knobs widen to float (grok ``f64::from``)."""
    config = RenderConfig(
        flowchart=FlowchartConfig(
            node_spacing=40,
            rank_spacing=60,
            padding=20,
            wrapping_width=180,
        ),
    )
    opts = FlowchartLayoutOptions.from_render_config(config)
    assert opts.node_spacing == 40.0
    assert opts.rank_spacing == 60.0
    assert opts.padding == 20.0
    assert opts.wrapping_width == 180.0
    # widened to float, not int
    assert type(opts.node_spacing) is float


def test_from_render_config_partial_config_falls_back() -> None:
    """A partially-set flowchart block falls back to defaults for unset knobs."""
    config = RenderConfig(flowchart=FlowchartConfig(node_spacing=99))
    opts = FlowchartLayoutOptions.from_render_config(config)
    assert opts.node_spacing == 99.0
    assert opts.rank_spacing == layout_mod.RANK_SEP
    assert opts.padding == layout_mod.FLOWCHART_PADDING


def test_from_render_config_font_size_px_delegation() -> None:
    """``font_size`` delegates to ``RenderConfig.font_size_px`` (one call)."""
    config = RenderConfig(font_size="24px")
    opts = FlowchartLayoutOptions.from_render_config(config)
    assert opts.font_size == 24.0

    # invalid font_size -> falls back to default
    bad = RenderConfig(font_size="abc")
    assert FlowchartLayoutOptions.from_render_config(bad).font_size == 16.0

    # unset font_size -> default
    unset = RenderConfig()
    assert FlowchartLayoutOptions.from_render_config(unset).font_size == 16.0


def test_from_render_config_mirrors_grok_unwrap_or() -> None:
    """``Option<u32>.map(f64::from).unwrap_or(default)`` -> float-or-default."""
    # mix of set + unset + font
    config = RenderConfig(
        font_size="32px",
        flowchart=FlowchartConfig(rank_spacing=80, wrapping_width=220),
    )
    opts = FlowchartLayoutOptions.from_render_config(config)
    assert opts.node_spacing == layout_mod.NODE_SEP  # unset -> default
    assert opts.rank_spacing == 80.0  # set -> widened
    assert opts.padding == layout_mod.FLOWCHART_PADDING  # unset
    assert opts.wrapping_width == 220.0  # set
    assert opts.font_size == 32.0  # from font_size_px


# === graph_contains_state_shapes ============================================


def test_graph_contains_state_shapes_empty_is_false() -> None:
    """No statements -> not a state diagram."""
    assert graph_contains_state_shapes([]) is False


def test_graph_contains_state_shapes_non_state_node_is_false() -> None:
    """A plain flowchart shape (Rectangle) does not flag state diagram."""
    assert graph_contains_state_shapes(
        [Node(id="A", label="A", shape=NodeShape.Rectangle)]
    ) is False


@pytest.mark.parametrize(
    "shape",
    [NodeShape.StartState, NodeShape.EndState, NodeShape.ForkJoin],
)
def test_graph_contains_state_shapes_state_shapes_flag_true(shape: NodeShape) -> None:
    """StartState / EndState / ForkJoin each flag the diagram as state."""
    assert graph_contains_state_shapes(
        [Node(id="s", label="s", shape=shape)]
    ) is True


def test_graph_contains_state_shapes_recursive_subgraph() -> None:
    """A state shape nested inside a subgraph is detected recursively."""
    nested = Subgraph(
        id="outer",
        title=None,
        statements=[
            Subgraph(
                id="inner",
                title=None,
                statements=[Node(id="end", label="*", shape=NodeShape.EndState)],
            ),
        ],
    )
    assert graph_contains_state_shapes([nested]) is True


def test_graph_contains_state_shapes_non_state_subgraph_is_false() -> None:
    """A subgraph holding only non-state shapes does not flag state."""
    sub = Subgraph(
        id="g",
        title=None,
        statements=[Node(id="A", label="A", shape=NodeShape.Circle)],
    )
    assert graph_contains_state_shapes([sub]) is False


# === type aliases ===========================================================


def test_type_aliases_are_dict_generic_forms() -> None:
    """The 4 stdlib aliases map grok ``HashMap``/``Vec`` to ``dict``/``list``."""
    # EdgeMap = dict[int, tuple[str, str]] (HashMap<usize, (String, String)>)
    em: EdgeMap = {0: ("A", "B")}
    assert em[0] == ("A", "B")
    # PositionMap = dict[str, tuple[float, float]] (HashMap<String, (f64, f64)>)
    pm: PositionMap = {"A": (1.0, 2.0)}
    assert pm["A"] == (1.0, 2.0)
    # EdgePointMap = dict[int, list[tuple[float, float]]]
    epm: EdgePointMap = {0: [(0.0, 0.0), (1.0, 1.0)]}
    assert epm[0] == [(0.0, 0.0), (1.0, 1.0)]
    # EdgeLabelPosMap = dict[int, tuple[float, float]]
    elpm: EdgeLabelPosMap = {0: (0.5, 0.5)}
    assert elpm[0] == (0.5, 0.5)


# === barrel contract: layout is internal ====================================


def test_layout_module_all_is_4_public_structs() -> None:
    """``layout.py`` declares a 4-symbol ``__all__`` (the ``pub struct`` surface).

    Mirrors grok's private ``mod layout;`` -- the 4 ``pub struct`` are the
    intended public surface at this leaf. The 2 ``pub fn`` entries
    (``compute_layout`` / ``compute_layout_with_config``) join in R274e; the
    constants / internal structs / type aliases / options are module-private
    (grok ``const`` / private ``struct`` / ``type`` -- not ``pub``).
    """
    assert layout_mod.__all__ == [
        "LayoutEdge",
        "LayoutNode",
        "LayoutResult",
        "LayoutSubgraph",
    ]


def test_layout_symbols_not_in_to_svg_barrel() -> None:
    """``layout`` is internal: its symbols are NOT re-exported via barrel.

    The ``to_svg`` barrel tracks grok's crate-root ``pub use`` surface, which
    omits ``layout`` (the crate calls ``layout::compute_layout`` internally
    from ``lib.rs`` -- it is never ``pub use``-d at the crate root).
    """
    for symbol in layout_mod.__all__:
        assert symbol not in to_svg.__all__


def test_layout_importable_via_deep_path() -> None:
    """``layout`` is importable as ``minimax_code.mermaid.to_svg.layout``.

    The deep path is how the future ``svg_renderer.py`` leaf will consume the
    layout (``from .layout import LayoutResult, ...``) -- no barrel re-export.
    """
    import importlib

    deep = importlib.import_module("minimax_code.mermaid.to_svg.layout")
    assert deep.LayoutNode is LayoutNode
    assert deep.graph_contains_state_shapes is graph_contains_state_shapes


def test_mermaid_root_barrel_unchanged_by_layout_leaf() -> None:
    """R274a adds an internal module; the R38 root surface stays at 17."""
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__
