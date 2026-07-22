"""Black-box tests for the migrated dagre acyclic layer (R251).

Exercises :mod:`minimax_code.dagre.layout.acyclic` purely through the two
public entry points (``run`` / ``undo``) on a real
``Graph<GraphConfig, GraphNode, GraphEdge>`` flat directed graph. Covers:

* three-node cycle breaking (the back-edge is reversed, the graph becomes a
  DAG),
* two-node cycle (the back-edge is reversed alongside a kept forward edge),
* the acyclic-graph no-op (no back-edges -> empty FAS -> nothing reversed),
* the ``greedy`` acyclicer TODO branch (``greedyFAS`` unimplemented -> empty
  FAS -> graph untouched, cycle preserved),
* back-edge detection via the DFS on-stack marker (an edge whose target is an
  ancestor on the active traversal path),
* the ``reversed=True`` stamp on every reversed edge label,
* ``forward_name`` preserving the original edge name across the reversal,
* the ``rev{id}`` name prefix and per-edge uniqueness (R248 ``unique_id``),
* the reversed edge direction (``w -> v``, i.e. the original ``v -> w``
  flipped),
* ``undo`` restoring the original direction under ``forward_name``,
* ``undo`` NOT removing the reversed edge (grok is additive on edges -- it
  only mints the restored forward one, a faithful port),
* ``undo`` leaving the in-graph reversed label untouched (``reversed`` stays
  ``True``; only the deep-cloned restored copy is cleared),
* ``undo`` no-op on an acyclic graph (no ``reversed`` labels),
* ``run`` idempotence on a DAG (a second pass reverses nothing),
* in-place mutation,
* the barrel surface contract (``run`` / ``undo`` stay out of
  ``dagre.__all__``; crate-root barrel count unchanged at 4).
"""

from __future__ import annotations

import minimax_code.dagre as dagre
from minimax_code.dagre import GraphConfig, GraphEdge, GraphNode
from minimax_code.dagre.layout.acyclic import run, undo
from minimax_code.data_structures import Graph, GraphOption


def _make_graph() -> Graph:
    """Build an empty flat directed graph with a ``GraphConfig`` label.

    ``multigraph=True`` because acyclic may mint a reversed edge (``rev{id}``)
    alongside a same-direction sibling (a two-node cycle reverses ``B->A`` into
    ``A->B`` while the original ``A->B`` stays); ``compound=False`` because
    acyclic runs before ``nesting_graph`` on the flat input graph.
    """
    g: Graph = Graph(
        GraphOption(directed=True, multigraph=True, compound=False),
        node_default_factory=GraphNode,
        edge_default_factory=GraphEdge,
    )
    g.set_graph(GraphConfig())
    return g


def _reversed_edges(g: Graph) -> list[tuple[object, GraphEdge]]:
    """Collect every edge whose in-graph label carries ``reversed == True``.

    Returns ``(edge_obj, label)`` pairs so callers can assert on both the
    structural ``Edge`` (``v`` / ``w`` / ``name``) and the label payload
    (``forward_name`` / ``reversed``). The label is the live in-graph object,
    not a copy -- callers that mutate should deepcopy first.
    """
    out: list[tuple[object, GraphEdge]] = []
    for e in g.edges():
        label = g.edge_with_obj(e)
        if label is not None and label.reversed:
            out.append((e, label))
    return out


