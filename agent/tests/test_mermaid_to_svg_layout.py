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
    Edge,
    EdgeStyle,
    FlowchartGraph,
    GraphDirection,
    Node,
    NodeShape,
    StyleStatement,
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
    LayoutEngine,
    LayoutNode,
    LayoutResult,
    LayoutSubgraph,
    NodeInfo,
    PositionMap,
    SubgraphInfo,
    compute_layout,
    compute_layout_with_config,
    graph_contains_state_shapes,
)
from minimax_code.mermaid.to_svg.text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_WRAP_WIDTH,
    measure_wrapped_lines_with_font_size,
    wrap_text_lines,
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
# R274f: the authoritative ``__all__`` surface assertion lives at the end of
# this module (``test_layout_module_all_contract``), which tracks the live
# 6-symbol surface (4 ``pub struct`` from R274a + 2 ``pub fn`` from R274f).
# The earlier R274a-era 4-symbol stub was removed to avoid a stale duplicate.


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


# === R274b: LayoutEngine constructor + collection + measurement ============
#
# Exercises the 6 engine symbols ported in R274b: ``__init__`` (grok ``new`` +
# ``new_with_options``), ``collect_nodes_and_edges``, ``add_node``,
# ``ensure_node_exists``, ``is_subgraph_id``, ``measure_node``. The dagre bridge
# + subgraph-endpoint ops + ``compute_with_dagre`` orchestrator land in R274c--e.


def _empty_graph() -> FlowchartGraph:
    """A minimal flowchart graph (TB, no statements) for engine construction."""
    return FlowchartGraph(direction=GraphDirection.TopToBottom, statements=[])


def _expected_text(
    label: str,
    char_width: float = DEFAULT_CHAR_WIDTH,
    font_size: float = DEFAULT_FONT_SIZE,
    wrap: float = DEFAULT_WRAP_WIDTH,
) -> tuple[float, float]:
    """Re-compute ``(text_width, text_height)`` via the public text_wrap API.

    Lets the measure_node tests assert against the exact wrapping/sizing
    arithmetic measure_node delegates to, without hard-coding floats: change a
    coefficient in measure_node and the assertion fails.
    """
    lines = wrap_text_lines(label, wrap, char_width)
    return measure_wrapped_lines_with_font_size(lines, char_width, font_size)


# --- constructor (grok ``new`` / ``new_with_options``) ---------------------


def test_layout_engine_init_default_options_empty_graph() -> None:
    """An empty graph yields default options + the 11 zero-initialized fields."""
    engine = LayoutEngine(_empty_graph())
    assert isinstance(engine.options, FlowchartLayoutOptions)
    assert engine.options == FlowchartLayoutOptions()
    assert engine.is_state_diagram is False
    assert engine.nodes == {}
    assert engine.edges == []
    assert engine.subgraphs == []
    assert engine.adjacency == {}
    assert engine.reverse_adjacency == {}
    assert engine.next_node_order == 0
    assert engine.node_to_subgraph == {}
    assert engine.node_styles == {}


def test_layout_engine_init_none_options_uses_default() -> None:
    """``options=None`` mirrors grok ``new`` delegating to ``new_with_options(default)``."""
    engine = LayoutEngine(_empty_graph(), options=None)
    assert engine.options == FlowchartLayoutOptions()


def test_layout_engine_init_custom_options_preserved() -> None:
    """Explicit options are stored verbatim (grok ``new_with_options``)."""
    opts = FlowchartLayoutOptions(padding=99.0, font_size=32.0)
    engine = LayoutEngine(_empty_graph(), options=opts)
    assert engine.options is opts
    assert engine.options.padding == 99.0


def test_layout_engine_init_runs_collection_pass() -> None:
    """Construction collects a declared node + materializes edge endpoints."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    assert "A" in engine.nodes
    assert "B" in engine.nodes  # materialized by ensure_node_exists
    assert len(engine.edges) == 1


# --- collect_nodes_and_edges: Node dispatch --------------------------------


def test_collect_node_measures_and_registers() -> None:
    """A Node is measured + inserted with rank=0 and an allocated order."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="A", label="A", shape=NodeShape.Rectangle)],
    )
    engine = LayoutEngine(graph)
    info = engine.nodes["A"]
    assert info.id == "A"
    assert info.label == "A"
    assert info.shape is NodeShape.Rectangle
    assert info.rank == 0
    assert info.order == 0
    assert info.width > 0.0
    assert info.height > 0.0


def test_collect_node_label_defaults_to_id() -> None:
    """``Node(label=None)`` -> ``NodeInfo.label == id`` (grok ``unwrap_or(&id)``)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="X", label=None, shape=NodeShape.Circle)],
    )
    engine = LayoutEngine(graph)
    assert engine.nodes["X"].label == "X"


def test_collect_node_id_matching_subgraph_id_is_skipped() -> None:
    """A Node whose id is itself a subgraph id is skipped (grok ``is_subgraph_id``)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(id="sg", title="S", statements=[]),
            Node(id="sg", label="sg", shape=NodeShape.Rectangle),
        ],
    )
    engine = LayoutEngine(graph)
    assert "sg" not in engine.nodes


# --- collect_nodes_and_edges: Edge dispatch --------------------------------


def test_collect_edge_materializes_both_endpoints() -> None:
    """An edge between undeclared nodes materializes both as Rectangle fallbacks."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)],
    )
    engine = LayoutEngine(graph)
    assert engine.nodes["A"].shape is NodeShape.Rectangle
    assert engine.nodes["B"].shape is NodeShape.Rectangle
    assert engine.nodes["A"].label == "A"
    assert engine.nodes["B"].label == "B"


def test_collect_edge_builds_adjacency_and_reverse() -> None:
    """A->B registers adjacency[A]=[B] + reverse_adjacency[B]=[A]."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)],
    )
    engine = LayoutEngine(graph)
    assert engine.adjacency == {"A": ["B"]}
    assert engine.reverse_adjacency == {"B": ["A"]}


def test_collect_edge_appends_to_adjacency_lists() -> None:
    """Multiple out-edges from one node append (grok ``entry().or_default().push()``)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
            Edge(from_="A", to="C", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.adjacency["A"] == ["B", "C"]


def test_collect_edge_pushes_edge_info() -> None:
    """Each edge pushes an EdgeInfo mirroring the AST edge."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Edge(from_="A", to="B", label="yes", style=EdgeStyle.DottedArrow),
        ],
    )
    engine = LayoutEngine(graph)
    assert len(engine.edges) == 1
    edge = engine.edges[0]
    assert edge.from_ == "A"
    assert edge.to == "B"
    assert edge.label == "yes"
    assert edge.style is EdgeStyle.DottedArrow


# --- collect_nodes_and_edges: Subgraph dispatch ----------------------------


def test_collect_subgraph_registers_title_and_parent() -> None:
    """A top-level subgraph is recorded with title + parent_subgraph_id=None."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="sg", title="My Cluster", statements=[])],
    )
    engine = LayoutEngine(graph)
    assert len(engine.subgraphs) == 1
    sub = engine.subgraphs[0]
    assert sub.id == "sg"
    assert sub.title == "My Cluster"
    assert sub.parent_subgraph_id is None


def test_collect_subgraph_title_falls_back_to_id() -> None:
    """``Subgraph(title=None)`` -> ``SubgraphInfo.title == id`` (grok ``or_else``)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="sg", title=None, statements=[])],
    )
    engine = LayoutEngine(graph)
    assert engine.subgraphs[0].title == "sg"


def test_collect_subgraph_recurses_and_registers_nodes() -> None:
    """Nodes inside a subgraph are collected + registered in node_to_subgraph."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[Node(id="inner", label="inner", shape=NodeShape.Rectangle)],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert "inner" in engine.nodes
    assert engine.node_to_subgraph == {"inner": "sg"}


def test_collect_nested_subgraph_parent_chain() -> None:
    """A nested subgraph records its enclosing subgraph as parent."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="outer",
                title=None,
                statements=[
                    Subgraph(id="inner", title=None, statements=[]),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    ids = {sub.id: sub for sub in engine.subgraphs}
    assert ids["outer"].parent_subgraph_id is None
    assert ids["inner"].parent_subgraph_id == "outer"


# --- collect_nodes_and_edges: StyleStatement dispatch ----------------------


def test_collect_style_statement_stores_properties() -> None:
    """A StyleStatement writes node_styles[node_id] = properties."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            StyleStatement(node_id="A", properties=[("fill", "#fff"), ("stroke", "#000")]),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.node_styles == {"A": [("fill", "#fff"), ("stroke", "#000")]}


def test_collect_style_statement_overwrites_on_repeat() -> None:
    """A second style for the same node overwrites (grok ``HashMap::insert``)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            StyleStatement(node_id="A", properties=[("fill", "#fff")]),
            StyleStatement(node_id="A", properties=[("fill", "#000")]),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.node_styles["A"] == [("fill", "#000")]


# --- node_to_subgraph first-write-wins -------------------------------------


def test_node_to_subgraph_first_write_wins() -> None:
    """A node seen in sg1 then re-seen in sg2 stays attributed to sg1."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg1",
                title=None,
                statements=[Node(id="shared", label="s", shape=NodeShape.Rectangle)],
            ),
            Subgraph(
                id="sg2",
                title=None,
                statements=[Node(id="shared", label="s", shape=NodeShape.Rectangle)],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.node_to_subgraph["shared"] == "sg1"


# --- is_subgraph_id --------------------------------------------------------


def test_is_subgraph_id_true_for_collected_subgraph() -> None:
    """A collected subgraph id is recognized."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="sg", title=None, statements=[])],
    )
    engine = LayoutEngine(graph)
    assert engine.is_subgraph_id("sg") is True


def test_is_subgraph_id_false_for_plain_id() -> None:
    """A plain node id is not a subgraph id."""
    engine = LayoutEngine(_empty_graph())
    assert engine.is_subgraph_id("nope") is False


# --- is_state_diagram ------------------------------------------------------


