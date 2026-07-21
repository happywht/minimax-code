"""Black-box tests for the migrated dagre type-foundation layer (R246).

Exercises :mod:`minimax_code.dagre.lib` purely through the public ``dagre``
barrel + the four crate-root structs (``GraphNode`` / ``GraphEdgePoint`` /
``GraphEdge`` / ``GraphConfig``) and the early-migrated ``BorderTypeName``
enum. Covers:

* the ``#[derive(Default)]`` contract on ``GraphNode`` / ``GraphEdgePoint``
  (all fields zeroed/``None``/empty),
* the **manual** ``Default`` contracts on ``GraphEdge`` (``minlen=1.0`` /
  ``weight=1.0`` / ``labelpos="r"`` ...) and ``GraphConfig`` (``nodesep=50`` /
  ``edgesep=20`` / ``rankdir="tb"`` ...),
* mutability (the layout pipeline mutates nodes/edges in place),
* ``slots=True`` (no ``__dict__``, typo-proofing),
* per-instance ``self_edges`` isolation (no shared default list),
* cross-crate type fields consuming the R241 ``OrderedHashMap`` + R242 ``Edge``
  primitives, and the ``GraphEdge`` / ``GraphEdgePoint`` self-references,
* the ``class_`` Python-keyword rename of grok's ``class`` field,
* the ``BorderTypeName`` enum identity contract + the early-migration
  cyclic-dependency break,
* the barrel surface contract (4 crate-root symbols exported, ``BorderTypeName``
  kept module-private).
"""

from __future__ import annotations

import pytest

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphEdgePoint, GraphNode
from minimax_code.dagre.lib import BorderTypeName
from minimax_code.data_structures.graphlib import Edge
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

# === derive(Default) contract: GraphNode ====================================


def test_graph_node_defaults_all_zero_or_none() -> None:
    """``GraphNode::default()`` zeroes geometry, ``None``s every Option,
    empties ``self_edges`` (mirrors grok ``#[derive(Default)]``)."""
    n = GraphNode()
    assert n.x == 0.0
    assert n.y == 0.0
    assert n.width == 0.0
    assert n.height == 0.0
    # Every Option<T> field starts None.
    assert n.class_ is None
    assert n.label is None
    assert n.padding is None
    assert n.padding_x is None
    assert n.padding_y is None
    assert n.rx is None
    assert n.ry is None
    assert n.shape is None
    assert n.dummy is None
    assert n.rank is None
    assert n.min_rank is None
    assert n.max_rank is None
    assert n.order is None
    assert n.border_top is None
    assert n.border_bottom is None
    assert n.border_left is None
    assert n.border_right is None
    assert n.border_left_ is None
    assert n.border_right_ is None
    assert n.low is None
    assert n.lim is None
    assert n.parent is None
    assert n.e is None
    assert n.edge_label is None
    assert n.edge_obj is None
    assert n.labelpos is None
    assert n.border_type is None
    # The Vec<(Edge, GraphEdge)> starts empty.
    assert n.self_edges == []


def test_graph_node_has_full_field_surface() -> None:
    """All 32 grok fields (31 typed + ``self_edges``) are present by name --
    guards against a dropped field silently shrinking the migrated surface."""
    n = GraphNode()
    expected = {
        "x", "y", "width", "height", "class_", "label", "padding", "padding_x",
        "padding_y", "rx", "ry", "shape", "dummy", "rank", "min_rank",
        "max_rank", "order", "border_top", "border_bottom", "border_left",
        "border_right", "border_left_", "border_right_", "low", "lim",
        "parent", "e", "edge_label", "edge_obj", "labelpos", "border_type",
        "self_edges",
    }
    assert set(n.__slots__) == expected  # type: ignore[attr-defined]


# === derive(Default) contract: GraphEdgePoint ===============================


def test_graph_edge_point_defaults_zero() -> None:
    """``GraphEdgePoint::default()`` -> ``(0.0, 0.0)`` (grok derive Default)."""
    p = GraphEdgePoint()
    assert p.x == 0.0
    assert p.y == 0.0


