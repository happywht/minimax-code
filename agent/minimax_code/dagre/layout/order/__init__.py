"""``order`` sub-package barrel (R258+, vendored ``third_party/dagre_rust``).

Mirrors the ``layout/order`` directory of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
within-rank node-ordering stage of the layered layout pipeline -- the
crossing-minimization pass that runs after ``rank`` (R254-R257) assigns
every node a rank and before ``position`` (unmigrated) computes
coordinates. ``run_layout`` calls ``order`` at ``layout/mod.rs`` line 632,
between ``add_border_segments`` (line 631, R249) and the position phase --
once every node carries a rank and the long-edge / border dummies are in
place, ``order`` decides the left-to-right sequence of nodes within each
rank to minimize edge crossings (Gansner et al., "A Technique for Drawing
Directed Graphs", section 4).

grok's ``order`` module splits into nine files -- the largest layout
sub-package (``rank`` was four): ``order::init_order`` (the deterministic
seed ordering), ``order::cross_count`` (the Barth bilinear cross-counting
measure), ``order::barycenter`` (the barycentric heuristic weights),
``order::build_layer_graph`` (the per-layer sort graph + the
``GraphRelationship`` enum), ``order::sort_subgraph`` / ``order::sort`` /
``order::resolve_conflicts`` (the sweep sub-graph sorters), and
``order::add_subgraph_constraints`` (compound-graph constraints), all
orchestrated by ``order::mod`` (the ``order()`` dispatcher that sweeps up
and down the ranks, keeping the best-crossing-count layering). Leaves
migrate file-by-file; ``order::mod`` lands only after every stage it
calls is in place. This is the **second layout sub-package** (sibling to
``rank`` R254-R257); like ``rank`` it mirrors grok's
directory-of-files shape rather than a single-file module, because it
bundles an init ordering, a crossing measure, and a multi-pass sweep
heuristic behind one dispatcher.

First ``order`` leaf (R258): ``init_order`` -- the self-contained
deterministic seed ordering
(:func:`~minimax_code.dagre.layout.order.init_order.init_order`).
``order::mod`` calls it first (``layout/order/mod.rs`` line 41) to mint
the initial ``layering`` matrix every later sweep refines; with no
crossings yet computed it walks every leaf node's DFS successor tree,
placing each first-visited node into the layer of its rank. It is the
**seventh zero-semantic-clone leaf** after R252 / R253 / R254 / R255 /
R256 / R257 (every grok ``clone()`` is a ``String`` / ``i32`` / ``usize``
borrow-or-Copy artefact, stripped; the stage performs no structural
removal, so no ``copy.deepcopy`` survives). Same barrel policy as the
sibling layout stages: the symbol stays out of ``dagre.__all__``,
reachable only as
``minimax_code.dagre.layout.order.init_order.init_order``. The barrel
re-exports the ``init_order`` submodule (mirroring grok's ``pub mod
init_order`` visibility); the remaining eight ``order`` files land in
later leaves, re-exported here as they arrive.

Second ``order`` leaf (R259): ``cross_count`` -- the Barth bilinear
cross-counting measure
(:mod:`~minimax_code.dagre.layout.order.cross_count`). ``order::mod``'s
sweep loop calls :func:`cross_count` after every up/down pass to score
the candidate ``layering`` against the prior best, so this is the
**first direct downstream consumer** of the R258 ``init_order`` seed
ordering: ``init_order`` mints the initial matrix, ``cross_count``
measures how many edge crossings it has, and the barycenter sweep
(later leaves) keeps the best-crossing-count ``layering`` across
iterations. The algorithm (Barth, Mutzel & Yannakakis, "Bilayer Cross
Counting", JGAA 2004) collects every north -> south edge as a
``(south_position, weight)`` pair and accumulates them through a
complete binary tree, turning the naive O(k^2) pairwise comparison
into O(k log k). It is the **eighth zero-semantic-clone leaf** after
R252 / R253 / R254 / R255 / R256 / R257 / R258 (every grok ``clone()``
is a ``String`` / ``usize`` borrow-or-Copy artefact, stripped; the
stage performs no structural removal, so no ``copy.deepcopy``
survives). Same barrel policy: the symbol stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.order.cross_count.cross_count``. The barrel
now re-exports ``cross_count`` alongside ``init_order``.
"""

from minimax_code.dagre.layout.order import cross_count, init_order

__all__ = ["cross_count", "init_order"]