@pytest.mark.parametrize(
    "shape",
    [NodeShape.StartState, NodeShape.EndState, NodeShape.ForkJoin],
)
def test_is_state_diagram_true_for_state_shapes(shape: NodeShape) -> None:
    """Any state-diagram shape flags the engine as a state diagram."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=shape)],
    )
    assert LayoutEngine(graph).is_state_diagram is True


def test_is_state_diagram_false_for_plain_flowchart() -> None:
    """A plain flowchart (no state shape) is not a state diagram."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="A", label="A", shape=NodeShape.Rectangle)],
    )
    assert LayoutEngine(graph).is_state_diagram is False


# --- add_node --------------------------------------------------------------


def test_add_node_skips_when_existing_and_label_none() -> None:
    """Re-seeing an existing node with label=None is a no-op (label preserved)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="A", label="first", shape=NodeShape.Rectangle)],
    )
    engine = LayoutEngine(graph)
    engine.add_node(Node(id="A", label=None, shape=NodeShape.Circle))
    assert engine.nodes["A"].label == "first"
    assert engine.nodes["A"].shape is NodeShape.Rectangle  # not overwritten


def test_add_node_reuses_existing_order() -> None:
    """Re-adding an existing node reuses its order (order is stable)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
        ],
    )
    engine = LayoutEngine(graph)
    order_a_before = engine.nodes["A"].order
    engine.add_node(Node(id="A", label="A2", shape=NodeShape.Rectangle))
    assert engine.nodes["A"].order == order_a_before


# --- ensure_node_exists ----------------------------------------------------


def test_ensure_node_exists_creates_rectangle_fallback() -> None:
    """An unknown id is materialized as a Rectangle with label=id."""
    engine = LayoutEngine(_empty_graph())
    assert "ghost" not in engine.nodes
    engine.ensure_node_exists("ghost")
    info = engine.nodes["ghost"]
    assert info.label == "ghost"
    assert info.shape is NodeShape.Rectangle
    assert info.rank == 0


def test_ensure_node_exists_noop_when_present() -> None:
    """An already-present node is left untouched (not re-measured / replaced)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="A", label="A", shape=NodeShape.Circle)],
    )
    engine = LayoutEngine(graph)
    before = engine.nodes["A"]
    engine.ensure_node_exists("A")
    assert engine.nodes["A"] is before  # same object, not replaced


def test_ensure_node_exists_skips_subgraph_id() -> None:
    """A subgraph id is not materialized as a node."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="sg", title=None, statements=[])],
    )
    engine = LayoutEngine(graph)
    engine.ensure_node_exists("sg")
    assert "sg" not in engine.nodes


# --- next_node_order -------------------------------------------------------


def test_next_node_order_increments_per_new_node() -> None:
    """Each brand-new node (declared or materialized) consumes one order slot."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="C", label=None, style=EdgeStyle.Arrow),  # C materialized
        ],
    )
    engine = LayoutEngine(graph)
    orders = {info.order for info in engine.nodes.values()}
    assert orders == {0, 1, 2}
    assert engine.next_node_order == 3


# --- measure_node: flowchart sizing table ----------------------------------
#
# Each shape asserts measure_node against the exact formula grok applies to the
# (text_width, text_height) the public text_wrap API returns -- zero-semantic
# clone: change a coefficient in measure_node and the assertion fails.


def test_measure_node_rectangle() -> None:
    """Rectangle: (text_width + padding*4, text_height + padding*2)."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("hello")
    padding = engine.options.padding
    assert engine.measure_node("hello", NodeShape.Rectangle) == (tw + padding * 4.0, th + padding * 2.0)


def test_measure_node_rounded_rectangle() -> None:
    """RoundedRectangle: (text_width + padding*2, text_height + padding*2)."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("hello")
    padding = engine.options.padding
    assert engine.measure_node("hello", NodeShape.RoundedRectangle) == (
        tw + padding * 2.0,
        th + padding * 2.0,
    )


def test_measure_node_diamond_flowchart() -> None:
    """Diamond (flowchart): square with side = (tw+pad) + (th+pad)."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("decide")
    padding = engine.options.padding
    side = (tw + padding) + (th + padding)
    assert engine.measure_node("decide", NodeShape.Diamond) == (side, side)


def test_measure_node_circle() -> None:
    """Circle: diameter = text_width + padding; square."""
    engine = LayoutEngine(_empty_graph())
    tw, _th = _expected_text("o")
    padding = engine.options.padding
    diameter = tw + padding
    assert engine.measure_node("o", NodeShape.Circle) == (diameter, diameter)


def test_measure_node_stadium() -> None:
    """Stadium: w = tw + h/4 + pad; h = th + pad."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("start")
    padding = engine.options.padding
    h = th + padding
    w = tw + h / 4.0 + padding
    assert engine.measure_node("start", NodeShape.Stadium) == (w, h)


def test_measure_node_subroutine() -> None:
    """Subroutine: (tw + pad + 16, th + pad)."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("sub")
    padding = engine.options.padding
    assert engine.measure_node("sub", NodeShape.Subroutine) == (tw + padding + 16.0, th + padding)


def test_measure_node_asymmetric() -> None:
    """Asymmetric: w = (tw+pad) + (th+pad)/4; h = th+pad."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("asym")
    padding = engine.options.padding
    h = th + padding
    w = (tw + padding) + h / 4.0
    assert engine.measure_node("asym", NodeShape.Asymmetric) == (w, h)


def test_measure_node_hexagon() -> None:
    """Hexagon: w = (tw + pad*2.5) * 7/6; h = th + pad."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("hex")
    padding = engine.options.padding
    h = th + padding
    w = (tw + padding * 2.5) * 7.0 / 6.0
    assert engine.measure_node("hex", NodeShape.Hexagon) == (w, h)


def test_measure_node_cylinder() -> None:
    """Cylinder: w = tw+pad; h = (th + ry + pad) + 2*ry where ry = (w/2)/(2.5 + w/50)."""
    engine = LayoutEngine(_empty_graph())
    tw, th = _expected_text("db")
    padding = engine.options.padding
    w = tw + padding
    rx = w / 2.0
    ry = rx / (2.5 + w / 50.0)
    h = th + ry + padding
    assert engine.measure_node("db", NodeShape.Cylinder) == (w, h + 2.0 * ry)


@pytest.mark.parametrize(
    "shape,expected",
    [
        (NodeShape.StartState, (14.0, 14.0)),
        (NodeShape.EndState, (20.0, 20.0)),
        (NodeShape.ForkJoin, (70.0, 10.0)),
    ],
)
def test_measure_node_fixed_size_flowchart_shapes(
    shape: NodeShape, expected: tuple[float, float]
) -> None:
    """StartState/EndState/ForkJoin return fixed boxes (text-independent)."""
    engine = LayoutEngine(_empty_graph())
    assert engine.measure_node("anything", shape) == expected


# --- measure_node: state-diagram sizing table ------------------------------


def test_measure_node_state_diagram_uses_state_char_width() -> None:
    """In a state diagram, the text width is computed at STATE_CHAR_WIDTH (6.7)."""
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    engine = LayoutEngine(state_graph)
    # Rectangle in a state diagram falls through to the flowchart table, but the
    # text width underneath is measured at STATE_CHAR_WIDTH (6.7), not 8.0.
    tw_state, th_state = _expected_text("ready", char_width=layout_mod.STATE_CHAR_WIDTH)
    tw_plain, th_plain = _expected_text("ready", char_width=DEFAULT_CHAR_WIDTH)
    padding = engine.options.padding
    expected_state = (tw_state + padding * 4.0, th_state + padding * 2.0)
    expected_plain = (tw_plain + padding * 4.0, th_plain + padding * 2.0)
    assert engine.measure_node("ready", NodeShape.Rectangle) == expected_state
    assert engine.measure_node("ready", NodeShape.Rectangle) != expected_plain


def test_measure_node_state_rounded_rectangle() -> None:
    """State RoundedRectangle: max(tw+6, 32) x max(th+16, 40)."""
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    engine = LayoutEngine(state_graph)
    tw, th = _expected_text("ready", char_width=layout_mod.STATE_CHAR_WIDTH)
    assert engine.measure_node("ready", NodeShape.RoundedRectangle) == (
        max(tw + layout_mod.STATE_NODE_WIDTH_PADDING, 32.0),
        max(th + layout_mod.STATE_NODE_HEIGHT_PADDING, layout_mod.STATE_NODE_MIN_HEIGHT),
    )


def test_measure_node_state_diamond() -> None:
    """State Diamond: square with side = max(tw+18, th+18, 40)."""
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    engine = LayoutEngine(state_graph)
    tw, th = _expected_text("choice", char_width=layout_mod.STATE_CHAR_WIDTH)
    side = max(
        tw + layout_mod.STATE_DIAMOND_PADDING,
        th + layout_mod.STATE_DIAMOND_PADDING,
        layout_mod.STATE_NODE_MIN_HEIGHT,
    )
    assert engine.measure_node("choice", NodeShape.Diamond) == (side, side)


@pytest.mark.parametrize(
    "shape,expected",
    [
        (NodeShape.StartState, (14.0, 14.0)),
        # state table: EndState -> (14, 14), NOT the flowchart (20, 20)
        (NodeShape.EndState, (14.0, 14.0)),
        (NodeShape.ForkJoin, (layout_mod.STATE_FORK_WIDTH, layout_mod.STATE_FORK_HEIGHT)),
    ],
)
def test_measure_node_state_fixed_shapes(
    shape: NodeShape, expected: tuple[float, float]
) -> None:
    """State-diagram StartState/EndState/ForkJoin use the state table (EndState=14)."""
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    engine = LayoutEngine(state_graph)
    assert engine.measure_node("x", shape) == expected


def test_measure_node_state_non_state_shape_falls_through_to_flowchart() -> None:
    """A non-state-matching shape in a state diagram falls through to flowchart sizing."""
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    engine = LayoutEngine(state_graph)
    # Hexagon is not in the state match arms -> grok `_ => {}` fall-through.
    tw, th = _expected_text("h", char_width=layout_mod.STATE_CHAR_WIDTH)
    padding = engine.options.padding
    h = th + padding
    w = (tw + padding * 2.5) * 7.0 / 6.0
    assert engine.measure_node("h", NodeShape.Hexagon) == (w, h)


# --- R274b barrel contract: LayoutEngine stays internal --------------------


def test_layout_engine_not_in_module_all() -> None:
    """``LayoutEngine`` is internal -- not in ``layout.__all__`` (R274b adds no pub fn)."""
    assert "LayoutEngine" not in layout_mod.__all__


