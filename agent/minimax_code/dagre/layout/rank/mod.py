"""Rank dispatcher (R257, vendored ``third_party/dagre_rust``).

Mirrors ``layout/rank/mod.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the single public
:func:`rank` entry point that ``run_layout`` calls at ``layout/mod.rs``
line 620, between ``nesting_graph::run`` (line 617) and
``nesting_graph::cleanup`` (line 625). ``rank`` reads
``GraphConfig.ranker`` and delegates to one of three strategies -- the
rank-layer assignment the layered layout pipeline runs after the compound
nesting scaffolding is erected and before it is torn down.

R257 is the **fourth and final ``rank`` leaf** (closes the ``rank::mod``
-> ``run_layout`` line 620 ``rank`` call chain) and the **eleventh layout
leaf** overall (R246 types + R247 ``coordinate_system`` + R248 ``util``
+ R249 ``add_border_segments`` + R250 ``normalize`` + R251 ``acyclic``
+ R252 ``parent_dummy_chains`` + R253 ``nesting_graph`` + R254
``rank/util`` + R255 ``rank/feasible_tree`` + R256 ``rank/network_simplex``
+ R257 ``rank/mod``). With R254 ``longest_path`` / R255 ``feasible_tree``
/ R256 ``network_simplex`` all in place, every strategy ``rank``
dispatches to is closed, so the dispatcher lands last -- a faithful mirror
of grok's file-by-file dependency order (``rank::mod`` imports all three
strategies and could not compile before them).

Dispatch policy (mirrors grok's ``match g.graph().ranker.clone()``):

* ``ranker is None`` (the manual-Default ``GraphConfig`` seed) ->
  ``network_simplex`` (grok's ``_ =>`` arm; the default ranker).
* ``"network-simplex"`` -> ``network_simplex`` (the explicit strategy).
* ``"tight-tree"`` -> :func:`_tight_tree_ranker` (``longest_path`` then
  ``feasible_tree``).
* ``"longest-path"`` -> ``longest_path`` alone (ranks stay negative and
  un-normalized; a later stage shifts them).
* any other ``Some(ranker)`` string -> silent no-op (grok faithful: the
  ``Some(ranker)`` arm runs but none of the ``if ranker_str == ...``
  clauses match, so nothing happens).

The ``None`` and ``"network-simplex"`` arms both call ``network_simplex``
but stay as two distinct branches to mirror grok's ``match`` shape 1:1
(the ``_`` arm and the ``Some("network-simplex")`` arm), preserving
source-diff readability alongside the R254 ``longest_path`` / ``slack``
asymmetric-default pattern.

The single grok ``.clone()`` (``g.graph().ranker.clone()``) is a B-class
ownership clone: it lifts the ``String`` out of the borrowed
``g.graph()`` reference so the ``&mut g`` can be passed to the strategy
afterward (a borrow-checker workaround, not a semantic copy). Python reads
the immutable ``str`` reference directly -- no clone survives.
"""

from __future__ import annotations

from minimax_code.dagre.layout.rank.feasible_tree import feasible_tree
from minimax_code.dagre.layout.rank.network_simplex import network_simplex
from minimax_code.dagre.layout.rank.util import longest_path
from minimax_code.data_structures import Graph

__all__ = ["rank"]


def rank(g: Graph) -> None:
    """Assign ranks to ``g`` via the configured ranker strategy.

    Reads ``g.graph().ranker`` (``GraphConfig.ranker: str | None``) and
    dispatches to one of the three migrated strategies. Mirrors grok's
    ``rank::rank`` match / if cascade: ``None`` defaults to
    ``network_simplex``, the three known strings each route to their named
    strategy, and an unrecognised string is a silent no-op (the
    ``Some(ranker)`` arm runs but no ``if`` clause matches). Mutates ``g``
    in place; returns ``None``.

    Args:
        g: The graph to rank. Assumes the graph label (``GraphConfig``) is
            set -- ``run_layout`` always calls ``set_graph`` first, so
            ``g.graph()`` is non-``None`` by the time ``rank`` runs (grok
            makes the same assumption; ``g.graph().ranker`` would panic on
            a label-less graph).
    """
    ranker = g.graph().ranker
    if ranker is None:
        network_simplex(g)
    elif ranker == "network-simplex":
        network_simplex(g)
    elif ranker == "tight-tree":
        _tight_tree_ranker(g)
    elif ranker == "longest-path":
        longest_path(g)
    # An unrecognised ranker string is a silent no-op (grok faithful):
    # the ``Some(ranker)`` arm runs but none of the ``if`` clauses match.


def _tight_tree_ranker(g: Graph) -> None:
    """Rank ``g`` with the tight-tree strategy (mirrors grok).

    The ``"tight-tree"`` ranker: seed ranks with ``longest_path``, then
    build the ``feasible_tree`` tight-edge spanning tree (which shifts
    ranks to make every spanning-tree edge slack 0). Mirrors grok's
    private ``tight_tree_ranker``. Mutates ``g`` in place; returns
    ``None``.

    Args:
        g: The graph to rank. Assumes the graph label is set and the graph
            is connected (the ``feasible_tree`` pre-condition -- a
            disconnected graph would loop forever on
            ``_find_min_slack_edge`` returning ``None``).
    """
    longest_path(g)
    feasible_tree(g)