def _make_three_cycle(g: Graph, name: str | None = None) -> None:
    """Wire ``a -> b -> c -> a`` (a three-node cycle) with an optional edge name.

    The name is applied to the ``c -> a`` edge only -- that is the back-edge
    the DFS discovers (``c`` recurses last, finds ``a`` on its stack), so its
    name lands on ``forward_name`` after the reversal.
    """
    for nid in ("a", "b", "c"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    g.set_edge("c", "a", GraphEdge(), name)


# === cycle breaking ========================================================


def test_run_breaks_three_node_cycle() -> None:
    """``a -> b -> c -> a`` reverses the ``c -> a`` back-edge into ``a -> c``."""
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    assert g.has_edge("a", "b", None)
    assert g.has_edge("b", "c", None)
    assert not g.has_edge("c", "a", None)  # original back-edge removed
    rev = _reversed_edges(g)
    assert len(rev) == 1
    edge, _label = rev[0]
    assert edge.v == "a" and edge.w == "c"  # reversed direction (w -> v)


def test_run_leaves_acyclic_graph_untouched() -> None:
    """An acyclic graph has an empty FAS -> nothing reversed."""
    g = _make_graph()
    for nid in ("a", "b", "c"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    run(g)
    assert g.has_edge("a", "b", None)
    assert g.has_edge("b", "c", None)
    assert _reversed_edges(g) == []


def test_two_cycle_reverses_one_back_edge() -> None:
    """``a -> b`` + ``b -> a`` reverses ``b -> a`` into a second ``a -> b``."""
    g = _make_graph()
    for nid in ("a", "b"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "a", GraphEdge(), None)
    run(g)
    assert g.has_edge("a", "b", None)  # original forward edge kept
    assert not g.has_edge("b", "a", None)  # back-edge removed
    rev = _reversed_edges(g)
    assert len(rev) == 1
    edge, _label = rev[0]
    assert edge.v == "a" and edge.w == "b"  # reversed into a -> b


# === greedy acyclicer TODO branch ==========================================


def test_greedy_acyclicer_is_noop_todo() -> None:
    """``acyclicer == "greedy"`` yields an empty FAS (greedyFAS unimplemented)."""
    g = _make_graph()
    g.graph().acyclicer = "greedy"
    for nid in ("a", "b"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "a", GraphEdge(), None)  # cycle
    run(g)
    # greedy FAS unimplemented -> empty -> no reversal -> cycle preserved
    assert g.has_edge("a", "b", None)
    assert g.has_edge("b", "a", None)
    assert _reversed_edges(g) == []


# === back-edge DFS detection ===============================================


def test_back_edge_detected_via_on_stack_marker() -> None:
    """A self-closing edge (target on the active DFS stack) is the back-edge.

    In ``a -> b -> c -> a`` the DFS reaches ``c`` last; ``c``'s only out-edge
    targets ``a``, which is on the stack (an ancestor), so exactly that edge is
    collected -- not ``a -> b`` or ``b -> c``.
    """
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    edge, _label = rev[0]
    # only the c -> a edge is reversed; a -> b and b -> c are left as-is
    assert (edge.v, edge.w) == ("a", "c")


# === reversed-edge label stamps ============================================


def test_reversed_edge_marked_true() -> None:
    """Every reversed edge label carries ``reversed == True``."""
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    _edge, label = rev[0]
    assert label.reversed is True


def test_forward_name_preserves_original_name() -> None:
    """The reversed edge's ``forward_name`` stashes the original edge name."""
    g = _make_graph()
    _make_three_cycle(g, name="original")
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    _edge, label = rev[0]
    assert label.forward_name == "original"


def test_forward_name_none_when_original_unnamed() -> None:
    """An unnamed original edge yields ``forward_name is None``."""
    g = _make_graph()
    _make_three_cycle(g, name=None)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    _edge, label = rev[0]
    assert label.forward_name is None


def test_reversed_edge_name_starts_with_rev() -> None:
    """The reversed edge is minted under a ``rev{id}`` name (R248 unique_id)."""
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    edge, _label = rev[0]
    assert edge.name is not None
    assert edge.name.startswith("rev")


def test_reversed_edge_names_are_unique() -> None:
    """Two disjoint cycles yield two reversed edges with distinct ``rev{id}`` names."""
    g = _make_graph()
    # cycle 1: a -> b -> a
    g.set_node("a", GraphNode())
    g.set_node("b", GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "a", GraphEdge(), None)
    # cycle 2: c -> d -> c
    g.set_node("c", GraphNode())
    g.set_node("d", GraphNode())
    g.set_edge("c", "d", GraphEdge(), None)
    g.set_edge("d", "c", GraphEdge(), None)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 2
    names = {edge.name for edge, _label in rev}
    assert len(names) == 2  # distinct rev{id} per reversed edge
    assert all(n is not None and n.startswith("rev") for n in names)


# === reversed-edge direction ==============================================


def test_reversed_edge_direction_is_w_to_v() -> None:
    """The reversed edge points ``edge.w -> edge.v`` (the original flipped)."""
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    edge, _label = rev[0]
    # original c -> a (v=c, w=a) is re-created as a -> c (v=a, w=c)
    assert edge.v == "a"
    assert edge.w == "c"


# === undo round-trip =======================================================


def test_undo_restores_original_direction() -> None:
    """``undo`` re-creates the original-direction edge under ``forward_name``."""
    g = _make_graph()
    _make_three_cycle(g, name="original")
    run(g)
    undo(g)
    # restored c -> a edge under the saved forward_name
    restored = g.edge("c", "a", "original")
    assert restored is not None
    assert restored.reversed is None
    assert restored.forward_name is None


def test_undo_restores_unnamed_edge() -> None:
    """``undo`` restores an unnamed edge under a ``None`` name."""
    g = _make_graph()
    _make_three_cycle(g, name=None)
    run(g)
    undo(g)
    restored = g.edge("c", "a", None)
    assert restored is not None
    assert restored.reversed is None


def test_undo_does_not_remove_reversed_edge() -> None:
    """``undo`` keeps the reversed edge (grok is additive on edges)."""
    g = _make_graph()
    _make_three_cycle(g)
    run(g)
    rev_before = _reversed_edges(g)
    assert len(rev_before) == 1
    rev_edge_before = rev_before[0][0]
    undo(g)
    # the very same reversed edge is still in the graph after undo
    assert g.edge_with_obj(rev_edge_before) is not None
    assert len(_reversed_edges(g)) == 1


def test_undo_leaves_reversed_label_untouched_in_graph() -> None:
    """``undo`` does not clear the in-graph reversed label (only the clone)."""
    g = _make_graph()
    _make_three_cycle(g, name="original")
    run(g)
    undo(g)
    rev = _reversed_edges(g)
    assert len(rev) == 1
    _edge, label = rev[0]
    # grok clones the label, clears reversed/forward_name on the clone, and
    # mints the restored edge from it -- the in-graph reversed label is never
    # mutated, so it still carries reversed=True + forward_name="original".
    assert label.reversed is True
    assert label.forward_name == "original"


def test_undo_noop_on_acyclic_graph() -> None:
    """``undo`` on a graph with no reversed labels is a no-op (must not raise)."""
    g = _make_graph()
    for nid in ("a", "b"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    undo(g)
    assert g.has_edge("a", "b", None)
    assert _reversed_edges(g) == []


# === idempotence + in-place mutation ======================================


def test_run_idempotent_on_dag() -> None:
    """Running ``run`` twice on a DAG reverses nothing either time."""
    g = _make_graph()
    for nid in ("a", "b", "c"):
        g.set_node(nid, GraphNode())
    g.set_edge("a", "b", GraphEdge(), None)
    g.set_edge("b", "c", GraphEdge(), None)
    run(g)
    first = _reversed_edges(g)
    run(g)  # second pass over the (still acyclic) graph
    second = _reversed_edges(g)
    assert first == []
    assert second == []


def test_run_mutates_graph_in_place() -> None:
    """``run`` mutates the live graph object (no copy/return)."""
    g = _make_graph()
    _make_three_cycle(g)
    assert _reversed_edges(g) == []
    run(g)
    assert len(_reversed_edges(g)) == 1  # same graph, now reversed


# === barrel surface contract ===============================================


def test_acyclic_not_in_dagre_all() -> None:
    """Neither ``run`` nor ``undo`` earns a crate-root barrel slot."""
    assert "run" not in dagre.__all__
    assert "undo" not in dagre.__all__
    assert "acyclic" not in dagre.__all__


def test_acyclic_not_reachable_at_dagre_top_level() -> None:
    """The symbols are not bound on the ``dagre`` package top level."""
    assert not hasattr(dagre, "acyclic")
    assert not hasattr(dagre, "run")
    assert not hasattr(dagre, "undo")


def test_submodule_exports_run_and_undo() -> None:
    """The submodule exposes exactly ``run`` / ``undo`` (the two ``pub fn``)."""
    import minimax_code.dagre.layout.acyclic as acyclic_mod

    assert acyclic_mod.__all__ == ["run", "undo"]
    assert acyclic_mod.run is run
    assert acyclic_mod.undo is undo


def test_dagre_barrel_count_unchanged_at_four() -> None:
    """R251 adds no crate-root barrel symbols; the count set by R246 stays at 4."""
    assert len(dagre.__all__) == 4


def test_layout_subpackage_reachable_via_acyclic_import() -> None:
    """Importing ``acyclic`` binds the ``layout`` subpackage on ``dagre``."""
    import minimax_code.dagre.layout as layout_pkg
    import minimax_code.dagre.layout.acyclic as acyclic_mod

    assert dagre.layout is layout_pkg
    assert layout_pkg.acyclic is acyclic_mod