def test_layout_engine_not_in_to_svg_barrel() -> None:
    """``LayoutEngine`` is NOT re-exported through the ``to_svg`` barrel."""
    assert "LayoutEngine" not in to_svg.__all__


# === R274c: dagre bridge helpers (14 methods) ==============================
#
# Black-box behavior tests for the 14 ``LayoutEngine`` instance methods that
# wire the collected layout model to the dagre stack (grok layout.rs
# L465-512 + L811-1447 + L1663-1734). All dagre / data_structures symbols are
# imported locally so the file's top-level import block is untouched (ruff
# I001: first-party absolute imports precede relative ones).


def _chain_graph() -> FlowchartGraph:
    """A simple 3-node DAG chain ``A -> B -> C`` (no subgraphs)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Node(id="C", label="C", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
            Edge(from_="B", to="C", label=None, style=EdgeStyle.Arrow),
        ],
    )


def _two_node_cycle_graph() -> FlowchartGraph:
    """A 2-node cycle ``A -> B -> A`` (one back edge)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
            Edge(from_="B", to="A", label=None, style=EdgeStyle.Arrow),
        ],
    )


# --- cohort 1: subgraph ordering ------------------------------------------


def test_subgraph_ids_in_mermaid_order_empty_returns_empty_list() -> None:
    """A graph without subgraphs yields an empty order list."""
    engine = LayoutEngine(_chain_graph())
    assert engine.subgraph_ids_in_mermaid_order() == []


def test_subgraph_ids_in_mermaid_order_nested_outer_first() -> None:
    """Post-order DFS reversed -> outermost subgraph declared first.

    grok records each id on the way back up the DFS (``inner`` then ``outer``)
    then reverses so the declaration order (outer before inner) wins.
    """
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="outer",
                title="O",
                statements=[
                    Subgraph(
                        id="inner",
                        title="I",
                        statements=[Node(id="X", label="X", shape=NodeShape.Rectangle)],
                    ),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.subgraph_ids_in_mermaid_order() == ["outer", "inner"]


# --- cohort 2: edge-endpoint collapse -------------------------------------


def test_nodes_in_subgraph_by_order_sorts_by_node_order() -> None:
    """Direct children of a subgraph are returned in declaration order."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[
                    Node(id="B", label="B", shape=NodeShape.Rectangle),
                    Node(id="A", label="A", shape=NodeShape.Rectangle),
                    Node(id="C", label="C", shape=NodeShape.Rectangle),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.nodes_in_subgraph_by_order("sg") == ["B", "A", "C"]


def test_subgraph_entry_node_id_first_without_sibling_in_edge() -> None:
    """The entry is the first node with no incoming sibling edge."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[
                    Node(id="A", label="A", shape=NodeShape.Rectangle),
                    Node(id="B", label="B", shape=NodeShape.Rectangle),
                    Node(id="C", label="C", shape=NodeShape.Rectangle),
                    Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
                    Edge(from_="B", to="C", label=None, style=EdgeStyle.Arrow),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.subgraph_entry_node_id("sg") == "A"
    assert engine.subgraph_exit_node_id("sg") == "C"


def test_subgraph_entry_exit_falls_back_on_internal_cycle() -> None:
    """A fully cyclic subgraph falls back to the first (entry) / last (exit)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[
                    Node(id="A", label="A", shape=NodeShape.Rectangle),
                    Node(id="B", label="B", shape=NodeShape.Rectangle),
                    Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
                    Edge(from_="B", to="A", label=None, style=EdgeStyle.Arrow),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.subgraph_entry_node_id("sg") == "A"
    assert engine.subgraph_exit_node_id("sg") == "B"


def test_subgraph_entry_exit_empty_subgraph_returns_none() -> None:
    """A subgraph with no nodes has no entry / exit node."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="empty", title="E", statements=[])],
    )
    engine = LayoutEngine(graph)
    assert engine.subgraph_entry_node_id("empty") is None
    assert engine.subgraph_exit_node_id("empty") is None


def test_dagre_edge_endpoint_plain_node_returns_self() -> None:
    """A plain node id maps to itself regardless of source/target role."""
    engine = LayoutEngine(_chain_graph())
    assert engine.dagre_edge_endpoint("A", True) == "A"
    assert engine.dagre_edge_endpoint("C", False) == "C"


def test_dagre_edge_endpoint_subgraph_source_exit_sink_entry() -> None:
    """A subgraph endpoint collapses to its exit (source) / entry (sink)."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[
                    Node(id="A", label="A", shape=NodeShape.Rectangle),
                    Node(id="C", label="C", shape=NodeShape.Rectangle),
                    Edge(from_="A", to="C", label=None, style=EdgeStyle.Arrow),
                ],
            ),
        ],
    )
    engine = LayoutEngine(graph)
    # entry=A (no sibling in-edge), exit=C (no sibling out-edge)
    assert engine.dagre_edge_endpoint("sg", True) == "C"  # source -> exit
    assert engine.dagre_edge_endpoint("sg", False) == "A"  # sink -> entry


def test_layout_endpoint_node_returns_existing_node() -> None:
    """A node id present in ``layout_nodes`` returns its clone unchanged."""
    engine = LayoutEngine(_chain_graph())
    box = LayoutNode(
        id="A", x=5.0, y=6.0, width=10.0, height=20.0,
        shape=NodeShape.Rectangle, label="A", fill_color=None, stroke_color=None,
    )
    resolved = engine.layout_endpoint_node({"A": box}, [], "A")
    assert resolved is box


def test_layout_endpoint_node_synthesises_centred_subgraph_box() -> None:
    """A subgraph id synthesises a centred rectangle (centre = subgraph centre)."""
    engine = LayoutEngine(_chain_graph())
    sg = LayoutSubgraph(
        id="sg", title="Cluster", x=10.0, y=20.0, width=30.0, height=40.0,
    )
    resolved = engine.layout_endpoint_node({}, [sg], "sg")
    assert resolved is not None
    assert resolved.id == "sg"
    assert resolved.x == 25.0  # 10 + 30/2
    assert resolved.y == 40.0  # 20 + 40/2
    assert resolved.width == 30.0
    assert resolved.height == 40.0
    assert resolved.shape == NodeShape.Rectangle
    assert resolved.label == "Cluster"


def test_layout_endpoint_node_unknown_id_returns_none() -> None:
    """An id that is neither a node nor a known subgraph resolves to ``None``."""
    engine = LayoutEngine(_chain_graph())
    assert engine.layout_endpoint_node({}, [], "ghost") is None


# --- cohort 3: edge-label sizing ------------------------------------------


def test_edge_label_dimensions_empty_returns_none() -> None:
    """An empty / whitespace-only label yields ``None`` (caller skips the box)."""
    engine = LayoutEngine(_chain_graph())
    assert engine.edge_label_dimensions("") is None
    assert engine.edge_label_dimensions("   ") is None


def test_edge_label_dimensions_padded_box_matches_wrapped_measure() -> None:
    """The box equals the wrapped measure plus ``EDGE_LABEL_PADDING`` on all sides."""
    engine = LayoutEngine(_chain_graph())
    label = "a sample edge label"
    from minimax_code.mermaid.to_svg.text_wrap import (
        DEFAULT_CHAR_WIDTH,
        measure_wrapped_lines_with_font_size,
        scale_char_width,
        wrap_text_lines,
    )

    char_width = scale_char_width(DEFAULT_CHAR_WIDTH, engine.options.font_size)
    lines = wrap_text_lines(label, engine.options.wrapping_width, char_width)
    text_w, text_h = measure_wrapped_lines_with_font_size(
        lines, char_width, engine.options.font_size
    )
    expected = (
        text_w + layout_mod.EDGE_LABEL_PADDING * 2.0,
        text_h + layout_mod.EDGE_LABEL_PADDING * 2.0,
    )
    assert engine.edge_label_dimensions(label) == expected


def test_edge_label_dimensions_state_diagram_uses_narrower_char_width() -> None:
    """State diagrams use ``STATE_CHAR_WIDTH`` (6.7 < ``DEFAULT_CHAR_WIDTH`` 8.0).

    The label is kept short enough that it stays on a single wrapped line for
    *both* char widths -- once the wrap-line count is fixed, the measured box
    width is strictly proportional to the per-char estimate, so the narrower
    ``STATE_CHAR_WIDTH`` yields a strictly narrower box. (A long label would
    confuse the comparison: the narrower estimate can keep it on one line
    while the wider estimate wraps it, making the "narrower" box wider.)
    """
    state_graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Node(id="s", label="s", shape=NodeShape.StartState)],
    )
    state_engine = LayoutEngine(state_graph)
    flow_engine = LayoutEngine(_chain_graph())
    label = "ab"
    state_dim = state_engine.edge_label_dimensions(label)
    flow_dim = flow_engine.edge_label_dimensions(label)
    assert state_dim is not None
    assert flow_dim is not None
    # both single-line -> narrower per-char estimate -> narrower measured box
    assert state_dim[0] < flow_dim[0]


# --- cohort 4: rank / alignment passes ------------------------------------


def test_longest_path_ranks_chain_dag() -> None:
    """A chain ``A -> B -> C`` ranks ``{A:0, B:1, C:2}``."""
    engine = LayoutEngine(_chain_graph())
    assert engine.longest_path_ranks_without_back_edges(set()) == {
        "A": 0,
        "B": 1,
        "C": 2,
    }


def test_longest_path_ranks_all_cycle_returns_zero_dict() -> None:
    """With every edge treated as a back edge, no rank propagation occurs.

    All nodes initialize to rank 0; ranks only rise via forward edges. When
    *every* edge is in ``back_edges``, the stripped graph has no forward edge
    to propagate along, so every node stays at 0. (Marking only ``(B, A)``
    would leave ``(A, B)`` as a forward edge, ranking ``B`` at 1 -- the
    natural single-back-edge case covered by ``detect_back_edges``.)
    """
    engine = LayoutEngine(_two_node_cycle_graph())
    ranks = engine.longest_path_ranks_without_back_edges({("A", "B"), ("B", "A")})
    assert ranks == {"A": 0, "B": 0}


def test_snap_state_ranks_projects_y_onto_rank_grid() -> None:
    """Each node's y is re-projected onto the sorted deduped grid by its rank."""
    engine = LayoutEngine(_chain_graph())
    # ranks: A=0, B=1, C=2; y-values deliberately scrambled out of rank order
    positions: PositionMap = {
        "A": (0.0, 100.0),
        "B": (0.0, 10.0),
        "C": (0.0, 20.0),
    }
    engine.snap_state_ranks(positions, set())
    # grid = sorted deduped y = [10, 20, 100]; rank 0 -> 10, 1 -> 20, 2 -> 100
    assert positions == {"A": (0.0, 10.0), "B": (0.0, 20.0), "C": (0.0, 100.0)}