# === manual Default contract: GraphEdge =====================================


def test_graph_edge_manual_defaults_match_grok_impl() -> None:
    """``GraphEdge::default()`` uses grok's *manual* impl (not derive Default):
    ``minlen=1.0`` / ``weight=1.0`` / ``width=0.0`` / ``height=0.0`` /
    ``labeloffset=0.0`` / ``labelpos="r"`` / ``x=0.0`` / ``y=0.0`` and every
    other field ``None``. The ranker and positioner rely on these exact
    starting values."""
    e = GraphEdge()
    assert e.forward_name is None
    assert e.reversed is None
    assert e.minlen == 1.0
    assert e.weight == 1.0
    assert e.width == 0.0
    assert e.height == 0.0
    assert e.label_rank is None
    assert e.labeloffset == 0.0
    assert e.labelpos == "r"
    assert e.nesting_edge is None
    assert e.cutvalue is None
    assert e.points is None
    assert e.x == 0.0
    assert e.y == 0.0


def test_graph_edge_minlen_weight_not_none() -> None:
    """``minlen`` / ``weight`` are the layout-critical ``Some(...)`` defaults,
    NOT ``None`` -- distinguishes the manual impl from a derive."""
    e = GraphEdge()
    assert e.minlen is not None
    assert e.weight is not None
    assert e.labelpos is not None


# === manual Default contract: GraphConfig ===================================


def test_graph_config_manual_defaults_match_grok_impl() -> None:
    """``GraphConfig::default()`` uses grok's manual impl: ``nodesep=50`` /
    ``edgesep=20`` / ``ranksep=50`` / ``rankdir="tb"`` / zeroed bounds, with
    ``marginx`` / ``marginy`` / ``ranker`` / ``align`` / ``node_rank_factor``
    / ``dummy_chains`` starting ``None``."""
    c = GraphConfig()
    assert c.width == 0.0
    assert c.height == 0.0
    assert c.nodesep == 50.0
    assert c.edgesep == 20.0
    assert c.ranksep == 50.0
    assert c.marginx is None
    assert c.marginy is None
    assert c.rankdir == "tb"
    assert c.acyclicer is None
    assert c.ranker is None
    assert c.align is None
    assert c.nesting_root is None
    assert c.root is None
    assert c.node_rank_factor is None
    assert c.dummy_chains is None


def test_graph_config_well_known_seps() -> None:
    """The three dagre separators are the documented CSS-px defaults."""
    c = GraphConfig()
    assert c.nodesep == 50.0
    assert c.edgesep == 20.0
    assert c.ranksep == 50.0


# === mutability ==============================================================


def test_graph_node_is_mutable_in_place() -> None:
    """The layout pipeline mutates nodes in place (grok takes ``&mut``) --
    ``GraphNode`` is NOT frozen."""
    n = GraphNode()
    n.x = 12.5
    n.y = -3.0
    n.rank = 2
    n.order = 0
    assert n.x == 12.5
    assert n.y == -3.0
    assert n.rank == 2
    assert n.order == 0


def test_graph_edge_and_config_are_mutable_in_place() -> None:
    """``GraphEdge`` / ``GraphConfig`` are mutable too (positioner writes
    ``x`` / ``y`` / ``points``; config is overridden per-layout)."""
    e = GraphEdge()
    e.cutvalue = -7.0
    e.points = [GraphEdgePoint(0.0, 0.0), GraphEdgePoint(10.0, 10.0)]
    assert e.cutvalue == -7.0
    assert len(e.points) == 2

    c = GraphConfig()
    c.ranker = "network-simplex"
    c.rankdir = "lr"
    assert c.ranker == "network-simplex"
    assert c.rankdir == "lr"


# === slots=True ==============================================================


