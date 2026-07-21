"""Tests for data_structures.graphlib (R242+R243+R244, vendored ``third_party/graphlib_rust``).

R242 covers the edge-encoding vocabulary layer: the 3 sentinel constants
(:data:`DEFAULT_EDGE_NAME`, :data:`GRAPH_NODE`, :data:`EDGE_KEY_DELIM`), the
:class:`Edge` and :class:`GraphOption` frozen value objects, and the pure
edge-id encoding helpers (:func:`edge_args_to_id`, :func:`edge_args_to_obj`,
:func:`edge_obj_to_id`).

R243 covers the ``Graph`` core struct + node primitives: construction (default
+ option-driven, multigraph bug-fix verification, compound seeding), graph-label
accessors, the default-node-label tagged union (:class:`NodeLabelValue` /
:class:`NodeLabelFactory`), node CRUD, compound parent/child queries, and the
adjacency queries (predecessors / successors / neighbors / is_leaf).

R244 closes the complete Graph CRUD surface: the edge-method subset
(``set_edge`` / ``edge`` / ``edge_mut`` / ``has_edge`` / ``remove_edge`` /
``in_edges`` / ``out_edges`` / ``node_edges`` / ``edge_count`` / ``edges`` /
``set_path``), the default-edge-label tagged union (:class:`EdgeLabelValue` /
:class:`EdgeLabelFactory``), and the edge-dependent node methods
(``remove_node`` cascade + ``filter_nodes`` predicate filter with compound
parent re-threading). With ``set_edge`` available, the predecessor / successor
/ neighbor / is_leaf tests now exercise a populated adjacency table via the
public API (R243's white-box ``_in`` / ``_out`` / ``_preds`` / ``_sucs``
backfill is retired).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.data_structures import (
    Edge,
    Graph,
    GraphOption,
    OrderedHashMap,
)
from minimax_code.data_structures.graphlib import (
    DEFAULT_EDGE_NAME,
    EDGE_KEY_DELIM,
    GRAPH_NODE,
    EdgeLabelFactory,
    EdgeLabelValue,
    NodeLabelFactory,
    NodeLabelValue,
    _decrement_or_remove_entry,
    _increment_or_init_entry,
    edge_args_to_id,
    edge_args_to_obj,
    edge_obj_to_id,
)

# ---------------------------------------------------------------------------
# sentinel + delimiter constants
# ---------------------------------------------------------------------------


def test_constants_values() -> None:
    """Sentinel / delimiter values match grok (NUL bytes below the delim)."""
    assert DEFAULT_EDGE_NAME == "\x00"
    assert GRAPH_NODE == "\x00"
    assert EDGE_KEY_DELIM == "\x01"


def test_default_edge_name_below_delim() -> None:
    """Sentinel ordering: DEFAULT_EDGE_NAME < EDGE_KEY_DELIM (no collision).

    The whole point of substituting a NUL sentinel for an absent name is that
    a real (non-empty) name can never collide with the anonymous slot -- the
    sentinel sorts strictly below the delimiter so composition is unambiguous.
    """
    assert DEFAULT_EDGE_NAME < EDGE_KEY_DELIM


# ---------------------------------------------------------------------------
# Edge (frozen value object, mirrors grok #[derive(Debug, Clone)])
# ---------------------------------------------------------------------------


def test_edge_basic() -> None:
    e = Edge(v="a", w="b")
    assert e.v == "a"
    assert e.w == "b"
    assert e.name is None  # default


def test_edge_with_name() -> None:
    e = Edge(v="a", w="b", name="edge1")
    assert e.name == "edge1"


def test_edge_positional_args() -> None:
    """Positional construction mirrors grok ``Edge { v, w, name }``."""
    e = Edge("a", "b", "n")
    assert e == Edge(v="a", w="b", name="n")


def test_edge_frozen() -> None:
    """frozen=True: edge identity is immutable once constructed."""
    e = Edge(v="a", w="b")
    with pytest.raises(FrozenInstanceError):
        e.v = "c"  # type: ignore[misc]


def test_edge_equality() -> None:
    assert Edge("a", "b") == Edge("a", "b")
    assert Edge("a", "b", "n") == Edge("a", "b", "n")
    assert Edge("a", "b") != Edge("a", "b", "n")  # name None vs "n"
    assert Edge("a", "b") != Edge("b", "a")  # v/w swapped


def test_edge_hashable() -> None:
    """Frozen dataclass is hashable (usable as dict key / set member)."""
    s = {Edge("a", "b"), Edge("a", "b"), Edge("b", "c")}
    assert len(s) == 2


def test_edge_repr() -> None:
    assert repr(Edge("a", "b")) == "Edge(v='a', w='b', name=None)"


def test_edge_repr_with_name() -> None:
    assert repr(Edge("a", "b", "n")) == "Edge(v='a', w='b', name='n')"


# ---------------------------------------------------------------------------
# GraphOption (frozen value object, mirrors grok #[derive(Default)])
# ---------------------------------------------------------------------------


def test_graph_option_defaults_all_none() -> None:
    """Default GraphOption has all flags None (mirrors #[derive(Default)])."""
    opts = GraphOption()
    assert opts.directed is None
    assert opts.multigraph is None
    assert opts.compound is None


def test_graph_option_fields() -> None:
    opts = GraphOption(directed=True, multigraph=False, compound=True)
    assert opts.directed is True
    assert opts.multigraph is False
    assert opts.compound is True


def test_graph_option_partial() -> None:
    """Only some flags set; the rest stay None."""
    opts = GraphOption(directed=False)
    assert opts.directed is False
    assert opts.multigraph is None
    assert opts.compound is None


def test_graph_option_frozen() -> None:
    opts = GraphOption()
    with pytest.raises(FrozenInstanceError):
        opts.directed = True  # type: ignore[misc]


def test_graph_option_equality() -> None:
    assert GraphOption(directed=True) == GraphOption(directed=True)
    assert GraphOption(directed=True) != GraphOption(directed=False)
    assert GraphOption() == GraphOption()


# ---------------------------------------------------------------------------
# edge_args_to_id -- directed (order-preserving)
# ---------------------------------------------------------------------------


def test_edge_args_to_id_directed_preserves_order() -> None:
    """Directed: (v, w) keeps order; (w, v) is a DIFFERENT edge."""
    expected = f"a{EDGE_KEY_DELIM}b{EDGE_KEY_DELIM}{DEFAULT_EDGE_NAME}"
    assert edge_args_to_id(True, "a", "b", None) == expected
    other = f"b{EDGE_KEY_DELIM}a{EDGE_KEY_DELIM}{DEFAULT_EDGE_NAME}"
    assert edge_args_to_id(True, "b", "a", None) == other
    assert edge_args_to_id(True, "a", "b", None) != edge_args_to_id(True, "b", "a", None)


def test_edge_args_to_id_directed_with_name() -> None:
    assert edge_args_to_id(True, "a", "b", "n") == f"a{EDGE_KEY_DELIM}b{EDGE_KEY_DELIM}n"


def test_edge_args_to_id_anonymous_uses_default_name() -> None:
    """Absent name -> DEFAULT_EDGE_NAME sentinel (never collides with a named edge)."""
    anon = edge_args_to_id(True, "a", "b", None)
    explicit_sentinel = edge_args_to_id(True, "a", "b", "\x00")
    assert anon == explicit_sentinel  # explicit \x00 == implicit sentinel
    # but a real name "n" produces a different id
    assert anon != edge_args_to_id(True, "a", "b", "n")


# ---------------------------------------------------------------------------
# edge_args_to_id -- undirected (order normalisation)
# ---------------------------------------------------------------------------


def test_edge_args_to_id_undirected_normalises_order() -> None:
    """Undirected: (v, w) and (w, v) produce the SAME id when v > w swap applies."""
    assert edge_args_to_id(False, "a", "b", None) == edge_args_to_id(False, "b", "a", None)


def test_edge_args_to_id_undirected_no_swap_when_v_le_w() -> None:
    """Undirected with v <= w: no swap, id == directed-style order."""
    expected = f"a{EDGE_KEY_DELIM}b{EDGE_KEY_DELIM}{DEFAULT_EDGE_NAME}"
    assert edge_args_to_id(False, "a", "b", None) == expected


def test_edge_args_to_id_undirected_swap_when_v_gt_w() -> None:
    """Undirected with v > w: swap endpoints so canonical v <= w."""
    expected = f"a{EDGE_KEY_DELIM}b{EDGE_KEY_DELIM}{DEFAULT_EDGE_NAME}"
    assert edge_args_to_id(False, "b", "a", None) == expected


def test_edge_args_to_id_undirected_with_name() -> None:
    assert edge_args_to_id(False, "b", "a", "n") == f"a{EDGE_KEY_DELIM}b{EDGE_KEY_DELIM}n"


# ---------------------------------------------------------------------------
# edge_args_to_obj
# ---------------------------------------------------------------------------


def test_edge_args_to_obj_directed() -> None:
    e = edge_args_to_obj(True, "a", "b", "n")
    assert e == Edge("a", "b", "n")


def test_edge_args_to_obj_undirected_swap() -> None:
    """Undirected edge_args_to_obj applies the same endpoint swap."""
    e = edge_args_to_obj(False, "b", "a", None)
    assert e == Edge("a", "b", None)


def test_edge_args_to_obj_anonymous_name_none() -> None:
    """The Edge object keeps name=None; only the ID substitutes the sentinel."""
    e = edge_args_to_obj(True, "a", "b", None)
    assert e.name is None


def test_edge_args_to_obj_directed_no_swap() -> None:
    """Directed edge_args_to_obj never swaps, even when v > w."""
    e = edge_args_to_obj(True, "b", "a", "n")
    assert e == Edge("b", "a", "n")


# ---------------------------------------------------------------------------
# edge_obj_to_id
# ---------------------------------------------------------------------------


def test_edge_obj_to_id_directed() -> None:
    e = Edge("a", "b", "n")
    assert edge_obj_to_id(True, e) == edge_args_to_id(True, "a", "b", "n")


def test_edge_obj_to_id_undirected_swap() -> None:
    """edge_obj_to_id applies the same undirected swap as edge_args_to_id."""
    e = Edge("b", "a", None)  # v > w
    assert edge_obj_to_id(False, e) == edge_args_to_id(False, "a", "b", None)


def test_edge_obj_to_id_anonymous_matches_args() -> None:
    e = Edge("a", "b", None)
    assert edge_obj_to_id(True, e) == edge_args_to_id(True, "a", "b", None)


def test_edge_obj_to_id_consistency_parametrised() -> None:
    """For ANY (is_directed, v, w, name): obj_to_id(edge_args_to_obj(...)) == args_to_id(...).

    This is the round-trip consistency invariant that ``Graph`` (R243) will lean
    on: storing an edge via edge_args_to_obj and later recovering its id via
    edge_obj_to_id must agree with the id computed directly from the args.
    """
    cases = [
        (True, "a", "b", None),
        (True, "b", "a", "n"),
        (True, "x", "x", "\x00"),
        (False, "a", "b", None),
        (False, "b", "a", "n"),
        (False, "x", "x", "name"),
    ]
    for is_directed, v, w, name in cases:
        obj = edge_args_to_obj(is_directed, v, w, name)
        assert edge_obj_to_id(is_directed, obj) == edge_args_to_id(is_directed, v, w, name)


# ===========================================================================
# R243: DefaultNodeLabel tagged union (NodeLabelValue / NodeLabelFactory)
# ===========================================================================


def test_node_label_value_default_none() -> None:
    """Default NodeLabelValue has value=None (the Val(None) variant)."""
    nlv = NodeLabelValue()
    assert nlv.value is None


def test_node_label_value_with_value() -> None:
    nlv = NodeLabelValue(value=42)
    assert nlv.value == 42


def test_node_label_value_frozen() -> None:
    """frozen=True: the default-label value object is immutable."""
    nlv = NodeLabelValue(value=1)
    with pytest.raises(FrozenInstanceError):
        nlv.value = 2  # type: ignore[misc]


def test_node_label_value_equality() -> None:
    assert NodeLabelValue() == NodeLabelValue()
    assert NodeLabelValue(value=1) == NodeLabelValue(value=1)
    assert NodeLabelValue(value=1) != NodeLabelValue(value=2)
    assert NodeLabelValue() != NodeLabelValue(value=1)


def test_node_label_factory_basic() -> None:
    """NodeLabelFactory wraps a per-node-id callable (the Func variant)."""
    nlf = NodeLabelFactory(factory=lambda v: f"label-{v}")
    assert nlf.factory("a") == "label-a"


def test_node_label_factory_not_frozen() -> None:
    """NodeLabelFactory is mutable (it wraps a behaviour, not a value)."""
    nlf = NodeLabelFactory(factory=lambda v: 1)
    nlf.factory = lambda v: 2  # type: ignore[method-assign]
    assert nlf.factory("a") == 2


# ===========================================================================
# R243: Graph construction
# ===========================================================================


def test_graph_default_construction() -> None:
    """Graph() with no options is directed, non-multigraph, non-compound."""
    g = Graph()
    assert g.is_directed() is True
    assert g.is_multigraph() is False
    assert g.is_compound() is False
    assert g.node_count() == 0
    assert g.nodes() == []
    assert g.graph() is None


def test_graph_empty_option_equivalent_to_default() -> None:
    """GraphOption() with all-None flags == default construction."""
    g = Graph(GraphOption())
    assert g.is_directed() is True
    assert g.is_multigraph() is False
    assert g.is_compound() is False


def test_graph_option_directed_false() -> None:
    g = Graph(GraphOption(directed=False))
    assert g.is_directed() is False
    assert g.is_multigraph() is False
    assert g.is_compound() is False


def test_graph_option_multigraph_true() -> None:
    """multigraph=True is honoured (grok bug-fix: checked once, not twice)."""
    g = Graph(GraphOption(multigraph=True))
    assert g.is_directed() is True
    assert g.is_multigraph() is True
    assert g.is_compound() is False


def test_graph_option_compound_true() -> None:
    """compound=True is honoured (grok bug-fix: compound was the dead-code branch)."""
    g = Graph(GraphOption(compound=True))
    assert g.is_directed() is True
    assert g.is_multigraph() is False
    assert g.is_compound() is True


def test_graph_option_compound_seeds_graph_node_children() -> None:
    """compound=True seeds GRAPH_NODE's empty child list (grok graph.rs:151-159)."""
    g = Graph(GraphOption(compound=True))
    graph_node_children = g._children.get(GRAPH_NODE)
    assert graph_node_children is not None
    assert len(graph_node_children) == 0


def test_graph_non_compound_does_not_seed_graph_node_children() -> None:
    """Non-compound: GRAPH_NODE's child list is absent."""
    g = Graph()
    assert g._children.get(GRAPH_NODE) is None


def test_graph_node_default_factory_param() -> None:
    """node_default_factory overrides the lambda: None fallback."""
    sentinel = {"n": 0}

    def factory() -> int:
        sentinel["n"] += 1
        return sentinel["n"]

    g = Graph(node_default_factory=factory)
    g.set_node("a", None)  # NodeLabelValue(value=None) -> factory()
    g.set_node("b", None)
    assert g.node("a") == 1
    assert g.node("b") == 2


# ===========================================================================
# R243: graph-label accessors (set_graph / graph / graph_mut)
# ===========================================================================


def test_set_graph_returns_self_for_chaining() -> None:
    g = Graph()
    assert g.set_graph("my-graph") is g


def test_graph_label_roundtrip() -> None:
    g = Graph()
    assert g.graph() is None
    g.set_graph(42)
    assert g.graph() == 42


def test_graph_mut_returns_live_label() -> None:
    """graph_mut returns the live label object (mutable N mutates in place)."""
    g = Graph()
    g.set_graph([1, 2, 3])
    label = g.graph_mut()
    assert label is not None
    label.append(4)
    assert g.graph() == [1, 2, 3, 4]


# ===========================================================================
# R243: default-node-label resolution
# ===========================================================================


def test_default_node_label_value_variant() -> None:
    """NodeLabelValue with a value -> that value for any node id."""
    g = Graph()
    g.set_default_node_label(NodeLabelValue(value=99))
    assert g.default_node_label("any") == 99


def test_default_node_label_factory_variant() -> None:
    """NodeLabelFactory -> invoke factory(node_id)."""
    g = Graph()
    g.set_default_node_label(NodeLabelFactory(factory=lambda v: f"L-{v}"))
    assert g.default_node_label("a") == "L-a"
    assert g.default_node_label("b") == "L-b"


def test_default_node_label_value_none_falls_back_to_factory() -> None:
    """NodeLabelValue(value=None) -> node_default_factory() (Python stand-in for N::default())."""
    g = Graph(node_default_factory=lambda: "default")
    g.set_default_node_label(NodeLabelValue(value=None))
    assert g.default_node_label("a") == "default"


def test_default_node_label_factory_none_falls_back() -> None:
    """NodeLabelFactory returning None -> fall back to node_default_factory() (robustness vs grok unwrap panic)."""
    g = Graph(node_default_factory=lambda: "fallback")
    g.set_default_node_label(NodeLabelFactory(factory=lambda v: None))
    assert g.default_node_label("a") == "fallback"


def test_set_default_node_label_returns_self_for_chaining() -> None:
    g = Graph()
    assert g.set_default_node_label(NodeLabelValue(value=1)) is g


# ===========================================================================
# R243: node CRUD (set_node / set_nodes / nodes / node / node_mut / has_node)
# ===========================================================================


def test_set_node_creates_with_label() -> None:
    g = Graph()
    g.set_node("a", 1)
    assert g.node_count() == 1
    assert g.node("a") == 1
    assert g.has_node("a") is True


def test_set_node_returns_self_for_chaining() -> None:
    g = Graph()
    assert g.set_node("a", 1) is g


def test_set_node_uses_default_when_value_none() -> None:
    """New node + value=None -> resolve via default_node_label."""
    g = Graph()
    g.set_default_node_label(NodeLabelValue(value=7))
    g.set_node("a", None)
    assert g.node("a") == 7


def test_set_node_update_existing_with_value() -> None:
    """Existing node + new value -> label updated, count unchanged."""
    g = Graph()
    g.set_node("a", 1)
    g.set_node("a", 2)
    assert g.node("a") == 2
    assert g.node_count() == 1


def test_set_node_update_existing_none_preserves_label() -> None:
    """Existing node + value=None -> label left untouched (no clobber to default)."""
    g = Graph()
    g.set_node("a", 1)
    g.set_node("a", None)
    assert g.node("a") == 1
    assert g.node_count() == 1


def test_set_nodes_batch() -> None:
    """set_nodes applies set_node for each id with a shared value."""
    g = Graph()
    g.set_nodes(["a", "b", "c"], 1)
    assert g.node_count() == 3
    assert g.node("a") == 1
    assert g.node("b") == 1
    assert g.node("c") == 1


def test_set_nodes_returns_self_for_chaining() -> None:
    g = Graph()
    assert g.set_nodes(["a"], 1) is g


def test_nodes_preserves_insertion_order() -> None:
    """nodes() returns ids in insertion order (OrderedHashMap contract)."""
    g = Graph()
    g.set_node("c", None)
    g.set_node("a", None)
    g.set_node("b", None)
    assert g.nodes() == ["c", "a", "b"]


def test_node_absent_returns_none() -> None:
    g = Graph()
    assert g.node("missing") is None


def test_node_mut_returns_live_label() -> None:
    """node_mut returns the live label object (mutable N mutates in place)."""
    g = Graph()
    g.set_node("a", [1, 2])
    label = g.node_mut("a")
    assert label is not None
    label.append(3)
    assert g.node("a") == [1, 2, 3]


def test_node_mut_absent_returns_none() -> None:
    g = Graph()
    assert g.node_mut("missing") is None


def test_has_node_absent() -> None:
    g = Graph()
    assert g.has_node("missing") is False


def test_set_node_seeds_empty_adjacency_tables() -> None:
    """set_node initialises empty _in/_out/_preds/_sucs tables (grok graph.rs:307-318)."""
    g = Graph()
    g.set_node("a", None)
    assert g._in.get("a") is not None
    assert len(g._in.get("a")) == 0
    assert g._out.get("a") is not None
    assert len(g._out.get("a")) == 0
    assert g._preds.get("a") is not None
    assert len(g._preds.get("a")) == 0
    assert g._sucs.get("a") is not None
    assert len(g._sucs.get("a")) == 0


# ===========================================================================
# R243: node_count / sources / sinks
# ===========================================================================


def test_node_count_tracks_creates_and_updates() -> None:
    """node_count increments only on create, not on update."""
    g = Graph()
    assert g.node_count() == 0
    g.set_node("a", 1)
    assert g.node_count() == 1
    g.set_node("a", 2)  # update -- no increment
    assert g.node_count() == 1
    g.set_node("b", 1)
    assert g.node_count() == 2


def test_sources_all_nodes_when_no_edges() -> None:
    """Without edges every node is a source (empty _in table)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    assert g.sources() == ["a", "b"]


def test_sinks_all_nodes_when_no_edges() -> None:
    """Without edges every node is a sink (empty _out table)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    assert g.sinks() == ["a", "b"]


def test_sources_excludes_node_with_in_edges() -> None:
    """A node with an in-edge is not a source (exercised via set_edge, R244)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_edge("b", "a", None, None)  # a gains an in-edge -> not a source
    assert g.sources() == ["b"]


def test_sinks_excludes_node_with_out_edges() -> None:
    """A node with an out-edge is not a sink (exercised via set_edge, R244)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_edge("a", "b", None, None)  # a gains an out-edge -> not a sink
    assert g.sinks() == ["b"]


# ===========================================================================
# R243: compound parent / child queries (set_parent / parent / children)
# ===========================================================================


def test_set_parent_non_compound_raises() -> None:
    """set_parent on a non-compound graph is a contract violation (grok panics)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    with pytest.raises(RuntimeError):
        g.set_parent("a", "b")


def test_set_parent_assigns_parent() -> None:
    """set_parent makes b the parent of a; parent(a) == b; a under b's children."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_parent("a", "b")
    assert g.parent("a") == "b"
    assert "a" in g.children("b")


def test_set_parent_none_detaches_to_graph_node() -> None:
    """set_parent(v, None) detaches v back to the synthetic GRAPH_NODE root."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_parent("a", "b")
    assert g.parent("a") == "b"
    g.set_parent("a", None)
    assert g.parent("a") is None  # back under GRAPH_NODE (hidden root)
    assert "a" not in g.children("b")
    assert "a" in g.children(GRAPH_NODE)


def test_set_parent_cycle_raises() -> None:
    """Setting a descendant as parent of an ancestor would close a cycle (grok panics)."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_node("c", None)
    g.set_parent("a", "b")  # b is parent of a
    g.set_parent("b", "c")  # c is parent of b
    # now make c a child of a -> cycle a -> b -> c -> a
    with pytest.raises(RuntimeError):
        g.set_parent("c", "a")


def test_set_parent_ensures_nodes_exist() -> None:
    """set_parent auto-creates a missing parent or child (grok graph.rs:436-437)."""
    g = Graph(GraphOption(compound=True))
    # neither "a" nor "b" created yet
    g.set_parent("a", "b")
    assert g.has_node("a") is True
    assert g.has_node("b") is True
    assert g.parent("a") == "b"


def test_set_parent_removes_from_old_parent_child_list() -> None:
    """Re-parenting removes v from the old parent's child list (grok _remove_from_parents_child_list)."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_node("c", None)
    g.set_parent("a", "b")
    assert "a" in g.children("b")
    g.set_parent("a", "c")  # re-parent a from b to c
    assert "a" not in g.children("b")
    assert "a" in g.children("c")
    assert g.parent("a") == "c"


def test_set_parent_returns_self_for_chaining() -> None:
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    assert g.set_parent("a", "b") is g


def test_parent_compound_node_under_graph_node_returns_none() -> None:
    """A node directly under GRAPH_NODE has parent() == None (hidden root)."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)  # auto under GRAPH_NODE
    assert g.parent("a") is None


def test_parent_non_compound_returns_none() -> None:
    """Non-compound graphs have no parent forest; parent() is always None."""
    g = Graph()
    g.set_node("a", None)
    assert g.parent("a") is None


def test_parent_absent_node_returns_none() -> None:
    g = Graph(GraphOption(compound=True))
    assert g.parent("missing") is None


def test_children_compound_returns_direct_children() -> None:
    """children(v) returns only v's direct children, insertion-ordered."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_node("c", None)
    g.set_node("d", None)
    g.set_parent("b", "a")
    g.set_parent("c", "a")
    g.set_parent("d", "b")  # d is under b, not directly under a
    assert g.children("a") == ["b", "c"]
    assert g.children("b") == ["d"]


def test_children_compound_no_children_returns_empty() -> None:
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    assert g.children("a") == []


def test_children_compound_absent_node_returns_empty() -> None:
    g = Graph(GraphOption(compound=True))
    assert g.children("missing") == []


def test_children_graph_node_returns_all_in_non_compound() -> None:
    """Non-compound: children(GRAPH_NODE) returns every node (implicit single-level forest)."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    assert g.children(GRAPH_NODE) == ["a", "b"]


def test_children_non_compound_regular_node_returns_empty() -> None:
    """Non-compound: a regular node id returns [] (only GRAPH_NODE has the implicit set)."""
    g = Graph()
    g.set_node("a", None)
    assert g.children("a") == []


# ===========================================================================
# R243: adjacency queries (predecessors / successors / neighbors / is_leaf)
# ===========================================================================


def test_predecessors_absent_node_returns_none() -> None:
    g = Graph()
    assert g.predecessors("missing") is None


def test_successors_absent_node_returns_none() -> None:
    g = Graph()
    assert g.successors("missing") is None


def test_neighbors_absent_node_returns_none() -> None:
    g = Graph()
    assert g.neighbors("missing") is None


def test_predecessors_isolated_node_returns_empty() -> None:
    """A node with no in-edges has an empty (but present) predecessor list."""
    g = Graph()
    g.set_node("a", None)
    assert g.predecessors("a") == []


def test_successors_isolated_node_returns_empty() -> None:
    """A node with no out-edges has an empty (but present) successor list."""
    g = Graph()
    g.set_node("a", None)
    assert g.successors("a") == []


def test_neighbors_isolated_node_returns_empty() -> None:
    g = Graph()
    g.set_node("a", None)
    assert g.neighbors("a") == []


def test_predecessors_insertion_order() -> None:
    """predecessors(v) returns the source nodes of v's in-edges in insertion order."""
    g = Graph()
    g.set_node("a", None)
    g.set_edge("b", "a", None, None)
    g.set_edge("c", "a", None, None)
    assert g.predecessors("a") == ["b", "c"]


def test_successors_insertion_order() -> None:
    """successors(v) returns the target nodes of v's out-edges in insertion order."""
    g = Graph()
    g.set_node("a", None)
    g.set_edge("a", "b", None, None)
    g.set_edge("a", "c", None, None)
    assert g.successors("a") == ["b", "c"]


def test_neighbors_union_order_preserved() -> None:
    """neighbors(v) = predecessors + new successors, first-seen order preserved.

    "c" appears in both preds (c->a) and sucs (a->c) and is deduplicated.
    """
    g = Graph()
    g.set_node("a", None)
    g.set_edge("b", "a", None, None)
    g.set_edge("c", "a", None, None)  # c is a predecessor of a
    g.set_edge("a", "c", None, None)  # c is also a successor of a
    g.set_edge("a", "d", None, None)
    # neighbors = preds (b, c) + new sucs (d); c deduped
    assert g.neighbors("a") == ["b", "c", "d"]


def test_is_leaf_directed_isolated_node_true() -> None:
    """Directed: a node with no successors is a leaf."""
    g = Graph()  # directed
    g.set_node("a", None)
    assert g.is_leaf("a") is True


def test_is_leaf_directed_with_successors_false() -> None:
    """Directed: a node with a successor is not a leaf (exercised via set_edge, R244)."""
    g = Graph()  # directed
    g.set_node("a", None)
    g.set_edge("a", "b", None, None)
    assert g.is_leaf("a") is False


def test_is_leaf_undirected_uses_neighbors() -> None:
    """Undirected: is_leaf checks neighbors (preds OR sucs), not just successors.

    A node with only a predecessor is still a non-leaf in undirected mode
    (because neighbors merges preds + sucs).
    """
    g = Graph(GraphOption(directed=False))
    g.set_node("a", None)
    g.set_edge("b", "a", None, None)  # a has a predecessor b (undirected: same edge)
    # undirected: neighbors(a) = {b}, so a is NOT a leaf
    assert g.is_leaf("a") is False


def test_is_leaf_absent_node_true() -> None:
    """An absent node is treated as a leaf (matches grok)."""
    g = Graph()
    assert g.is_leaf("missing") is True


# === R244: edge-method + remove_node + filter_nodes black-box suite ===========
# Closes the complete Graph CRUD surface. The R243 white-box mode (filling the
# internal _in/_out/_preds/_sucs tables directly because set_edge was deferred)
# is retired -- every adjacency state below is built via the public set_edge API.


# --- DefaultEdgeLabel tagged union (mirrors grok ``enum DefaultEdgeLabel``) ----


def test_edge_label_value_defaults_to_none() -> None:
    """EdgeLabelValue() with no argument has ``value=None`` (fall-back signal)."""
    assert EdgeLabelValue().value is None


def test_edge_label_value_carries_value() -> None:
    """EdgeLabelValue(v) holds the fixed default value."""
    assert EdgeLabelValue("lbl").value == "lbl"


def test_edge_label_value_is_frozen() -> None:
    """EdgeLabelValue is frozen (value object); mutation raises (FrozenInstanceError)."""
    variant = EdgeLabelValue("lbl")
    with pytest.raises(AttributeError):  # FrozenInstanceError subclasses AttributeError
        variant.value = "other"  # type: ignore[misc]


def test_edge_label_value_equality() -> None:
    """Two EdgeLabelValues with the same value compare equal (dataclass eq)."""
    assert EdgeLabelValue("x") == EdgeLabelValue("x")
    assert EdgeLabelValue("x") != EdgeLabelValue("y")
    assert EdgeLabelValue() == EdgeLabelValue(None)


def test_edge_label_factory_holds_callable() -> None:
    """EdgeLabelFactory stores the per-edge factory callable verbatim."""

    def fn(edge_id: str) -> str:
        return f"label-{edge_id}"

    variant = EdgeLabelFactory(fn)
    assert variant.factory is fn


def test_edge_label_factory_is_mutable() -> None:
    """EdgeLabelFactory is NOT frozen (it wraps a behaviour, not a value)."""
    variant = EdgeLabelFactory(lambda eid: "a")
    variant.factory = lambda eid: "b"  # type: ignore[method-assign]
    assert variant.factory("x") == "b"


def test_set_default_edge_label_returns_self_for_chaining() -> None:
    """set_default_edge_label returns ``&mut self`` (grok chaining convention)."""
    g = Graph()
    assert g.set_default_edge_label(EdgeLabelValue("v")) is g


# --- default_edge_label three-way fallback -----------------------------------


def test_default_edge_label_value_variant() -> None:
    """EdgeLabelValue with a value -> that value."""
    g = Graph()
    g.set_default_edge_label(EdgeLabelValue("fixed"))
    assert g.default_edge_label("a-b") == "fixed"


def test_default_edge_label_factory_variant() -> None:
    """EdgeLabelFactory -> factory(edge_id) when non-None."""
    g = Graph()
    g.set_default_edge_label(EdgeLabelFactory(lambda edge_id: f"f-{edge_id}"))
    assert g.default_edge_label("a-b") == "f-a-b"


def test_default_edge_label_value_none_falls_back_to_factory() -> None:
    """EdgeLabelValue(None) -> edge_default_factory() (grok ``E::default()``)."""
    g = Graph(GraphOption(), edge_default_factory=lambda: "dft")
    g.set_default_edge_label(EdgeLabelValue(None))
    assert g.default_edge_label("a-b") == "dft"


def test_default_edge_label_factory_none_falls_back() -> None:
    """EdgeLabelFactory returning None -> edge_default_factory()."""
    g = Graph(GraphOption(), edge_default_factory=lambda: "dft")
    g.set_default_edge_label(EdgeLabelFactory(lambda edge_id: None))
    assert g.default_edge_label("a-b") == "dft"


def test_default_edge_label_no_default_returns_none() -> None:
    """Default EdgeLabelValue() + default edge_default_factory (lambda: None) -> None."""
    g = Graph()  # default_edge_label_fn = EdgeLabelValue(); factory = lambda: None
    assert g.default_edge_label("a-b") is None


# --- set_edge ----------------------------------------------------------------


def test_set_edge_creates_edge_increments_count() -> None:
    """Creating a new edge bumps edge_count from 0 to 1."""
    g = Graph()
    assert g.edge_count() == 0
    g.set_edge("a", "b", None, None)
    assert g.edge_count() == 1


def test_set_edge_returns_self_for_chaining() -> None:
    """set_edge returns ``&mut self`` (grok chaining convention)."""
    g = Graph()
    assert g.set_edge("a", "b", None, None) is g


def test_set_edge_ensures_endpoints_exist() -> None:
    """set_edge auto-creates both endpoints via set_node."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    assert g.has_node("a")
    assert g.has_node("b")


def test_set_edge_unit_label_defaults_none() -> None:
    """For an E=() graph, a created edge stores label None (disambiguate via has_edge)."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    assert g.edge("a", "b", None) is None
    assert g.has_edge("a", "b", None)


def test_set_edge_with_explicit_label() -> None:
    """An explicit edge_label is stored and readable via edge()."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    assert g.edge("a", "b", None) == "lbl"


def test_set_edge_updates_existing_label() -> None:
    """Re-setting an existing edge with a label updates it (count unchanged)."""
    g = Graph()
    g.set_edge("a", "b", "x", None)
    g.set_edge("a", "b", "y", None)
    assert g.edge("a", "b", None) == "y"
    assert g.edge_count() == 1


def test_set_edge_keeps_label_when_edge_label_none_on_existing() -> None:
    """Re-setting an existing edge with edge_label=None leaves the label untouched."""
    g = Graph()
    g.set_edge("a", "b", "x", None)
    g.set_edge("a", "b", None, None)
    assert g.edge("a", "b", None) == "x"
    assert g.edge_count() == 1


def test_set_edge_named_edge_non_multigraph_raises() -> None:
    """A named edge on a non-multigraph raises RuntimeError (grok Err)."""
    g = Graph()  # multigraph=False
    with pytest.raises(RuntimeError):
        g.set_edge("a", "b", None, "name")


def test_set_edge_named_edge_multigraph_allowed() -> None:
    """A named edge on a multigraph is allowed (parallel-edge discriminator)."""
    g = Graph(GraphOption(multigraph=True))
    g.set_edge("a", "b", "x", "n1")
    assert g.edge_count() == 1
    assert g.has_edge("a", "b", "n1")


def test_set_edge_idempotent_no_double_count() -> None:
    """Setting the same edge twice does not double-count."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("a", "b", None, None)
    assert g.edge_count() == 1


def test_set_edge_with_obj() -> None:
    """set_edge_with_obj creates the edge (name hardcoded None)."""
    g = Graph()
    g.set_edge_with_obj(Edge("a", "b"), "lbl")
    assert g.edge("a", "b", None) == "lbl"
    assert g.has_edge("a", "b", None)


# --- edge / edge_mut / has_edge family ---------------------------------------


def test_edge_absent_returns_none() -> None:
    """edge() on a missing edge returns None."""
    g = Graph()
    assert g.edge("a", "b", None) is None


def test_edge_with_obj() -> None:
    """edge_with_obj reads via the edge-object overload."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    assert g.edge_with_obj(Edge("a", "b")) == "lbl"


def test_edge_with_obj_matches_set_edge_none_name() -> None:
    """Edge(name=None) maps to DEFAULT_EDGE_NAME -- same id as set_edge(...,None)."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    e = Edge("a", "b")  # name defaults to None -> DEFAULT_EDGE_NAME
    assert g.has_edge_with_obj(e)
    assert g.edge_with_obj(e) == "lbl"


def test_edge_mut_returns_mutable_handle() -> None:
    """edge_mut returns a live mutable handle; mutating it mutates the stored label."""
    g = Graph()
    g.set_edge("a", "b", {"n": 1}, None)
    handle = g.edge_mut("a", "b", None)
    assert handle is not None
    handle["n"] = 2
    assert g.edge("a", "b", None) == {"n": 2}


def test_edge_mut_absent_returns_none() -> None:
    """edge_mut on a missing edge returns None."""
    g = Graph()
    assert g.edge_mut("a", "b", None) is None


def test_has_edge_true_false() -> None:
    """has_edge reports existence regardless of the label value."""
    g = Graph()
    assert g.has_edge("a", "b", None) is False
    g.set_edge("a", "b", None, None)
    assert g.has_edge("a", "b", None) is True


def test_has_edge_with_obj() -> None:
    """has_edge_with_obj reports existence via the edge-object overload."""
    g = Graph()
    e = Edge("a", "b")
    assert g.has_edge_with_obj(e) is False
    g.set_edge("a", "b", None, None)
    assert g.has_edge_with_obj(e) is True


# --- remove_edge -------------------------------------------------------------


def test_remove_edge_decrements_count_drops_label() -> None:
    """remove_edge drops the label and decrements the count."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    g.remove_edge("a", "b", None)
    assert g.edge_count() == 0
    assert not g.has_edge("a", "b", None)
    assert g.edge("a", "b", None) is None


def test_remove_edge_updates_adjacency() -> None:
    """remove_edge decrements the predecessor/successor counters to empty."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.remove_edge("a", "b", None)
    # _preds["b"] / _sucs["a"] still exist as empty maps -> [] not None
    assert g.predecessors("b") == []
    assert g.successors("a") == []


def test_remove_edge_keeps_nodes() -> None:
    """remove_edge drops only the edge, not its endpoints."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.remove_edge("a", "b", None)
    assert g.has_node("a")
    assert g.has_node("b")


def test_remove_edge_absent_noop() -> None:
    """remove_edge on a missing edge is a no-op (count unchanged)."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.remove_edge("x", "y", None)  # absent
    assert g.edge_count() == 1


def test_remove_edge_with_obj() -> None:
    """remove_edge_with_obj removes via the edge-object overload."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    g.remove_edge_with_obj(Edge("a", "b"))
    assert g.edge_count() == 0


def test_remove_edge_then_re_add() -> None:
    """After removal the edge can be re-created (count 0 -> 1)."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.remove_edge("a", "b", None)
    assert g.edge_count() == 0
    g.set_edge("a", "b", "lbl", None)
    assert g.edge_count() == 1
    assert g.edge("a", "b", None) == "lbl"


# --- in_edges / out_edges / node_edges ---------------------------------------


def test_in_edges_absent_node_returns_none() -> None:
    """in_edges on an absent node returns None (no adjacency table)."""
    g = Graph()
    assert g.in_edges("missing", None) is None


def test_in_edges_isolated_returns_empty_list() -> None:
    """in_edges on a node that exists but has no in-edges returns []."""
    g = Graph()
    g.set_node("a", None)
    assert g.in_edges("a", None) == []


def test_in_edges_returns_edge_list() -> None:
    """in_edges returns the Edge objects pointing at v."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    edges = g.in_edges("b", None)
    assert edges is not None
    assert len(edges) == 1
    assert edges[0].v == "a" and edges[0].w == "b"


def test_in_edges_filters_by_u() -> None:
    """in_edges(v, u) filters to just the edges coming from u."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("c", "b", None, None)
    edges = g.in_edges("b", "a")
    assert edges is not None
    assert len(edges) == 1
    assert edges[0].v == "a"


def test_in_edges_multiple_preserve_insertion_order() -> None:
    """in_edges lists edges in insertion order (deterministic)."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("c", "b", None, None)
    g.set_edge("d", "b", None, None)
    edges = g.in_edges("b", None)
    assert edges is not None
    assert [e.v for e in edges] == ["a", "c", "d"]


def test_out_edges_absent_returns_none() -> None:
    """out_edges on an absent node returns None."""
    g = Graph()
    assert g.out_edges("missing", None) is None


def test_out_edges_returns_list_and_filters_by_w() -> None:
    """out_edges returns v's out-edges; filtered to those pointing at w."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("a", "c", None, None)
    all_out = g.out_edges("a", None)
    assert all_out is not None
    assert len(all_out) == 2
    to_b = g.out_edges("a", "b")
    assert to_b is not None
    assert len(to_b) == 1
    assert to_b[0].w == "b"


def test_node_edges_merges_in_and_out() -> None:
    """node_edges merges in-edges and out-edges regardless of direction."""
    g = Graph()
    g.set_edge("x", "v", None, None)  # in-edge to v
    g.set_edge("v", "y", None, None)  # out-edge from v
    edges = g.node_edges("v", None)
    assert edges is not None
    assert len(edges) == 2


def test_node_edges_absent_returns_none() -> None:
    """node_edges on an absent node returns None."""
    g = Graph()
    assert g.node_edges("missing", None) is None


def test_node_edges_filters_by_w_both_directions() -> None:
    """node_edges(v, w) returns edges between v and w in either direction."""
    g = Graph()
    g.set_edge("v", "w", None, None)  # out-edge v->w
    g.set_edge("w", "v", None, None)  # in-edge w->v
    edges = g.node_edges("v", "w")
    assert edges is not None
    assert len(edges) == 2  # one in + one out


# --- edges / edge_count / set_path -------------------------------------------


def test_edges_empty_initially() -> None:
    """A fresh graph has no edges."""
    g = Graph()
    assert g.edges() == []


def test_edges_returns_insertion_order() -> None:
    """edges() lists Edge objects in insertion order."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("c", "d", None, None)
    objs = g.edges()
    assert [(e.v, e.w) for e in objs] == [("a", "b"), ("c", "d")]


def test_edge_count_tracks_add_remove() -> None:
    """edge_count reflects the running total across add/remove."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("b", "c", None, None)
    assert g.edge_count() == 2
    g.remove_edge("a", "b", None)
    assert g.edge_count() == 1


def test_set_path_links_consecutive_pairs() -> None:
    """set_path([a,b,c,d]) creates a->b, b->c, c->d."""
    g = Graph()
    g.set_path(["a", "b", "c", "d"], None)
    assert g.edge_count() == 3
    assert g.has_edge("a", "b", None)
    assert g.has_edge("b", "c", None)
    assert g.has_edge("c", "d", None)


def test_set_path_single_node_no_edges() -> None:
    """set_path over a single node creates no edges."""
    g = Graph()
    g.set_path(["a"], None)
    assert g.edge_count() == 0


# --- remove_node -------------------------------------------------------------


def test_remove_node_cascades_incident_edges() -> None:
    """remove_node drops every incident edge (both in and out)."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    g.set_edge("b", "c", None, None)
    assert g.edge_count() == 2
    g.remove_node("b")
    assert g.edge_count() == 0
    assert not g.has_node("b")
    assert g.has_node("a")
    assert g.has_node("c")


def test_remove_node_absent_noop() -> None:
    """remove_node on an absent node is a no-op."""
    g = Graph()
    g.set_node("a", None)
    g.remove_node("missing")
    assert g.node_count() == 1
    assert g.has_node("a")


def test_remove_node_compound_reparents_children_to_root() -> None:
    """In a compound graph, removing a parent re-parents its children onto the root."""
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_parent("b", "a")  # b is a child of a
    g.remove_node("a")
    assert not g.has_node("a")
    assert g.has_node("b")
    assert g.parent("b") is None  # re-parented onto synthetic GRAPH_NODE root


# --- filter_nodes ------------------------------------------------------------


def test_filter_nodes_keeps_matching() -> None:
    """filter_nodes keeps only the nodes whose id passes the predicate."""
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    filtered = g.filter_nodes(lambda n: n == "a")
    assert filtered.nodes() == ["a"]


def test_filter_nodes_drops_incident_edges() -> None:
    """Edges incident to a rejected node are dropped."""
    g = Graph()
    g.set_edge("a", "b", None, None)
    filtered = g.filter_nodes(lambda n: n == "a")
    assert filtered.edge_count() == 0
    assert not filtered.has_edge("a", "b", None)


def test_filter_nodes_preserves_edges_between_survivors() -> None:
    """An edge between two surviving nodes is kept."""
    g = Graph()
    g.set_edge("a", "b", "lbl", None)
    g.set_node("c", None)
    filtered = g.filter_nodes(lambda n: n != "c")
    assert filtered.edge_count() == 1
    assert filtered.edge("a", "b", None) == "lbl"


def test_filter_nodes_preserves_graph_flags() -> None:
    """filter_nodes copies the directed/multigraph/compound flags onto the result."""
    g = Graph(GraphOption(directed=False, multigraph=True, compound=True))
    g.set_node("a", None)
    filtered = g.filter_nodes(lambda n: True)
    assert filtered.is_directed() is False
    assert filtered.is_multigraph() is True
    assert filtered.is_compound() is True


def test_filter_nodes_compound_intermediate_filtered_rethreads_descendant() -> None:
    """Each node is filtered by its own predicate -- descendants survive a rejected
    intermediate and re-thread onto the closest surviving ancestor.

    NOTE: the filter_nodes docstring claim that "if a parent is rejected all its
    children are rejected too" does NOT match the grok implementation (nor this
    port): the predicate is applied per-node with no recursive descent. Dropping
    an intermediate node leaves its descendants in the result, re-threaded by
    :func:`_find_parent`. This test pins the actual (implementation-faithful)
    behaviour.
    """
    g = Graph(GraphOption(compound=True))
    g.set_node("a", None)
    g.set_node("b", None)
    g.set_node("c", None)
    g.set_parent("b", "a")  # b is a child of a
    g.set_parent("c", "b")  # c is a child of b
    # Drop the intermediate "a"; b and c survive.
    filtered = g.filter_nodes(lambda n: n != "a")
    assert "a" not in filtered.nodes()
    assert "b" in filtered.nodes()
    assert "c" in filtered.nodes()
    # "a" was filtered out and its own parent is None (root) -> b re-threads to root.
    assert filtered.parent("b") is None
    # "b" survived, so c's parent chain is intact -> c's parent stays "b".
    assert filtered.parent("c") == "b"


def test_filter_nodes_compound_rethreads_to_grandparent() -> None:
    """A leaf whose parent chain crosses a filtered-out intermediate re-threads
    onto the closest surviving ancestor (the grandparent)."""
    g = Graph(GraphOption(compound=True))
    g.set_node("root", None)
    g.set_node("mid", None)
    g.set_node("leaf", None)
    g.set_parent("mid", "root")
    g.set_parent("leaf", "mid")
    # Drop "mid"; leaf should re-thread onto grandparent "root".
    filtered = g.filter_nodes(lambda n: n != "mid")
    assert filtered.parent("leaf") == "root"


# --- module-private edge helpers ---------------------------------------------


def test_increment_or_init_entry_initial_one() -> None:
    """_increment_or_init_entry on a vacant key initialises the count to 1."""
    m: OrderedHashMap[str, int] = OrderedHashMap()
    _increment_or_init_entry(m, "x")
    assert m.get("x") == 1
    assert list(m.keys()) == ["x"]


def test_increment_or_init_entry_in_place_increment() -> None:
    """_increment_or_init_entry on an occupied key increments in place."""
    m: OrderedHashMap[str, int] = OrderedHashMap()
    _increment_or_init_entry(m, "x")
    _increment_or_init_entry(m, "x")
    assert m.get("x") == 2


def test_increment_preserves_insertion_position() -> None:
    """Incrementing an existing key keeps its insertion position (grok get_mut)."""
    m: OrderedHashMap[str, int] = OrderedHashMap()
    _increment_or_init_entry(m, "a")
    _increment_or_init_entry(m, "b")
    _increment_or_init_entry(m, "a")  # bump existing "a"
    assert list(m.keys()) == ["a", "b"]
    assert m.get("a") == 2


def test_decrement_or_remove_entry_removes_at_zero() -> None:
    """_decrement_or_remove_entry drops the entry when the count hits zero."""
    m: OrderedHashMap[str, int] = OrderedHashMap()
    _increment_or_init_entry(m, "x")
    _decrement_or_remove_entry(m, "x")
    assert not m.contains_key("x")


def test_decrement_or_remove_entry_in_place_decrement() -> None:
    """_decrement_or_remove_entry decrements in place above zero."""
    m: OrderedHashMap[str, int] = OrderedHashMap()
    _increment_or_init_entry(m, "x")
    _increment_or_init_entry(m, "x")
    _decrement_or_remove_entry(m, "x")
    assert m.get("x") == 1
