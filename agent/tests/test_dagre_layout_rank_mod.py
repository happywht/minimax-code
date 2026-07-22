"""Black-box tests for the migrated dagre rank/mod dispatcher (R257).

Exercises :mod:`minimax_code.dagre.layout.rank.mod` through its single
public entry point :func:`rank` plus the private ``_tight_tree_ranker``
helper on a real ``Graph<GraphConfig, GraphNode, GraphEdge>`` (the state
``nesting_graph::run`` leaves behind, since ``run_layout`` calls ``rank``
immediately after the nesting scaffolding is erected). Covers:

* :func:`rank` dispatch via monkeypatch spies -- ``None`` ->
  network_simplex (the default), ``"network-simplex"`` -> network_simplex,
  ``"tight-tree"`` -> longest_path + feasible_tree, ``"longest-path"`` ->
  longest_path, and an unrecognised string -> silent no-op (the
  ``Some(ranker)`` arm runs but no ``if`` clause matches),
* :func:`rank` end-to-end on a linear chain through every strategy
  (default / network-simplex / longest-path / tight-tree all yield
  a=-2, b=-1, c=0 since the chain is already optimal -- network_simplex
  finds no negative-cut tree edge, feasible_tree finds a fully-tight
  spanning tree), the no-op leaving ranks unset, and the ``None`` return
  contract,
* the private ``_tight_tree_ranker`` helper (longest_path + feasible_tree
  on a linear chain),
* the barrel surface contract (``rank`` stays out of ``dagre.__all__``;
  crate-root barrel count unchanged at 4; the ``rank`` sub-package
  re-exports ``mod`` alongside ``feasible_tree`` / ``network_simplex`` /
  ``util``; ``mod.__all__`` is ASCII-sorted; ``rank`` is reachable via
  ``dagre.layout.rank.mod``).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
import minimax_code.dagre.layout.rank.mod as rank_mod
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.rank.mod import rank
from minimax_code.data_structures import Graph, GraphOption


def _make_graph(ranker: str | None = None) -> Graph:
    """Build an empty directed graph seeded with ``GraphConfig(ranker)``.

    Mirrors the production config ``rank`` runs on: ``compound=True`` +
    ``multigraph=True`` (the ``nesting_graph::run`` output). ``rank`` reads
    only ``g.graph().ranker`` and delegates; the compound + GraphConfig
    dressing keeps the fixture faithful to the real pipeline.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=True),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig(ranker=ranker))
    return g


def _make_spy(calls: list[str], tag: str):
    """Build a no-op spy that records ``tag`` (in order) when invoked.

    Stands in for a strategy function under :func:`rank` so the dispatch
    path is observable without depending on the strategy's output. A named
    closure (not a ``lambda``) avoids the E731 / B023 traps.
    """

    def spy(_g: Graph) -> None:
        calls.append(tag)

    return spy


