"""dagre layout submodule barrel (R247+, vendored ``third_party/dagre_rust``).

Mirrors the ``layout`` module of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the layered graph layout
pipeline that drives the mermaid render path. grok's ``layout/mod.rs`` declares
each algorithm stage as a ``pub mod`` (``coordinate_system`` / ``order`` /
``rank`` / ``position`` / ``add_border_segments`` / ``acyclic`` /
``nesting_graph`` / ``parent_dummy_chains`` / ``normalize`` / ``util``) and
orchestrates them in ``run_layout``; the stage symbols are NOT re-exported at
the crate root (``lib.rs`` has no ``pub use layout::*``).

This package is the natural downstream of the R246 type foundation: every
``layout/*`` algorithm operates on ``Graph<GraphConfig, GraphNode, GraphEdge>``
and mutates the R246 structs in place (``node_mut`` writes ``x`` / ``y`` /
``rank`` / ``order``; ``edge_mut_with_obj`` writes ``points`` / ``x`` / ``y``).
Leaves migrate stage-by-stage; the ``run_layout`` orchestrator itself lands
only after every stage it calls is in place.

First layout leaf (R247): ``coordinate_system`` -- the rank-direction
``adjust`` / ``undo`` transforms applied around the position phase. The barrel
does NOT re-export the stage symbols (mirroring grok's ``pub mod``-only
visibility); reach them as ``minimax_code.dagre.layout.coordinate_system.adjust``
etc., paralleling the R245 ``algo`` decision (public submodule, crate-root-
private symbols).

Second layout leaf (R248): ``util`` -- the foundational grab-bag of pure
helpers (``unique_id`` / ``add_dummy_node`` / ``add_border_node`` / ``simplify``
/ ``simplify_ref`` / ``as_non_compound_graph`` / ``transfer_node_edge_labels``
/ ``intersect_rect`` / ``build_layer_matrix`` / ``normalize_ranks`` /
``remove_empty_ranks`` / ``max_rank`` / ``partition`` + the ``Rect`` /
``PartitionResponse`` structs) that every later ``layout/*`` stage is built on
and that ``run_layout`` imports six symbols from directly. Same barrel policy:
the 15 symbols stay out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.util.<symbol>``.

Third layout leaf (R249): ``add_border_segments`` -- the first stage that
mutates compound-graph *structure* (every prior leaf only read or reshaped
flat nodes/edges). For each compound node spanning a rank range (``min_rank``
set), injects a pair of ``_bl`` (border-left) / ``_br`` (border-right) sentinel
``border`` dummies per rank in ``[min_rank, max_rank + 1)``, chained
top-to-bottom with weight-1 edges, so the positioner can pad a subgraph's
rank span. It is the **first consumer of the R246 ``BorderTypeName`` enum**
(identity-dispatched via ``is``) and reuses the R248 ``add_dummy_node`` helper
to mint the sentinels; its private ``_add_border_node`` is deliberately
distinct from ``util.add_border_node`` (the ``_`` prefix keeps both in scope
collision-free, since grok nests its private helper inside the same file).
``run_layout`` calls it between ``parent_dummy_chains`` and ``order``
(``layout/mod.rs`` line 631). Same barrel policy: the stage symbol stays out
of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.add_border_segments``.
"""
