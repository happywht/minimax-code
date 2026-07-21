"""Tests for data_structures.graphlib (R242+R243, vendored ``third_party/graphlib_rust``).

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

Edge-method coverage (``set_edge`` / ``edge`` / ``remove_edge`` / ``in_edges``
/ ``out_edges``) and the node methods that depend on them (``remove_node`` /
``filter_nodes``) migrate in R243b. Until then, non-empty adjacency state
cannot be created via the public API, so the predecessor/successor/neighbor
tests that need a populated adjacency table are white-box: they insert
entries directly into the ``_in`` / ``_out`` / ``_preds`` / ``_sucs`` internal
tables (clearly commented "set_edge deferred to R243b"). The black-box
contract (absent node -> ``None``, isolated node -> ``[]``) is covered via
``set_node``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.data_structures import (
    Edge,
    Graph,
    GraphOption,
)
from minimax_code.data_structures.graphlib import (
    DEFAULT_EDGE_NAME,
    EDGE_KEY_DELIM,
    GRAPH_NODE,
    NodeLabelFactory,
    NodeLabelValue,
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


def test_sources_excludes_node_with_in_edges_whitebox() -> None:
    """A node whose _in table is populated is not a source.

    set_edge is deferred to R243b, so the _in entry is inserted directly
    (white-box) to exercise the non-empty branch.
    """
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    # white-box: give "a" an in-edge entry (set_edge deferred to R243b)
    in_a = g._in.get("a")
    assert in_a is not None
    in_a.insert("x|a", Edge(v="x", w="a"))
    assert g.sources() == ["b"]


def test_sinks_excludes_node_with_out_edges_whitebox() -> None:
    """A node whose _out table is populated is not a sink.

    set_edge is deferred to R243b, so the _out entry is inserted directly
    (white-box) to exercise the non-empty branch.
    """
    g = Graph()
    g.set_node("a", None)
    g.set_node("b", None)
    # white-box: give "a" an out-edge entry (set_edge deferred to R243b)
    out_a = g._out.get("a")
    assert out_a is not None
    out_a.insert("a|x", Edge(v="a", w="x"))
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


def test_predecessors_whitebox_insertion_order() -> None:
    """predecessors(v) returns _preds[v] keys in insertion order.

    set_edge is deferred to R243b, so the _preds entry is inserted directly.
    """
    g = Graph()
    g.set_node("a", None)
    preds_a = g._preds.get("a")
    assert preds_a is not None
    preds_a.insert("b", 1)
    preds_a.insert("c", 2)
    assert g.predecessors("a") == ["b", "c"]


def test_successors_whitebox_insertion_order() -> None:
    """successors(v) returns _sucs[v] keys in insertion order.

    set_edge is deferred to R243b, so the _sucs entry is inserted directly.
    """
    g = Graph()
    g.set_node("a", None)
    sucs_a = g._sucs.get("a")
    assert sucs_a is not None
    sucs_a.insert("b", 1)
    sucs_a.insert("c", 2)
    assert g.successors("a") == ["b", "c"]


def test_neighbors_whitebox_union_order_preserved() -> None:
    """neighbors(v) = predecessors + new successors, first-seen order preserved (grok uses unordered HashSet).

    set_edge is deferred to R243b, so the _preds/_sucs entries are inserted
    directly. "c" appears in both preds and sucs and is deduplicated.
    """
    g = Graph()
    g.set_node("a", None)
    preds_a = g._preds.get("a")
    assert preds_a is not None
    preds_a.insert("b", 1)
    preds_a.insert("c", 2)
    sucs_a = g._sucs.get("a")
    assert sucs_a is not None
    sucs_a.insert("c", 1)  # duplicate of pred "c"
    sucs_a.insert("d", 2)
    # neighbors = preds (b, c) + new sucs (d); c deduped
    assert g.neighbors("a") == ["b", "c", "d"]


def test_is_leaf_directed_isolated_node_true() -> None:
    """Directed: a node with no successors is a leaf."""
    g = Graph()  # directed
    g.set_node("a", None)
    assert g.is_leaf("a") is True


def test_is_leaf_directed_with_successors_false_whitebox() -> None:
    """Directed: a node with a successor is not a leaf.

    set_edge is deferred to R243b, so the _sucs entry is inserted directly.
    """
    g = Graph()  # directed
    g.set_node("a", None)
    sucs_a = g._sucs.get("a")
    assert sucs_a is not None
    sucs_a.insert("b", 1)
    assert g.is_leaf("a") is False


def test_is_leaf_undirected_uses_neighbors_whitebox() -> None:
    """Undirected: is_leaf checks neighbors (preds OR sucs), not just successors.

    set_edge is deferred to R243b, so the _preds entry is inserted directly.
    A node with only a predecessor is still a non-leaf in undirected mode
    (because neighbors merges preds + sucs).
    """
    g = Graph(GraphOption(directed=False))
    g.set_node("a", None)
    preds_a = g._preds.get("a")
    assert preds_a is not None
    preds_a.insert("b", 1)  # a has a predecessor b
    # undirected: neighbors(a) = {b}, so a is NOT a leaf
    assert g.is_leaf("a") is False


def test_is_leaf_absent_node_true() -> None:
    """An absent node is treated as a leaf (matches grok)."""
    g = Graph()
    assert g.is_leaf("missing") is True
