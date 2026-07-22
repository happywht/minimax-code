"""Black-box tests for the migrated dagre rank/util layer (R254).

Exercises :mod:`minimax_code.dagre.layout.rank.util` through its two public
entry points (:func:`longest_path` / :func:`slack`) plus the private
:func:`_longest_path_dfs` helper on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state ``nesting_graph::run``
leaves behind, since ``run_layout`` calls ``rank`` immediately after the
nesting scaffolding is erected). Covers:

* :func:`longest_path` rank assignment on a single sink, a linear chain, a
  fork, a diamond, and two disjoint source components (the longest-path rank
  = ``min(child_rank - minlen)`` over the out-edges, pushing every node to
  the *lowest* rank it can occupy),
* the ``minlen`` three-branch translation: a default ``GraphEdge()``
  (manual-Default ``minlen = 1.0`` -> 1), an explicit ``minlen`` float
  (``round``), and an explicit ``minlen = None`` (the asymmetric
  ``longest_path`` default -> 0, NOT the ``slack`` default of 10),
* a stripped edge label (white-box: ``edge_with_obj`` returns ``None`` -> the
  default-label path, ``minlen`` 1) and a missing-node label (the private
  DFS helper returns 0 without crashing),
* that the ranking is NOT normalized (ranks stay negative -- a later stage
  shifts them) and that the graph is mutated in place,
* :func:`slack` on a tight edge (0), a loose edge (positive), an
  over-compressed edge (negative), the asymmetric ``minlen = None`` default
  (10, NOT 0), a stripped edge label (-> 1), and a missing node ``rank``
  (-> 0),
* the barrel surface contract (``longest_path`` / ``slack`` stay out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4; the ``rank``
  sub-package re-exports ``util``; ``util.__all__`` is ASCII-sorted).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.util import longest_path, slack
from minimax_code.data_structures import Graph, GraphOption
from minimax_code.data_structures.graphlib import Edge, edge_obj_to_id
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap


def _make_graph() -> Graph:
    """Build an empty directed graph (the state ``rank`` runs on).

    ``compound=True`` because the production graph ``rank`` runs on is the
    ``nesting_graph::run`` output (a compound graph); ``multigraph=True`` to
    mirror the production config. ``longest_path`` / ``slack`` read neither
    the graph label nor the parent forest -- only node ``rank`` and edge
    ``minlen`` -- so a bare graph suffices, but the compound + GraphConfig
    dressing keeps the fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


# === longest_path: rank assignment =======================================


def test_longest_path_single_sink_node() -> None:
    """A lone node with no out-edges lands on rank 0 (the sink rank)."""
    g = _make_graph()
    g.set_node("a", GraphNode())
    longest_path(g)
    assert g.node("a").rank == 0


def test_longest_path_linear_chain_default_minlen() -> None:
    """``a -> b -> c`` (default minlen 1) ranks a=-2, b=-1, c=0.

    The sink c is rank 0; each ancestor drops one rank per default-minlen
    edge (``rank = child_rank - 1``). This is the canonical longest-path
    shape: every node is pushed to the lowest rank it can occupy.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_node("c", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    longest_path(g)
    assert g.node("c").rank == 0
    assert g.node("b").rank == -1
    assert g.node("a").rank == -2


def test_longest_path_fork() -> None:
    """``a -> {b, c}`` (two sinks) ranks a=-1, b=0, c=0.

    Both children are sinks (rank 0); a takes ``min(0 - 1, 0 - 1) = -1``.
    """
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("c").rank == 0
    assert g.node("a").rank == -1


def test_longest_path_diamond() -> None:
    """``a -> {b, c} -> d`` ranks a=-2, b=c=-1, d=0.

    The diamond's two mid nodes both hit rank -1; a takes ``min(-1-1, -1-1)
    = -2``. Verifies the ``min`` over both out-edge paths.
    """
    g = _make_graph()
    for node_id in ("a", "b", "c", "d"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("a", "c", GraphEdge(), None)
    g.set_edge("b", "d", GraphEdge(), None)
    g.set_edge("c", "d", GraphEdge(), None)
    longest_path(g)
    assert g.node("d").rank == 0
    assert g.node("b").rank == -1
    assert g.node("c").rank == -1
    assert g.node("a").rank == -2


def test_longest_path_two_source_components() -> None:
    """Two disjoint chains are ranked independently (each source seeds a DFS)."""
    g = _make_graph()
    for node_id in ("a", "b", "c", "d"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)  # chain 1: a -> b
    g.set_edge("c", "d", GraphEdge(), None)  # chain 2: c -> d
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("a").rank == -1
    assert g.node("d").rank == 0
    assert g.node("c").rank == -1


def test_longest_path_minlen_two() -> None:
    """An explicit ``minlen = 2`` edge drops the source two ranks.

    ``a -> b`` with ``minlen = 2``: b is a sink (rank 0), a takes ``0 - 2
    = -2``. Verifies the ``round(minlen)`` read of a real label.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(minlen=2.0), None)
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("a").rank == -2


