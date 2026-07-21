"""Tests for data_structures.graphlib (R242, vendored ``third_party/graphlib_rust``).

Covers the edge-encoding vocabulary layer migrated in R242: the 3 sentinel
constants (:data:`DEFAULT_EDGE_NAME`, :data:`GRAPH_NODE`, :data:`EDGE_KEY_DELIM`),
the :class:`Edge` and :class:`GraphOption` frozen value objects, and the pure
edge-id encoding helpers (:func:`edge_args_to_id`, :func:`edge_args_to_obj`,
:func:`edge_obj_to_id`).

The encoding invariants under test (the contract ``Graph`` will rely on in
R243): directed edges preserve endpoint order; undirected edges normalise to
``v <= w`` so ``(v, w)`` and ``(w, v)`` produce the same id; an absent name
substitutes :data:`DEFAULT_EDGE_NAME` so named and anonymous edges never
collide; and :func:`edge_obj_to_id` is consistent with
:func:`edge_args_to_id` for the same triple.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.data_structures import Edge, GraphOption
from minimax_code.data_structures.graphlib import (
    DEFAULT_EDGE_NAME,
    EDGE_KEY_DELIM,
    GRAPH_NODE,
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
