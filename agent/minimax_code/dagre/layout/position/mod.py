"""dagre layout position orchestrator (R267, vendored ``third_party/dagre_rust``).

Mirrors ``layout/position/mod.rs`` of the vendored ``dagre_rust`` 0.0.5 crate
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0): the **orchestration
layer** of the horizontal-coordinate-assignment stage -- the thin ``position()``
entry point that the top-level ``run_layout`` (``layout/mod.rs``) calls at line
633, right after ``order`` (R265) has fixed every node's within-rank
left-to-right ``order``. ``position`` is the third and final layout sub-package
(sibling to ``rank`` R254-R257 + ``order`` R258-R265), and this leaf closes it
at 2/2 -- once every node carries a ``rank`` + ``order``, ``position`` decides
the exact ``x`` / ``y`` pixel of each so that no two blocks overlap and the edge
routes stay compact.

grok's ``position::mod`` is a single public ``position()`` orchestrator plus a
private ``position_y()`` rank-sep stacking helper:

* ``position(g)`` projects ``g`` onto its non-compound leaf-only view via
  :func:`util.as_non_compound_graph` (R248) so ``position_x`` (R266) and
  ``position_y`` operate on the same flattened graph that ``rank`` did,
  then runs ``position_y`` to stamp a ``y`` on every node, then ``position_x``
  to stamp an ``x``, and finally writes each ``(x, y)`` back onto the original
  compound graph. The ``y`` is read off the non-compound projection (``ncg``)
  rather than recomputed because ``position_y`` already wrote it there;
  the original ``g`` may carry compound parents whose nodes were skipped by
  the projection, so the write-back loop iterates ``position_x``'s ``xs`` map
  (leaf nodes only).
* ``position_y(g)`` walks the per-rank layer matrix (R248
  :func:`util.build_layer_matrix`), and for each layer stamps every node's
  ``y`` to ``prev_y + max_height / 2`` (centred on the layer's tallest node)
  then advances ``prev_y`` by ``max_height + ranksep`` (the R246
  ``GraphConfig.ranksep`` inter-rank gap). Layers stack top-to-bottom.

This is the **seventeenth zero-semantic-clone leaf** (the second ``position``
leaf, the final leaf of the dagre layout stack's position sub-package) after
R266: every grok ``clone()`` in this file is a borrow-or-Copy artefact
(``ranksep.clone()`` is a no-op ``f32`` Copy read) and is stripped on the
Python side. Two faithful translation choices:

* ``g.node_mut(v).unwrap()`` / ``ncg.node(v).unwrap()`` / ``g.node(v).unwrap()``
  -> ``assert node is not None  # grok: ...unwrap()``: the grok ``unwrap`` panics
  on an absent node; Python mirrors it as an assert (defensive -- every node in
  ``position_x``'s ``xs`` and every node in a layer matrix entry is guaranteed
  present by construction), matching the R248 ``util.py`` convention
  (``assert node is not None  # grok: g.node(v).unwrap()``).
* ``ranksep.clone().unwrap()`` -> ``assert rank_sep is not None``: the
  ``.clone()`` is an ``f32`` Copy no-op, ``.unwrap()`` panics on ``None``.
  ``GraphConfig.ranksep`` defaults to ``50.0`` (R246) and ``as_non_compound_graph``
  deep-copies the config onto ``ncg``, so the value is always set by the time
  ``position_y`` runs; the assert preserves grok's panic-on-None contract.

Two integration details specific to this orchestrator:

* ``position_x(&mut ncg).iter().for_each(|(v, x)| { ... })`` -> ``for v, x in
  position_x(ncg).iter()``: R266's ``position_x`` returns an
  :class:`~minimax_code.data_structures.ordered_hashmap.OrderedHashMap` and
  ``OrderedHashMap.iter()`` yields ``(key, value)`` pairs in insertion order
  -- the exact ``f32`` analogue of grok's ``HashMap::iter``. The loop body
  writes ``node.x = x`` and ``node.y = ncg.node(v).y`` (the ``y`` is read off
  the projection, where ``position_y`` already wrote it).
* ``max_height`` truncation: grok maps each ``height`` through ``as i32``
  before the ``max().unwrap_or(0) as f32``. Python mirrors this with
  ``int(height)`` so a fractional ``height`` (e.g. ``10.5``) contributes its
  integer part (``10``) -- a faithful carry-over of grok's pixel-rounding,
  preserved verbatim rather than "fixed" to a true ``f32`` max. An empty layer
  yields ``max(..., default=0)`` (grok ``unwrap_or(0)``).

Visibility mirrors grok: ``position`` is ``pub fn`` (the single public entry
``run_layout`` imports) and ``position_y`` is a private ``fn`` (no ``pub``);
the migration keeps ``position_y`` out of ``__all__``. Same barrel policy as
the sibling layout stages: ``position`` earns no crate-root
:class:`~minimax_code.dagre` ``__all__`` slot (the R246 barrel count stays at
4) and is reachable only as ``minimax_code.dagre.layout.position.mod.position``
(or, after this leaf, re-exported through the ``position`` sub-package barrel).
Bare ``Graph`` type annotations (no ``GraphConfig`` / ``GraphNode`` /
``GraphEdge`` imports) match R266 ``bk.py`` -- the orchestrator never names a
node/edge label type, so importing them would be an ``F401`` unused-import.
"""

from __future__ import annotations

from minimax_code.dagre.layout.position.bk import position_x
from minimax_code.dagre.layout.util import as_non_compound_graph, build_layer_matrix
from minimax_code.data_structures import Graph

__all__ = ["position"]


def position(g: Graph) -> None:
    """Stamp final ``(x, y)`` coordinates on every leaf node (mirrors grok ``position``).

    Projects ``g`` onto its non-compound leaf-only view, runs ``position_y``
    to stack layers by ``ranksep`` (writing ``y`` on the projection), runs
    ``position_x`` (R266) to assign ``x`` on the projection, then writes each
    leaf's ``(x, y)`` back onto the original graph -- the ``y`` is read off the
    projection where ``position_y`` already wrote it.
    """
    ncg = as_non_compound_graph(g)

    position_y(ncg)
    for v, x in position_x(ncg).iter():
        node = g.node_mut(v)
        assert node is not None  # grok: g.node_mut(v).unwrap()
        node.x = x
        ncg_node = ncg.node(v)
        assert ncg_node is not None  # grok: ncg.node(v).unwrap()
        node.y = ncg_node.y


def position_y(g: Graph) -> None:
    """Stack layers vertically by ``ranksep`` and stamp ``y`` on every node.

    Walks the per-rank layer matrix, centres each layer's nodes at
    ``prev_y + max_height / 2`` (the tallest node in the layer), then advances
    ``prev_y`` by ``max_height + ranksep``. The per-node ``height`` is truncated
    to its integer part before the max (mirrors grok ``height as i32``).
    """
    layering = build_layer_matrix(g)
    rank_sep = g.graph().ranksep
    assert rank_sep is not None  # grok: g.graph().ranksep.clone().unwrap()

    prev_y = 0.0
    for layer in layering:
        # grok: layer.iter().map(|v| g.node(v).unwrap().height as i32)
        #                .max().unwrap_or(0) as f32 -- the ``as i32`` truncation
        # is preserved as ``int(height)``; an empty layer yields 0.
        max_height = float(
            max((int(g.node(v).height) for v in layer), default=0),
        )

        for v in layer:
            node = g.node_mut(v)
            assert node is not None  # grok: g.node_mut(v).unwrap()
            node.y = prev_y + max_height / 2.0

        prev_y += max_height + rank_sep