def test_longest_path_default_graph_edge_minlen_is_one() -> None:
    """A freshly minted ``GraphEdge()`` already carries ``minlen = 1.0``.

    grok's ``GraphEdge`` has a hand-written ``Default`` impl (``lib.rs``
    lines 112-131) that seeds ``minlen = Some(1.0)`` -- NOT the all-None
    default a derived ``Default`` would give. So a bare ``GraphEdge()`` edge
    yields ``round(1.0) == 1``, and ``a -> b`` drops a by exactly one rank.
    This is the R246 / R253 manual-Default contract: ``minlen`` defaults to
    a real value, not ``None``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    default_edge = GraphEdge()  # no kwargs -> manual Default minlen 1.0
    assert default_edge.minlen == 1.0  # the manual-Default contract
    g.set_edge("a", "b", default_edge, None)
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("a").rank == -1  # round(1.0) == 1


def test_longest_path_explicit_minlen_none_uses_zero() -> None:
    """An edge label carrying ``minlen = None`` resolves to 0 in longest_path.

    grok: ``edge.minlen.unwrap_or(0.0).round()``. Because ``GraphEdge()``'s
    manual Default already sets ``minlen = 1.0``, this branch only fires for
    a real label explicitly constructed with ``minlen = None`` -- and here
    the asymmetric default is 0 (NOT the 10 that :func:`slack` uses). So
    ``a -> b`` with ``minlen = None`` drops a by 0 ranks: both land on 0.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(minlen=None), None)
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("a").rank == 0  # 0 - 0 (minlen None -> 0)


def test_longest_path_does_not_normalize() -> None:
    """longest_path leaves ranks negative -- normalization is a later stage.

    grok's ``longest_path`` pushes every node to the lowest rank it can
    occupy and does NOT shift the ranks so the top is 0; ``normalize_ranks``
    (R248 ``util``) does that later. A linear chain therefore ends with the
    source at a negative rank, not 0.
    """
    g = _make_graph()
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    longest_path(g)
    assert g.node("a").rank < 0  # NOT normalized to 0
    assert g.node("b").rank < 0


def test_longest_path_mutates_graph_in_place() -> None:
    """longest_path writes ranks on the live graph object (no copy/return)."""
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    assert g.node("a").rank is None  # unset before
    longest_path(g)
    assert g.node("a").rank is not None  # same graph, now ranked


def test_longest_path_missing_edge_label_uses_default_minlen() -> None:
    """A real edge whose label was stripped resolves to default minlen 1.0 -> 1.

    grok's ``edge_with_obj(&e).cloned().unwrap_or(GraphEdge::default())``
    yields ``GraphEdge::default().minlen == Some(1.0)`` for a missing label,
    so ``round(1.0) == 1``. The missing-label branch is reached for grok's
    unit-type ``E = ()`` edges (whose stored label is ``None``); here we
    stand in for that by creating an edge then stripping its label from the
    label map (white-box: the edge stays in the adjacency table so
    ``out_edges`` still yields it, but ``edge_with_obj`` now returns
    ``None``). ``a -> b`` therefore drops a by 1, exactly as a default-edge
    would.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    # White-box: strip the label so edge_with_obj returns None for a -> b.
    # OrderedHashMap.remove(key) -> V | None (absent key yields None, no raise).
    edge_id = edge_obj_to_id(True, Edge("a", "b"))
    g._edge_labels.remove(edge_id)
    assert g.edge_with_obj(Edge("a", "b")) is None  # label now missing
    longest_path(g)
    assert g.node("b").rank == 0
    assert g.node("a").rank == -1  # missing label -> default minlen 1.0 -> 1


def test_longest_path_dfs_skips_missing_node_label() -> None:
    """_longest_path_dfs returns 0 for a label-less node without crashing.

    grok's ``g.node_mut(v)`` returns ``None`` for a missing node, so the
    ``node_label.rank = Some(rank)`` write is skipped; the rank (0 for a node
    with no out-edges) is still returned and the node is marked visited. We
    drive the private helper directly with a node id absent from the graph.
    """
    from minimax_code.dagre.layout.rank.util import _longest_path_dfs

    g = _make_graph()
    visited: OrderedHashMap = OrderedHashMap()
    # "ghost" is absent: g.node -> None, g.out_edges -> None -> [].
    assert _longest_path_dfs("ghost", g, visited) == 0
    assert "ghost" in visited  # still marked visited


# === slack ===============================================================


def test_slack_tight_edge_is_zero() -> None:
    """An edge whose rank span exactly matches its minlen has slack 0.

    ``a -> b`` with default minlen 1, a on rank -1, b on rank 0: the span
    (0 - (-1) = 1) equals the minlen, so slack is 0 (a tight edge -- the
    target ``network_simplex`` / ``feasible_tree`` loops drive edges toward).
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=-1))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    assert slack(g, Edge("a", "b")) == 0  # 0 - (-1) - 1