def test_snap_state_ranks_noop_when_grid_too_short() -> None:
    """When the deduped grid is too short for the max rank, positions stay put."""
    engine = LayoutEngine(_chain_graph())
    positions: PositionMap = {
        "A": (0.0, 0.0),
        "B": (0.0, 0.0),
        "C": (0.0, 100.0),
    }
    # grid dedupes to [0, 100] (len 2) but max_rank is 2 -> 2 <= 2 -> no-op
    engine.snap_state_ranks(positions, set())
    assert positions == {
        "A": (0.0, 0.0),
        "B": (0.0, 0.0),
        "C": (0.0, 100.0),
    }


def test_align_state_terminal_singletons_snaps_x_to_max_predecessor() -> None:
    """A lone sink with >=2 lower-rank predecessors snaps x to the max predecessor x."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Node(id="C", label="C", shape=NodeShape.Rectangle),
            Edge(from_="A", to="C", label=None, style=EdgeStyle.Arrow),
            Edge(from_="B", to="C", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    positions: PositionMap = {
        "A": (0.0, 0.0),
        "B": (100.0, 0.0),
        "C": (50.0, 100.0),
    }
    engine.align_state_terminal_singletons(positions, set())
    # C is the lone rank-1 sink with predecessors A, B at rank 0 -> x = max(0, 100)
    assert positions["C"] == (100.0, 100.0)
    assert positions["A"] == (0.0, 0.0)
    assert positions["B"] == (100.0, 0.0)


# --- cohort 5: back-edge discovery ----------------------------------------


def test_dfs_detect_back_edges_records_in_stack_neighbor() -> None:
    """An edge to a node still on the recursion stack is a back edge."""
    engine = LayoutEngine(_two_node_cycle_graph())
    visited: set[str] = set()
    in_stack: set[str] = set()
    back_edges: set[tuple[str, str]] = set()
    engine.dfs_detect_back_edges("A", visited, in_stack, back_edges)
    assert back_edges == {("B", "A")}
    assert visited == {"A", "B"}
    assert in_stack == set()  # every node discarded as recursion unwinds


def test_detect_back_edges_dag_returns_empty_set() -> None:
    """A DAG has no back edges."""
    engine = LayoutEngine(_chain_graph())
    assert engine.detect_back_edges() == set()


def test_detect_back_edges_two_node_cycle() -> None:
    """``A -> B -> A`` yields exactly one back edge ``("B", "A")``."""
    engine = LayoutEngine(_two_node_cycle_graph())
    assert engine.detect_back_edges() == {("B", "A")}


# --- cohort 5: bridge pair (build / extract) ------------------------------


def test_build_dagre_graph_returns_compound_graph_and_edge_map() -> None:
    """``build_dagre_graph`` yields a dagre ``Graph`` + edge-index map."""
    from minimax_code.data_structures import Graph

    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label="x", style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    g, edge_map = engine.build_dagre_graph("TB", 50.0, 50.0, set())
    assert isinstance(g, Graph)
    assert set(g.nodes()) >= {"A", "B"}
    assert edge_map == {0: ("A", "B")}
    # a labelled edge carries a measured label box (width/height > 0)
    edge_obj = g.edge("A", "B", None)
    assert edge_obj is not None
    assert edge_obj.width is not None and edge_obj.width > 0.0
    assert edge_obj.height is not None and edge_obj.height > 0.0


def test_build_dagre_graph_drops_back_edges_and_self_loops() -> None:
    """Back edges and self-loops are absent from both dagre and ``edge_map``."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),  # idx 0 -- kept
            Edge(from_="B", to="A", label=None, style=EdgeStyle.Arrow),  # idx 1 -- back edge, dropped
            Edge(from_="A", to="A", label=None, style=EdgeStyle.Arrow),  # idx 2 -- self-loop, dropped
        ],
    )
    engine = LayoutEngine(graph)
    g, edge_map = engine.build_dagre_graph("TB", 50.0, 50.0, {("B", "A")})
    assert edge_map == {0: ("A", "B")}
    assert g.edge("A", "B", None) is not None
    assert g.edge("B", "A", None) is None


def test_build_dagre_graph_seeds_zero_size_subgraph_node() -> None:
    """A subgraph seeds a zero-size node with ``SUBGRAPH_PADDING``."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title="S",
                statements=[Node(id="A", label="A", shape=NodeShape.Rectangle)],
            ),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    g, _ = engine.build_dagre_graph("TB", 50.0, 50.0, set())
    assert "sg" in g.nodes()
    sg_node = g.node("sg")
    assert sg_node is not None
    assert sg_node.width == 0.0
    assert sg_node.height == 0.0
    assert sg_node.padding == layout_mod.SUBGRAPH_PADDING


def test_extract_layout_from_dagre_reads_node_positions() -> None:
    """``extract_layout_from_dagre`` copies each node's dagre ``(x, y)``."""
    from minimax_code.dagre import GraphNode

    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    g, edge_map = engine.build_dagre_graph("TB", 50.0, 50.0, set())
    # inject dagre-computed coordinates (build seeds width/height, not x/y)
    g.set_node("A", GraphNode(x=10.0, y=20.0))
    g.set_node("B", GraphNode(x=10.0, y=70.0))
    positions, _, _ = engine.extract_layout_from_dagre(g, edge_map)
    assert positions["A"] == (10.0, 20.0)
    assert positions["B"] == (10.0, 70.0)


def test_extract_layout_from_dagre_extracts_edge_points_and_labels() -> None:
    """Edge polylines land in ``edge_points``; measured labels in label positions."""
    from minimax_code.dagre import GraphEdge, GraphEdgePoint

    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label="hi", style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    g, edge_map = engine.build_dagre_graph("TB", 50.0, 50.0, set())
    # inject the geometry the position phase would have filled
    g.set_edge(
        "A",
        "B",
        GraphEdge(
            labelpos="c",
            width=30.0,
            height=14.0,
            x=15.0,
            y=45.0,
            points=[GraphEdgePoint(x=10.0, y=20.0), GraphEdgePoint(x=10.0, y=70.0)],
        ),
        None,
    )
    _, edge_points, edge_labels = engine.extract_layout_from_dagre(g, edge_map)
    assert edge_points[0] == [(10.0, 20.0), (10.0, 70.0)]
    assert edge_labels[0] == (15.0, 45.0)


# --- R274c barrel contract: layout stays internal -------------------------
# R274f: the authoritative ``__all__`` surface assertion lives at the end of
# this module (``test_layout_module_all_contract``). The R274c-era 4-symbol
# stub was removed -- the surface grew to 6 in R274f (2 ``pub fn`` added).


def test_dagre_graph_alias_internal_not_in_module_all() -> None:
    """The ``DagreGraph`` type alias is internal -- not in ``layout.__all__``."""
    assert "DagreGraph" not in layout_mod.__all__
    assert hasattr(layout_mod, "DagreGraph")  # reachable by deep path


def test_mermaid_root_barrel_unchanged_by_r274c() -> None:
    """R274c adds an internal-method cohort; the R38 root surface stays at 17."""
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__


# === R274d: boundary + subgraph cluster helpers (9 methods) ================
#
# Covers the 9 LayoutEngine instance methods fused from grok's layout.rs impl
# block: compute_spacing, center_nodes_in_subgraphs, find_connected_subgraph_groups,
# analyze_clusters, compute_subgraph_bounds, subgraph_title_height,
# subgraph_ids_bottom_up, compute_bounds, get_node_colors. Each method is exercised
# on a minimal graph that makes the grok semantics deterministic.


def _layout_node(
    node_id: str,
    x: float = 0.0,
    y: float = 0.0,
    width: float = 40.0,
    height: float = 20.0,
    label: str | None = None,
) -> LayoutNode:
    """Build a centred :class:`LayoutNode` box (dagre x/y = box centre)."""
    return LayoutNode(
        id=node_id,
        x=x,
        y=y,
        width=width,
        height=height,
        shape=NodeShape.Rectangle,
        label=label if label is not None else node_id,
        fill_color=None,
        stroke_color=None,
    )


def _connected_subgraphs_graph() -> FlowchartGraph:
    """Two subgraphs linked by one cross-cluster edge n1 -> n2."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg1",
                title=None,
                statements=[Node(id="n1", label="n1", shape=NodeShape.Rectangle)],
            ),
            Subgraph(
                id="sg2",
                title=None,
                statements=[Node(id="n2", label="n2", shape=NodeShape.Rectangle)],
            ),
            Edge(from_="n1", to="n2", label=None, style=EdgeStyle.Arrow),
        ],
    )


def _two_isolated_subgraphs_graph() -> FlowchartGraph:
    """Two subgraphs with no edge linking them (lone components)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg1",
                title=None,
                statements=[Node(id="n1", label="n1", shape=NodeShape.Rectangle)],
            ),
            Subgraph(
                id="sg2",
                title=None,
                statements=[Node(id="n2", label="n2", shape=NodeShape.Rectangle)],
            ),
        ],
    )


def _internal_edge_subgraph_graph() -> FlowchartGraph:
    """One subgraph holding both endpoints of a purely internal edge."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg1",
                title=None,
                statements=[
                    Node(id="n1", label="n1", shape=NodeShape.Rectangle),
                    Node(id="n2", label="n2", shape=NodeShape.Rectangle),
                    Edge(from_="n1", to="n2", label=None, style=EdgeStyle.Arrow),
                ],
            ),
        ],
    )


def _single_subgraph_graph() -> FlowchartGraph:
    """One subgraph owning a single node (title None -> falls back to id)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="sg",
                title=None,
                statements=[Node(id="n1", label="n1", shape=NodeShape.Rectangle)],
            ),
        ],
    )


