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

Fourth layout leaf (R250): ``normalize`` -- long-edge normalization, the first
stage with an explicit ``run`` / ``undo`` pair that ``run_layout`` brackets a
later phase with (``normalize::run`` at ``layout/mod.rs`` line 629 before
``add_border_segments`` / ``position`` / ``order``, ``normalize::undo`` at line
638 after). ``run`` breaks every edge whose endpoints span more than one rank
into a chain of unit-length segments joined by ``_d`` ``edge`` (or ``edge-label``
at the label rank) dummy nodes -- one per intermediate rank -- and records each
chain's head on ``GraphConfig.dummy_chains``; ``undo`` walks those heads back,
collapsing each chain and accumulating the positioned dummy coordinates as
waypoints (``points``) on the restored original edge. It is the **first
consumer of the R248 ``add_dummy_node`` helper in a structural-split context**
(R249 used it for border padding; R250 uses it for edge splitting) and mutates
the live graph in place. Two grok ``clone()`` calls survive the port with full
semantic weight -- ``_edge_label = deepcopy(edge_label)`` before
``remove_edge_with_obj`` (the in-graph label reference is invalidated by
removal) and ``node = deepcopy(node_)`` in ``undo`` (``remove_node`` invalidates
the node reference while the loop still reads its ``x`` / ``y``) -- while the
loop-internal ``attrs.edge_label = _edge_label`` and ``orig_label =
node.edge_label`` share the live reference (``add_dummy_node`` deep-clones
``attrs`` before storing, and ``node`` is already an independent deepcopy). Same
barrel policy: the stage symbols stay out of ``dagre.__all__``, reachable only
as ``minimax_code.dagre.layout.normalize.run`` / ``.undo``.

Fifth layout leaf (R251): ``acyclic`` -- feedback-arc-set reversal, the
outermost bracket in ``run_layout``: ``acyclic::run`` at ``layout/mod.rs`` line
616 is the **first** stage called (before ``nesting_graph`` / ``rank`` /
``normalize``), and ``acyclic::undo`` at line 644 is the **last** undone (after
``coordinate_system::undo`` / ``normalize::undo``). DAG-ification must precede
ranking (a cycle has no valid rank assignment), and the reversal must outlive
every later coordinate mutation so the final edge directions match the original
input. ``run`` discovers the feedback arc set via DFS -- a back-edge is one
whose target (``edge.w``) is on the active DFS stack (it closes a cycle) --
then for each back-edge captures its label, removes the edge, stamps
``forward_name`` + ``reversed=True``, and re-creates the edge reversed
(``w -> v``) under a fresh ``rev{id}`` name minted via the R248 ``unique_id``
counter. ``undo`` walks every edge and flips each ``reversed`` label back to
its original direction under the saved ``forward_name``; the reversed edge
itself is NOT removed (grok only mints the restored forward edge), so undo is
additive on edges -- a faithful port. The ``greedy`` acyclicer branch is a grok
TODO (``greedyFAS`` unimplemented) that yields an empty FAS and leaves the
graph untouched. Same barrel policy: the stage symbols stay out of
``dagre.__all__``, reachable only as ``minimax_code.dagre.layout.acyclic.run``
/ ``.undo``.

Sixth layout leaf (R252): ``parent_dummy_chains`` -- compound-forest parent
reassignment for long-edge dummies, the only stage in ``run_layout`` with a
single ``pub fn`` and no ``run`` / ``undo`` pair (the reassignment is permanent,
not bracketed and undone). ``run_layout`` calls it at ``layout/mod.rs`` line
630, immediately after ``normalize::run`` (line 629, which produces
``dummy_chains``) and before ``add_border_segments`` (line 631). For every
chain head recorded on ``GraphConfig.dummy_chains`` it walks the head-to-tail
dummy chain, computing each endpoint's compound ancestor path via a post-order
``[low, lim]`` interval numbering (``_postorder``) and the lowest common
ancestor (``_find_path``), then re-parents each dummy onto whichever compound
path node's rank range spans it -- ascending up the ``v -> lca`` arm while the
path node's ``max_rank`` is below the dummy's rank, then descending the ``lca
-> w`` arm while the next path node's ``min_rank`` still fits -- so a dummy
that crosses a subgraph boundary lands inside the subgraph rather than at the
top level. It is the **first zero-semantic-clone leaf**: unlike R250
``normalize`` and R251 ``acyclic`` (each keeps two deep copies around an
invalidating ``remove_edge`` / ``remove_node``), this stage performs no
structural removal -- every grok ``clone()`` is a pure borrow artefact
(immutable ``str`` / ``int`` / ``tuple`` / ``Edge`` / ``Option<String>``) and
is stripped. Same barrel policy: the stage symbol stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.parent_dummy_chains``.
"""
