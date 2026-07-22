"""``rank`` sub-package barrel (R254+, vendored ``third_party/dagre_rust``).

Mirrors the ``layout/rank`` directory of the vendored ``dagre_rust`` 0.0.5
crate (upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the
rank-layer assignment stage of the layered layout pipeline.
``run_layout`` calls ``rank`` at ``layout/mod.rs`` line 620, between
``nesting_graph::run`` (line 617) and ``nesting_graph::cleanup`` (line
625) -- after the compound-graph nesting scaffolding is erected and
before it is torn down, so ranks can keep every subgraph vertically
compact and bordered.

grok's ``rank`` module splits into four files: ``rank::util``
(``longest_path`` + ``slack``, the shared primitives),
``rank::network_simplex`` (the heavyweight default ranker),
``rank::feasible_tree`` (the tight-edge spanning tree the network-simplex
loop pivots on), and ``rank::mod`` (the ``rank()`` dispatcher that reads
``GraphConfig.ranker`` and delegates to one of the three strategies).
Leaves migrate file-by-file; ``rank::mod`` lands only after every
strategy it dispatches to is in place. This is the **first layout
sub-package** (sibling stages ``coordinate_system`` / ``util`` /
``add_border_segments`` / ``normalize`` / ``acyclic`` /
``parent_dummy_chains`` / ``nesting_graph`` are single-file modules; only
``rank`` mirrors grok's directory-of-files shape, because it bundles
three independent ranker strategies behind one dispatcher).

First ``rank`` leaf (R254): ``util`` -- the self-contained foundation
(:func:`~minimax_code.dagre.layout.rank.util.longest_path` seed ranker +
:func:`~minimax_code.dagre.layout.rank.util.slack` edge measure). Same
barrel policy as the sibling layout stages: the symbols stay out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.rank.util.longest_path`` / ``.slack``. The
barrel re-exports the ``util`` submodule (mirroring grok's ``pub mod
util`` visibility) so ``from minimax_code.dagre.layout.rank import util``
works once ``rank::mod`` is wired.

Second ``rank`` leaf (R255): ``feasible_tree`` -- the tight-edge spanning
tree (:func:`~minimax_code.dagre.layout.rank.feasible_tree.feasible_tree`)
that ``network_simplex`` pivots on and the body of the ``tight-tree``
ranker strategy (``rank::mod`` dispatches ``tight-tree`` to
``longest_path`` then ``feasible_tree``). Consumes the R254 ``slack``
primitive and is the direct upstream of the unmigrated ``network_simplex``
ranker. Same barrel policy: ``feasible_tree`` stays out of
``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.rank.feasible_tree.feasible_tree``. The barrel
re-exports the ``feasible_tree`` submodule alongside ``util`` (mirroring
grok's ``pub mod feasible_tree`` visibility).

Third ``rank`` leaf (R256): ``network_simplex`` -- the heavyweight default
ranker (:func:`~minimax_code.dagre.layout.rank.network_simplex.network_simplex`)
that ``rank::mod`` dispatches the unmatched / ``"network-simplex"`` arms
to. Simplifies ``g``, seeds it with R254 ``longest_path``, builds an R255
``feasible_tree`` tight tree, then pivots negative-cut-value tree edges
for smaller-slack non-tree edges until no cut value is negative, finally
copying the optimized ranks back onto ``g``. Consumes R254
``util.longest_path`` / ``util.slack``, R255 ``feasible_tree``, R248
``util.simplify``, and R245 ``data_structures.algo.postorder`` -- all
four upstream deps now closed. Same barrel policy: ``network_simplex``
stays out of ``dagre.__all__``, reachable only as
``minimax_code.dagre.layout.rank.network_simplex.network_simplex``. The
barrel re-exports the ``network_simplex`` submodule alongside
``feasible_tree`` and ``util`` (mirroring grok's ``pub mod
network_simplex`` visibility). Only ``rank::mod`` (the dispatcher)
remains for a later leaf.
"""

from minimax_code.dagre.layout.rank import feasible_tree, network_simplex, util

__all__ = ["feasible_tree", "network_simplex", "util"]
