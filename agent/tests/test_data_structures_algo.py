"""Black-box tests for the migrated graphlib ``algo`` traversal layer (R245).

Exercises :mod:`minimax_code.data_structures.algo` purely through the public
``Graph`` interface + the three public algo entry points (``dfs`` /
``preorder`` / ``postorder``). Covers:

* pre-/post-order ordering on a directed diamond + extra leaf,
* the directed-vs-undirected navigation difference (successors vs neighbors),
* boundary cases (absent node, cycle termination, empty graph, single node,
  multiple shared-visited entries, disconnected components),
* the ``order`` fallthrough semantics (only ``"post"`` selects post-order),
* the ``preorder`` / ``postorder`` wrappers and their silent-swallow contract,
* the R245 upstream-bug fix (grok ``pre_order_dfs`` always panics),
* the barrel exposure contract (algo symbols stay out of ``__all__``).
"""

from __future__ import annotations

import pytest

import minimax_code.data_structures as data_structures
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.algo import dfs, postorder, preorder


def _diamond() -> Graph:
    """Directed diamond + extra leaf: a->b, a->c, b->d, c->d, c->e.

    Successors (insertion order):
    a=[b, c], b=[d], c=[d, e], d=[], e=[].
    """
    g: Graph = Graph(GraphOption())
    g.set_nodes(["a", "b", "c", "d", "e"], None)
    g.set_edge("a", "b", None, None)
    g.set_edge("a", "c", None, None)
    g.set_edge("b", "d", None, None)
    g.set_edge("c", "d", None, None)
    g.set_edge("c", "e", None, None)
    return g


# === dfs: core ordering =====================================================


def test_dfs_pre_order_diamond_directed() -> None:
    """Pre-order visits root, then recurses left-to-right (insertion order)."""
    assert dfs(_diamond(), ["a"], "pre") == ["a", "b", "d", "c", "e"]


def test_dfs_post_order_diamond_directed() -> None:
    """Post-order emits a node only after all its descendants."""
    assert dfs(_diamond(), ["a"], "post") == ["d", "b", "e", "c", "a"]


def test_dfs_post_order_reverse_is_topological() -> None:
    """Reverse(post-order) of a DAG is a valid topological order.

    For every edge u->v, u must appear before v in the reversed sequence.
    """
    g = _diamond()
    edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("c", "e")]
    order = dfs(g, ["a"], "post")
    reversed_order = list(reversed(order))
    pos = {node: i for i, node in enumerate(reversed_order)}
    assert all(pos[u] < pos[v] for u, v in edges)


# === navigation: directed successors vs undirected neighbors ===============


def test_dfs_directed_does_not_ascend_to_predecessors() -> None:
    """Directed traversal uses successors only: from b we cannot reach a."""
    g: Graph = Graph(GraphOption())  # directed by default
    g.set_edge("a", "b", None, None)
    assert dfs(g, ["a"], "pre") == ["a", "b"]
    # b has no successors in a directed graph, so traversal stops at b.
    assert dfs(g, ["b"], "pre") == ["b"]


def test_dfs_undirected_traverses_both_directions() -> None:
    """Undirected traversal uses neighbors: from b we CAN reach a."""
    g: Graph = Graph(GraphOption(directed=False))
    g.set_edge("a", "b", None, None)
    assert dfs(g, ["a"], "pre") == ["a", "b"]
    assert dfs(g, ["b"], "pre") == ["b", "a"]


def test_dfs_undirected_diamond_visits_all_via_neighbors() -> None:
    """Undirected diamond reaches every node from any starting node."""
    g: Graph = Graph(GraphOption(directed=False))
    g.set_nodes(["a", "b", "c", "d"], None)
    g.set_edge("a", "b", None, None)
    g.set_edge("b", "c", None, None)
    g.set_edge("c", "d", None, None)
    g.set_edge("a", "d", None, None)
    # From d (neighbors: a, c), traversal reaches the whole component.
    result = dfs(g, ["d"], "pre")
    assert set(result) == {"a", "b", "c", "d"}
    assert result[0] == "d"


# === dfs: boundary cases ====================================================


def test_dfs_missing_entry_node_raises_value_error() -> None:
    """An absent entry node raises ValueError (mirrors grok Err arm)."""
    g = _diamond()
    with pytest.raises(ValueError, match="Graph does not have node: z"):
        dfs(g, ["a", "z"], "pre")


def test_dfs_missing_sole_entry_raises_value_error() -> None:
    g = _diamond()
    with pytest.raises(ValueError, match="Graph does not have node: ghost"):
        dfs(g, ["ghost"], "pre")


def test_dfs_cycle_terminates() -> None:
    """A cycle must not loop forever; visited set caps each node to one visit."""
    g: Graph = Graph(GraphOption())
    g.set_nodes(["x", "y", "z"], None)
    g.set_edge("x", "y", None, None)
    g.set_edge("y", "z", None, None)
    g.set_edge("z", "x", None, None)  # back-edge closing the cycle
    assert dfs(g, ["x"], "pre") == ["x", "y", "z"]
    assert dfs(g, ["x"], "post") == ["z", "y", "x"]


def test_dfs_empty_graph_with_empty_entries() -> None:
    """Empty entry list on any graph yields an empty traversal."""
    g: Graph = Graph(GraphOption())
    assert dfs(g, [], "pre") == []
    assert dfs(g, [], "post") == []


def test_dfs_single_node_no_edges() -> None:
    """A lone node traverses to itself in both orders."""
    g: Graph = Graph(GraphOption())
    g.set_node("solo", None)
    assert dfs(g, ["solo"], "pre") == ["solo"]
    assert dfs(g, ["solo"], "post") == ["solo"]