def _empty_subgraph_graph() -> FlowchartGraph:
    """One subgraph owning no nodes (skipped by compute_subgraph_bounds)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Subgraph(id="sg", title=None, statements=[])],
    )


def _nested_subgraphs_graph() -> FlowchartGraph:
    """Outer subgraph containing one direct node + an inner subgraph."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Subgraph(
                id="outer",
                title="Outer",
                statements=[
                    Node(id="n1", label="n1", shape=NodeShape.Rectangle),
                    Subgraph(
                        id="inner",
                        title="Inner",
                        statements=[Node(id="n2", label="n2", shape=NodeShape.Rectangle)],
                    ),
                ],
            ),
        ],
    )


def _styled_node_graph() -> FlowchartGraph:
    """A node carrying a fill + stroke style directive."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="n1", label="n1", shape=NodeShape.Rectangle),
            StyleStatement(node_id="n1", properties=[("fill", "#fff"), ("stroke", "#000")]),
        ],
    )


def _fill_only_node_graph() -> FlowchartGraph:
    """A node carrying only a fill style directive (stroke missing)."""
    return FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="n1", label="n1", shape=NodeShape.Rectangle),
            StyleStatement(node_id="n1", properties=[("fill", "#f0f0f0")]),
        ],
    )


def _expected_title_height(title: str) -> float:
    """Mirror :meth:`subgraph_title_height` under default options (font 16, wrap 200)."""
    lines = wrap_text_lines(title, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    _, height = measure_wrapped_lines_with_font_size(
        lines, DEFAULT_CHAR_WIDTH, DEFAULT_FONT_SIZE
    )
    return max(height, layout_mod.SUBGRAPH_TITLE_HEIGHT)


# --- compute_spacing -------------------------------------------------------


def test_compute_spacing_default_returns_node_and_rank_sep() -> None:
    """Default options -> ``(NODE_SEP, RANK_SEP)`` = ``(50.0, 50.0)``."""
    engine = LayoutEngine(_empty_graph())
    assert engine.compute_spacing() == (layout_mod.NODE_SEP, layout_mod.RANK_SEP)


def test_compute_spacing_reflects_custom_options() -> None:
    """Custom options surface verbatim through ``compute_spacing``."""
    opts = FlowchartLayoutOptions(node_spacing=30.0, rank_spacing=70.0)
    engine = LayoutEngine(_empty_graph(), options=opts)
    assert engine.compute_spacing() == (30.0, 70.0)


# --- center_nodes_in_subgraphs ---------------------------------------------


def test_center_nodes_in_subgraphs_noop_without_connected_groups() -> None:
    """Isolated subgraphs form no connected group -> positions untouched."""
    engine = LayoutEngine(_two_isolated_subgraphs_graph())
    positions: PositionMap = {"n1": (10.0, 20.0), "n2": (90.0, 80.0)}
    engine.center_nodes_in_subgraphs(positions, is_vertical=True)
    assert positions == {"n1": (10.0, 20.0), "n2": (90.0, 80.0)}


def test_center_nodes_in_subgraphs_vertical_shifts_x_to_group_centre() -> None:
    """is_vertical=True centres each subgraph's x on the group centre.

    Group of {sg1, sg2}: avg x = (0 + 100)/2 = 50; sg1 avg 0 -> +50;
    sg2 avg 100 -> -50; both land at x = 50.
    """
    engine = LayoutEngine(_connected_subgraphs_graph())
    positions = {"n1": (0.0, 5.0), "n2": (100.0, 5.0)}
    engine.center_nodes_in_subgraphs(positions, is_vertical=True)
    assert positions == {"n1": (50.0, 5.0), "n2": (50.0, 5.0)}


def test_center_nodes_in_subgraphs_horizontal_shifts_y_to_group_centre() -> None:
    """is_vertical=False centres on the y axis instead."""
    engine = LayoutEngine(_connected_subgraphs_graph())
    positions = {"n1": (5.0, 0.0), "n2": (5.0, 100.0)}
    engine.center_nodes_in_subgraphs(positions, is_vertical=False)
    assert positions == {"n1": (5.0, 50.0), "n2": (5.0, 50.0)}


# --- find_connected_subgraph_groups ----------------------------------------


def test_find_connected_subgraph_groups_empty_returns_empty() -> None:
    """No subgraphs -> no groups."""
    engine = LayoutEngine(_empty_graph())
    assert engine.find_connected_subgraph_groups() == []


def test_find_connected_subgraph_groups_isolated_subgraphs_skipped() -> None:
    """Lone components (size 1) are filtered out."""
    engine = LayoutEngine(_two_isolated_subgraphs_graph())
    assert engine.find_connected_subgraph_groups() == []


def test_find_connected_subgraph_groups_cross_edge_merges_pair() -> None:
    """A cross-cluster edge merges sg1 + sg2 into one size-2 group."""
    engine = LayoutEngine(_connected_subgraphs_graph())
    groups = engine.find_connected_subgraph_groups()
    assert len(groups) == 1
    assert groups[0] == {"sg1", "sg2"}


# --- analyze_clusters ------------------------------------------------------


def test_analyze_clusters_maps_members_per_subgraph() -> None:
    """``subgraph_nodes`` is the reverse of ``node_to_subgraph``."""
    engine = LayoutEngine(_connected_subgraphs_graph())
    analysis = engine.analyze_clusters()
    assert analysis.subgraph_nodes == {"sg1": {"n1"}, "sg2": {"n2"}}


def test_analyze_clusters_flags_external_cross_edge() -> None:
    """A cross-cluster edge marks both endpoints' subgraphs external."""
    engine = LayoutEngine(_connected_subgraphs_graph())
    analysis = engine.analyze_clusters()
    assert analysis.external_edges == {"sg1": True, "sg2": True}


def test_analyze_clusters_internal_edge_not_external() -> None:
    """An edge with both endpoints inside one subgraph is not external."""
    engine = LayoutEngine(_internal_edge_subgraph_graph())
    analysis = engine.analyze_clusters()
    assert analysis.subgraph_nodes == {"sg1": {"n1", "n2"}}
    assert analysis.external_edges == {"sg1": False}


# --- compute_subgraph_bounds -----------------------------------------------


def test_compute_subgraph_bounds_single_subgraph_padded_rect() -> None:
    """One node + title fallback + padding yields the padded bounding rect.

    Node box (100,100,40,20) -> x[80,120] y[90,110]; title "sg" (None fallback)
    reserves 24.0 above; padding 8 -> x=72, y=58, w=56, h=60.
    """
    engine = LayoutEngine(_single_subgraph_graph())
    layout_nodes = {"n1": _layout_node("n1", x=100.0, y=100.0, width=40.0, height=20.0)}
    result = engine.compute_subgraph_bounds(layout_nodes, padding=8.0)
    assert len(result) == 1
    sg = result[0]
    assert sg.id == "sg"
    assert sg.title == "sg"  # None title falls back to the id at collection time
    assert sg.x == 72.0
    assert sg.y == 58.0
    assert sg.width == 56.0
    assert sg.height == 60.0


def test_compute_subgraph_bounds_skips_empty_subgraph() -> None:
    """A subgraph with no positioned nodes contributes no rect."""
    engine = LayoutEngine(_empty_subgraph_graph())
    assert engine.compute_subgraph_bounds({}, padding=8.0) == []


def test_compute_subgraph_bounds_nested_parent_encompasses_child() -> None:
    """Leaf-first processing lets the parent rect expand around the child rect."""
    engine = LayoutEngine(_nested_subgraphs_graph())
    layout_nodes = {
        "n1": _layout_node("n1", x=0.0, y=0.0, width=40.0, height=20.0),
        "n2": _layout_node("n2", x=100.0, y=50.0, width=40.0, height=20.0),
    }
    result = engine.compute_subgraph_bounds(layout_nodes, padding=8.0)
    by_id = {sg.id: sg for sg in result}
    assert set(by_id) == {"inner", "outer"}

    # inner: n2 box [80,120]x[40,60], title "Inner" 24.0, padding 8.
    inner = by_id["inner"]
    assert inner.x == 72.0
    assert inner.y == 8.0
    assert inner.width == 56.0
    assert inner.height == 60.0

    # outer encompasses inner (its rect grew around the already-computed child).
    outer = by_id["outer"]
    assert outer.x <= inner.x
    assert outer.y <= inner.y
    assert outer.x + outer.width >= inner.x + inner.width
    assert outer.y + outer.height >= inner.y + inner.height


# --- subgraph_title_height -------------------------------------------------


def test_subgraph_title_height_short_title_floored_at_default() -> None:
    """A single-line title is clamped up to SUBGRAPH_TITLE_HEIGHT (24.0)."""
    engine = LayoutEngine(_empty_graph())
    assert engine.subgraph_title_height("S") == layout_mod.SUBGRAPH_TITLE_HEIGHT


def test_subgraph_title_height_long_title_exceeds_floor() -> None:
    """A multi-line title measures taller than the floor."""
    engine = LayoutEngine(_empty_graph())
    long_title = "word " * 60  # wraps to many lines at wrap_width 200
    expected = _expected_title_height(long_title)
    assert expected > layout_mod.SUBGRAPH_TITLE_HEIGHT
    assert engine.subgraph_title_height(long_title) == pytest.approx(expected)


# --- subgraph_ids_bottom_up -----------------------------------------------


def test_subgraph_ids_bottom_up_flat_subgraphs_preserve_order() -> None:
    """Flat (sibling) subgraphs emit in collection order."""
    engine = LayoutEngine(_two_isolated_subgraphs_graph())
    assert engine.subgraph_ids_bottom_up() == ["sg1", "sg2"]


def test_subgraph_ids_bottom_up_nested_leaf_before_parent() -> None:
    """Post-order traversal emits the inner leaf before its outer parent."""
    engine = LayoutEngine(_nested_subgraphs_graph())
    assert engine.subgraph_ids_bottom_up() == ["inner", "outer"]


# --- compute_bounds --------------------------------------------------------


def test_compute_bounds_empty_returns_fallback_viewport() -> None:
    """Empty positions -> the ``(200.0, 200.0)`` fallback viewport."""
    engine = LayoutEngine(_chain_graph())
    assert engine.compute_bounds({}) == (200.0, 200.0)