@pytest.mark.parametrize("obj", [GraphNode(), GraphEdgePoint(), GraphEdge(), GraphConfig()])
def test_structs_use_slots_no_dict(obj: object) -> None:
    """``slots=True`` -> no ``__dict__``; the instance carries only its slots."""
    assert not hasattr(obj, "__dict__")


def test_unknown_field_attr_error() -> None:
    """Slots prevent typo-assignments: an undeclared attribute raises."""
    n = GraphNode()
    with pytest.raises(AttributeError):
        n.bogus_field = 1  # type: ignore[attr-defined]


# === per-instance self_edges isolation =======================================


def test_self_edges_default_is_per_instance() -> None:
    """``self_edges`` uses ``field(default_factory=list)`` so two default
    nodes do not share one list (the classic mutable-default trap)."""
    a = GraphNode()
    b = GraphNode()
    a.self_edges.append((Edge(v="n", w="n"), GraphEdge()))
    assert a.self_edges != []
    assert b.self_edges == []
    assert a.self_edges is not b.self_edges


# === cross-crate type fields (consume R241 OrderedHashMap + R242 Edge) =======


def test_graph_node_e_field_accepts_edge() -> None:
    """``e`` / ``edge_obj`` carry an :class:`Edge` (R242 graphlib vocabulary)."""
    an_edge = Edge(v="a", w="b")
    n = GraphNode(e=an_edge, edge_obj=Edge(v="a", w="b", name="chain"))
    assert n.e is not None and n.e.v == "a" and n.e.w == "b"
    assert n.edge_obj is not None and n.edge_obj.name == "chain"


def test_graph_node_border_fields_accept_ordered_hashmap() -> None:
    """``border_left`` / ``border_right`` carry an ``OrderedHashMap<i32,String>``
    (R241) -- seeded by ``add_border_segments`` in a later leaf."""
    bl: OrderedHashMap[int, str] = OrderedHashMap()
    bl.insert(0, "_a_border")
    n = GraphNode(border_left=bl)
    assert n.border_left is not None
    assert n.border_left.get(0) == "_a_border"


def test_graph_node_label_and_edge_label_accept_graph_edge() -> None:
    """``label`` / ``edge_label`` are self-references to :class:`GraphEdge`."""
    lbl = GraphEdge(minlen=2.0, weight=3.0)
    n = GraphNode(label=lbl, edge_label=GraphEdge(labelpos="c"))
    assert n.label is not None and n.label.minlen == 2.0
    assert n.edge_label is not None and n.edge_label.labelpos == "c"


def test_graph_edge_points_field_accepts_point_list() -> None:
    """``points`` is the edge polyline as ``list[GraphEdgePoint]``."""
    e = GraphEdge(points=[GraphEdgePoint(1.0, 2.0), GraphEdgePoint(3.0, 4.0)])
    assert e.points is not None
    assert len(e.points) == 2
    assert e.points[1].x == 3.0


def test_graph_node_self_edges_field_accepts_edge_graph_edge_pairs() -> None:
    """``self_edges`` is ``Vec<(Edge, GraphEdge)>`` -- a self-loop records both
    the graphlib :class:`Edge` and its :class:`GraphEdge` label."""
    pairs = [(Edge(v="s", w="s"), GraphEdge(minlen=1.0))]
    n = GraphNode(self_edges=pairs)
    assert len(n.self_edges) == 1
    edge, label = n.self_edges[0]
    assert edge.v == "s"
    assert label.minlen == 1.0


def test_graph_node_border_type_field_accepts_border_type_name() -> None:
    """``border_type`` carries the early-migrated :class:`BorderTypeName`."""
    n = GraphNode(border_type=BorderTypeName.BorderLeft)
    assert n.border_type is BorderTypeName.BorderLeft


# === class_ Python-keyword rename ===========================================


def test_graph_node_class_underscore_rename() -> None:
    """grok's ``class`` field is renamed ``class_`` (Python keyword); the
    attribute is reachable under the trailing-underscore name only."""
    n = GraphNode(class_="node-cls")
    assert n.class_ == "node-cls"
    # No bare ``class`` attribute exists on the slotted dataclass.
    assert not hasattr(n, "class")


