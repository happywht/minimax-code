"""Acyclic feedback-arc-set reversal (R251, vendored ``third_party/dagre_rust``).

Mirrors ``layout/acyclic.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). Breaks every cycle in the
input graph by reversing its back-edges -- the feedback arc set (FAS) discovered
via DFS -- stamping ``forward_name`` + ``reversed=True`` on each reversed edge
label so :func:`undo` can flip it back. ``run_layout`` calls this as the
**first** stage of the pipeline (``layout/mod.rs`` line 616 ``acyclic::run``,
before ``nesting_graph`` / ``rank`` / ``normalize``) and undoes it as the
**last** (line 644 ``acyclic::undo``, after ``coordinate_system::undo`` /
``normalize::undo``): DAG-ification must precede ranking (a cycle has no valid
rank assignment), and the reversal must outlive every later coordinate
mutation so the final edge directions match the original input.

Pre-conditions: any directed graph (cycles allowed). Post-conditions: the graph
is a DAG -- every back-edge is *reversed* (never removed, only re-pointed
``w -> v`` under a fresh ``rev{id}`` name, with the original name stashed on
``forward_name``). The ``greedy`` acyclicer branch is a grok TODO
(``greedyFAS`` unimplemented, an upstream ``println!("greedy_fas")``
placeholder); it leaves the graph untouched -- the FAS stays empty -- which
this port faithfully reproduces.

This is the fifth layout-stage leaf (R247 ``coordinate_system`` -> R248
``util`` -> R249 ``add_border_segments`` -> R250 ``normalize`` -> R251
``acyclic``) and the second ``run`` / ``undo`` pair after R250 ``normalize``
-- the first pair whose ``run`` mutates edge *directions* rather than splitting
edges into dummy chains, and the outermost bracket in ``run_layout`` (the first
stage called, the last undone). It reuses the R248
:func:`~minimax_code.dagre.layout.util.unique_id` monotonic counter to mint
distinct ``rev{id}`` names on reversed edges (so a reversed edge never collides
with its forward sibling in the multigraph) and mutates the live graph in
place.

Borrow-checker clone artefacts
------------------------------
Two clones survive the port because they carry semantic weight, not just Rust
ownership:

* ``edge_label = copy.deepcopy(_edge_label)`` in :func:`run` before
  :meth:`~minimax_code.data_structures.graphlib.Graph.remove_edge_with_obj` --
  the in-graph label reference is invalidated by the removal, so the label that
  gets re-attached to the reversed edge must be captured first (grok
  ``_edge_label.cloned().unwrap()``).
* ``label = copy.deepcopy(edge)`` in :func:`undo` -- the reversed edge label is
  mutated (``reversed`` / ``forward_name`` cleared) before being re-attached to
  the restored forward edge; the deep copy keeps the in-graph reversed label
  pristine, because grok does NOT remove the reversed edge -- it only mints a
  fresh forward one (undo is additive on edges, a faithful port of upstream
  behaviour).

Where grok clones purely for ownership -- ``edge.name.clone()`` (the
``forward_name`` assignment in :func:`run`), ``edge.forward_name.clone()`` (the
restored-edge name in :func:`undo`), ``fas.push(edge.clone())`` (DFS back-edge
collection in :func:`_dfs`), and the ``node_id.clone()`` / ``edge.w.clone()``
DFS recursion args -- Python shares the live reference: ``str`` is immutable,
and a list-append shares the ``Edge`` value whose later graph-side removal never
mutates the ``Edge`` struct itself (it is a frozen snapshot; mirrors the R248 /
R250 borrow-checker-clone-stripping decision).
"""

from __future__ import annotations

import copy

from minimax_code.dagre.layout.util import unique_id
from minimax_code.data_structures.graphlib import Edge, Graph
from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

__all__ = ["run", "undo"]