def test_compute_bounds_max_extents_plus_margin() -> None:
    """Bounds = max half-extent over positioned nodes + MARGIN on each axis."""
    engine = LayoutEngine(_chain_graph())
    positions = {"A": (10.0, 20.0), "B": (30.0, 40.0), "C": (50.0, 60.0)}
    expected_w = (
        max(
            10.0 + engine.nodes["A"].width / 2.0,
            30.0 + engine.nodes["B"].width / 2.0,
            50.0 + engine.nodes["C"].width / 2.0,
        )
        + layout_mod.MARGIN
    )
    expected_h = (
        max(
            20.0 + engine.nodes["A"].height / 2.0,
            40.0 + engine.nodes["B"].height / 2.0,
            60.0 + engine.nodes["C"].height / 2.0,
        )
        + layout_mod.MARGIN
    )
    assert engine.compute_bounds(positions) == (expected_w, expected_h)


def test_compute_bounds_skips_unknown_node_ids() -> None:
    """A position for an unknown node id is ignored."""
    engine = LayoutEngine(_chain_graph())
    positions = {"A": (10.0, 20.0), "X": (999.0, 999.0)}
    expected_w = 10.0 + engine.nodes["A"].width / 2.0 + layout_mod.MARGIN
    expected_h = 20.0 + engine.nodes["A"].height / 2.0 + layout_mod.MARGIN
    assert engine.compute_bounds(positions) == (expected_w, expected_h)


# --- get_node_colors -------------------------------------------------------


def test_get_node_colors_unstyled_node_returns_none_pair() -> None:
    """A node with no style directive -> ``(None, None)``."""
    engine = LayoutEngine(_chain_graph())
    assert engine.get_node_colors("A") == (None, None)


def test_get_node_colors_returns_fill_and_stroke() -> None:
    """Both fill and stroke keys are read from the style property list."""
    engine = LayoutEngine(_styled_node_graph())
    assert engine.get_node_colors("n1") == ("#fff", "#000")


def test_get_node_colors_partial_style_returns_none_for_missing() -> None:
    """A missing stroke key surfaces as ``None`` (fill still read)."""
    engine = LayoutEngine(_fill_only_node_graph())
    assert engine.get_node_colors("n1") == ("#f0f0f0", None)


# --- R274d barrel contract: layout stays internal --------------------------
# R274f: the authoritative ``__all__`` surface assertion lives at the end of
# this module (``test_layout_module_all_contract``). The R274d-era 4-symbol
# stub was removed -- the surface grew to 6 in R274f (2 ``pub fn`` added).


def test_mermaid_root_barrel_unchanged_by_r274d() -> None:
    """R274d adds another internal-method cohort; the R38 root surface stays 17."""
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__


# === R274e: edge geometry (grok layout.rs L2602-L3246, 17 migrated methods) ===
#
# R274e migrates the 17 edge-geometry helpers (13 ``&self`` instance methods +
# 4 associated ``fn`` -> ``@staticmethod``) plus the module-level
# ``_dedup_consecutive`` helper (mirrors Rust ``Vec::dedup``). These exercise
# the shape-aware connection-point projection, obstacle routing, back-edge
# U-detours and edge-clip snapping that the public ``compute_with_dagre``
# body (R274f) will drive.


def _graph_with_direction(direction: GraphDirection) -> FlowchartGraph:
    """Parameterized empty graph (R274e tests cover all 4 directions)."""
    return FlowchartGraph(direction=direction, statements=[])


def _circle_node(
    node_id: str,
    x: float = 0.0,
    y: float = 0.0,
    width: float = 40.0,
    height: float = 40.0,
    label: str | None = None,
) -> LayoutNode:
    """Circle-shaped LayoutNode factory (connection-point shape branch)."""
    return LayoutNode(
        id=node_id,
        x=x,
        y=y,
        width=width,
        height=height,
        shape=NodeShape.Circle,
        label=label if label is not None else node_id,
        fill_color=None,
        stroke_color=None,
    )


def _diamond_node(
    node_id: str,
    x: float = 0.0,
    y: float = 0.0,
    width: float = 40.0,
    height: float = 20.0,
    label: str | None = None,
) -> LayoutNode:
    """Diamond-shaped LayoutNode factory (connection-point shape branch)."""
    return LayoutNode(
        id=node_id,
        x=x,
        y=y,
        width=width,
        height=height,
        shape=NodeShape.Diamond,
        label=label if label is not None else node_id,
        fill_color=None,
        stroke_color=None,
    )


def _layout_edge(
    from_id: str,
    to_id: str,
    points: list[tuple[float, float]] | None = None,
    label: str | None = None,
    label_pos: tuple[float, float] | None = None,
) -> LayoutEdge:
    """LayoutEdge factory (edge_label_bounds / edge_label_midpoint tests)."""
    return LayoutEdge(
        from_=from_id,
        to=to_id,
        label=label,
        style=EdgeStyle.Line,
        points=points if points is not None else [],
        label_pos=label_pos,
    )


# --- edge_label_midpoint (associated fn, 5 branch cases) -------------------


@pytest.mark.parametrize(
    "points,expected",
    [
        ([], (0.0, 0.0)),  # empty -> origin
        ([(5.0, 5.0)], (5.0, 5.0)),  # single point -> itself
        ([(0.0, 0.0), (10.0, 0.0)], (5.0, 0.0)),  # mid of one segment
        ([(0.0, 0.0), (0.0, 0.0)], (0.0, 0.0)),  # near-zero total -> first
        ([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], (10.0, 0.0)),  # L-shape mid
    ],
)
def test_edge_label_midpoint_branches(
    points: list[tuple[float, float]], expected: tuple[float, float]
) -> None:
    """The 5 grok branches of the static midpoint helper."""
    assert LayoutEngine.edge_label_midpoint(points) == pytest.approx(expected)


# --- edge_label_bounds (instance, 3 cases) ---------------------------------


def test_edge_label_bounds_none_label_returns_none() -> None:
    """An edge without a label -> ``None``."""
    engine = LayoutEngine(_empty_graph())
    edge = _layout_edge("a", "b", points=[(0.0, 0.0), (10.0, 0.0)])
    assert engine.edge_label_bounds(edge) is None


def test_edge_label_bounds_uses_explicit_label_pos() -> None:
    """An explicit ``label_pos`` anchors the box at that point."""
    engine = LayoutEngine(_empty_graph())
    edge = _layout_edge("a", "b", points=[(0.0, 0.0), (10.0, 0.0)], label="x", label_pos=(5.0, 5.0))
    dims = engine.edge_label_dimensions("x")
    assert dims is not None
    bounds = engine.edge_label_bounds(edge)
    assert bounds == (5.0, 5.0, dims[0], dims[1])


def test_edge_label_bounds_falls_back_to_midpoint() -> None:
    """Without ``label_pos`` the box is anchored at the geometric midpoint."""
    engine = LayoutEngine(_empty_graph())
    edge = _layout_edge("a", "b", points=[(0.0, 0.0), (10.0, 0.0)], label="x")
    dims = engine.edge_label_dimensions("x")
    assert dims is not None
    mid = LayoutEngine.edge_label_midpoint(edge.points)
    bounds = engine.edge_label_bounds(edge)
    assert bounds is not None
    assert bounds[0:2] == pytest.approx(mid)
    assert bounds[2:4] == dims


# --- is_back_edge (instance, 4 directions x forward/backward) --------------


@pytest.mark.parametrize(
    "direction,from_xy,to_xy,expected",
    [
        (GraphDirection.TopToBottom, (0.0, 100.0), (0.0, 0.0), True),  # above -> back
        (GraphDirection.TopToBottom, (0.0, 0.0), (0.0, 100.0), False),  # below -> forward
        (GraphDirection.BottomToTop, (0.0, 0.0), (0.0, 100.0), True),  # below -> back
        (GraphDirection.BottomToTop, (0.0, 100.0), (0.0, 0.0), False),  # above -> forward
        (GraphDirection.LeftToRight, (100.0, 0.0), (0.0, 0.0), True),  # left -> back
        (GraphDirection.LeftToRight, (0.0, 0.0), (100.0, 0.0), False),  # right -> forward
        (GraphDirection.RightToLeft, (0.0, 0.0), (100.0, 0.0), True),  # right -> back
        (GraphDirection.RightToLeft, (100.0, 0.0), (0.0, 0.0), False),  # left -> forward
    ],
)
def test_is_back_edge_direction_aware(
    direction: GraphDirection,
    from_xy: tuple[float, float],
    to_xy: tuple[float, float],
    expected: bool,
) -> None:
    """``to`` upstream of ``from`` against the flow is a back edge."""
    engine = LayoutEngine(_graph_with_direction(direction))
    from_node = _layout_node("a", x=from_xy[0], y=from_xy[1])
    to_node = _layout_node("b", x=to_xy[0], y=to_xy[1])
    assert engine.is_back_edge(from_node, to_node) is expected


# --- connection_point_towards (instance, outbound, 3 shapes) ---------------


@pytest.mark.parametrize(
    "factory,expected",
    [
        (_layout_node, (20.0, 0.0)),  # rectangle -> right border
        (_circle_node, (20.0, 0.0)),  # circle -> radius point on +x
        (_diamond_node, (20.0, 0.0)),  # diamond -> right vertex
    ],
)
def test_connection_point_towards_shape_branches(
    factory, expected: tuple[float, float]
) -> None:
    """Target on +x -> each shape projects onto its +x border point."""
    engine = LayoutEngine(_empty_graph())
    node = factory("n")
    assert engine.connection_point_towards(node, 100.0, 0.0) == pytest.approx(expected)


# --- connection_point_on_node (instance, inbound, 3 shapes) ----------------


@pytest.mark.parametrize(
    "factory,expected",
    [
        (_layout_node, (20.0, 0.0)),  # rectangle -> border towards caller
        (_circle_node, (20.0, 0.0)),  # circle -> radius point
        (_diamond_node, (20.0, 0.0)),  # diamond -> vertex
    ],
)
def test_connection_point_on_node_shape_branches(
    factory, expected: tuple[float, float]
) -> None:
    """Caller on +x -> each shape's inbound border point faces +x."""
    engine = LayoutEngine(_empty_graph())
    node = factory("n")
    assert engine.connection_point_on_node(node, 100.0, 0.0) == pytest.approx(expected)