# === BorderTypeName enum ====================================================


def test_border_type_name_has_two_variants() -> None:
    """grok ``enum BorderTypeName { BorderLeft, BorderRight }`` -> two members."""
    members = {m.name for m in BorderTypeName}
    assert members == {"BorderLeft", "BorderRight"}


def test_border_type_name_identity_match() -> None:
    """``add_border_segments`` matches ``BorderTypeName::BorderRight`` by
    identity (``prop is BorderTypeName.BorderRight``); the enum preserves
    that exact identity comparison."""
    assert BorderTypeName.BorderLeft is not BorderTypeName.BorderRight
    assert BorderTypeName.BorderLeft is BorderTypeName.BorderLeft
    # Equality agrees with identity for enum members.
    assert BorderTypeName.BorderRight == BorderTypeName.BorderRight


# === barrel surface contract ================================================


def test_barrel_exports_four_crate_root_symbols() -> None:
    """grok re-exports the four structs at the crate root (``pub struct`` in
    ``lib.rs``); the barrel mirrors that -- exactly four ``__all__`` entries."""
    assert set(dagre.__all__) == {"GraphConfig", "GraphEdge", "GraphEdgePoint", "GraphNode"}
    assert len(dagre.__all__) == 4


def test_barrel_re_exports_are_identical_to_lib_definitions() -> None:
    """The barrel symbols are the exact objects defined in ``dagre.lib``."""
    import minimax_code.dagre.lib as lib

    assert dagre.GraphNode is lib.GraphNode
    assert dagre.GraphEdge is lib.GraphEdge
    assert dagre.GraphEdgePoint is lib.GraphEdgePoint
    assert dagre.GraphConfig is lib.GraphConfig


def test_border_type_name_not_in_barrel_all() -> None:
    """``BorderTypeName`` is module-private to ``dagre.lib`` (grok keeps it in
    ``layout::add_border_segments``, not the crate root) -- it stays out of
    the barrel ``__all__`` and off the package top level."""
    assert "BorderTypeName" not in dagre.__all__
    assert not hasattr(dagre, "BorderTypeName")


def test_border_type_name_reachable_via_lib_submodule() -> None:
    """``BorderTypeName`` is reachable as ``dagre.lib.BorderTypeName`` --
    the early-migration landing site that breaks the lib/layout cycle."""
    import minimax_code.dagre.lib as lib

    assert lib.BorderTypeName is BorderTypeName
    assert "BorderTypeName" in lib.__all__


# === integration: structs parameterise Graph<GL,N,E> (type-level only) ======


def test_types_can_parameterise_graphlib_graph_at_type_level() -> None:
    """The whole point of the type layer: ``Graph<GraphConfig, GraphNode,
    GraphEdge>`` is the concrete specialisation the dagre layout algorithms
    operate on. This is a type-level smoke check (no layout call yet -- that
    is later leaves): a ``Graph`` whose node factory is ``GraphNode`` and
    whose edge factory is ``GraphEdge`` round-trips a ``GraphNode`` label
    through ``set_node`` / ``node`` -- i.e. the dagre types are accepted as
    the graphlib ``Graph<GL, N, E>`` label parameters without typing error."""
    from minimax_code.data_structures import Graph, GraphOption

    g: Graph[GraphConfig, GraphNode, GraphEdge] = Graph(
        GraphOption(),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    # A node label is a GraphNode; round-trip it through set_node / node.
    g.set_node("n1", GraphNode(x=5.0, y=6.0, width=10.0, height=20.0))
    label = g.node("n1")
    assert label is not None
    assert isinstance(label, GraphNode)
    assert label.x == 5.0
    assert label.width == 10.0
    # The node factory itself yields a default GraphNode when no label is given.
    g.set_node("n2", None)
    factory_label = g.node("n2")
    assert isinstance(factory_label, GraphNode)
    assert factory_label.x == 0.0