def run(graph: Graph) -> None:
    """Break every cycle by reversing back-edges (mirrors grok ``acyclic::run``).

    Selects the feedback arc set via DFS (or, when the graph config's
    ``acyclicer == "greedy"``, the unimplemented greedy branch -- a grok TODO
    that yields an empty FAS), then for every back-edge captures its label,
    removes the edge, stamps ``forward_name`` + ``reversed=True`` on the label,
    and re-creates the edge reversed (``w -> v``) under a fresh ``rev{id}``
    name. Mutates ``graph`` in place (no return).
    """
    graph_config = graph.graph()
    assert graph_config is not None  # grok: graph.graph() (graph always present)
    if graph_config.acyclicer is not None and graph_config.acyclicer == "greedy":
        # TODO: grok leaves ``greedyFAS`` unimplemented (an upstream
        # ``println!("greedy_fas")`` placeholder); the FAS stays empty, so no
        # edge is reversed and the graph is left untouched.
        fas: list[Edge] = []
    else:
        fas = _dfs_fas(graph)

    for edge in fas:
        _edge_label = graph.edge_with_obj(edge)
        if _edge_label is None:
            continue
        # grok: ``let mut edge_label = _edge_label.cloned().unwrap()`` -- a deep
        # clone because ``remove_edge_with_obj`` below invalidates the in-graph
        # label reference; the label must survive to be re-attached reversed.
        edge_label = copy.deepcopy(_edge_label)
        graph.remove_edge_with_obj(edge)
        # grok: ``edge.name.clone()`` is a borrow-checker artefact; Python ``str``
        # is immutable, so the live reference is reused as the forward name.
        edge_label.forward_name = edge.name
        edge_label.reversed = True
        graph.set_edge(
            edge.w,
            edge.v,
            edge_label,
            f"rev{unique_id()}",
        )


def _dfs_fas(graph: Graph) -> list[Edge]:
    """Discover the feedback arc set via DFS (private, mirrors grok ``dfs_fas``).

    Walks every node; a back-edge is one whose target (``edge.w``) is on the
    current DFS stack (i.e. an ancestor in the active traversal -- it closes a
    cycle). Each back-edge is collected into the FAS; non-back edges are
    recursed into. The on-stack marker is popped on the way back up so only the
    current root-to-node path counts as "on stack".
    """
    fas: list[Edge] = []
    stack: OrderedHashMap[str, bool] = OrderedHashMap()
    visited: OrderedHashMap[str, bool] = OrderedHashMap()
    for node_id in graph.nodes():
        _dfs(node_id, stack, visited, graph, fas)
    return fas


def _dfs(
    node_id: str,
    stack: OrderedHashMap[str, bool],
    visited: OrderedHashMap[str, bool],
    graph: Graph,
    fas: list[Edge],
) -> None:
    """Depth-first walk collecting back-edges (private, mirrors grok nested ``dfs``).

    grok nests ``dfs`` inside ``dfs_fas`` as a Rust nested ``fn`` (no closure
    capture, every parameter passed explicitly); Python hoists it to module
    scope as a private helper with the same explicit-arg signature, paralleling
    the R249 ``add_border_segments`` private ``_dfs`` decision.
    """
    if node_id in visited:
        return
    visited.insert(node_id, True)
    stack.insert(node_id, True)
    out_edges = graph.out_edges(node_id, None) or []
    for edge in out_edges:
        if edge.w in stack:
            # grok: ``fas.push(edge.clone())`` is a borrow-checker artefact; the
            # Python list-append shares the edge object, whose later graph-side
            # removal never mutates the frozen ``Edge`` value (a plain snapshot).
            fas.append(edge)
        else:
            _dfs(edge.w, stack, visited, graph, fas)
    stack.remove(node_id)


def undo(g: Graph) -> None:
    """Reverse every back-edge flip back to its original direction (mirrors grok).

    Walks every edge; for each whose label carries ``reversed == True``, clones
    the label, clears ``reversed`` / ``forward_name``, and re-creates the edge
    in its original direction (``w -> v``) under the saved ``forward_name``.
    The reversed edge itself is NOT removed (grok only mints the restored
    forward edge), so undo is additive on edges -- a faithful port of upstream
    behaviour. Mutates ``g`` in place (no return).
    """
    for e in g.edges():
        edge = g.edge_mut_with_obj(e)
        if edge is None:
            continue
        # grok: ``edge.reversed.clone().unwrap_or(false)`` -- ``bool | None``'s
        # truthy semantics are exactly ``unwrap_or(false)`` (None -> False), so a
        # bare truthiness test is the faithful port.
        if edge.reversed:
            # grok: ``forward_name.clone()`` is a borrow-checker artefact; Python
            # ``str`` is immutable, so the live reference is reused as the name.
            forward_name = edge.forward_name
            # grok: ``let mut label = edge.clone()`` -- a deep clone because the
            # reversed edge label is mutated (reversed / forward_name cleared)
            # before being re-attached to the restored forward edge; the in-graph
            # reversed label must stay pristine (grok does NOT remove it).
            label = copy.deepcopy(edge)
            label.reversed = None
            label.forward_name = None
            g.set_edge(e.w, e.v, label, forward_name)