def _linear_chain(g: Graph) -> Graph:
    """Wire a ``-> b -> c`` chain with default-minlen edges on ``g``."""
    for node_id in ("a", "b", "c"):
        g.set_node(node_id, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    return g


# === rank: dispatch (monkeypatch spy) ====================================


def test_rank_default_none_dispatches_to_network_simplex(monkeypatch) -> None:
    """``ranker=None`` routes to network_simplex (grok's ``_ =>`` arm)."""
    calls: list[str] = []
    monkeypatch.setattr(rank_mod, "network_simplex", _make_spy(calls, "ns"))
    monkeypatch.setattr(rank_mod, "longest_path", _make_spy(calls, "lp"))
    monkeypatch.setattr(rank_mod, "feasible_tree", _make_spy(calls, "ft"))
    g = _make_graph(ranker=None)
    rank(g)
    assert calls == ["ns"]


def test_rank_explicit_network_simplex(monkeypatch) -> None:
    """``ranker="network-simplex"`` routes to network_simplex (explicit)."""
    calls: list[str] = []
    monkeypatch.setattr(rank_mod, "network_simplex", _make_spy(calls, "ns"))
    monkeypatch.setattr(rank_mod, "longest_path", _make_spy(calls, "lp"))
    monkeypatch.setattr(rank_mod, "feasible_tree", _make_spy(calls, "ft"))
    g = _make_graph(ranker="network-simplex")
    rank(g)
    assert calls == ["ns"]


def test_rank_tight_tree_dispatches_to_longest_path_then_feasible_tree(monkeypatch) -> None:
    """``ranker="tight-tree"`` routes to _tight_tree_ranker (lp then ft)."""
    calls: list[str] = []
    monkeypatch.setattr(rank_mod, "network_simplex", _make_spy(calls, "ns"))
    monkeypatch.setattr(rank_mod, "longest_path", _make_spy(calls, "lp"))
    monkeypatch.setattr(rank_mod, "feasible_tree", _make_spy(calls, "ft"))
    g = _make_graph(ranker="tight-tree")
    rank(g)
    assert calls == ["lp", "ft"]


def test_rank_longest_path_dispatches_to_longest_path(monkeypatch) -> None:
    """``ranker="longest-path"`` routes to longest_path alone."""
    calls: list[str] = []
    monkeypatch.setattr(rank_mod, "network_simplex", _make_spy(calls, "ns"))
    monkeypatch.setattr(rank_mod, "longest_path", _make_spy(calls, "lp"))
    monkeypatch.setattr(rank_mod, "feasible_tree", _make_spy(calls, "ft"))
    g = _make_graph(ranker="longest-path")
    rank(g)
    assert calls == ["lp"]


def test_rank_unknown_ranker_is_noop(monkeypatch) -> None:
    """An unrecognised ranker string triggers no strategy (grok faithful).

    grok's ``Some(ranker)`` arm runs but none of the ``if ranker_str == ...``
    clauses match, so the graph is left untouched. ``rank`` returns without
    invoking any strategy.
    """
    calls: list[str] = []
    monkeypatch.setattr(rank_mod, "network_simplex", _make_spy(calls, "ns"))
    monkeypatch.setattr(rank_mod, "longest_path", _make_spy(calls, "lp"))
    monkeypatch.setattr(rank_mod, "feasible_tree", _make_spy(calls, "ft"))
    g = _make_graph(ranker="bogus")
    rank(g)
    assert calls == []


# === rank: end-to-end (real strategies) ==================================


def test_rank_default_ranks_linear_chain() -> None:
    """Default (None -> network-simplex) ranks a chain a=-2, b=-1, c=0."""
    g = _make_graph(ranker=None)
    _linear_chain(g)
    rank(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_rank_network_simplex_strategy_ranks_chain() -> None:
    """Explicit ``"network-simplex"`` ranks a chain a=-2, b=-1, c=0."""
    g = _make_graph(ranker="network-simplex")
    _linear_chain(g)
    rank(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_rank_longest_path_strategy_ranks_chain() -> None:
    """``"longest-path"`` ranks a chain a=-2, b=-1, c=0 (negative, not normalized)."""
    g = _make_graph(ranker="longest-path")
    _linear_chain(g)
    rank(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_rank_tight_tree_strategy_ranks_chain() -> None:
    """``"tight-tree"`` ranks a chain a=-2, b=-1, c=0 (lp then feasible_tree)."""
    g = _make_graph(ranker="tight-tree")
    _linear_chain(g)
    rank(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


def test_rank_unknown_ranker_leaves_ranks_unset() -> None:
    """An unrecognised ranker string leaves node ranks at ``None`` (no-op)."""
    g = _make_graph(ranker="bogus")
    _linear_chain(g)
    rank(g)
    assert g.node("a").rank is None
    assert g.node("b").rank is None
    assert g.node("c").rank is None


def test_rank_returns_none() -> None:
    """``rank`` mutates in place and returns ``None`` (grok ``fn rank`` unit)."""
    g = _make_graph(ranker="longest-path")
    g.set_node("a", GraphNode())
    assert rank(g) is None


# === _tight_tree_ranker ==================================================


def test_tight_tree_ranker_runs_longest_path_then_feasible_tree() -> None:
    """``_tight_tree_ranker`` seeds ranks with longest_path then feasible_tree.

    On a fully-tight linear chain (longest_path output), feasible_tree's
    spanning tree covers the whole graph in one pass and ``_shift_ranks``
    never fires, so the longest_path ranks survive unchanged: a=-2, b=-1,
    c=0.
    """
    from minimax_code.dagre.layout.rank.mod import _tight_tree_ranker

    g = _make_graph()
    _linear_chain(g)
    _tight_tree_ranker(g)
    assert g.node("a").rank == -2
    assert g.node("b").rank == -1
    assert g.node("c").rank == 0


# === barrel surface contract =============================================


def test_rank_not_in_dagre_all() -> None:
    """``rank`` earns no crate-root barrel slot (run_layout-internal only)."""
    assert "rank" not in dagre.__all__


def test_rank_not_reachable_at_dagre_top_level() -> None:
    """The symbol is not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "rank")


def test_rank_mod_submodule_exports_rank() -> None:
    """The submodule exposes exactly ``rank`` (ASCII-sorted singleton)."""
    import minimax_code.dagre.layout.rank.mod as mod

    assert mod.__all__ == ["rank"]
    assert mod.rank is rank


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R257 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_rank_subpackage_barrel_reexports_mod() -> None:
    """The ``rank`` barrel re-exports ``mod`` alongside the three strategies.

    Mirrors grok's four-file ``rank`` directory (feasible_tree /
    network_simplex / util / mod). The grok ``mod.rs`` dispatcher is split
    into ``__init__.py`` (barrel) + ``mod.py`` (logic) on the Python side;
    the barrel re-exports all four submodules. ``__all__`` is ASCII-sorted
    (``feasible_tree`` < ``mod`` < ``network_simplex`` < ``util``).
    """
    import minimax_code.dagre.layout.rank as rank_pkg
    import minimax_code.dagre.layout.rank.feasible_tree as ft_mod
    import minimax_code.dagre.layout.rank.mod as mod
    import minimax_code.dagre.layout.rank.network_simplex as ns_mod
    import minimax_code.dagre.layout.rank.util as util_mod

    assert rank_pkg.__all__ == ["feasible_tree", "mod", "network_simplex", "util"]
    assert rank_pkg.feasible_tree is ft_mod
    assert rank_pkg.mod is mod
    assert rank_pkg.network_simplex is ns_mod
    assert rank_pkg.util is util_mod


def test_rank_reachable_via_layout_rank() -> None:
    """Importing the ``rank`` sub-package binds it on ``dagre.layout``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.rank as rank_pkg

    assert dagre.layout is layout_pkg
    assert layout_pkg.rank is rank_pkg
    assert rank_pkg.mod.rank is rank
