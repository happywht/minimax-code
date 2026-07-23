"""``position`` sub-package barrel (R266+, vendored ``third_party/dagre_rust``).

Mirrors the ``layout/position`` directory of the vendored ``dagre_rust``
0.0.5 crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
horizontal-coordinate-assignment stage of the layered layout pipeline --
the pass that runs after ``order`` (R258-R265) decides the within-rank
left-to-right sequence of every node and before ``position::mod``
(unmigrated) stamps the final ``x`` / ``y`` coordinates on each node.
``run_layout`` calls ``position`` at ``layout/mod.rs`` line 633, right
after ``order`` (line 632, R265 closes the ``order`` sub-package at 9/9)
-- once every node carries a rank and an ``order``, ``position`` decides
the exact ``x`` pixel of each node so that no two blocks overlap and the
edge routes stay compact.

grok's ``position`` module splits into two files -- the third layout
sub-package (sibling to ``rank`` R254-R257 + ``order`` R258-R265):
``position::bk`` (the Brandes-Kopf "Fast and Simple Horizontal Coordinate
Assignment" four-alignment algorithm -- type-1 / type-2 conflict
detection, vertical alignment, horizontal compaction, and balance) and
``position::mod`` (the ``position()`` orchestrator that wraps ``bk``'s
``position_x`` with a ``position_y`` rank-sep stacking pass and projects
the non-compound view via ``util::as_non_compound_graph``), orchestrated
by ``position::mod::position`` (the single public entry the top-level
``run_layout`` imports). Leaves migrate file-by-file; ``position::mod``
lands only after ``bk`` is in place (it is the only caller of
``position_x``). Like ``rank`` + ``order`` this sub-package mirrors
grok's directory-of-files shape rather than a single-file module,
because it bundles a 700-line coordinate-assignment algorithm behind a
thin orchestrator.

First ``position`` leaf (R266): ``bk`` -- the Brandes-Kopf four-alignment
horizontal-coordinate-assignment algorithm
(:mod:`~minimax_code.dagre.layout.position.bk`). ``position::mod``'s
``position_x`` call (``layout/position/bk.rs`` line 703 -- the public
entry point of this leaf) is the **only consumer** of the entire ``bk``
module; the orchestrator runs it on the non-compound projection of the
graph, then stamps each node's ``x`` from the returned ``xs`` map. The
algorithm is Brandes & Kopf, "Fast and Simple Horizontal Coordinate
Assignment" (JGAA 2001): it detects type-1 conflicts (a non-inner-segment
edge crossing an inner segment -- :func:`_find_type_1_conflicts`) and
type-2 conflicts (involving the R249 ``add_border_segments`` border
dummies -- :func:`find_type_2_conflicts`), then for each of the four
alignment directions ``ul`` / ``ur`` / ``dl`` / ``dr`` it vertically
aligns each node with a conflict-free neighbor
(:func:`vertical_alignment`), compacts the aligned blocks horizontally
(:func:`horizontal_compaction` via the :func:`build_block_graph` block
separator DAG + the :func:`_iterate` stack-based longest-path pass),
picks the narrowest alignment (:func:`find_smallest_width_alignment`),
shifts the other three to share its left-most / right-most edge
(:func:`_align_coordinates`), and finally balances the four into one
per-node ``x`` (:func:`balance`). It is the **sixteenth
zero-semantic-clone leaf** (the first ``position`` leaf) after R252 /
R253 / R254 / R255 / R256 / R257 / R258 / R259 / R260 / R261 / R262 /
R263 / R264 / R265: every grok ``clone()`` is a borrow-or-Copy artefact
(``Option<f32>`` Copy reads, ``String`` immutable-str borrows, ``&Edge``
releases for ``&mut g`` reborrow, ``Option<&GraphNode>`` borrow releases)
stripped on the Python side -- with one faithful deep-copy exception:
``balance`` clones the four-alignment ``xss`` map because it rewrites
the ``ul`` alignment in place while still reading the other three for
the median computation (a :math:`(xs_1 + xs_2) / 2` average over the
four per-node coordinates, where ``xs_1`` / ``xs_2`` are the second- and
third-order statistics). The grok nested ``fn`` closures
(``vertical_alignment``'s median-index sweep, ``_sep``'s separator
closure, ``find_smallest_width_alignment``'s ``width_of`` inner) are
promoted to module-level private helpers (``_predecessors_of`` /
``_successors_of`` / ``_sep`` / the nested ``width_of`` stays nested as
a pure read of the captured ``g``) to avoid Python closure-capture
over-capture and to keep ``__all__`` clean. The ``block_graph`` is a
plain ``Graph()`` (no opts, no factory) because it only needs topology +
a per-edge ``f32`` separator label -- it never reads a node label, so
``set_node(v_root, None)`` mints the key and ``None`` is the faithful
``Option<String>`` = ``None`` default. The R249 ``border_type`` field on
``GraphNode`` is the discriminator :func:`_pass2` reads to keep the
outermost border sentinel pinned at its compacted coordinate (the
``node.border_type != border_type`` guard), and the R246 ``GraphConfig``
``align`` / ``nodesep`` / ``edgesep`` fields drive the
``balance`` tie-break and the ``_sep`` separator. Same barrel policy as
the sibling layout stages: every ``bk`` symbol stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.position.bk.position_x``. The barrel
re-exports the ``bk`` submodule (mirroring grok's ``pub mod bk``
visibility); the second ``position`` file (``mod``) lands in a later
leaf, re-exported here as it arrives.
"""

from minimax_code.dagre.layout.position import bk

__all__ = ["bk"]