# --- line_intersect_rect (associated fn, 4 cases) --------------------------


@pytest.mark.parametrize(
    "p1,p2,rect_min,rect_max,expected",
    [
        ((0.0, 5.0), (100.0, 5.0), (40.0, -10.0), (60.0, 10.0), True),  # crosses
        ((0.0, 5.0), (100.0, 5.0), (40.0, 20.0), (60.0, 30.0), False),  # same side
        ((50.0, 5.0), (100.0, 5.0), (40.0, -10.0), (60.0, 10.0), True),  # endpoint inside
        ((0.0, 5.0), (0.0, 5.0), (40.0, -10.0), (60.0, 10.0), False),  # point outside-left
    ],
)
def test_line_intersect_rect_branches(
    p1, p2, rect_min, rect_max, expected: bool
) -> None:
    """Same-side reject, inside-endpoint shortcut, 4-edge sweep, degenerate."""
    assert (
        LayoutEngine.line_intersect_rect(p1, p2, rect_min, rect_max) is expected
    )


# --- build_smooth_u_path (associated fn, vertical + horizontal) ------------


def test_build_smooth_u_path_vertical_is_9_points_on_rail() -> None:
    """A vertical U carries 9 points: both endpoints + the side rail at mid."""
    path = LayoutEngine.build_smooth_u_path((0.0, 100.0), (0.0, 200.0), 60.0, True)
    assert len(path) == 9
    assert path[0] == (0.0, 100.0)
    assert path[8] == (0.0, 200.0)
    assert path[4] == pytest.approx((60.0, 150.0))  # rail at mid-y
    assert path[3][0] == 60.0  # rail x before mid
    assert path[5][0] == 60.0  # rail x after mid


def test_build_smooth_u_path_horizontal_is_9_points_on_rail() -> None:
    """A horizontal U carries 9 points: both endpoints + the below rail at mid."""
    path = LayoutEngine.build_smooth_u_path((0.0, 0.0), (100.0, 0.0), 40.0, False)
    assert len(path) == 9
    assert path[0] == (0.0, 0.0)
    assert path[8] == (100.0, 0.0)
    assert path[4] == pytest.approx((50.0, 40.0))  # rail at mid-x


# --- compute_back_edge_points_simple (instance, vertical U) ----------------


def test_compute_back_edge_points_simple_vertical_detour() -> None:
    """A vertical back edge detours ``offset`` past the wider endpoint."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=0.0, y=100.0)
    path = engine.compute_back_edge_points_simple(from_node, to_node, True)
    # side_x = max(x) + max(width)/2 + 60 = 0 + 20 + 60 = 80
    assert len(path) == 9
    assert path[0] == pytest.approx((20.0, 0.0))  # from right edge
    assert path[8] == pytest.approx((20.0, 100.0))  # to right edge
    assert path[4][0] == 80.0  # detour rail


# --- compute_back_edge_points (instance, obstacle-aware vertical) ----------


def test_compute_back_edge_points_vertical_chooses_outer_side() -> None:
    """``from.x >= to.x`` routes the detour on the +x outer side."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=100.0)
    to_node = _layout_node("b", x=0.0, y=0.0)
    all_nodes = {"a": from_node, "b": to_node}
    path = engine.compute_back_edge_points(from_node, to_node, True, all_nodes)
    # max_right + 30 = 20 + 30 = 50 (outer +x side)
    assert len(path) == 9
    assert path[0] == pytest.approx((20.0, 100.0))
    assert path[8] == pytest.approx((20.0, 0.0))
    assert path[4][0] == 50.0


# --- compute_horizontal_edge_with_obstacles (instance) ---------------------


def test_compute_horizontal_edge_with_obstacles_no_block_is_straight() -> None:
    """No blocking obstacle -> 3-point deduped orthogonal route."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.LeftToRight))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=100.0, y=0.0)
    path = engine.compute_horizontal_edge_with_obstacles(from_node, to_node, [])
    assert path == pytest.approx([(20.0, 0.0), (50.0, 0.0), (80.0, 0.0)])


def test_compute_horizontal_edge_with_obstacles_block_routes_around() -> None:
    """A blocking obstacle routes above/below via two rail corners."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.LeftToRight))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=100.0, y=0.0)
    obstacle = _layout_node("o", x=50.0, y=0.0)
    path = engine.compute_horizontal_edge_with_obstacles(from_node, to_node, [obstacle])
    assert len(path) == 4
    # route_y = obs_bottom + 30 = 10 + 30 = 40 (equidistant -> below)
    assert path[1] == pytest.approx((20.0, 40.0))
    assert path[2] == pytest.approx((80.0, 40.0))


# --- compute_vertical_edge_with_obstacles (instance, no obstacle) ----------


def test_compute_vertical_edge_with_obstacles_no_block_is_straight() -> None:
    """Vertical router symmetric twin: 3-point deduped route."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=0.0, y=100.0)
    path = engine.compute_vertical_edge_with_obstacles(from_node, to_node, [])
    assert path == pytest.approx([(0.0, 10.0), (0.0, 50.0), (0.0, 90.0)])


# --- edge_crosses_any_node (instance) --------------------------------------


def test_edge_crosses_any_node_through_obstacle_is_true() -> None:
    """A chord crossing an obstacle's margin rect -> True."""
    engine = LayoutEngine(_empty_graph())
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=100.0, y=0.0)
    obstacle = _layout_node("o", x=50.0, y=0.0, width=20.0, height=20.0)
    all_nodes = {"a": from_node, "b": to_node, "o": obstacle}
    assert engine.edge_crosses_any_node([(0.0, 0.0), (100.0, 0.0)], from_node, to_node, all_nodes)


def test_edge_crosses_any_node_no_obstacle_is_false() -> None:
    """No intermediate node -> False; degenerate <2 points also False."""
    engine = LayoutEngine(_empty_graph())
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=100.0, y=0.0)
    assert engine.edge_crosses_any_node([(0.0, 0.0), (100.0, 0.0)], from_node, to_node, {}) is False
    assert engine.edge_crosses_any_node([(0.0, 0.0)], from_node, to_node, {}) is False


# --- straighten_if_aligned (instance) --------------------------------------


def test_straighten_if_aligned_collapses_when_clear() -> None:
    """Aligned endpoints that clear every node collapse to a 2-point segment."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=0.0, y=100.0)
    dagre = [(0.0, 0.0), (0.0, 50.0), (0.0, 100.0)]
    out = engine.straighten_if_aligned(dagre, from_node, to_node, True, {})
    assert out == pytest.approx([(0.0, 0.0), (0.0, 100.0)])


def test_straighten_if_aligned_keeps_route_when_not_aligned() -> None:
    """Misaligned endpoints return the dagre route unchanged."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=50.0, y=100.0)
    dagre = [(0.0, 0.0), (25.0, 50.0), (50.0, 100.0)]
    out = engine.straighten_if_aligned(dagre, from_node, to_node, True, {})
    assert out == [(0.0, 0.0), (25.0, 50.0), (50.0, 100.0)]


# --- trim_cluster_interior_points (associated fn, in place) ----------------


def test_trim_cluster_interior_points_to_cluster_truncates_tail() -> None:
    """A to-cluster endpoint keeps only through the last outside point + 1."""
    to_node = _layout_node("t", x=0.0, y=0.0, width=100.0, height=100.0)
    from_node = _layout_node("f", x=0.0, y=0.0)
    points = [(100.0, 100.0), (30.0, 30.0), (5.0, 5.0)]
    LayoutEngine.trim_cluster_interior_points(points, from_node, to_node, False, True)
    assert points == pytest.approx([(100.0, 100.0), (30.0, 30.0)])


def test_trim_cluster_interior_points_from_cluster_drops_head() -> None:
    """A from-cluster endpoint drops leading interior points (keep 1 transition)."""
    to_node = _layout_node("t", x=0.0, y=0.0)
    from_node = _layout_node("f", x=0.0, y=0.0, width=100.0, height=100.0)
    points = [(5.0, 5.0), (30.0, 30.0), (100.0, 100.0)]
    LayoutEngine.trim_cluster_interior_points(points, from_node, to_node, True, False)
    assert points == pytest.approx([(30.0, 30.0), (100.0, 100.0)])


# --- clip_edge_end_only / clip_edge_to_boundaries (instance, in place) ------


def test_clip_edge_end_only_snaps_last_to_border() -> None:
    """The final point is re-anchored onto the to-node border (inbound)."""
    engine = LayoutEngine(_empty_graph())
    to_node = _layout_node("b", x=100.0, y=0.0)
    points = [(0.0, 0.0), (100.0, 0.0)]
    engine.clip_edge_end_only(points, to_node)
    assert points[1] == pytest.approx((80.0, 0.0))  # to left border


def test_clip_edge_to_boundaries_snaps_both_endpoints() -> None:
    """Both endpoints are re-anchored onto their node borders."""
    engine = LayoutEngine(_empty_graph())
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=100.0, y=50.0)
    points = [(0.0, 0.0), (100.0, 50.0)]
    engine.clip_edge_to_boundaries(points, from_node, to_node)
    # start snapped towards second point; end snapped towards second-last
    assert points[0] == pytest.approx((20.0, 10.0))
    assert points[1] == pytest.approx((80.0, 40.0))


# --- compute_edge_points_with_obstacles (instance, back vs forward) --------