def test_slack_positive_for_loose_edge() -> None:
    """An edge whose rank span exceeds its minlen has positive slack."""
    g = _make_graph()
    g.set_node("a", GraphNode(rank=-3))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    assert slack(g, Edge("a", "b")) == 2  # 0 - (-3) - 1


def test_slack_negative_when_edge_too_short() -> None:
    """An edge whose rank span is below its minlen has negative slack.

    Here a sits above b (rank 5 -> 0) yet the edge demands minlen 1, so the
    span runs the wrong way: slack = 0 - 5 - 1 = -6.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=5))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(), None)
    assert slack(g, Edge("a", "b")) == -6


def test_slack_minlen_none_uses_ten() -> None:
    """An edge label carrying ``minlen = None`` resolves to 10 in slack.

    grok: ``edge.minlen.unwrap_or(10.0).round()``. NOTE the asymmetric
    default: :func:`longest_path` uses 0 for a ``None`` minlen, but
    :func:`slack` uses 10 -- faithfully reproduced. So ``a -> b`` with
    ``minlen = None`` and both endpoints on rank 0 yields slack -10.
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    g.set_edge("a", "b", GraphEdge(minlen=None), None)
    assert slack(g, Edge("a", "b")) == -10  # 0 - 0 - 10 (asymmetric default)


def test_slack_missing_edge_label_uses_default_minlen() -> None:
    """A stripped / absent edge label resolves to default minlen 1.0 -> 1.

    Passing an :class:`Edge` whose ``(v, w)`` is not in the graph makes
    ``edge_with_obj`` return ``None``, hitting the same default-label path
    as :func:`longest_path` (``GraphEdge::default().minlen == Some(1.0)`` ->
    ``round(1.0) == 1``).
    """
    g = _make_graph()
    g.set_node("a", GraphNode(rank=0))
    g.set_node("b", GraphNode(rank=0))
    # No a -> b edge exists -> edge_with_obj returns None.
    assert slack(g, Edge("a", "b")) == -1  # 0 - 0 - 1 (missing label -> 1)


def test_slack_missing_node_rank_uses_zero() -> None:
    """An endpoint whose ``rank`` is ``None`` contributes 0 to the slack.

    A freshly minted ``GraphNode()`` has ``rank = None``; grok's
    ``node.rank.unwrap_or(0)`` treats that as 0. With both endpoints
    rank-less and a default-minlen edge, slack is ``0 - 0 - 1 = -1``.
    """
    g = _make_graph()
    g.set_node("a", GraphNode())  # rank defaults to None
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    assert g.node("a").rank is None
    assert g.node("b").rank is None
    assert slack(g, Edge("a", "b")) == -1  # 0 - 0 - 1


# === barrel surface contract =============================================


def test_rank_util_not_in_dagre_all() -> None:
    """Neither ``longest_path`` nor ``slack`` earns a crate-root barrel slot."""
    assert "longest_path" not in dagre.__all__
    assert "slack" not in dagre.__all__
    assert "util" not in dagre.__all__


def test_rank_util_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "longest_path")
    assert not hasattr(dagre, "slack")


def test_rank_util_submodule_exports_longest_path_and_slack() -> None:
    """The submodule exposes exactly ``longest_path`` / ``slack`` (ASCII-sorted)."""
    import minimax_code.dagre.layout.rank.util as util_mod

    assert util_mod.__all__ == ["longest_path", "slack"]
    assert util_mod.longest_path is longest_path
    assert util_mod.slack is slack


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R254 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_rank_subpackage_barrel_reexports_util() -> None:
    """The ``rank`` sub-package barrel re-exports the ``util`` submodule.

    Mirrors grok's ``pub mod util`` visibility: ``rank/__init__.py`` exposes
    ``util`` and nothing else, so ``from minimax_code.dagre.layout.rank
    import util`` resolves once ``rank::mod`` is wired in a later leaf.
    """
    import minimax_code.dagre.layout.rank as rank_pkg
    import minimax_code.dagre.layout.rank.util as util_mod

    assert rank_pkg.__all__ == ["util"]
    assert rank_pkg.util is util_mod


def test_rank_util_reachable_via_layout_rank() -> None:
    """Importing the ``rank`` sub-package binds it on ``dagre.layout``.

    The barrel does NOT auto-import ``rank`` (it lands only once
    ``rank::mod`` is migrated); an explicit import binds the sub-package and
    its ``util`` leaf on ``dagre.layout``, paralleling the R253
    ``nesting_graph`` decision.
    """
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.rank as rank_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.rank is rank_pkg
    assert rank_pkg.util.longest_path is longest_path
    assert rank_pkg.util.slack is slack