def test_dfs_multiple_entries_share_visited_set() -> None:
    """Multiple entry nodes share one visited map; overlaps are not repeated."""
    g = _diamond()
    # b is reachable from a, so entering [a, b] does not re-emit b.
    result = dfs(g, ["a", "b"], "pre")
    assert result == ["a", "b", "d", "c", "e"]


def test_dfs_disconnected_components_only_reach_reachable() -> None:
    """Traversal from one component never crosses into a disconnected one."""
    g: Graph = Graph(GraphOption())
    g.set_nodes(["a", "b", "c", "d"], None)
    g.set_edge("a", "b", None, None)
    g.set_edge("c", "d", None, None)  # separate component
    assert dfs(g, ["a"], "pre") == ["a", "b"]
    assert dfs(g, ["c"], "pre") == ["c", "d"]
    # Entering both components yields both, in entry order.
    assert dfs(g, ["a", "c"], "pre") == ["a", "b", "c", "d"]


# === order parameter semantics =============================================


def test_dfs_order_only_exact_post_is_post() -> None:
    """Only the exact string ``"post"`` selects post-order; anything else is pre.

    Mirrors the grok ``match`` fallthrough (``"post" => post, _ => pre``).
    """
    g = _diamond()
    expected_pre = ["a", "b", "d", "c", "e"]
    expected_post = ["d", "b", "e", "c", "a"]
    assert dfs(g, ["a"], "pre") == expected_pre
    assert dfs(g, ["a"], "post") == expected_post
    assert dfs(g, ["a"], "PRE") == expected_pre  # case-sensitive, falls through
    assert dfs(g, ["a"], "") == expected_pre
    assert dfs(g, ["a"], "bogus") == expected_pre


# === preorder / postorder wrappers =========================================


def test_postorder_wrapper_returns_post_order() -> None:
    assert postorder(_diamond(), ["a"]) == ["d", "b", "e", "c", "a"]


def test_preorder_wrapper_returns_pre_order() -> None:
    assert preorder(_diamond(), ["a"]) == ["a", "b", "d", "c", "e"]


def test_postorder_wrapper_swallows_missing_node() -> None:
    """postorder returns [] (not raises) on an absent node -- grok's forgiving arm."""
    assert postorder(_diamond(), ["ghost"]) == []
    # A valid-then-invalid entry list also collapses to [].
    assert postorder(_diamond(), ["a", "ghost"]) == []


def test_preorder_wrapper_swallows_missing_node() -> None:
    assert preorder(_diamond(), ["ghost"]) == []
    assert preorder(_diamond(), ["a", "ghost"]) == []


def test_wrappers_on_empty_entries_return_empty() -> None:
    g = _diamond()
    assert postorder(g, []) == []
    assert preorder(g, []) == []


# === R245 upstream-bug regression ===========================================


def test_preorder_bugfix_no_panic_with_multiple_children() -> None:
    """R245 regression: grok ``pre_order_dfs`` always panics.

    The upstream source seeds ``idx = len`` then indexes
    ``_navigation_nodes.get(idx)`` -- ``get(len)`` is always ``None`` so
    ``unwrap()`` panics on the very first descent from any node that has at
    least one navigation neighbour. The migrated fix returns a correct
    pre-order instead of raising. Both a multi-child node (a has [b, c]) and
    a single-child node (p has [q]) would panic upstream.
    """
    # Multi-child descent from `a` (successors [b, c]).
    assert preorder(_diamond(), ["a"]) == ["a", "b", "d", "c", "e"]
    # Single-child descent also panics upstream.
    g: Graph = Graph(GraphOption())
    g.set_edge("p", "q", None, None)
    assert preorder(g, ["p"]) == ["p", "q"]


def test_preorder_visits_children_in_insertion_order() -> None:
    """The bugfix must preserve left-to-right (insertion-order) child visits,
    matching post-order's descent direction."""
    g: Graph = Graph(GraphOption())
    g.set_nodes(["root", "first", "second", "third"], None)
    g.set_edge("root", "first", None, None)
    g.set_edge("root", "second", None, None)
    g.set_edge("root", "third", None, None)
    assert dfs(g, ["root"], "pre") == ["root", "first", "second", "third"]


# === barrel exposure contract ==============================================


def test_algo_symbols_not_in_barrel_all() -> None:
    """algo is a public module (grok ``pub mod algo``) but NOT re-exported at
    the crate root (no ``pub use algo::*``); the barrel mirrors that -- the
    three algo symbols stay out of ``__all__`` and off the package top level.
    """
    assert "dfs" not in data_structures.__all__
    assert "postorder" not in data_structures.__all__
    assert "preorder" not in data_structures.__all__
    # Not re-exported at the package top level either -- must import from algo.
    assert not hasattr(data_structures, "dfs")
    assert not hasattr(data_structures, "postorder")
    assert not hasattr(data_structures, "preorder")


def test_algo_submodule_importable_with_own_all() -> None:
    """The ``algo`` submodule is importable as a public module and exposes its
    own ``__all__`` of the three traversal symbols."""
    import minimax_code.data_structures.algo as algo_mod

    assert algo_mod.dfs is dfs
    assert algo_mod.postorder is postorder
    assert algo_mod.preorder is preorder
    assert algo_mod.__all__ == ["dfs", "postorder", "preorder"]


def test_barrel_count_unchanged_at_five() -> None:
    """R245 adds no barrel symbols (algo stays module-private); the barrel
    count set by R241-R243 is unchanged at 5."""
    assert len(data_structures.__all__) == 5