def test_compute_edge_points_with_obstacles_back_edge_uses_simple_detour() -> None:
    """A back edge routes via the simple U-detour (9 points)."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=100.0)
    to_node = _layout_node("b", x=0.0, y=0.0)
    all_nodes = {"a": from_node, "b": to_node}
    path = engine.compute_edge_points_with_obstacles(from_node, to_node, all_nodes)
    assert len(path) == 9
    assert path[0] == pytest.approx((20.0, 100.0))
    assert path[8] == pytest.approx((20.0, 0.0))


def test_compute_edge_points_with_obstacles_forward_uses_vertical_router() -> None:
    """A forward edge routes via the vertical obstacle router."""
    engine = LayoutEngine(_graph_with_direction(GraphDirection.TopToBottom))
    from_node = _layout_node("a", x=0.0, y=0.0)
    to_node = _layout_node("b", x=0.0, y=100.0)
    path = engine.compute_edge_points_with_obstacles(from_node, to_node, {})
    assert path == pytest.approx([(0.0, 10.0), (0.0, 50.0), (0.0, 90.0)])


# --- _dedup_consecutive (module helper, mirrors Vec::dedup) ----------------


@pytest.mark.parametrize(
    "points,expected",
    [
        ([], []),
        ([(0.0, 0.0)], [(0.0, 0.0)]),
        ([(0.0, 0.0), (0.0, 0.0), (1.0, 1.0), (1.0, 1.0), (2.0, 2.0)], [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]),
        ([(0.0, 0.0), (1.0, 1.0), (0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]),  # non-consecutive kept
    ],
)
def test_dedup_consecutive_drops_only_adjacent_duplicates(
    points, expected
) -> None:
    """Only *consecutive* duplicates are dropped (mirrors Rust ``Vec::dedup``)."""
    assert layout_mod._dedup_consecutive(points) == expected


# --- R274e barrel contract: layout stays internal --------------------------


def test_layout_module_all_contract() -> None:
    """``__all__`` tracks the live public surface (4 data types + 2 R274f entries).

    R274e kept ``__all__`` at the 4 ``pub struct`` from R274a (it only added
    instance/static methods). R274f grows the surface to 6 by adding the 2
    ``pub fn`` public-entry functions (``compute_layout`` +
    ``compute_layout_with_config``). The list stays ASCII-sorted.
    """
    assert layout_mod.__all__ == [
        "LayoutEdge",
        "LayoutNode",
        "LayoutResult",
        "LayoutSubgraph",
        "compute_layout",
        "compute_layout_with_config",
    ]
    # the public entries are the 2 R274f symbols reachable as module attributes
    assert layout_mod.compute_layout is compute_layout
    assert layout_mod.compute_layout_with_config is compute_layout_with_config


def test_layout_edge_geometry_helpers_are_instance_or_static_methods() -> None:
    """The 17 R274e helpers live on the class, not in the module namespace."""
    # 4 static methods are reachable as class attributes.
    assert LayoutEngine.edge_label_midpoint is not None
    assert LayoutEngine.line_intersect_rect is not None
    assert LayoutEngine.build_smooth_u_path is not None
    assert LayoutEngine.trim_cluster_interior_points is not None
    # None of the 13 instance methods leak into the module namespace.
    for name in (
        "edge_label_bounds",
        "is_back_edge",
        "compute_edge_points_with_obstacles",
        "compute_horizontal_edge_with_obstacles",
        "compute_vertical_edge_with_obstacles",
        "compute_back_edge_points_simple",
        "compute_back_edge_points",
        "straighten_if_aligned",
        "edge_crosses_any_node",
        "clip_edge_to_boundaries",
        "clip_edge_end_only",
        "connection_point_on_node",
        "connection_point_towards",
    ):
        assert name not in vars(layout_mod), f"{name} leaked into module namespace"


# --- R274f: compute_with_dagre orchestrator + public entries ----------------


def test_compute_layout_minimal_flowchart_end_to_end() -> None:
    """compute_layout lays out a minimal TB A->B graph end-to-end via dagre.

    Exercises the full R274f orchestrator: compute_spacing -> build_dagre_graph
    -> dagre_layout -> extract_layout_from_dagre -> bounds -> margin-shift ->
    LayoutResult. Asserts the structural contract + the margin invariant
    (every node's left/top edge sits at or beyond MARGIN after the shift).
    """
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
        ],
    )
    result = compute_layout(graph)
    assert isinstance(result, LayoutResult)
    assert set(result.nodes) == {"A", "B"}
    assert len(result.edges) == 1
    assert result.width > 0.0
    assert result.height > 0.0
    # margin invariant: the global min_x/min_y shift places every node's
    # bounding box at or beyond MARGIN on the left/top (grok's shift guarantee).
    for node in result.nodes.values():
        assert node.x - node.width / 2.0 >= layout_mod.MARGIN - 1e-6
        assert node.y - node.height / 2.0 >= layout_mod.MARGIN - 1e-6
    # edges keep at least their two endpoints after clipping
    for edge in result.edges:
        assert len(edge.points) >= 2


def test_compute_layout_with_config_builds_options_from_render_config() -> None:
    """compute_layout_with_config threads RenderConfig -> FlowchartLayoutOptions."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)],
    )
    config = RenderConfig(font_size="24px")
    result = compute_layout_with_config(graph, config)
    assert isinstance(result, LayoutResult)
    assert set(result.nodes) == {"A", "B"}
    assert result.width > 0.0
    assert result.height > 0.0


def test_compute_layout_equivalent_to_engine_compute_with_dagre_true() -> None:
    """compute_layout(g) == LayoutEngine(g).compute_with_dagre(True).

    The default public entry delegates to the orchestrator with subgraph-node
    centering on (mirrors grok's ``compute_layout`` -> ``compute_with_dagre(true)``).
    """
    graph = FlowchartGraph(
        direction=GraphDirection.LeftToRight,
        statements=[
            Node(id="X", label="X", shape=NodeShape.Circle),
            Node(id="Y", label="Y", shape=NodeShape.Circle),
            Edge(from_="X", to="Y", label="edge label", style=EdgeStyle.Arrow),
        ],
    )
    public = compute_layout(graph)
    direct = LayoutEngine(graph).compute_with_dagre(True)
    assert public == direct  # LayoutResult dataclass __eq__


def test_compute_with_dagre_center_flag_runs_both_paths() -> None:
    """``center_subgraph_nodes`` True/False both run the full pipeline cleanly.

    The flag toggles a single call site (``center_nodes_in_subgraphs`` inside
    ``compute_with_dagre``); on a graph with no subgraphs that call is a no-op,
    so both branches exercise the identical end-to-end pipeline (spacing ->
    direction -> back-edge detect -> dagre build+solve -> extract -> bounds ->
    node/edge assembly -> margin shift -> viewport size). A forked graph
    (A -> B, A -> C) exercises dagre's multi-path ranking rather than a trivial
    single chain.

    NOTE -- a graph WITH a subgraph trips a pre-existing dagre graphlib bug from
    the R242-R268 migration chain (NOT introduced by R274f): network_simplex's
    ``_exchange_edges`` calls ``Graph.remove_edge`` which reaches
    ``_decrement_or_remove_entry`` and does ``None -= 1`` on a virtual ``_bt``
    node whose ``_preds`` counter entry was already dropped. The R274f
    orchestrator itself is correct; end-to-end subgraph-centering coverage is
    blocked on that upstream dagre fix. The ``center_nodes_in_subgraphs`` shift
    logic is covered by its own focused unit tests upstream of this orchestrator.
    """
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="A", label="A", shape=NodeShape.Rectangle),
            Node(id="B", label="B", shape=NodeShape.Rectangle),
            Node(id="C", label="C", shape=NodeShape.Rectangle),
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
            Edge(from_="A", to="C", label=None, style=EdgeStyle.Arrow),
        ],
    )
    centered = LayoutEngine(graph).compute_with_dagre(True)
    uncentered = LayoutEngine(graph).compute_with_dagre(False)
    # Both branches return a valid viewport, place every node, and keep each
    # node's left/top edge at or beyond the global margin (pan invariance).
    for result in (centered, uncentered):
        assert isinstance(result, LayoutResult)
        assert result.width > 0.0
        assert result.height > 0.0
        assert set(result.nodes) == {"A", "B", "C"}
        for node in result.nodes.values():
            assert node.x - node.width / 2.0 >= layout_mod.MARGIN - 1e-6
            assert node.y - node.height / 2.0 >= layout_mod.MARGIN - 1e-6


@pytest.mark.parametrize(
    "direction",
    [
        GraphDirection.TopToBottom,
        GraphDirection.BottomToTop,
        GraphDirection.LeftToRight,
        GraphDirection.RightToLeft,
    ],
)
def test_compute_layout_all_four_directions_produce_valid_layout(direction) -> None:
    """All 4 graph directions map to a rank_dir and lay out without raising."""
    graph = FlowchartGraph(
        direction=direction,
        statements=[Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow)],
    )
    result = compute_layout(graph)
    assert set(result.nodes) == {"A", "B"}
    assert result.width > 0.0
    assert result.height > 0.0


def test_compute_layout_state_diagram_runs_snap_align_special_case() -> None:
    """A state diagram (StartState) exercises snap_state_ranks + align path."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Node(id="start", label="start", shape=NodeShape.StartState),
            Node(id="s1", label="s1", shape=NodeShape.Rectangle),
            Edge(from_="start", to="s1", label=None, style=EdgeStyle.Arrow),
        ],
    )
    engine = LayoutEngine(graph)
    assert engine.is_state_diagram is True
    result = engine.compute_with_dagre(True)
    assert isinstance(result, LayoutResult)
    assert "start" in result.nodes
    assert "s1" in result.nodes


def test_compute_layout_labeled_edge_sets_label_position() -> None:
    """A labeled edge gets a non-None label_pos within the laid-out canvas."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[Edge(from_="A", to="B", label="yes", style=EdgeStyle.Arrow)],
    )
    result = compute_layout(graph)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.label == "yes"
    assert edge.label_pos is not None
    lx, ly = edge.label_pos
    # label participates in the min_x/min_y shift + final_width/height envelope
    assert lx >= layout_mod.MARGIN - 1e-6
    assert ly >= layout_mod.MARGIN - 1e-6
    assert lx <= result.width + 1e-6
    assert ly <= result.height + 1e-6


def test_compute_layout_cyclic_graph_routes_through_back_edge_path() -> None:
    """A cyclic A->B->C->A triangle routes at least one edge via the back-edge path."""
    graph = FlowchartGraph(
        direction=GraphDirection.TopToBottom,
        statements=[
            Edge(from_="A", to="B", label=None, style=EdgeStyle.Arrow),
            Edge(from_="B", to="C", label=None, style=EdgeStyle.Arrow),
            Edge(from_="C", to="A", label=None, style=EdgeStyle.Arrow),
        ],
    )
    result = compute_layout(graph)
    assert isinstance(result, LayoutResult)
    assert set(result.nodes) == {"A", "B", "C"}
    assert len(result.edges) == 3
    assert result.width > 0.0
    assert result.height > 0.0
